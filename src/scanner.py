"""Scanner orchestrator — coordinates the full WB Monitor scanning pipeline.

Entry points:
    run_full_scan()  — async, called programmatically
    main()           — CLI with argparse
"""

import argparse
import asyncio
import logging
import time
from datetime import datetime, timezone

import httpx

from src.browser import (
    close_browser,
    fetch_with_browser,
    init_browser,
    should_use_browser,
)
from src.config import MAX_PARALLEL, USER_AGENT
from src.db import get_db, init_db, save_pa_steps
from src.discovery import discover_wb_section
from src.exporter import export_all
from src.fingerprint import fingerprint_software
from src.logging_config import (
    save_scan_summary,
    setup_scan_logging,
    teardown_scan_logging,
)
from src.policy import download_wb_policy
from src.probe import probe_wb_channel
from src.rpct import extract_rpct_contacts

logger = logging.getLogger("wbmonitor.scanner")


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def _create_scan_run(mode: str = "browser") -> int:
    """Insert a new scan_run row and return its id."""
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO scan_run (started_at, status, mode) VALUES (?, 'running', ?)",
            (datetime.now(timezone.utc).isoformat(), mode),
        )
        return cur.lastrowid


def _finish_scan_run(scan_run_id: int, total: int, scanned: int, errors: int):
    """Mark a scan_run as finished with final stats."""
    with get_db() as db:
        db.execute(
            """UPDATE scan_run
                  SET finished_at = ?, total_pa = ?, scanned_pa = ?,
                      errors = ?, status = 'finished'
                WHERE id = ?""",
            (
                datetime.now(timezone.utc).isoformat(),
                total,
                scanned,
                errors,
                scan_run_id,
            ),
        )


def _get_pa_list(pa_filter: dict | None = None) -> list[dict]:
    """Return list of PAs to scan, optionally filtered.

    pa_filter may contain:
        cod_amm  — list[str]   specific PA codes
        regione  — str         region name
        limit    — int         max number of PAs
    """
    clauses = []
    params: list = []

    if pa_filter:
        if cod_amm_list := pa_filter.get("cod_amm"):
            placeholders = ",".join("?" for _ in cod_amm_list)
            clauses.append(f"cod_amm IN ({placeholders})")
            params.extend(cod_amm_list)
        if regione := pa_filter.get("regione"):
            clauses.append("regione = ?")
            params.append(regione)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT cod_amm, denominazione, sito_web FROM pa{where} ORDER BY regione, denominazione"

    with get_db() as db:
        rows = db.execute(sql, params).fetchall()
        result = [dict(r) for r in rows]

    if pa_filter and (limit := pa_filter.get("limit")):
        result = result[:limit]

    return result


def _save_pa_scan(scan_run_id: int, cod_amm: str, results: dict):
    """Insert one pa_scan row."""
    with get_db() as db:
        db.execute(
            """INSERT INTO pa_scan (
                scan_run_id, cod_amm, scanned_at,
                site_reachable, site_http_status, site_error, render_mode,
                wb_section_found, wb_section_url,
                wb_digital_channel, wb_channel_url, wb_channel_reachable,
                wb_channel_type, wb_requires_auth, wb_auth_type,
                wb_anonymous_allowed, wb_strong_auth_required,
                wb_software, wb_software_version, wb_software_confidence,
                rpct_email, rpct_phone, rpct_name,
                wb_email, wb_phone,
                wb_policy_visible, wb_policy_url,
                wb_policy_pdf_path, wb_policy_pdf_hash,
                discovery_method,
                scan_duration_s, notes
            ) VALUES (
                ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?,
                ?, ?
            )""",
            (
                scan_run_id,
                cod_amm,
                datetime.now(timezone.utc).isoformat(),
                results.get("site_reachable"),
                results.get("site_http_status"),
                results.get("site_error"),
                results.get("render_mode"),
                results.get("wb_section_found"),
                results.get("wb_section_url"),
                results.get("wb_digital_channel"),
                results.get("wb_channel_url"),
                results.get("wb_channel_reachable"),
                results.get("wb_channel_type"),
                results.get("wb_requires_auth"),
                results.get("wb_auth_type"),
                results.get("wb_anonymous_allowed"),
                results.get("wb_strong_auth_required"),
                results.get("wb_software"),
                results.get("wb_software_version"),
                results.get("wb_software_confidence"),
                results.get("rpct_email"),
                results.get("rpct_phone"),
                results.get("rpct_name"),
                results.get("wb_email"),
                results.get("wb_phone"),
                results.get("wb_policy_visible"),
                results.get("wb_policy_url"),
                results.get("wb_policy_pdf_path"),
                results.get("wb_policy_pdf_hash"),
                results.get("discovery_method"),
                results.get("scan_duration_s"),
                results.get("notes"),
            ),
        )


