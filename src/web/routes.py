"""Web routes for dashboard, search, detail, open data, trends."""

from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.config import EXPORTS_DIR
from src.db import query_db


def register_routes(app: FastAPI, templates: Jinja2Templates):

    @app.get("/")
    async def index(request: Request):
        kpi = compute_dashboard_kpi()
        software = get_software_distribution()
        by_region = get_region_breakdown()
        scan_info = get_latest_scan_info()
        return templates.TemplateResponse(request, "index.html", {
            "kpi": kpi,
            "software": software,
            "by_region": by_region,
            "scan_info": scan_info,
        })

    @app.get("/ricerca")
    async def search(
        request: Request,
        q: str = Query("", description="Search term"),
        regione: str = Query("", description="Filter by region"),
        categoria: str = Query("", description="Filter by category"),
        has_channel: str = Query("", description="Filter by channel"),
        software: str = Query("", description="Filter by software"),
        page: int = Query(1, ge=1),
    ):
        per_page = 50
        max_run = query_db("SELECT MAX(id) as id FROM scan_run", one=True)
        max_run_id = max_run["id"] if max_run else 0

        filters, params = build_search_filters(q, regione, categoria, has_channel, software)
        where = f"WHERE {' AND '.join(filters)}" if filters else ""

        count_params = [max_run_id] + params
        total = query_db(
            f"SELECT COUNT(*) as c FROM pa p LEFT JOIN pa_scan s ON s.cod_amm = p.cod_amm AND s.scan_run_id = ? {where}",
            count_params, one=True
        )
        total_count = total["c"] if total else 0
        total_pages = max(1, (total_count + per_page - 1) // per_page)

        results = query_db(f"""
            SELECT p.cod_amm, p.denominazione, p.regione, p.categoria, p.sito_web,
                   s.wb_section_found, s.wb_digital_channel, s.wb_channel_reachable,
                   s.wb_anonymous_allowed, s.wb_software, s.wb_policy_visible,
                   s.rpct_email, s.site_reachable
            FROM pa p
            LEFT JOIN pa_scan s ON s.cod_amm = p.cod_amm
                AND s.scan_run_id = ?
            {where}
            ORDER BY p.regione, p.denominazione
            LIMIT ? OFFSET ?
        """, [max_run_id] + params + [per_page, (page - 1) * per_page])

        regioni = query_db("SELECT DISTINCT regione FROM pa WHERE regione != '' ORDER BY regione")
        categorie = query_db("SELECT DISTINCT categoria FROM pa WHERE categoria != '' ORDER BY categoria")
        softwares = query_db("""
            SELECT DISTINCT wb_software FROM pa_scan
            WHERE wb_software IS NOT NULL AND wb_software != ''
            ORDER BY wb_software
        """)

        return templates.TemplateResponse(request, "search.html", {
            "results": results,
            "q": q, "regione": regione, "categoria": categoria,
            "has_channel": has_channel, "software": software,
            "page": page, "total_pages": total_pages, "total_count": total_count,
            "regioni": [r["regione"] for r in regioni],
            "categorie": [r["categoria"] for r in categorie],
            "softwares": [r["wb_software"] for r in softwares],
        })

    @app.get("/dettaglio/{cod_amm}")
    async def detail(request: Request, cod_amm: str):
        pa = query_db("SELECT * FROM pa WHERE cod_amm = ?", (cod_amm,), one=True)
        if not pa:
            return RedirectResponse("/ricerca")

        scans = query_db("""
            SELECT s.*, sr.started_at as scan_date
            FROM pa_scan s
            JOIN scan_run sr ON sr.id = s.scan_run_id
            WHERE s.cod_amm = ?
            ORDER BY sr.started_at DESC
        """, (cod_amm,))

        return templates.TemplateResponse(request, "detail.html", {
            "pa": pa,
            "scans": scans,
        })

    @app.get("/opendata")
    async def opendata(request: Request):
        files = []
        if EXPORTS_DIR.exists():
            for f in sorted(EXPORTS_DIR.iterdir(), reverse=True):
                if f.suffix in (".csv", ".xlsx", ".json"):
                    size_kb = f.stat().st_size / 1024
                    files.append({
                        "name": f.name,
                        "size": f"{size_kb:.0f} KB" if size_kb < 1024 else f"{size_kb/1024:.1f} MB",
                        "format": f.suffix[1:].upper(),
                    })
        return templates.TemplateResponse(request, "opendata.html", {
            "files": files,
        })

    @app.get("/opendata/download/{filename}")
    async def download_file(filename: str):
        path = EXPORTS_DIR / filename
        if not path.exists() or not path.is_file():
            return RedirectResponse("/opendata")
        media_types = {
            ".csv": "text/csv",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".json": "application/json",
        }
        return FileResponse(path, filename=filename,
                            media_type=media_types.get(path.suffix, "application/octet-stream"))

    @app.get("/trend")
    async def trend(request: Request):
        scan_runs = query_db("""
            SELECT id, started_at, total_pa, scanned_pa, errors, status
            FROM scan_run ORDER BY started_at DESC LIMIT 12
        """)
        kpi_by_run = []
        for run in scan_runs:
            row = query_db(f"""
                SELECT
                    COUNT(*) as totale,
                    SUM(CASE WHEN site_reachable = 1 THEN 1 ELSE 0 END) as raggiungibili,
                    SUM(CASE WHEN wb_section_found = 1 THEN 1 ELSE 0 END) as sezione_wb,
                    SUM(CASE WHEN wb_digital_channel = 1 THEN 1 ELSE 0 END) as canale_digitale,
                    SUM(CASE WHEN wb_channel_reachable = 1 THEN 1 ELSE 0 END) as canale_accessibile,
                    SUM(CASE WHEN wb_anonymous_allowed = 1 THEN 1 ELSE 0 END) as anonimato,
                    SUM(CASE WHEN wb_policy_visible = 1 THEN 1 ELSE 0 END) as policy_visibile
                FROM pa_scan WHERE scan_run_id = ?
            """, (run["id"],), one=True)
            if row:
                kpi_by_run.append({
                    "scan_date": run["started_at"][:10],
                    "totale": row["totale"],
                    "raggiungibili": row["raggiungibili"],
                    "sezione_wb": row["sezione_wb"],
                    "canale_digitale": row["canale_digitale"],
                    "canale_accessibile": row["canale_accessibile"],
                    "anonimato": row["anonimato"],
                    "policy_visibile": row["policy_visibile"],
                })
        return templates.TemplateResponse(request, "trend.html", {
            "kpi_by_run": kpi_by_run,
        })


def build_search_filters(q, regione, categoria, has_channel, software):
    filters = []
    params = []
    if q:
        filters.append("(p.denominazione LIKE ? OR p.cod_amm LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%"])
    if regione:
        filters.append("p.regione = ?")
        params.append(regione)
    if categoria:
        filters.append("p.categoria = ?")
        params.append(categoria)
    if has_channel == "yes":
        filters.append("s.wb_digital_channel = 1")
    elif has_channel == "no":
        filters.append("(s.wb_digital_channel = 0 OR s.wb_digital_channel IS NULL)")
    if software:
        filters.append("s.wb_software = ?")
        params.append(software)
    return filters, params


