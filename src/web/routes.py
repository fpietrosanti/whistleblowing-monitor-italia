"""Web routes for dashboard, search, detail, open data, trends."""

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.config import DATA_DIR, EXPORTS_DIR
from src.db import query_db

ARCHIVE_ROOT = DATA_DIR / "archive"
_ARCHIVE_ALLOWED = {"screenshot.png", "links.json", "homepage.html", "meta.json"}


def _priv_token():
    """Secret token for private (non-public) dashboards, from env or file."""
    t = os.environ.get("WB_PRIV_TOKEN")
    if t:
        return t.strip()
    f = DATA_DIR / "priv_token.txt"
    if f.exists():
        return f.read_text().strip()
    return None


def _latest_archive(cod_amm: str):
    """Return (date, dir Path) of the most recent archive for a PA, or (None, None)."""
    if not ARCHIVE_ROOT.exists():
        return None, None
    for day in sorted(ARCHIVE_ROOT.iterdir(), reverse=True):
        if not day.is_dir():
            continue
        d = day / cod_amm
        if d.is_dir() and (d / "meta.json").exists():
            return day.name, d
    return None, None


def register_routes(app: FastAPI, templates: Jinja2Templates):

    @app.get("/")
    async def index(request: Request):
        kpi = compute_dashboard_kpi()
        software = get_software_distribution()
        by_region = get_region_breakdown()
        scan_info = get_latest_scan_info()
        net_bucket = compute_net_bucket()
        click_depth = get_click_depth_dist()
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "kpi": kpi,
                "software": software,
                "by_region": by_region,
                "scan_info": scan_info,
                "net_bucket": net_bucket,
                "click_depth": click_depth,
            },
        )

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
        max_run_id = _best_run_id()

        filters, params = build_search_filters(
            q, regione, categoria, has_channel, software
        )
        where = f"WHERE {' AND '.join(filters)}" if filters else ""

        count_params = [max_run_id] + params
        total = query_db(
            f"SELECT COUNT(*) as c FROM pa p LEFT JOIN pa_scan s ON s.cod_amm = p.cod_amm AND s.scan_run_id = ? {where}",
            count_params,
            one=True,
        )
        total_count = total["c"] if total else 0
        total_pages = max(1, (total_count + per_page - 1) // per_page)

        results = query_db(
            f"""
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
        """,
            [max_run_id] + params + [per_page, (page - 1) * per_page],
        )

        regioni = query_db(
            "SELECT DISTINCT regione FROM pa WHERE regione != '' ORDER BY regione"
        )
        categorie = query_db(
            "SELECT DISTINCT categoria FROM pa WHERE categoria != '' ORDER BY categoria"
        )
        softwares = query_db("""
            SELECT DISTINCT wb_software FROM pa_scan
            WHERE wb_software IS NOT NULL AND wb_software != ''
            ORDER BY wb_software
        """)

        return templates.TemplateResponse(
            request,
            "search.html",
            {
                "results": results,
                "q": q,
                "regione": regione,
                "categoria": categoria,
                "has_channel": has_channel,
                "software": software,
                "page": page,
                "total_pages": total_pages,
                "total_count": total_count,
                "regioni": [r["regione"] for r in regioni],
                "categorie": [r["categoria"] for r in categorie],
                "softwares": [r["wb_software"] for r in softwares],
            },
        )

    @app.get("/dettaglio/{cod_amm}")
    async def detail(request: Request, cod_amm: str):
        pa = query_db("SELECT * FROM pa WHERE cod_amm = ?", (cod_amm,), one=True)
        if not pa:
            return RedirectResponse("/ricerca")

        scans = query_db(
            """
            SELECT s.*, sr.started_at as scan_date
            FROM pa_scan s
            JOIN scan_run sr ON sr.id = s.scan_run_id
            WHERE s.cod_amm = ?
            ORDER BY sr.started_at DESC
        """,
            (cod_amm,),
        )

        rpct_anac = query_db(
            "SELECT * FROM rpct_anac WHERE cod_amm = ? ORDER BY data_nomina DESC LIMIT 1",
            (cod_amm,),
            one=True,
        )

        # Per-attempt step ledger for this PA's most recent scan that has one.
        step_run = query_db(
            "SELECT MAX(scan_run_id) as rid FROM pa_scan_step WHERE cod_amm = ?",
            (cod_amm,),
            one=True,
        )
        steps = []
        if step_run and step_run["rid"]:
            steps = query_db(
                """
                SELECT phase, step, seq, method, status, reason, detail
                FROM pa_scan_step
                WHERE cod_amm = ? AND scan_run_id = ?
                ORDER BY id
            """,
                (cod_amm, step_run["rid"]),
            )

        # Claude gold-standard verdict (authoritative), if present.
        gold = query_db(
            "SELECT * FROM gold_label WHERE cod_amm = ? ORDER BY created_at DESC LIMIT 1",
            (cod_amm,),
            one=True,
        )

        # Retry queue status + per-attempt history for this PA.
        retry_q = query_db(
            "SELECT * FROM retry_queue WHERE cod_amm = ? ORDER BY id DESC LIMIT 1",
            (cod_amm,),
            one=True,
        )
        retry_log = query_db(
            "SELECT attempt, error_type, outcome, http_status, new_error, attempted_at "
            "FROM retry_log WHERE cod_amm = ? ORDER BY attempted_at",
            (cod_amm,),
        )

        # Latest dated archive (screenshot/links) for visual diagnosis.
        archive_date, archive_dir = _latest_archive(cod_amm)
        archive = None
        if archive_dir is not None:
            archive = {
                "date": archive_date,
                "has_screenshot": (archive_dir / "screenshot.png").exists(),
                "has_links": (archive_dir / "links.json").exists(),
            }

        return templates.TemplateResponse(
            request,
            "detail.html",
            {
                "pa": pa,
                "scans": scans,
                "rpct_anac": rpct_anac,
                "steps": [dict(s) for s in steps],
                "gold": dict(gold) if gold else None,
                "archive": archive,
                "retry_q": dict(retry_q) if retry_q else None,
                "retry_log": [dict(r) for r in retry_log],
            },
        )

    @app.get("/documentazione")
    async def documentazione(request: Request):
        return templates.TemplateResponse(request, "documentazione.html", {})

    @app.get("/p/{token}/whistleblowingpa")
    async def wbpa_dashboard(
        request: Request,
        token: str,
        q: str = Query(""),
        stato: str = Query(""),
        attive: str = Query(""),
        page: int = Query(1, ge=1),
    ):
        real = _priv_token()
        if not real or token != real:
            raise HTTPException(status_code=404)

        # Summary
        summary = query_db(
            """
            SELECT COUNT(*) tot,
                SUM(CASE WHEN piat_stato='Registrata' THEN 1 ELSE 0 END) registrate,
                SUM(CASE WHEN piat_stato='Cancellata' THEN 1 ELSE 0 END) cancellate,
                SUM(CASE WHEN cod_amm IS NOT NULL THEN 1 ELSE 0 END) riconciliati,
                SUM(CASE WHEN piat_public_link != '' THEN 1 ELSE 0 END) con_pagina_pubblica
            FROM wbpa_registry
            """,
            one=True,
        )
        st = query_db(
            "SELECT link_type, SUM(active) attive, COUNT(*) controllati "
            "FROM wbpa_status GROUP BY link_type",
        )
        status_by_type = {r["link_type"]: dict(r) for r in st}

        # Qualitative analysis stats (rubric v1.0)
        q_outcomes = query_db(
            "SELECT outcome, COUNT(*) c FROM wbpa_quality GROUP BY outcome"
        )
        q_el = query_db(
            """SELECT COUNT(*) tot,
                SUM(has_tema) tema, SUM(has_canale) canale,
                SUM(has_canale_aperto) canale_aperto, SUM(has_rpct) rpct,
                SUM(has_tutele) tutele,
                SUM(has_presupposti) presupposti, SUM(has_distinzione) distinzione,
                SUM(has_anonimato) anonimato, SUM(has_procedura) procedura,
                SUM(has_privacy) privacy, SUM(has_legge) legge
               FROM wbpa_quality""",
            one=True,
        )
        quality = {
            "outcomes": {r["outcome"]: r["c"] for r in q_outcomes},
            "el": dict(q_el) if q_el else {},
        }

        # Health of the WB page URL they gave us (piat_public_link): is it still
        # good? = online (HTTP 200) AND content still confirmed as a WB page.
        # HTTP statuses that mean "blocked" (WAF/IP) rather than genuinely down —
        # recorded as a specific "anomalia" cause, not "offline".
        link_health = query_db(
            """
            SELECT
                COUNT(*) tot,
                SUM(CASE WHEN pub=1 AND outcome='confermata' THEN 1 ELSE 0 END) buoni,
                SUM(CASE WHEN pub=1 AND COALESCE(outcome,'')<>'confermata' THEN 1 ELSE 0 END) non_wb,
                SUM(CASE WHEN COALESCE(pub,0)<>1 AND http IN (403,401,406,429) THEN 1 ELSE 0 END) anomalia,
                SUM(CASE WHEN COALESCE(pub,0)<>1 AND http=404 THEN 1 ELSE 0 END) non_trovata,
                SUM(CASE WHEN COALESCE(pub,0)<>1 AND http>=500 THEN 1 ELSE 0 END) errore_server,
                SUM(CASE WHEN COALESCE(pub,0)<>1 AND (http IS NULL OR (http NOT IN (403,401,406,429,404) AND http<500)) THEN 1 ELSE 0 END) offline
            FROM (
                SELECT r.id,
                    (SELECT s.active FROM wbpa_status s WHERE s.wbpa_id=r.id AND s.link_type='public' ORDER BY s.id DESC LIMIT 1) pub,
                    (SELECT s.http_status FROM wbpa_status s WHERE s.wbpa_id=r.id AND s.link_type='public' ORDER BY s.id DESC LIMIT 1) http,
                    (SELECT q.outcome FROM wbpa_quality q WHERE q.wbpa_id=r.id ORDER BY q.id DESC LIMIT 1) outcome
                FROM wbpa_registry r WHERE r.piat_public_link LIKE 'http%'
            )
            """,
            one=True,
        )
        link_health = dict(link_health) if link_health else {}

        per_page = 100
        filters, params = [], []
        if q:
            filters.append("(denominazione LIKE ? OR cf LIKE ?)")
            params += [f"%{q}%", f"%{q}%"]
        if stato:
            filters.append("piat_stato = ?")
            params.append(stato)
        outcome = request.query_params.get("outcome", "")
        if outcome:
            filters.append(
                "EXISTS (SELECT 1 FROM wbpa_quality qq WHERE qq.wbpa_id=wbpa_registry.id AND qq.outcome=?)"
            )
            params.append(outcome)
        # Health filter on the provided WB-page link (piat_public_link).
        salute = request.query_params.get("salute", "")
        _pub = "(SELECT s.active FROM wbpa_status s WHERE s.wbpa_id=wbpa_registry.id AND s.link_type='public' ORDER BY s.id DESC LIMIT 1)"
        _out = "(SELECT q.outcome FROM wbpa_quality q WHERE q.wbpa_id=wbpa_registry.id ORDER BY q.id DESC LIMIT 1)"
        _http = "(SELECT s.http_status FROM wbpa_status s WHERE s.wbpa_id=wbpa_registry.id AND s.link_type='public' ORDER BY s.id DESC LIMIT 1)"
        if salute == "buono":
            filters.append(f"{_pub}=1 AND {_out}='confermata'")
        elif salute == "anomalia":
            filters.append(f"COALESCE({_pub},0)<>1 AND {_http} IN (403,401,406,429)")
        elif salute == "non_trovata":
            filters.append(f"COALESCE({_pub},0)<>1 AND {_http}=404")
        elif salute == "errore_server":
            filters.append(f"COALESCE({_pub},0)<>1 AND {_http}>=500")
        elif salute == "offline":
            filters.append(
                f"wbpa_registry.piat_public_link LIKE 'http%' AND COALESCE({_pub},0)<>1 "
                f"AND ({_http} IS NULL OR ({_http} NOT IN (403,401,406,429,404) AND {_http}<500))"
            )
        elif salute == "non_wb":
            filters.append(f"{_pub}=1 AND COALESCE({_out},'')<>'confermata'")
        where = ("WHERE " + " AND ".join(filters)) if filters else ""

        total = query_db(
            f"SELECT COUNT(*) c FROM wbpa_registry {where}", params, one=True
        )
        total_count = total["c"] if total else 0
        total_pages = max(1, (total_count + per_page - 1) // per_page)

        rows = query_db(
            f"""
            SELECT r.*,
                (SELECT s.active FROM wbpa_status s WHERE s.wbpa_id=r.id AND s.link_type='piat' ORDER BY s.id DESC LIMIT 1) piat_active,
                (SELECT s.http_status FROM wbpa_status s WHERE s.wbpa_id=r.id AND s.link_type='piat' ORDER BY s.id DESC LIMIT 1) piat_http,
                (SELECT s.active FROM wbpa_status s WHERE s.wbpa_id=r.id AND s.link_type='public' ORDER BY s.id DESC LIMIT 1) public_active,
                (SELECT s.http_status FROM wbpa_status s WHERE s.wbpa_id=r.id AND s.link_type='public' ORDER BY s.id DESC LIMIT 1) public_http,
                (SELECT q.outcome FROM wbpa_quality q WHERE q.wbpa_id=r.id ORDER BY q.id DESC LIMIT 1) q_outcome,
                (SELECT q.score FROM wbpa_quality q WHERE q.wbpa_id=r.id ORDER BY q.id DESC LIMIT 1) q_score
            FROM wbpa_registry r
            {where}
            ORDER BY r.denominazione
            LIMIT ? OFFSET ?
            """,
            params + [per_page, (page - 1) * per_page],
        )

        return templates.TemplateResponse(
            request,
            "wbpa_dashboard.html",
            {
                "token": token,
                "summary": dict(summary) if summary else {},
                "status_by_type": status_by_type,
                "quality": quality,
                "link_health": link_health,
                "rows": [dict(r) for r in rows],
                "q": q,
                "stato": stato,
                "outcome": outcome,
                "salute": salute,
                "page": page,
                "total_pages": total_pages,
                "total_count": total_count,
            },
        )

    @app.get("/archive/{archive_date}/{cod_amm}/{fname}")
    async def archive_file(archive_date: str, cod_amm: str, fname: str):
        """Serve a file from the dated homepage archive (screenshot, links, html)."""
        if fname not in _ARCHIVE_ALLOWED:
            return RedirectResponse("/ricerca")
        # Resolve safely under ARCHIVE_ROOT (block path traversal).
        target = (ARCHIVE_ROOT / archive_date / cod_amm / fname).resolve()
        try:
            target.relative_to(ARCHIVE_ROOT.resolve())
        except ValueError:
            return RedirectResponse("/ricerca")
        if not target.is_file():
            return RedirectResponse("/ricerca")
        return FileResponse(str(target))

    @app.get("/opendata")
    async def opendata(request: Request):
        files = []
        if EXPORTS_DIR.exists():
            for f in sorted(EXPORTS_DIR.iterdir(), reverse=True):
                if f.suffix in (".csv", ".xlsx", ".json"):
                    size_kb = f.stat().st_size / 1024
                    files.append(
                        {
                            "name": f.name,
                            "size": f"{size_kb:.0f} KB"
                            if size_kb < 1024
                            else f"{size_kb / 1024:.1f} MB",
                            "format": f.suffix[1:].upper(),
                        }
                    )
        return templates.TemplateResponse(
            request,
            "opendata.html",
            {
                "files": files,
            },
        )

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
        return FileResponse(
            path,
            filename=filename,
            media_type=media_types.get(path.suffix, "application/octet-stream"),
        )

    @app.get("/trend")
    async def trend(request: Request):
        scan_runs = query_db("""
            SELECT id, started_at, total_pa, scanned_pa, errors, status
            FROM scan_run ORDER BY started_at DESC LIMIT 12
        """)
        kpi_by_run = []
        for run in scan_runs:
            row = query_db(
                f"""
                SELECT
                    COUNT(*) as totale,
                    SUM(CASE WHEN site_reachable = 1 THEN 1 ELSE 0 END) as raggiungibili,
                    SUM(CASE WHEN wb_section_found = 1 THEN 1 ELSE 0 END) as sezione_wb,
                    SUM(CASE WHEN wb_digital_channel = 1 THEN 1 ELSE 0 END) as canale_digitale,
                    SUM(CASE WHEN wb_channel_reachable = 1 THEN 1 ELSE 0 END) as canale_accessibile,
                    SUM(CASE WHEN wb_anonymous_allowed = 1 THEN 1 ELSE 0 END) as anonimato,
                    SUM(CASE WHEN wb_policy_visible = 1 THEN 1 ELSE 0 END) as policy_visibile
                FROM pa_scan WHERE scan_run_id = ?
            """,
                (run["id"],),
                one=True,
            )
            if row:
                kpi_by_run.append(
                    {
                        "scan_date": run["started_at"][:10],
                        "totale": row["totale"],
                        "raggiungibili": row["raggiungibili"],
                        "sezione_wb": row["sezione_wb"],
                        "canale_digitale": row["canale_digitale"],
                        "canale_accessibile": row["canale_accessibile"],
                        "anonimato": row["anonimato"],
                        "policy_visibile": row["policy_visibile"],
                    }
                )
        return templates.TemplateResponse(
            request,
            "trend.html",
            {
                "kpi_by_run": kpi_by_run,
            },
        )

    @app.get("/diagnostica")
    async def diagnostica(
        request: Request,
        run_id: int = Query(0, description="Scan run ID (0=best)"),
        q: str = Query("", description="Search term"),
        status: str = Query(
            "", description="Filter: ok, no_site, no_wb, no_channel, error"
        ),
        method: str = Query("", description="Filter by discovery method"),
        page: int = Query(1, ge=1),
    ):
        if run_id == 0:
            run_id = _best_run_id()

        scan_runs = query_db(
            "SELECT id, started_at, total_pa, scanned_pa, errors, status FROM scan_run ORDER BY id DESC"
        )
        current_run = query_db(
            "SELECT * FROM scan_run WHERE id = ?", (run_id,), one=True
        )

        per_page = 50

        # Build diagnostic query with filters
        filters = []
        params = [run_id]
        if q:
            filters.append("(p.denominazione LIKE ? OR p.cod_amm LIKE ?)")
            params.extend([f"%{q}%", f"%{q}%"])
        if status == "ok":
            filters.append("s.wb_section_found = 1")
        elif status == "no_site":
            filters.append("(s.site_reachable = 0 OR s.site_reachable IS NULL)")
        elif status == "no_wb":
            filters.append(
                "s.site_reachable = 1 AND (s.wb_section_found = 0 OR s.wb_section_found IS NULL)"
            )
        elif status == "no_channel":
            filters.append(
                "s.wb_section_found = 1 AND (s.wb_digital_channel = 0 OR s.wb_digital_channel IS NULL)"
            )
        elif status == "error":
            filters.append("s.site_error IS NOT NULL")
        if method:
            if method == "none":
                filters.append(
                    "(s.discovery_method IS NULL OR s.discovery_method = 'none')"
                )
            else:
                filters.append("s.discovery_method LIKE ?")
                params.append(f"{method}%")

        where = ""
        if filters:
            where = "AND " + " AND ".join(filters)

        total_q = query_db(
            f"""
            SELECT COUNT(*) as c FROM pa p
            LEFT JOIN pa_scan s ON s.cod_amm = p.cod_amm AND s.scan_run_id = ?
            WHERE 1=1 {where}
        """,
            params,
            one=True,
        )
        total_count = total_q["c"] if total_q else 0
        total_pages = max(1, (total_count + per_page - 1) // per_page)

        rows = query_db(
            f"""
            SELECT p.cod_amm, p.denominazione, p.regione, p.sito_web, p.mail_pec,
                   s.site_reachable, s.site_http_status, s.site_error,
                   s.wb_section_found, s.wb_section_url, s.discovery_method,
                   s.wb_digital_channel, s.wb_channel_url, s.wb_channel_reachable,
                   s.wb_software, s.wb_anonymous_allowed, s.wb_strong_auth_required,
                   s.rpct_name, s.rpct_email, s.wb_email,
                   s.wb_policy_visible, s.wb_policy_url,
                   s.scan_duration_s, s.render_mode, s.notes
            FROM pa p
            LEFT JOIN pa_scan s ON s.cod_amm = p.cod_amm AND s.scan_run_id = ?
            WHERE 1=1 {where}
            ORDER BY p.denominazione
            LIMIT ? OFFSET ?
        """,
            params + [per_page, (page - 1) * per_page],
        )

        # Completion stats
        completeness = query_db(
            f"""
            SELECT
                COUNT(*) as totale,
                SUM(CASE WHEN s.id IS NOT NULL THEN 1 ELSE 0 END) as scansionate,
                SUM(CASE WHEN s.site_reachable = 1 THEN 1 ELSE 0 END) as raggiungibili,
                SUM(CASE WHEN s.site_reachable = 0 THEN 1 ELSE 0 END) as irraggiungibili,
                SUM(CASE WHEN s.site_error IS NOT NULL THEN 1 ELSE 0 END) as con_errore,
                SUM(CASE WHEN s.wb_section_found = 1 THEN 1 ELSE 0 END) as wb_trovata,
                SUM(CASE WHEN s.wb_digital_channel = 1 THEN 1 ELSE 0 END) as canale_digitale,
                SUM(CASE WHEN s.wb_channel_reachable = 1 THEN 1 ELSE 0 END) as canale_ok,
                SUM(CASE WHEN s.wb_anonymous_allowed = 1 THEN 1 ELSE 0 END) as anonimato,
                SUM(CASE WHEN s.wb_strong_auth_required = 1 THEN 1 ELSE 0 END) as auth_forte,
                SUM(CASE WHEN s.wb_software IS NOT NULL AND s.wb_software != '' THEN 1 ELSE 0 END) as software_id,
                SUM(CASE WHEN s.rpct_name IS NOT NULL AND s.rpct_name != '' THEN 1 ELSE 0 END) as rpct_nome,
                SUM(CASE WHEN s.rpct_email IS NOT NULL AND s.rpct_email != '' THEN 1 ELSE 0 END) as rpct_email,
                SUM(CASE WHEN s.wb_email IS NOT NULL AND s.wb_email != '' THEN 1 ELSE 0 END) as wb_email_ok,
                SUM(CASE WHEN s.wb_policy_visible = 1 THEN 1 ELSE 0 END) as policy_ok,
                SUM(CASE WHEN s.wb_policy_pdf_hash IS NOT NULL THEN 1 ELSE 0 END) as pdf_scaricati
            FROM pa p
            LEFT JOIN pa_scan s ON s.cod_amm = p.cod_amm AND s.scan_run_id = ?
        """,
            (run_id,),
            one=True,
        )

        # Discovery method breakdown
        methods = query_db(
            """
            SELECT
                CASE
                    WHEN discovery_method IS NULL OR discovery_method = 'none' THEN 'non trovata'
                    WHEN discovery_method LIKE 'guess_url:%' THEN 'guess_url'
                    WHEN discovery_method LIKE 'menu_crawl%' THEN 'menu_crawl'
                    WHEN discovery_method LIKE 'sitemap%' THEN 'sitemap'
                    WHEN discovery_method LIKE 'at_drilldown%' THEN 'at_drilldown'
                    WHEN discovery_method LIKE 'deep_crawl%' THEN 'deep_crawl'
                    WHEN discovery_method LIKE 'google%' THEN 'google_fallback'
                    ELSE discovery_method
                END as method_group,
                COUNT(*) as cnt
            FROM pa_scan WHERE scan_run_id = ?
            GROUP BY method_group ORDER BY cnt DESC
        """,
            (run_id,),
        )

        # Error breakdown
        errors = query_db(
            """
            SELECT phase, error_type, COUNT(*) as cnt
            FROM scan_error_log WHERE scan_run_id = ?
            GROUP BY phase, error_type ORDER BY cnt DESC LIMIT 20
        """,
            (run_id,),
        )

        # RPCT ANAC stats
        rpct_stats = query_db(
            """
            SELECT
                (SELECT COUNT(*) FROM rpct_anac) as totale,
                (SELECT COUNT(*) FROM rpct_anac WHERE cod_amm IS NOT NULL) as riconciliati,
                (SELECT COUNT(*) FROM rpct_anac WHERE cod_amm IS NULL) as non_riconciliati
        """,
            one=True,
        )

        # Retry queue: how many transient failures, their status, and the
        # per-attempt outcomes (recovered vs failed).
        retry_status = query_db(
            "SELECT status, COUNT(*) c, SUM(attempts) tot_attempts "
            "FROM retry_queue WHERE scan_run_id = ? GROUP BY status",
            (run_id,),
        )
        retry_by_type = query_db(
            "SELECT error_type, COUNT(*) c, "
            "SUM(CASE WHEN status='recovered' THEN 1 ELSE 0 END) recovered, "
            "SUM(CASE WHEN status='exhausted' THEN 1 ELSE 0 END) exhausted, "
            "SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) pending "
            "FROM retry_queue WHERE scan_run_id = ? GROUP BY error_type ORDER BY c DESC",
            (run_id,),
        )
        retry_attempts = query_db(
            "SELECT outcome, COUNT(*) c FROM retry_log WHERE scan_run_id = ? GROUP BY outcome",
            (run_id,),
        )
        retry = {
            "by_status": {
                r["status"]: {"c": r["c"], "attempts": r["tot_attempts"]}
                for r in retry_status
            },
            "by_type": [dict(r) for r in retry_by_type],
            "attempts": {r["outcome"]: r["c"] for r in retry_attempts},
        }

        # Egress comparison: do connection failures recover when retried from a
        # different IP type (datacenter vs residential vs vpn)? Unmasks IP-based
        # WAF/anti-scraping blocks.
        egress_recovery = [
            dict(r)
            for r in query_db(
                "SELECT COALESCE(egress,'?') egress, outcome, COUNT(*) c "
                "FROM retry_log GROUP BY egress, outcome ORDER BY egress",
            )
        ]

        # Connection funnel: total enti -> with website -> DNS resolves ->
        # TCP/TLS connects -> responds HTTP -> HTTP 200 (the NET bucket on which
        # all other statistics should be compared).
        fr = query_db(
            """
            WITH scanned AS (
                SELECT s.cod_amm, s.site_reachable, s.site_http_status,
                    (SELECT e.error_message FROM scan_error_log e
                       WHERE e.scan_run_id = s.scan_run_id AND e.cod_amm = s.cod_amm
                         AND e.phase = 'homepage_fetch' ORDER BY e.id DESC LIMIT 1) AS emsg,
                    (SELECT e.error_type FROM scan_error_log e
                       WHERE e.scan_run_id = s.scan_run_id AND e.cod_amm = s.cod_amm
                         AND e.phase = 'homepage_fetch' ORDER BY e.id DESC LIMIT 1) AS etype
                FROM pa_scan s WHERE s.scan_run_id = ?
            )
            SELECT
                (SELECT COUNT(*) FROM pa) AS totale_enti,
                (SELECT COUNT(*) FROM pa WHERE sito_web != '') AS con_sito,
                COUNT(*) AS scansionati,
                SUM(CASE WHEN site_reachable = 1 THEN 1 ELSE 0 END) AS risponde_http,
                SUM(CASE WHEN site_reachable = 1 AND site_http_status = 200 THEN 1 ELSE 0 END) AS http_200,
                SUM(CASE WHEN site_reachable = 0 AND (
                        emsg LIKE '%Name or service not known%'
                     OR emsg LIKE '%Temporary failure in name resolution%'
                     OR emsg LIKE '%No address associated%'
                     OR emsg LIKE '%nodename nor servname%') THEN 1 ELSE 0 END) AS dns_fail,
                SUM(CASE WHEN site_reachable = 0 AND (
                        etype = 'ConnectTimeout'
                     OR emsg LIKE '%onnection refused%'
                     OR emsg LIKE '%All connection attempts failed%') THEN 1 ELSE 0 END) AS connect_fail
            FROM scanned
            """,
            (run_id,),
            one=True,
        )
        fr = dict(fr) if fr else {}
        _scans = fr.get("scansionati", 0) or 0
        _dns_fail = fr.get("dns_fail", 0) or 0
        _connect_fail = fr.get("connect_fail", 0) or 0
        conn_funnel = {
            "totale_enti": fr.get("totale_enti", 0) or 0,
            "con_sito": fr.get("con_sito", 0) or 0,
            "scansionati": _scans,
            "risolvono": _scans - _dns_fail,
            "si_collega": _scans - _dns_fail - _connect_fail,
            "risponde_http": fr.get("risponde_http", 0) or 0,
            "http_200": fr.get("http_200", 0) or 0,
        }

        # Click-depth distribution: clicks from homepage to the WB page.
        cd_rows = query_db(
            "SELECT wb_click_depth AS d, COUNT(*) c FROM pa_scan "
            "WHERE scan_run_id = ? AND wb_section_found = 1 "
            "GROUP BY wb_click_depth ORDER BY wb_click_depth",
            (run_id,),
        )
        click_depth = [{"depth": r["d"], "c": r["c"]} for r in cd_rows]

        # Per-attempt step ledger — aggregate per (phase, step): outcomes,
        # how many enti reached the step, and the winning-method / reason mix.
        step_agg = query_db(
            """
            SELECT phase, step,
                   COUNT(DISTINCT cod_amm) as enti,
                   SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) as ok_attempts,
                   SUM(CASE WHEN status='fail' THEN 1 ELSE 0 END) as fail_attempts,
                   SUM(CASE WHEN status='partial' THEN 1 ELSE 0 END) as partial_attempts,
                   SUM(CASE WHEN status='skip' THEN 1 ELSE 0 END) as skip_attempts,
                   COUNT(DISTINCT CASE WHEN status='ok' THEN cod_amm END) as enti_ok
            FROM pa_scan_step WHERE scan_run_id = ?
            GROUP BY phase, step
            ORDER BY phase DESC, enti DESC
        """,
            (run_id,),
        )
        # Winning-method distribution (only successful attempts) per step
        step_methods = query_db(
            """
            SELECT step, method, COUNT(*) as cnt
            FROM pa_scan_step
            WHERE scan_run_id = ? AND status='ok' AND method IS NOT NULL
            GROUP BY step, method ORDER BY step, cnt DESC
        """,
            (run_id,),
        )
        # Failure-reason distribution per step
        step_reasons = query_db(
            """
            SELECT step, reason, COUNT(*) as cnt
            FROM pa_scan_step
            WHERE scan_run_id = ? AND status='fail' AND reason IS NOT NULL
            GROUP BY step, reason ORDER BY step, cnt DESC
        """,
            (run_id,),
        )
        # attach method/reason lists to each step row
        methods_by_step: dict = {}
        for m in step_methods:
            methods_by_step.setdefault(m["step"], []).append(
                {"method": m["method"], "cnt": m["cnt"]}
            )
        reasons_by_step: dict = {}
        for r in step_reasons:
            reasons_by_step.setdefault(r["step"], []).append(
                {"reason": r["reason"], "cnt": r["cnt"]}
            )
        step_ledger = []
        for s in step_agg:
            d = dict(s)
            d["methods"] = methods_by_step.get(s["step"], [])[:6]
            d["reasons"] = reasons_by_step.get(s["step"], [])[:6]
            step_ledger.append(d)

        return templates.TemplateResponse(
            request,
            "diagnostica.html",
            {
                "scan_runs": scan_runs,
                "current_run": current_run,
                "run_id": run_id,
                "rows": rows,
                "completeness": dict(completeness) if completeness else {},
                "methods": [dict(m) for m in methods],
                "errors": [dict(e) for e in errors],
                "rpct_stats": dict(rpct_stats) if rpct_stats else {},
                "step_ledger": step_ledger,
                "retry": retry,
                "egress_recovery": egress_recovery,
                "conn_funnel": conn_funnel,
                "click_depth": click_depth,
                "q": q,
                "status": status,
                "method": method,
                "page": page,
                "total_pages": total_pages,
                "total_count": total_count,
            },
        )


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
    run_id = _best_run_id()
    row = query_db(
        """
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
        WHERE s.scan_run_id = ?
    """,
        (run_id,),
        one=True,
    )
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


def compute_net_bucket():
    """Net bucket = sites that respond HTTP 200; WB metrics relative to it."""
    run_id = _best_run_id()
    row = query_db(
        """
        SELECT
            (SELECT COUNT(*) FROM pa WHERE sito_web != '') AS con_sito,
            SUM(CASE WHEN site_reachable=1 AND site_http_status=200 THEN 1 ELSE 0 END) AS net,
            SUM(CASE WHEN site_http_status=200 AND wb_section_found=1 THEN 1 ELSE 0 END) AS wb,
            SUM(CASE WHEN site_http_status=200 AND wb_digital_channel=1 THEN 1 ELSE 0 END) AS channel,
            SUM(CASE WHEN site_http_status=200 AND wb_anonymous_allowed=1 THEN 1 ELSE 0 END) AS anon
        FROM pa_scan WHERE scan_run_id = ?
        """,
        (run_id,),
        one=True,
    )
    if not row:
        return {}
    net = row["net"] or 0
    base = net or 1
    return {
        "con_sito": row["con_sito"] or 0,
        "net": net,
        "wb": row["wb"] or 0,
        "channel": row["channel"] or 0,
        "anon": row["anon"] or 0,
        "pct_wb": round((row["wb"] or 0) / base * 100, 1),
        "pct_channel": round((row["channel"] or 0) / base * 100, 1),
        "pct_anon": round((row["anon"] or 0) / base * 100, 1),
    }


def get_click_depth_dist():
    run_id = _best_run_id()
    rows = query_db(
        "SELECT wb_click_depth AS d, COUNT(*) c FROM pa_scan "
        "WHERE scan_run_id = ? AND wb_section_found = 1 "
        "GROUP BY wb_click_depth ORDER BY wb_click_depth",
        (run_id,),
    )
    return [{"depth": r["d"], "c": r["c"]} for r in rows]


def get_software_distribution():
    run_id = _best_run_id()
    rows = query_db(
        """
        SELECT wb_software, COUNT(*) as cnt
        FROM pa_scan
        WHERE scan_run_id = ?
          AND wb_software IS NOT NULL AND wb_software != ''
        GROUP BY wb_software
        ORDER BY cnt DESC
    """,
        (run_id,),
    )
    return [dict(r) for r in rows]


def get_region_breakdown():
    run_id = _best_run_id()
    rows = query_db(
        """
        SELECT p.regione,
               COUNT(*) as totale,
               SUM(CASE WHEN s.wb_digital_channel = 1 THEN 1 ELSE 0 END) as canale_digitale,
               SUM(CASE WHEN s.wb_anonymous_allowed = 1 THEN 1 ELSE 0 END) as anonimato,
               SUM(CASE WHEN s.wb_policy_visible = 1 THEN 1 ELSE 0 END) as policy_visibile
        FROM pa p
        JOIN pa_scan s ON s.cod_amm = p.cod_amm
        WHERE s.scan_run_id = ?
          AND p.regione != ''
        GROUP BY p.regione
        ORDER BY p.regione
    """,
        (run_id,),
    )
    return [dict(r) for r in rows]


def _best_run_id():
    """Return the scan_run_id with the most actual pa_scan rows.

    Counts real rows (not the scanned_pa column, which is NULL while a run is
    still in progress) so the dashboard auto-points at the run with the most
    coverage — including a currently-running scan.
    """
    row = query_db(
        """
        SELECT sr.id AS id
        FROM scan_run sr
        LEFT JOIN (
            SELECT scan_run_id, COUNT(*) AS c FROM pa_scan GROUP BY scan_run_id
        ) x ON x.scan_run_id = sr.id
        ORDER BY COALESCE(x.c, 0) DESC, sr.id DESC
        LIMIT 1
        """,
        one=True,
    )
    return row["id"] if row else 0


def get_latest_scan_info():
    run_id = _best_run_id()
    return query_db("SELECT * FROM scan_run WHERE id = ?", (run_id,), one=True)
