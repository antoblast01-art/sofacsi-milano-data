import asyncio
import json
import re
from importlib import import_module
from datetime import datetime

try:
    BeautifulSoup = import_module("bs4").BeautifulSoup
except ModuleNotFoundError as exc:
    if exc.name != "bs4" and not (exc.name or "").startswith("bs4."):
        raise
    BeautifulSoup = None

try:
    async_playwright = import_module("playwright.async_api").async_playwright
except ModuleNotFoundError as exc:
    if exc.name != "playwright" and not (exc.name or "").startswith("playwright."):
        raise
    async_playwright = None


class CSIMilanoScraper:
    def __init__(self):
        self.url_base = "https://www.csi.milano.it/albo/risultati-calendario-classifiche-squadre.html?q=cla"
        self.sport_ids = {
            "Calcio a 11": "C11",
            "Calcio a 7": "C07",
            "Calcio femminile a 7": "C7F",
            "Calcio master": "AZ",
        }

    @staticmethod
    def _escludi_categoria(nome: str) -> bool:
        """Restituisce True per escludere categorie femminili e campionati a 7 (M/F)."""
        testo = (nome or "").upper()
        e_femminile = bool(re.search(r"\b(FEMMINIL\w*|DONNE|WOMEN|F\.?)\b", testo))
        e_calcio_a_7 = bool(re.search(r"\b(CALCIO\s*A\s*7|A\s*7|C7|C07|C7F)\b", testo))
        return e_femminile or e_calcio_a_7

    async def _rimuovi_banner_cookie(self, page):
        try:
            btn = page.locator("[c_data='c_cookie_main'] button, [c_data='c_cookie_main'] a, .lb-accept")
            if await btn.count() > 0:
                await btn.first.click(force=True)
                await page.wait_for_timeout(400)
        except Exception:
            pass

        await page.evaluate("""() => {
            document.querySelectorAll('[c_data*="cookie"], #lb-cookie-banner, .c--anim').forEach(el => el.remove());
        }""")

    async def _apri_e_seleziona_sport(self, page, sport_id: str):
        await page.goto(self.url_base, wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(1000)
        await self._rimuovi_banner_cookie(page)
        await page.click(f"#sport a[id='{sport_id}']", force=True)
        await page.wait_for_timeout(1800)

    async def estrai_campionati(
        self,
        sport_nome: str = "Calcio a 11",
        max_campionati: int | None = None,
        max_gironi: int | None = None,
        escludi_femminili: bool = True,
    ) -> list[dict]:
        if async_playwright is None:
            raise RuntimeError("Playwright non installato.")

        sport_id = self.sport_ids.get(sport_nome, "C11")
        campionati_estratti = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(viewport={"width": 1280, "height": 900})
            page = await context.new_page()

            print(f"[CSI Milano] Apertura portale per {sport_nome} (#{sport_id})...")
            await self._apri_e_seleziona_sport(page, sport_id)

            campionati = await page.evaluate("""() => {
                const items = Array.from(document.querySelectorAll('#search-filter-wrp .item'));
                const boxCamp = items.find(el => el.querySelector('h2')?.innerText.toUpperCase().includes('CAMPIONAT'));
                if (!boxCamp) return [];
                return Array.from(boxCamp.querySelectorAll('ul li a')).map(a => ({
                    id: a.id,
                    nome: a.innerText.trim()
                })).filter(x => x.nome);
            }""")

            if escludi_femminili:
                campionati = [c for c in campionati if not self._escludi_categoria(c["nome"])]

            print(f"[CSI Milano] Campionati {sport_nome} trovati ({len(campionati)}): {[c['nome'] for c in campionati]}")

            campionati_da_estrarre = campionati[:max_campionati] if max_campionati else campionati

            for camp in campionati_da_estrarre:
                print(f"\n[Campionato] {camp['nome']} (id={camp['id']})")
                await self._apri_e_seleziona_sport(page, sport_id)

                if camp["id"]:
                    await page.click(f"#search-filter-wrp a[id='{camp['id']}']", force=True)
                else:
                    await page.locator("#search-filter-wrp a", has_text=camp["nome"]).first.click(force=True)
                await page.wait_for_timeout(1800)

                gironi = await page.evaluate("""() => {
                    const items = Array.from(document.querySelectorAll('#search-filter-wrp .item'));
                    const boxGir = items.find(el => el.querySelector('h2')?.innerText.toUpperCase().includes('GIRON'));
                    if (!boxGir) return [];
                    return Array.from(boxGir.querySelectorAll('ul li a')).map(a => ({
                        id: a.id,
                        nome: a.innerText.trim()
                    })).filter(x => x.nome);
                }""")

                if escludi_femminili:
                    gironi = [g for g in gironi if not self._escludi_categoria(g["nome"])]

                print(f"  Gironi trovati ({len(gironi)}): {[g['nome'] for g in gironi]}")
                camp_data = {
                    "nome_campionato": camp["nome"],
                    "sport": sport_nome,
                    "gironi": [],
                }
                gironi_da_estrarre = gironi[:max_gironi] if max_gironi else gironi

                for idx_g, gir in enumerate(gironi_da_estrarre):
                    print(f"  -> Estrazione girone: {gir['nome']} (id={gir['id']})")

                    if idx_g > 0:
                        await self._apri_e_seleziona_sport(page, sport_id)
                        if camp["id"]:
                            await page.click(f"#search-filter-wrp a[id='{camp['id']}']", force=True)
                        else:
                            await page.locator("#search-filter-wrp a", has_text=camp["nome"]).first.click(force=True)
                        await page.wait_for_timeout(1800)

                    if gir["id"]:
                        await page.click(f"#search-filter-wrp a[id='{gir['id']}']", force=True)
                    else:
                        await page.locator("#search-filter-wrp a", has_text=gir["nome"]).first.click(force=True)
                    await page.wait_for_timeout(1200)

                    # 1. Clicca su CERCA -> carica Classifica Squadre
                    await page.locator("a.btn-search").first.click(force=True)
                    await page.wait_for_timeout(2500)

                    testo_classifica = await page.inner_text("body")
                    classifica = self.parse_classifica_testo(testo_classifica)

                    # 2. Clicca sul tab interno CLASSIFICA MARCATORI
                    marcatori = []
                    tab_marcatori = page.locator("#search-filter-wrp ~ * a:has-text('CLASSIFICA MARCATORI'), #search-filter-wrp ~ * li:has-text('CLASSIFICA MARCATORI')")
                    if await tab_marcatori.count() > 0:
                        await tab_marcatori.first.click(force=True)
                        await page.wait_for_timeout(1500)
                        marcatori = self.parse_marcatori(await page.content())

                    # 3. Clicca sul tab interno RISULTATI
                    partite = []
                    tab_risultati = page.locator("#search-filter-wrp ~ * a:has-text('RISULTATI'), #search-filter-wrp ~ * li:has-text('RISULTATI')")
                    if await tab_risultati.count() > 0:
                        await tab_risultati.first.click(force=True)
                        await page.wait_for_timeout(2000)
                        partite = self.parse_partite_testo(await page.inner_text("body"))

                    print(f"     Estratte {len(classifica)} squadre, {len(marcatori)} marcatori, {len(partite)} partite.")
                    camp_data["gironi"].append({
                        "nome_girone": gir["nome"],
                        "classifica": classifica,
                        "marcatori": marcatori,
                        "partite": partite,
                    })

                campionati_estratti.append(camp_data)

            await browser.close()
            return campionati_estratti

    def parse_classifica_testo(self, testo_body: str) -> list[dict]:
        classifica = []
        righe = [r.strip() for r in testo_body.splitlines() if r.strip()]
        in_tabella = False

        for riga in righe:
            if "SQUADRA" in riga.upper() and "PUNTI" in riga.upper() and "GIOCATE" in riga.upper():
                in_tabella = True
                continue
            if in_tabella:
                if riga.startswith("©") or "COMITATO TERRITORIALE" in riga.upper():
                    break
                parti = [p.strip() for p in re.split(r"\t+|\s{2,}", riga) if p.strip()]
                if len(parti) >= 10 and parti[0].isdigit() and parti[2].lstrip("-").isdigit():
                    classifica.append({
                        "pos": int(parti[0]),
                        "squadra": parti[1],
                        "pt": int(parti[2]),
                        "g": int(parti[3]),
                        "v": int(parti[4]),
                        "p": int(parti[5]),
                        "n": int(parti[6]),
                        "penalita": int(parti[7]),
                        "gf": int(parti[8]),
                        "gs": int(parti[9]),
                    })
        return classifica

    def parse_marcatori(self, html: str) -> list[dict]:
        soup = BeautifulSoup(html, "html.parser")
        marcatori = []
        ruoli_noti = {"PORTIERE", "DIFENSORE", "CENTROCAMPISTA", "ATTACCANTE"}

        for table in soup.find_all("table"):
            righe = table.find_all("tr")
            if len(righe) < 2:
                continue
            intestazione = [th.get_text(strip=True).upper() for th in righe[0].find_all(["th", "td"])]
            if any("GIOCATORE" in h or "ATLETA" in h or "MARCATOR" in h or "RETI" in h for h in intestazione):
                for tr in righe[1:]:
                    celle = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
                    if len(celle) < 3:
                        continue
                    numeri = [int(re.sub(r"[^\d]", "", c)) for c in celle if re.sub(r"[^\d]", "", c).isdigit()]
                    testi = [c.strip() for c in celle if not re.match(r"^[\d\.\s]+$", c) and len(c.strip()) > 1]

                    ruolo = ""
                    altri_testi = []
                    for t in testi:
                        if t.upper() in ruoli_noti:
                            ruolo = t
                        else:
                            altri_testi.append(t)

                    if altri_testi and numeri:
                        marcatori.append({
                            "giocatore": altri_testi[0],
                            "ruolo": ruolo or "N/D",
                            "squadra": altri_testi[1] if len(altri_testi) > 1 else "",
                            "gol": numeri[-1],
                        })
                if marcatori:
                    break
        return marcatori

    def parse_partite_testo(self, testo_body: str) -> list[dict]:
        partite = []
        righe = [r.strip() for r in testo_body.splitlines() if r.strip()]
        giornata_corrente = "Giornata 01"

        for riga in righe:
            match_giornata = re.match(r"^(GIORNATA\s+\d+)", riga.upper())
            if match_giornata:
                giornata_corrente = match_giornata.group(1).title()
                continue

            match_data = re.search(r"\b(\d{2}/\d{2}/\d{4})\b", riga)
            if not match_data:
                continue

            parti = [p.strip() for p in riga.split("\t") if p.strip()]
            if len(parti) < 4:
                continue

            casa = parti[0]
            ospite = parti[1]
            data_str = match_data.group(1)

            match_ora = re.search(r"\b(\d{2}:\d{2})\b", riga)
            ora_str = match_ora.group(1) if match_ora else "N/D"

            codice_gara = next((p for p in parti[2:] if re.match(r"^\d{6,}[A-Z0-9]+$", p)), "")

            gol_casa = None
            gol_ospite = None
            coda = " ".join(parti[4:])
            match_ris = re.search(r"\b(\d{1,2})\s*-\s*(\d{1,2})\b", coda)
            if match_ris:
                gol_casa = int(match_ris.group(1))
                gol_ospite = int(match_ris.group(2))
            else:
                numeri_coda = [int(p) for p in parti[5:] if p.isdigit()]
                if len(numeri_coda) >= 2:
                    gol_casa, gol_ospite = numeri_coda[0], numeri_coda[1]

            partite.append({
                "codice_gara": codice_gara,
                "giornata": giornata_corrente,
                "data": data_str,
                "ora": ora_str,
                "casa": casa,
                "ospite": ospite,
                "gol_casa": gol_casa,
                "gol_ospite": gol_ospite,
                "stato": "FT" if gol_casa is not None else "PROG",
            })

        return partite


async def main():
    scraper = CSIMilanoScraper()
    sport_da_estrarre = ["Calcio a 11"]

    tutti_i_campionati = []
    for sport in sport_da_estrarre:
        campionati_sport = await scraper.estrai_campionati(
            sport_nome=sport,
            max_campionati=None,
            max_gironi=None,
            escludi_femminili=True,
        )
        tutti_i_campionati.extend(campionati_sport)

    dati = {
        "comitato": "CSI Milano",
        "sport": " / ".join(sport_da_estrarre),
        "aggiornato_il": datetime.now().isoformat(),
        "campionati": tutti_i_campionati,
    }

    with open("csi_milano_calcio.json", "w", encoding="utf-8") as f:
        json.dump(dati, f, ensure_ascii=False, indent=4)

    print("\nEstrazione completata! Controlla 'csi_milano_calcio.json'.")


if __name__ == "__main__":
    asyncio.run(main())