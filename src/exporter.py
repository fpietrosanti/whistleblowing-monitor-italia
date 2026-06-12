"""Export scan data to CSV, XLSX, and JSON."""

import json
from datetime import datetime

import pandas as pd

from src.config import EXPORTS_DIR
from src.db import get_db

EXPORT_COLUMNS = [
    "cod_amm", "denominazione", "sito_web", "categoria", "regione", "provincia",
    "site_reachable", "wb_section_found", "wb_digital_channel",
    "wb_channel_url", "wb_channel_reachable", "wb_channel_type",
    "wb_requires_auth", "wb_auth_type", "wb_anonymous_allowed",
    "wb_strong_auth_required", "wb_software", "wb_software_version",
    "rpct_email", "rpct_phone", "rpct_name",
    "wb_email", "wb_phone",
    "wb_policy_visible", "wb_policy_url",
]


def get_latest_scan_df():
    with get_db() as db:
        run = db.execute(
            "SELECT id FROM scan_run ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        if not run:
            return pd.DataFrame()

        rows = db.execute("""
            SELECT p.cod_amm, p.denominazione, p.sito_web, p.categoria,
                   p.regione, p.provincia,
                   s.site_reachable, s.wb_section_found, s.wb_digital_channel,
                   s.wb_channel_url, s.wb_channel_reachable, s.wb_channel_type,
                   s.wb_requires_auth, s.wb_auth_type, s.wb_anonymous_allowed,
                   s.wb_strong_auth_required, s.wb_software, s.wb_software_version,
                   s.rpct_email, s.rpct_phone, s.rpct_name,
                   s.wb_email, s.wb_phone,
                   s.wb_policy_visible, s.wb_policy_url
            FROM pa_scan s
            JOIN pa p ON p.cod_amm = s.cod_amm
            WHERE s.scan_run_id = ?
            ORDER BY p.regione, p.denominazione
        """, (run["id"],)).fetchall()

        return pd.DataFrame([dict(r) for r in rows])


def export_csv():
    df = get_latest_scan_df()
    if df.empty:
        return None
    month = datetime.now().strftime("%Y-%m")
    path = EXPORTS_DIR / f"pa_whistleblowing_{month}.csv"
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def export_xlsx():
    df = get_latest_scan_df()
    if df.empty:
        return None
    month = datetime.now().strftime("%Y-%m")
    path = EXPORTS_DIR / f"pa_whistleblowing_{month}.xlsx"

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Dati", index=False)

        kpi = compute_kpi_df(df)
        kpi.to_excel(writer, sheet_name="KPI", index=False)

        by_region = df.groupby("regione").agg(
            totale=("cod_amm", "count"),
            canale_digitale=("wb_digital_channel", "sum"),
            anonimato=("wb_anonymous_allowed", "sum"),
            policy_visibile=("wb_policy_visible", "sum"),
        ).reset_index()
        by_region.to_excel(writer, sheet_name="Per Regione", index=False)

        sw = df[df["wb_software"].notna()].groupby("wb_software").size().reset_index(name="conteggio")
        sw = sw.sort_values("conteggio", ascending=False)
        sw.to_excel(writer, sheet_name="Per Software", index=False)

    return path


def export_json():
    df = get_latest_scan_df()
    if df.empty:
        return None
    month = datetime.now().strftime("%Y-%m")
    path = EXPORTS_DIR / f"pa_whistleblowing_{month}.json"

    total = len(df)
    out = {
        "metadata": {
            "scan_date": datetime.now().strftime("%Y-%m-%d"),
            "total_pa": total,
            "scanned_pa": int(df["site_reachable"].sum()),
            "version": "1.0",
        },
        "kpi": compute_kpi_dict(df),
        "data": json.loads(df.to_json(orient="records", force_ascii=False)),
    }
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def compute_kpi_df(df):
    total = len(df)
    kpis = compute_kpi_dict(df)
    rows = [{"indicatore": k, "valore": v} for k, v in kpis.items()]
    return pd.DataFrame(rows)


def compute_kpi_dict(df):
    total = len(df)
    if total == 0:
        return {}
    reachable = int(df["site_reachable"].fillna(0).sum())
    digital = int(df["wb_digital_channel"].fillna(0).sum())
    accessible = int(df["wb_channel_reachable"].fillna(0).sum())
    anon = int(df["wb_anonymous_allowed"].fillna(0).sum())
    strong = int(df["wb_strong_auth_required"].fillna(0).sum())
    rpct_pub = int(df["rpct_email"].notna().sum())
    email_ch = int(df["wb_email"].notna().sum())
    policy = int(df["wb_policy_visible"].fillna(0).sum())
    section = int(df["wb_section_found"].fillna(0).sum())

    return {
        "totale_pa": total,
        "siti_raggiungibili": reachable,
        "pct_siti_raggiungibili": round(reachable / total * 100, 1),
        "sezione_wb_trovata": section,
        "pct_sezione_wb": round(section / total * 100, 1),
        "canale_digitale": digital,
        "pct_canale_digitale": round(digital / total * 100, 1),
        "canale_accessibile": accessible,
        "pct_canale_accessibile": round(accessible / total * 100, 1),
        "anonimato_supportato": anon,
        "pct_anonimato": round(anon / total * 100, 1) if digital else 0,
        "auth_forte_richiesta": strong,
        "pct_auth_forte": round(strong / total * 100, 1) if digital else 0,
        "contatto_rpct": rpct_pub,
        "pct_contatto_rpct": round(rpct_pub / total * 100, 1),
        "canale_email_tel": email_ch,
        "pct_canale_email_tel": round(email_ch / total * 100, 1),
        "policy_visibile": policy,
        "pct_policy_visibile": round(policy / total * 100, 1),
    }


def export_all():
    print("Exporting CSV...")
    csv_path = export_csv()
    print(f"  -> {csv_path}")
    print("Exporting XLSX...")
    xlsx_path = export_xlsx()
    print(f"  -> {xlsx_path}")
    print("Exporting JSON...")
    json_path = export_json()
    print(f"  -> {json_path}")
    return csv_path, xlsx_path, json_path


if __name__ == "__main__":
    export_all()
