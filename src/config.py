from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_DIR = DATA_DIR / "db"
EXPORTS_DIR = DATA_DIR / "exports"
POLICIES_DIR = DATA_DIR / "policies"

DB_PATH = DB_DIR / "wbmonitor.db"

INDICEPA_CKAN_BASE = "https://www.indicepa.gov.it/ipa-dati/api/3/action"
INDICEPA_RESOURCE_ID = "d09adf99-dc10-4349-8c53-27b1e5aa97b6"

MAX_PARALLEL = 5

for d in (DB_DIR, EXPORTS_DIR, POLICIES_DIR):
    d.mkdir(parents=True, exist_ok=True)
