from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_DIR = DATA_DIR / "db"
EXPORTS_DIR = DATA_DIR / "exports"
POLICIES_DIR = DATA_DIR / "policies"

DB_PATH = DB_DIR / "wbmonitor.db"

INDICEPA_CKAN_BASE = "https://www.indicepa.gov.it/ipa-dati/api/3/action"
INDICEPA_RESOURCE_ID = "d09adf99-dc10-4349-8c53-27b1e5aa97b6"

# WhistleblowingPA registry (whistleblowing.it / GlobaLeaks) — Google Sheet
WBPA_SHEET_ID = "1sremKVmaCn3Lvoc6m11sEie8rf6_tQC3ofO95jEZcjE"

MAX_PARALLEL = 5

# Real Chrome UA — paired with curl_cffi Chrome impersonation (src/fetcher.py)
# so the TLS/HTTP2 fingerprint matches the UA and fingerprint-WAFs don't block us.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

for d in (DB_DIR, EXPORTS_DIR, POLICIES_DIR):
    d.mkdir(parents=True, exist_ok=True)
