"""Download PA registry from IndicePA CKAN JSON API."""

import httpx
from src.config import INDICEPA_CKAN_BASE, INDICEPA_RESOURCE_ID
from src.db import get_db, init_db

BATCH_SIZE = 1000


def fetch_all_pa():
    """Fetch all PA records from IndicePA CKAN datastore."""
    url = f"{INDICEPA_CKAN_BASE}/datastore_search"
    records = []
    offset = 0
    with httpx.Client(timeout=60) as client:
        while True:
            resp = client.get(
                url,
                params={
                    "resource_id": INDICEPA_RESOURCE_ID,
                    "limit": BATCH_SIZE,
                    "offset": offset,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            result = data.get("result", {})
            batch = result.get("records", [])
            if not batch:
                break
            records.extend(batch)
            offset += BATCH_SIZE
            total = result.get("total", 0)
            print(f"  IndicePA: {len(records)}/{total} records...")
            if len(records) >= total:
                break
    return records


REGIONE_MAP = {
    "01": "Piemonte",
    "02": "Valle d'Aosta",
    "03": "Lombardia",
    "04": "Trentino-Alto Adige",
    "05": "Veneto",
    "06": "Friuli Venezia Giulia",
    "07": "Liguria",
    "08": "Emilia-Romagna",
    "09": "Toscana",
    "10": "Umbria",
    "11": "Marche",
    "12": "Lazio",
    "13": "Abruzzo",
    "14": "Molise",
    "15": "Campania",
    "16": "Puglia",
    "17": "Basilicata",
    "18": "Calabria",
    "19": "Sicilia",
    "20": "Sardegna",
}


def normalize_record(r):
    """Map a CKAN record to our pa table schema."""
    cod_comune_istat = r.get("Codice_comune_ISTAT", "") or ""
    regione_code = cod_comune_istat[:2] if len(cod_comune_istat) >= 2 else ""
    sito = (r.get("Sito_istituzionale", "") or "").strip().rstrip("/")
    if sito and not sito.startswith("http"):
        sito = "http://" + sito
    return {
        "cod_amm": r.get("Codice_IPA", ""),
        "denominazione": r.get("Denominazione_ente", "") or "",
        "sito_web": sito,
        "categoria": r.get("Tipologia", "") or "",
        "regione": REGIONE_MAP.get(regione_code, ""),
        "provincia": "",
        "comune": "",
        "tipologia": r.get("Tipologia", "") or "",
        "cf": r.get("Codice_fiscale_ente", "") or "",
        "indirizzo": (r.get("Indirizzo", "") or "").strip(),
        "cap": (r.get("CAP", "") or "").strip(),
        "mail_pec": (r.get("Mail1", "") or "").strip(),
        "mail2": (r.get("Mail2", "") or "").strip(),
        "resp_nome": (r.get("Nome_responsabile", "") or "").strip(),
        "resp_cognome": (r.get("Cognome_responsabile", "") or "").strip(),
        "resp_titolo": (r.get("Titolo_responsabile", "") or "").strip(),
        "acronimo": (r.get("Acronimo", "") or "").strip(),
    }


def ingest_pa():
    """Full ingest: fetch from IndicePA and upsert into local DB."""
    init_db()
    print("Fetching PA data from IndicePA...")
    raw_records = fetch_all_pa()
    print(f"Fetched {len(raw_records)} records. Upserting into DB...")

    with get_db() as db:
        for r in raw_records:
            nr = normalize_record(r)
            if not nr["cod_amm"]:
                continue
            db.execute(
                """
                INSERT INTO pa (cod_amm, denominazione, sito_web, categoria,
                                regione, provincia, comune, tipologia, cf,
                                indirizzo, cap, mail_pec, mail2,
                                resp_nome, resp_cognome, resp_titolo, acronimo,
                                updated_at)
                VALUES (:cod_amm, :denominazione, :sito_web, :categoria,
                        :regione, :provincia, :comune, :tipologia, :cf,
                        :indirizzo, :cap, :mail_pec, :mail2,
                        :resp_nome, :resp_cognome, :resp_titolo, :acronimo,
                        datetime('now'))
                ON CONFLICT(cod_amm) DO UPDATE SET
                    denominazione=excluded.denominazione,
                    sito_web=excluded.sito_web,
                    categoria=excluded.categoria,
                    regione=excluded.regione,
                    provincia=excluded.provincia,
                    comune=excluded.comune,
                    tipologia=excluded.tipologia,
                    cf=excluded.cf,
                    indirizzo=excluded.indirizzo,
                    cap=excluded.cap,
                    mail_pec=excluded.mail_pec,
                    mail2=excluded.mail2,
                    resp_nome=excluded.resp_nome,
                    resp_cognome=excluded.resp_cognome,
                    resp_titolo=excluded.resp_titolo,
                    acronimo=excluded.acronimo,
                    updated_at=excluded.updated_at
            """,
                nr,
            )

    print(f"Ingest complete: {len(raw_records)} PA records.")


if __name__ == "__main__":
    ingest_pa()
