"""Ingest RPCT data from ANAC export and reconcile with PA table via CF."""

import json
import logging
from pathlib import Path

from src.db import get_db, init_db

logger = logging.getLogger("wbmonitor.ingest_rpct")

RPCT_JSON_PATH = Path(__file__).resolve().parent.parent / "data" / "rpct_anac.json"


def ingest_rpct(json_path: str | Path | None = None):
    """Load RPCT JSON export from ANAC and store in rpct_anac table.

    Reconciles with the pa table using CODICE_FISCALE_PERSONA_GIURIDICA → pa.cf.
    """
    path = Path(json_path) if json_path else RPCT_JSON_PATH
    if not path.exists():
        logger.error("RPCT JSON file not found: %s", path)
        raise FileNotFoundError(f"RPCT JSON not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        records = json.load(f)

    logger.info("Loaded %d RPCT records from %s", len(records), path.name)

    init_db()

    with get_db() as db:
        db.execute("DELETE FROM rpct_anac")

        matched = 0
        unmatched = 0

        for r in records:
            cf = (r.get("CODICE_FISCALE_PERSONA_GIURIDICA") or "").strip()
            nome = (r.get("NOME_SOGGETTO") or "").strip()
            cognome = (r.get("COGNOME_SOGGETTO") or "").strip()
            rpct_name = f"{nome} {cognome}".strip()
            link_nomina = (r.get("LINK_ATTO_NOMINA") or "").strip()
            data_nomina = (r.get("DATA_NOMINA") or "").strip()
            denom = (r.get("DENOMINAZIONE_PERSONA_GIURIDICA") or "").strip()
            anac_id = r.get("ID")

            pa_row = db.execute(
                "SELECT cod_amm FROM pa WHERE cf = ?", (cf,)
            ).fetchone()
            cod_amm = pa_row[0] if pa_row else None

            if cod_amm:
                matched += 1
            else:
                unmatched += 1

            db.execute("""
                INSERT INTO rpct_anac
                    (anac_id, cf_ente, denominazione_ente, cod_amm,
                     rpct_nome, rpct_cognome, rpct_nome_completo,
                     link_atto_nomina, data_nomina)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                anac_id, cf, denom, cod_amm,
                nome, cognome, rpct_name,
                link_nomina, data_nomina,
            ))

    logger.info(
        "RPCT ingest complete: %d matched to PA (by CF), %d unmatched",
        matched, unmatched,
    )
    print(f"RPCT ingest: {len(records)} records, {matched} matched, {unmatched} unmatched")
    return {"total": len(records), "matched": matched, "unmatched": unmatched}


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    path = sys.argv[1] if len(sys.argv) > 1 else None
    ingest_rpct(path)
