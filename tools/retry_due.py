"""Retry transient connection failures with exponential backoff (hours later).

Connection timeouts / connect errors are often transient (slow or briefly-down
PA sites). This runner:

  1. --seed : enqueue PAs that failed the homepage fetch with a transient error
              (ConnectTimeout/ReadTimeout/ConnectError/PoolTimeout/...) for a
              target scan run into retry_queue, scheduled for a first retry.
  2. (default) process DUE entries (status=pending, next_retry_at <= now):
              re-scan the PA in place; on success mark 'recovered', otherwise
              bump attempts and reschedule with exponential backoff, or mark
              'exhausted' after the last step.

Designed for an hourly cron: each run seeds new transient failures and retries
whatever is due. Concurrency stays low (these are slow sites).

    python -m tools.retry_due [--run-id N] [--seed] [--max-parallel 10]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx

from src.browser import close_browser, init_browser
from src.config import USER_AGENT
from src.db import get_db, init_db, query_db
from src.scanner import scan_single_pa

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("retry_due")

# Transient error types worth retrying (vs permanent DNS/cert failures).
TRANSIENT = (
    "ConnectTimeout",
    "ReadTimeout",
    "WriteTimeout",
    "PoolTimeout",
    "ConnectError",
    "ReadError",
    "RemoteProtocolError",
    "ConnectionResetError",
)

# Exponential backoff schedule (hours) between attempts.
BACKOFF_HOURS = [1, 3, 8, 24]
MAX_ATTEMPTS = len(BACKOFF_HOURS)  # after the last delay -> exhausted


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _latest_run() -> int:
    row = query_db("SELECT MAX(scan_run_id) AS r FROM pa_scan", one=True)
    return row["r"] if row and row["r"] else 0


def _seed(run_id: int) -> int:
    """Enqueue transient homepage-fetch failures from this run not already queued."""
    placeholders = ",".join("?" for _ in TRANSIENT)
    rows = query_db(
        f"""
        SELECT DISTINCT e.cod_amm, e.error_type
        FROM scan_error_log e
        JOIN pa_scan s ON s.cod_amm = e.cod_amm AND s.scan_run_id = e.scan_run_id
        WHERE e.scan_run_id = ?
          AND e.phase = 'homepage_fetch'
          AND e.error_type IN ({placeholders})
          AND (s.site_reachable = 0 OR s.site_reachable IS NULL)
        """,
        (run_id, *TRANSIENT),
    )
    first_due = (_now() + timedelta(hours=BACKOFF_HOURS[0])).isoformat()
    n = 0
    with get_db() as db:
        for r in rows:
            cur = db.execute(
                """INSERT OR IGNORE INTO retry_queue
                       (scan_run_id, cod_amm, error_type, attempts, next_retry_at,
                        status, created_at)
                   VALUES (?, ?, ?, 0, ?, 'pending', ?)""",
                (run_id, r["cod_amm"], r["error_type"], first_due, _now().isoformat()),
            )
            n += cur.rowcount
    logger.info("Seeded %d new retry entries for run %d", n, run_id)
    return n


def _due(run_id: int) -> list[dict]:
    return [
        dict(r)
        for r in query_db(
            """
            SELECT rq.*, p.sito_web
            FROM retry_queue rq JOIN pa p ON p.cod_amm = rq.cod_amm
            WHERE rq.scan_run_id = ? AND rq.status = 'pending'
              AND rq.next_retry_at <= ?
            ORDER BY rq.next_retry_at
            """,
            (run_id, _now().isoformat()),
        )
    ]


def _norm_url(url: str) -> str:
    url = (url or "").strip()
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def _log_attempt(item: dict, reachable: bool, http_status, new_error, egress):
    """Append-only record of this retry attempt and its outcome (with egress)."""
    with get_db() as db:
        db.execute(
            """INSERT INTO retry_log
                   (scan_run_id, cod_amm, attempt, error_type, outcome,
                    http_status, new_error, egress, attempted_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item["scan_run_id"],
                item["cod_amm"],
                item["attempts"] + 1,
                item["error_type"],
                "recovered" if reachable else "failed",
                http_status,
                new_error,
                egress,
                _now().isoformat(),
            ),
        )