def compute_dashboard_kpi():
    row = query_db("""
        SELECT
            COUNT(*) as totale,
            SUM(CASE WHEN s.site_reachable = 1 THEN 1 ELSE 0 END) as raggiungibili,
            SUM(CASE WHEN s.wb_section_found = 1 THEN 1 ELSE 0 END) as sezione_wb,
            SUM(CASE WHEN s.wb_digital_channel = 1 THEN 1 ELSE 0 END) as canale_digitale,
            SUM(CASE WHEN s.wb_channel_reachable = 1 THEN 1 ELSE 0 END) as canale_accessibile,
            SUM(CASE WHEN s.wb_anonymous_allowed = 1 THEN 1 ELSE 0 END) as anonimato,
            SUM(CASE WHEN s.wb_strong_auth_required = 1 THEN 1 ELSE 0 END) as auth_forte,
            SUM(CASE WHEN s.rpct_email IS NOT NULL THEN 1 ELSE 0 END) as rpct_email,
            SUM(CASE WHEN s.wb_email IS NOT NULL THEN 1 ELSE 0 END) as wb_email,
            SUM(CASE WHEN s.wb_policy_visible = 1 THEN 1 ELSE 0 END) as policy_visibile
        FROM pa_scan s
        WHERE s.scan_run_id = (SELECT MAX(id) FROM scan_run)
    """, one=True)
    if not row or not row["totale"]:
        return {}
    t = row["totale"]
    return {
        "totale": t,
        "raggiungibili": row["raggiungibili"],
        "pct_raggiungibili": round((row["raggiungibili"] or 0) / t * 100, 1),
        "sezione_wb": row["sezione_wb"],
        "pct_sezione_wb": round((row["sezione_wb"] or 0) / t * 100, 1),
        "canale_digitale": row["canale_digitale"],
        "pct_canale_digitale": round((row["canale_digitale"] or 0) / t * 100, 1),
        "canale_accessibile": row["canale_accessibile"],
        "pct_canale_accessibile": round((row["canale_accessibile"] or 0) / t * 100, 1),
        "anonimato": row["anonimato"],
        "pct_anonimato": round((row["anonimato"] or 0) / t * 100, 1),
        "auth_forte": row["auth_forte"],
        "pct_auth_forte": round((row["auth_forte"] or 0) / t * 100, 1),
        "rpct_email": row["rpct_email"],
        "pct_rpct_email": round((row["rpct_email"] or 0) / t * 100, 1),
        "wb_email": row["wb_email"],
        "pct_wb_email": round((row["wb_email"] or 0) / t * 100, 1),
        "policy_visibile": row["policy_visibile"],
        "pct_policy_visibile": round((row["policy_visibile"] or 0) / t * 100, 1),
    }


def get_software_distribution():
    rows = query_db("""
        SELECT wb_software, COUNT(*) as cnt
        FROM pa_scan
        WHERE scan_run_id = (SELECT MAX(id) FROM scan_run)
          AND wb_software IS NOT NULL AND wb_software != ''
        GROUP BY wb_software
        ORDER BY cnt DESC
    """)
    return [dict(r) for r in rows]


def get_region_breakdown():
    rows = query_db("""
        SELECT p.regione,
               COUNT(*) as totale,
               SUM(CASE WHEN s.wb_digital_channel = 1 THEN 1 ELSE 0 END) as canale_digitale,
               SUM(CASE WHEN s.wb_anonymous_allowed = 1 THEN 1 ELSE 0 END) as anonimato,
               SUM(CASE WHEN s.wb_policy_visible = 1 THEN 1 ELSE 0 END) as policy_visibile
        FROM pa p
        JOIN pa_scan s ON s.cod_amm = p.cod_amm
        WHERE s.scan_run_id = (SELECT MAX(id) FROM scan_run)
          AND p.regione != ''
        GROUP BY p.regione
        ORDER BY p.regione
    """)
    return [dict(r) for r in rows]


def get_latest_scan_info():
    return query_db(
        "SELECT * FROM scan_run ORDER BY started_at DESC LIMIT 1", one=True
    )