def _save_steps_safe(scan_run_id: int, cod_amm: str, steps: list):
    """Persist the per-attempt step ledger; never let a DB blip fail the scan."""
    try:
        save_pa_steps(scan_run_id, cod_amm, steps)
    except Exception as exc:
        logger.error("Could not save step ledger for %s: %s", cod_amm, exc)


def _log_scan_error(
    scan_run_id: int,
    cod_amm: str | None,
    phase: str,
    error: Exception,
    url: str | None = None,
):
    """Log an error to scan_error_log."""
    with get_db() as db:
        db.execute(
            """INSERT INTO scan_error_log
                   (scan_run_id, cod_amm, phase, error_type, error_message, url, occurred_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                scan_run_id,
                cod_amm,
                phase,
                type(error).__name__,
                str(error)[:2000],
                url,
                datetime.now(timezone.utc).isoformat(),
            ),
        )


# ---------------------------------------------------------------------------
# Single-PA scanner
# ---------------------------------------------------------------------------


async def scan_single_pa(
    scan_run_id: int,
    cod_amm: str,
    site_url: str,
    http_client: httpx.AsyncClient,
    mode: str = "browser",
) -> dict:
    """Scan a single PA through the full pipeline. Never raises.

    mode "python" skips the Playwright browser fallback entirely.
    """

    scan_run_id_str = str(scan_run_id)
    pa_logger = setup_scan_logging(scan_run_id_str, cod_amm)
    pa_logger.info("Starting scan for %s — %s", cod_amm, site_url)
    t0 = time.monotonic()

    results: dict = {
        "site_reachable": 0,
        "render_mode": "httpx",
    }
    # Per-attempt step ledger for this PA (saved at the end).
    steps: list[dict] = []

    try:
        # ---- Step 1: fetch homepage ----
        homepage_html = None
        try:
            resp = await http_client.get(site_url)
            results["site_http_status"] = resp.status_code
            results["site_reachable"] = 1
            homepage_html = resp.text
            steps.append(
                {
                    "phase": "discovery",
                    "step": "site_fetch",
                    "method": "httpx",
                    "status": "ok",
                    "detail": f"HTTP {resp.status_code}",
                }
            )
            pa_logger.info(
                "Homepage fetched — HTTP %d (%d bytes)",
                resp.status_code,
                len(homepage_html),
            )

            # Check if browser rendering is needed (skipped in python-only mode)
            if mode != "python" and await should_use_browser(
                homepage_html, resp.status_code
            ):
                pa_logger.info("Browser rendering required, re-fetching with browser")
                browser_result = await fetch_with_browser(site_url, pa_logger)
                # fetch_with_browser returns a dict {html, status, ...}
                browser_html = (
                    browser_result.get("html")
                    if isinstance(browser_result, dict)
                    else browser_result
                )
                # Only adopt the browser render if it actually has MORE content
                # than the httpx homepage — otherwise keep the good httpx HTML
                # (menu-heavy PA pages render fine over httpx and a worse browser
                #  render would strip the navigation links discovery relies on).
                if browser_html and len(browser_html) > len(homepage_html):
                    homepage_html = browser_html
                    results["render_mode"] = "browser"
                    steps.append(
                        {
                            "phase": "discovery",
                            "step": "render_decision",
                            "method": "browser",
                            "status": "ok",
                            "detail": "browser render adopted",
                        }
                    )
                    pa_logger.info(
                        "Browser fetch adopted (%d bytes)", len(homepage_html)
                    )
                else:
                    steps.append(
                        {
                            "phase": "discovery",
                            "step": "render_decision",
                            "method": "httpx-kept",
                            "status": "ok",
                            "detail": "browser discarded, httpx kept",
                        }
                    )
                    pa_logger.info(
                        "Browser fetch discarded (httpx HTML kept: %d vs %d bytes)",
                        len(homepage_html),
                        len(browser_html or ""),
                    )

        except httpx.HTTPError as exc:
            results["site_reachable"] = 0
            results["site_error"] = str(exc)[:500]
            steps.append(
                {
                    "phase": "discovery",
                    "step": "site_fetch",
                    "method": "httpx",
                    "status": "fail",
                    "reason": type(exc).__name__,
                }
            )
            pa_logger.warning("Homepage fetch failed: %s", exc)
            _log_scan_error(scan_run_id, cod_amm, "homepage_fetch", exc, url=site_url)

        if not homepage_html:
            results["scan_duration_s"] = round(time.monotonic() - t0, 2)
            _save_pa_scan(scan_run_id, cod_amm, results)
            _save_steps_safe(scan_run_id, cod_amm, steps)
            save_scan_summary(scan_run_id_str, cod_amm, results)
            pa_logger.info(
                "Scan complete (site unreachable) — %.1fs", results["scan_duration_s"]
            )
            teardown_scan_logging(pa_logger)
            return results

        # ---- Step 2: discovery ----
        discovery = await discover_wb_section(
            cod_amm,
            scan_run_id_str,
            site_url,
            http_client,
            pa_logger,
            homepage_html=homepage_html,
        )
        results["wb_section_found"] = discovery.get("wb_section_found", 0)
        results["wb_section_url"] = discovery.get("wb_section_url")
        results["discovery_method"] = discovery.get("discovery_method", "none")
        # record every discovery strategy attempt (per-attempt ledger)
        steps.extend(discovery.get("attempts", []))

        wb_page_html = discovery.get("wb_page_html", homepage_html)
        wb_links = discovery.get("wb_links", [])

        # ---- Step 3: probe (if section found) ----
        if results["wb_section_found"]:
            pa_logger.info(
                "WB section found at %s — probing channel", results["wb_section_url"]
            )
            probe_result = await probe_wb_channel(
                cod_amm,
                scan_run_id_str,
                results["wb_section_url"],
                wb_links,
                wb_page_html,
                http_client,
                pa_logger,
            )
            for key in (
                "wb_digital_channel",
                "wb_channel_url",
                "wb_channel_reachable",
                "wb_channel_type",
                "wb_requires_auth",
                "wb_auth_type",
                "wb_anonymous_allowed",
                "wb_strong_auth_required",
                "wb_email",
                "wb_phone",
            ):
                if key in probe_result:
                    results[key] = probe_result[key]

            # analysis: channel detection + reachability + anonymity
            if results.get("wb_digital_channel"):
                steps.append(
                    {
                        "phase": "analysis",
                        "step": "channel_detect",
                        "method": results.get("wb_channel_type") or "link",
                        "status": "ok",
                        "detail": results.get("wb_channel_url"),
                    }
                )
                steps.append(
                    {
                        "phase": "analysis",
                        "step": "channel_reach",
                        "method": "httpx",
                        "status": "ok"
                        if results.get("wb_channel_reachable")
                        else "fail",
                        "reason": None
                        if results.get("wb_channel_reachable")
                        else "channel_unreachable",
                    }
                )
                anon = results.get("wb_anonymous_allowed")
                steps.append(
                    {
                        "phase": "analysis",
                        "step": "anonymity",
                        "method": "keyword/form",
                        "status": "ok" if anon is not None else "partial",
                        "reason": None if anon is not None else "undetermined",
                    }
                )
            else:
                steps.append(
                    {
                        "phase": "analysis",
                        "step": "channel_detect",
                        "method": None,
                        "status": "fail",
                        "reason": "no_digital_channel",
                    }
                )

            # ---- Step 4: fingerprint (if channel found) ----
            if probe_result.get("wb_digital_channel") and probe_result.get(
                "wb_channel_url"
            ):
                pa_logger.info("Digital channel found — fingerprinting software")
                channel_html = probe_result.get("channel_html", "")
                fp = await fingerprint_software(
                    cod_amm,
                    scan_run_id_str,
                    probe_result["wb_channel_url"],
                    channel_html,
                    http_client,
                    pa_logger,
                )
                for key in (
                    "wb_software",
                    "wb_software_version",
                    "wb_software_confidence",
                ):
                    if key in fp:
                        results[key] = fp[key]
                steps.append(
                    {
                        "phase": "analysis",
                        "step": "software_fp",
                        "method": fp.get("wb_software_method") or "marker",
                        "status": "ok" if results.get("wb_software") else "fail",
                        "reason": None if results.get("wb_software") else "no_marker",
                        "detail": results.get("wb_software"),
                    }
                )

        # ---- Step 5: policy + RPCT in parallel ----
        policy_coro = download_wb_policy(
            cod_amm,
            scan_run_id_str,
            wb_page_html,
            results.get("wb_section_url") or site_url,
            http_client,
            pa_logger,
        )
        rpct_coro = extract_rpct_contacts(
            cod_amm,
            scan_run_id_str,
            wb_page_html,
            site_url,
            http_client,
            pa_logger,
        )
        policy_result, rpct_result = await asyncio.gather(
            policy_coro, rpct_coro, return_exceptions=True
        )

        if isinstance(policy_result, Exception):
            pa_logger.error("Policy download failed: %s", policy_result)
            _log_scan_error(scan_run_id, cod_amm, "policy_download", policy_result)
            steps.append(
                {
                    "phase": "analysis",
                    "step": "policy_pdf",
                    "method": None,
                    "status": "fail",
                    "reason": type(policy_result).__name__,
                }
            )
        else:
            for key in (
                "wb_policy_visible",
                "wb_policy_url",
                "wb_policy_pdf_path",
                "wb_policy_pdf_hash",
            ):
                if key in policy_result:
                    results[key] = policy_result[key]
            steps.append(
                {
                    "phase": "analysis",
                    "step": "policy_pdf",
                    "method": "pdf-link",
                    "status": "ok" if results.get("wb_policy_pdf_hash") else "fail",
                    "reason": None
                    if results.get("wb_policy_pdf_hash")
                    else "no_pdf_found",
                    "detail": results.get("wb_policy_url"),
                }
            )

        if isinstance(rpct_result, Exception):
            pa_logger.error("RPCT extraction failed: %s", rpct_result)
            _log_scan_error(scan_run_id, cod_amm, "rpct_extraction", rpct_result)
            steps.append(
                {
                    "phase": "analysis",
                    "step": "rpct_web",
                    "method": None,
                    "status": "fail",
                    "reason": type(rpct_result).__name__,
                }
            )
        else:
            for key in ("rpct_email", "rpct_phone", "rpct_name"):
                if key in rpct_result:
                    results[key] = rpct_result[key]
            steps.append(
                {
                    "phase": "analysis",
                    "step": "rpct_web",
                    "method": "page/at-fallback",
                    "status": "ok" if results.get("rpct_name") else "fail",
                    "reason": None if results.get("rpct_name") else "not_on_page",
                    "detail": results.get("rpct_name"),
                }
            )

    except Exception as exc:
        pa_logger.exception("Unhandled error scanning %s: %s", cod_amm, exc)
        _log_scan_error(scan_run_id, cod_amm, "scan_single_pa", exc, url=site_url)
        results["notes"] = f"Unhandled error: {type(exc).__name__}: {exc}"

    # ---- Finalize ----
    results["scan_duration_s"] = round(time.monotonic() - t0, 2)
    _save_pa_scan(scan_run_id, cod_amm, results)
    _save_steps_safe(scan_run_id, cod_amm, steps)
    save_scan_summary(scan_run_id_str, cod_amm, results)
    pa_logger.info("Scan complete — %.1fs", results["scan_duration_s"])

    teardown_scan_logging(pa_logger)
    return results


# ---------------------------------------------------------------------------
# Full-scan orchestrator
# ---------------------------------------------------------------------------


async def run_full_scan(
    max_parallel: int = MAX_PARALLEL,
    pa_filter: dict | None = None,
    mode: str = "browser",
) -> dict:
    """Run the complete scanning pipeline.

    mode:
        "python"  — httpx + heuristics only, no browser fallback
        "browser" — httpx + Playwright render fallback + heuristics (default)
        (the "claude" gold-standard mode runs as a separate offline harness
         over the dated archive, not through this async scanner)

    Returns a summary dict with counts and timing.
    """
    init_db()

    scan_run_id = _create_scan_run(mode=mode)
    logger.info("=== Scan run %d started (mode=%s) ===", scan_run_id, mode)

    pa_list = _get_pa_list(pa_filter)
    total = len(pa_list)
    logger.info("PAs to scan: %d (max_parallel=%d)", total, max_parallel)

    if total == 0:
        logger.warning("No PAs to scan — check filter or database content")
        _finish_scan_run(scan_run_id, 0, 0, 0)
        return {"scan_run_id": scan_run_id, "total": 0, "scanned": 0, "errors": 0}

    semaphore = asyncio.Semaphore(max_parallel)
    browser_initialized = False

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(15.0),
        limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        max_redirects=5,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    ) as http_client:

        async def _scan_with_semaphore(cod_amm: str, site_url: str) -> dict:
            async with semaphore:
                return await scan_single_pa(
                    scan_run_id, cod_amm, site_url, http_client, mode=mode
                )

        # Pre-init browser so it's ready when needed (skip in python-only mode)
        if mode == "python":
            logger.info("Mode=python — browser fallback disabled")
        else:
            try:
                await init_browser()
                browser_initialized = True
                logger.info("Browser initialized")
            except Exception as exc:
                logger.warning("Browser init failed (will use httpx only): %s", exc)

        # Build work list — skip PAs without a website
        work_items: list[tuple[str, str]] = []
        skipped = 0
        for pa in pa_list:
            site_url = (pa.get("sito_web") or "").strip()
            if not site_url:
                skipped += 1
                continue
            if not site_url.startswith(("http://", "https://")):
                site_url = f"https://{site_url}"
            work_items.append((pa["cod_amm"], site_url))

        if skipped:
            logger.info("Skipped %d PAs with no sito_web", skipped)

        # Process in batches to avoid file descriptor exhaustion
        BATCH_SIZE = max_parallel * 10
        scanned = 0
        errors = 0
        for batch_start in range(0, len(work_items), BATCH_SIZE):
            batch = work_items[batch_start : batch_start + BATCH_SIZE]
            tasks = [_scan_with_semaphore(cod, url) for cod, url in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for r in results:
                if isinstance(r, Exception):
                    errors += 1
                    logger.error("Task-level exception: %s", r)
                    try:
                        _log_scan_error(scan_run_id, None, "task_gather", r)
                    except Exception as log_exc:
                        logger.error("Could not log task error: %s", log_exc)
                else:
                    scanned += 1
                    if r.get("notes") and "Unhandled error" in str(r.get("notes", "")):
                        errors += 1

            logger.info(
                "Progress: %d/%d scanned, %d errors",
                batch_start + len(batch),
                len(work_items),
                errors,
            )

    # Close browser
    if browser_initialized:
        try:
            await close_browser()
            logger.info("Browser closed")
        except Exception as exc:
            logger.warning("Browser close error: %s", exc)

    # Update run (scanned/errors already counted above)
    # (skip the old counting loop below, jump to _finish_scan_run)
    _finish_scan_run(scan_run_id, total, scanned, errors)
    try:
        export_all()
    except Exception as exc:
        logger.error("Export failed: %s", exc)
    logger.info(
        "=== Scan run %s finished — scanned=%d errors=%d ===",
        scan_run_id,
        scanned,
        errors,
    )
    return {
        "scan_run_id": scan_run_id,
        "total": total,
        "scanned": scanned,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="WB Monitor Italia — scan Italian PAs for whistleblowing compliance"
    )
    parser.add_argument(
        "--max-parallel",
        type=int,
        default=MAX_PARALLEL,
        help=f"max concurrent PA scans (default: {MAX_PARALLEL})",
    )
    parser.add_argument(
        "--regione",
        type=str,
        default=None,
        help="filter PAs by regione (e.g. 'Lombardia')",
    )
    parser.add_argument(
        "--cod-amm",
        type=str,
        default=None,
        help="comma-separated list of cod_amm to scan (e.g. 'c_h501,c_f205')",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="only scan the first N PAs",
    )
    parser.add_argument(
        "--mode",
        choices=["python", "browser"],
        default="browser",
        help="detection mode: 'python' (httpx+heuristics, no browser) or "
        "'browser' (httpx+Playwright fallback, default). The 'claude' "
        "gold-standard mode runs via the separate runners/claude harness.",
    )
    args = parser.parse_args()

    # Build filter
    pa_filter: dict | None = None
    if args.regione or args.cod_amm or args.limit:
        pa_filter = {}
        if args.regione:
            pa_filter["regione"] = args.regione
        if args.cod_amm:
            pa_filter["cod_amm"] = [c.strip() for c in args.cod_amm.split(",")]
        if args.limit:
            pa_filter["limit"] = args.limit

    # Configure root logger
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    summary = asyncio.run(
        run_full_scan(
            max_parallel=args.max_parallel, pa_filter=pa_filter, mode=args.mode
        )
    )
    print(f"\nScan complete: {summary}")


if __name__ == "__main__":
    main()