def _reschedule(item: dict, reachable: bool):
    attempts = item["attempts"] + 1
    with get_db() as db:
        if reachable:
            db.execute(
                "UPDATE retry_queue SET status='recovered', attempts=?, last_attempt_at=? WHERE id=?",
                (attempts, _now().isoformat(), item["id"]),
            )
        elif attempts >= MAX_ATTEMPTS:
            db.execute(
                "UPDATE retry_queue SET status='exhausted', attempts=?, last_attempt_at=? WHERE id=?",
                (attempts, _now().isoformat(), item["id"]),
            )
        else:
            delay = BACKOFF_HOURS[min(attempts, len(BACKOFF_HOURS) - 1)]
            nxt = (_now() + timedelta(hours=delay)).isoformat()
            db.execute(
                "UPDATE retry_queue SET attempts=?, next_retry_at=?, last_attempt_at=? WHERE id=?",
                (attempts, nxt, _now().isoformat(), item["id"]),
            )


def _clear_old_rows(run_id: int, cod_amm: str):
    with get_db() as db:
        db.execute(
            "DELETE FROM pa_scan WHERE scan_run_id=? AND cod_amm=?", (run_id, cod_amm)
        )
        db.execute(
            "DELETE FROM pa_scan_step WHERE scan_run_id=? AND cod_amm=?",
            (run_id, cod_amm),
        )


async def _run(
    run_id: int, max_parallel: int, proxy: str | None = None, egress: str = "datacenter"
):
    due = _due(run_id)
    logger.info(
        "Due retries for run %d: %d%s",
        run_id,
        len(due),
        f" (via proxy {proxy})" if proxy else "",
    )
    if not due:
        return
    await init_browser()
    sem = asyncio.Semaphore(max_parallel)

    client_kwargs = dict(
        timeout=httpx.Timeout(25.0),
        limits=httpx.Limits(
            max_connections=max_parallel + 5, max_keepalive_connections=10
        ),
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        max_redirects=5,
    )
    if proxy:
        # Re-scan blocked/timeout sites from a different egress IP (VPN/proxy).
        client_kwargs["proxy"] = proxy
    async with httpx.AsyncClient(**client_kwargs) as client:

        async def _one(item: dict):
            async with sem:
                url = _norm_url(item["sito_web"])
                _clear_old_rows(run_id, item["cod_amm"])
                res = await scan_single_pa(
                    run_id, item["cod_amm"], url, client, mode="browser", egress=egress
                )
                reachable = bool(res.get("site_reachable"))
                _log_attempt(
                    item,
                    reachable,
                    res.get("site_http_status"),
                    res.get("site_error"),
                    egress,
                )
                _reschedule(item, reachable)
                logger.info(
                    "Retry %s attempt %d -> %s",
                    item["cod_amm"],
                    item["attempts"] + 1,
                    "RECOVERED" if reachable else "still failing",
                )

        await asyncio.gather(*[_one(i) for i in due], return_exceptions=True)
    await close_browser()


def main():
    ap = argparse.ArgumentParser(description="Retry transient connection failures")
    ap.add_argument("--run-id", type=int, default=None)
    ap.add_argument(
        "--seed", action="store_true", help="enqueue new transient failures first"
    )
    ap.add_argument("--max-parallel", type=int, default=10)
    ap.add_argument(
        "--proxy",
        default=None,
        help="route re-scans through a proxy/VPN egress (e.g. socks5://host:port "
        "or http://host:port) for sites that block our IPs",
    )
    ap.add_argument(
        "--egress",
        choices=["datacenter", "residential", "vpn"],
        default="datacenter",
        help="record which IP type these retries go out from",
    )
    args = ap.parse_args()

    init_db()
    run_id = args.run_id or _latest_run()
    if not run_id:
        logger.error("No scan run found")
        return
    if args.seed:
        _seed(run_id)
    asyncio.run(
        _run(
            run_id,
            max(1, min(args.max_parallel, 30)),
            proxy=args.proxy,
            egress=args.egress,
        )
    )

    stats = query_db(
        "SELECT status, COUNT(*) c FROM retry_queue WHERE scan_run_id=? GROUP BY status",
        (run_id,),
    )
    logger.info(
        "Retry queue (run %d): %s", run_id, {r["status"]: r["c"] for r in stats}
    )


if __name__ == "__main__":
    main()
