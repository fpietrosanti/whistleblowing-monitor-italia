"""Generate realistic mock scan data for all PAs in the database."""

import random
from datetime import datetime, timedelta

from src.db import get_db, init_db

SOFTWARE_DIST = [
    ("GlobaLeaks", 0.55),
    ("Legality Whistleblowing", 0.14),
    ("Segnalazioni.net", 0.10),
    ("WhistleblowerSoftware.com", 0.06),
    ("ISWEB", 0.05),
    ("Comunica WB", 0.04),
    ("Custom/Interno", 0.06),
]

AUTH_TYPES = ["none", "spid", "cie", "internal", "other"]
CHANNEL_TYPES = ["platform", "form", "email_only"]

RPCT_FIRST_NAMES = [
    "Marco", "Giuseppe", "Maria", "Anna", "Francesco", "Laura",
    "Antonio", "Paola", "Giovanni", "Francesca", "Luigi", "Chiara",
    "Roberto", "Sara", "Alessandro", "Elena", "Andrea", "Giulia",
]
RPCT_LAST_NAMES = [
    "Rossi", "Russo", "Ferrari", "Esposito", "Bianchi", "Romano",
    "Colombo", "Ricci", "Marino", "Greco", "Bruno", "Gallo",
    "Conti", "De Luca", "Mancini", "Costa", "Giordano", "Rizzo",
]


def pick_software():
    r = random.random()
    cumulative = 0
    for name, prob in SOFTWARE_DIST:
        cumulative += prob
        if r < cumulative:
            return name
    return SOFTWARE_DIST[-1][0]


def generate_mock_scans(seed=42):
    """Generate mock scan results for all PAs."""
    random.seed(seed)
    init_db()

    with get_db() as db:
        pas = db.execute("SELECT cod_amm, denominazione, sito_web, categoria, regione FROM pa").fetchall()

        if not pas:
            print("No PA records found. Run ingest first.")
            return

        existing = db.execute("SELECT COUNT(*) FROM scan_run").fetchone()[0]
        if existing:
            print(f"Scan data already exists ({existing} runs). Skipping.")
            return

        scan_date = datetime(2026, 6, 1, 2, 0, 0)
        db.execute(
            "INSERT INTO scan_run (started_at, finished_at, total_pa, scanned_pa, errors, status) VALUES (?,?,?,?,?,?)",
            (scan_date.isoformat(), (scan_date + timedelta(hours=18)).isoformat(),
             len(pas), len(pas), int(len(pas) * 0.03), "completed")
        )
        scan_run_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

        count = 0
        for pa in pas:
            cod = pa["cod_amm"]
            has_site = bool(pa["sito_web"] and pa["sito_web"].startswith("http"))

            cat = (pa["categoria"] or "").lower()
            is_large = any(k in cat for k in ("region", "comun", "provincia", "universit", "ministero", "asl", "azienda"))

            site_reachable = has_site and random.random() < 0.85
            http_status = 200 if site_reachable else (random.choice([0, 403, 404, 500, 503]) if has_site else None)

            if site_reachable:
                base_prob = 0.55 if is_large else 0.30
                wb_section_found = random.random() < base_prob
            else:
                wb_section_found = False

            wb_digital_channel = wb_section_found and random.random() < 0.70
            wb_channel_reachable = wb_digital_channel and random.random() < 0.90

            if wb_channel_reachable:
                software = pick_software()
                channel_type = "platform"
                wb_requires_auth = random.random() < 0.15
                auth_type = random.choice(["spid", "cie", "internal"]) if wb_requires_auth else "none"
                wb_anonymous = random.random() < 0.75
                wb_strong_auth = auth_type in ("spid", "cie")
                confidence = round(random.uniform(0.7, 1.0), 2)
                version = f"{random.randint(4, 6)}.{random.randint(0, 15)}" if software == "GlobaLeaks" else None
                site_domain = (pa["sito_web"] or "").replace("https://", "").replace("http://", "").split("/")[0]
                channel_url = f"https://whistleblowing.{site_domain}" if site_domain else None
            else:
                software = None
                channel_type = "email_only" if (wb_section_found and random.random() < 0.4) else None
                wb_requires_auth = None
                auth_type = None
                wb_anonymous = None
                wb_strong_auth = None
                confidence = None
                version = None
                channel_url = None

            if wb_section_found:
                fname = random.choice(RPCT_FIRST_NAMES)
                lname = random.choice(RPCT_LAST_NAMES)
                rpct_name = f"{fname} {lname}"
                domain = (pa["sito_web"] or "example.it").replace("https://", "").replace("http://", "").split("/")[0]
                rpct_email = f"rpct@{domain}" if random.random() < 0.70 else None
                rpct_phone = f"+39 0{random.randint(2,9)}{random.randint(1000000,9999999)}" if random.random() < 0.35 else None
                wb_email = f"segnalazioni@{domain}" if random.random() < 0.50 else None
                wb_phone = f"+39 0{random.randint(2,9)}{random.randint(1000000,9999999)}" if random.random() < 0.10 else None
                policy_visible = random.random() < 0.60
            else:
                rpct_name = rpct_email = rpct_phone = None
                wb_email = wb_phone = None
                policy_visible = False

            render_mode = "browser" if (site_reachable and random.random() < 0.08) else "light"

            wb_section_url = f"{pa['sito_web']}/amministrazione-trasparente/altri-contenuti/prevenzione-della-corruzione" if wb_section_found and pa["sito_web"] else None

            scan_time = scan_date + timedelta(seconds=random.randint(0, 64800))

            db.execute("""
                INSERT INTO pa_scan (
                    scan_run_id, cod_amm, scanned_at,
                    site_reachable, site_http_status, site_error, render_mode,
                    wb_section_found, wb_section_url,
                    wb_digital_channel, wb_channel_url, wb_channel_reachable, wb_channel_type,
                    wb_requires_auth, wb_auth_type, wb_anonymous_allowed, wb_strong_auth_required,
                    wb_software, wb_software_version, wb_software_confidence,
                    rpct_email, rpct_phone, rpct_name,
                    wb_email, wb_phone,
                    wb_policy_visible, wb_policy_url,
                    scan_duration_s
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                scan_run_id, cod, scan_time.isoformat(),
                int(site_reachable) if has_site else None,
                http_status,
                None if site_reachable else ("Connection timeout" if has_site else "No website"),
                render_mode if site_reachable else None,
                int(wb_section_found), wb_section_url,
                int(wb_digital_channel) if wb_digital_channel is not None else 0,
                channel_url,
                int(wb_channel_reachable) if wb_channel_reachable is not None else 0,
                channel_type,
                int(wb_requires_auth) if wb_requires_auth is not None else None,
                auth_type,
                int(wb_anonymous) if wb_anonymous is not None else None,
                int(wb_strong_auth) if wb_strong_auth is not None else None,
                software, version, confidence,
                rpct_email, rpct_phone, rpct_name,
                wb_email, wb_phone,
                int(policy_visible),
                f"{pa['sito_web']}/policy-whistleblowing.pdf" if policy_visible and pa["sito_web"] else None,
                round(random.uniform(1.0, 30.0), 1),
            ))

            count += 1
            if count % 2000 == 0:
                print(f"  Generated {count}/{len(pas)} mock scans...")

    print(f"Mock data generation complete: {count} scans for run #{scan_run_id}.")


if __name__ == "__main__":
    generate_mock_scans()
