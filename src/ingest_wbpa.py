"""Ingest the WhistleblowingPA registry (whistleblowing.it / GlobaLeaks).

Repeatable: downloads the source Google Sheet as CSV, normalizes it, and
upserts into wbpa_registry, reconciling each entity to a PA (cod_amm) by
codice fiscale. This is ground truth for the scraper:
  - piat_link        = the platform channel on *.whistleblowing.it
  - piat_public_link = the WB page on the entity's own site (discovery target)

Definition: an entity whose channel lives on whistleblowing.it is a
"WhistleblowingPA" (GlobaLeaks-based) regardless of the underlying software.

    python -m src.ingest_wbpa
"""

from __future__ import annotations

import csv
import io

import httpx

from src.config import WBPA_SHEET_ID
from src.db import get_db, init_db

CSV_URL = f"https://docs.google.com/spreadsheets/d/{WBPA_SHEET_ID}/export?format=csv"


def _norm_cf(cf: str) -> str:
    return (cf or "").strip().upper()


def fetch_csv() -> list[dict]:
    with httpx.Client(timeout=60, follow_redirects=True) as c:
        r = c.get(CSV_URL)
        r.raise_for_status()
        text = r.content.decode("utf-8", errors="replace")
    return list(csv.DictReader(io.StringIO(text)))


def _cf_to_cod_amm() -> dict:
    mapping = {}
    with get_db() as db:
        for row in db.execute("SELECT cod_amm, cf FROM pa WHERE cf != ''"):
            cf = _norm_cf(row["cf"])
            if cf:
                mapping[cf] = row["cod_amm"]
    return mapping


def ingest() -> None:
    init_db()
    rows = fetch_csv()
    print(f"Fetched {len(rows)} WhistleblowingPA rows")
    cf_map = _cf_to_cod_amm()

    matched = 0
    with get_db() as db:
        db.execute("DELETE FROM wbpa_registry")
        for r in rows:
            cf = _norm_cf(r.get("CODICE FISCALE", ""))
            cod_amm = cf_map.get(cf)
            if cod_amm:
                matched += 1
            db.execute(
                """INSERT INTO wbpa_registry
                       (denominazione, versione, categoria, regione, provincia,
                        piat_stato, piat_link, piat_regist_data, piat_disab_data,
                        piat_canc_data, piat_public, piat_public_link, cf, cod_amm,
                        note, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                (
                    (r.get("ENTE_DENOMINAZIONE") or "").strip(),
                    (r.get("VERSIONE") or "").strip(),
                    (r.get("ENTE_CAT_IPA") or "").strip(),
                    (r.get("ENTE_REGIONE") or "").strip(),
                    (r.get("ENTE_PROVINCIA") or "").strip(),
                    (r.get("PIAT_STATO") or "").strip(),
                    (r.get("PIAT_LINK") or "").strip(),
                    (r.get("PIAT_REGIST_DATA") or "").strip(),
                    (r.get("PIAT_DISAB_DATA") or "").strip(),
                    (r.get("PIAT_CANC_DATA") or "").strip(),
                    (r.get("PIAT_PUBLIC") or "").strip(),
                    (r.get("PIAT_PUBLIC_LINK") or "").strip(),
                    cf,
                    cod_amm,
                    (r.get("NOTE") or "").strip(),
                ),
            )

    print(f"Ingest complete: {len(rows)} rows, {matched} reconciled to a PA by CF")


if __name__ == "__main__":
    ingest()
