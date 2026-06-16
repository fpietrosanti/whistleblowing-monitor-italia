"""Check the live status of WhistleblowingPA links (active vs error).

For each entry in wbpa_registry, fetch the platform channel (piat_link) and,
optionally, the public WB page on the entity's site (piat_public_link), using
Chrome impersonation, and record active/error in wbpa_status. Supports the
scraper as ground truth and powers the private WhistleblowingPA dashboard.

    python -m tools.wbpa_check [--what piat|public|both] [--limit N]
                               [--max-parallel 10] [--egress datacenter]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime, timezone

from src.config import USER_AGENT
from src.db import get_db, init_db, query_db
from src.fetcher import make_client

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("wbpa_check")


def _targets(what: str, limit: int | None) -> list[dict]:
    rows = query_db("SELECT id, piat_link, piat_public_link FROM wbpa_registry")
    out = []
    for r in rows:
        if what in ("piat", "both") and (r["piat_link"] or "").startswith("http"):
            out.append({"wbpa_id": r["id"], "link_type": "piat", "url": r["piat_link"]})
        if what in ("public", "both") and (r["piat_public_link"] or "").startswith(
            "http"
        ):
            out.append(
                {
                    "wbpa_id": r["id"],
                    "link_type": "public",
                    "url": r["piat_public_link"],
                }
            )
    if limit:
        out = out[:limit]
    return out


def _save(rec: dict):
    with get_db() as db:
        db.execute(
            """INSERT INTO wbpa_status
                   (wbpa_id, link_type, url, http_status, active, error, egress, checked_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                rec["wbpa_id"],
                rec["link_type"],
                rec["url"],
                rec.get("http_status"),
                1 if rec.get("active") else 0,
                rec.get("error"),
                rec.get("egress"),
                datetime.now(timezone.utc).isoformat(),
            ),
        )


async def _check(client, t: dict, sem, egress: str):
    async with sem:
        rec = {**t, "egress": egress, "active": False}
        try:
            r = await client.get(t["url"], timeout=20.0)
            rec["http_status"] = r.status_code
            rec["active"] = r.status_code == 200
        except Exception as exc:
            rec["error"] = f"{type(exc).__name__}: {exc}"[:200]
        _save(rec)
        return rec["active"]


async def run(what: str, limit: int | None, max_parallel: int, egress: str):
    init_db()
    targets = _targets(what, limit)
    logger.info("Checking %d WhistleblowingPA links (what=%s)", len(targets), what)
    # Fresh snapshot for the link types being checked.
    with get_db() as db:
        if what == "both":
            db.execute("DELETE FROM wbpa_status")
        else:
            db.execute("DELETE FROM wbpa_status WHERE link_type = ?", (what,))
    sem = asyncio.Semaphore(max_parallel)
    active = 0
    async with make_client(timeout=20.0, headers={"User-Agent": USER_AGENT}) as client:
        results = await asyncio.gather(
            *[_check(client, t, sem, egress) for t in targets]
        )
    active = sum(1 for r in results if r)
    logger.info("Done: %d/%d active", active, len(targets))


def main():
    ap = argparse.ArgumentParser(description="Check WhistleblowingPA link status")
    ap.add_argument("--what", choices=["piat", "public", "both"], default="piat")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--max-parallel", type=int, default=10)
    ap.add_argument("--egress", default="datacenter")
    args = ap.parse_args()
    asyncio.run(
        run(args.what, args.limit, max(1, min(args.max_parallel, 30)), args.egress)
    )


if __name__ == "__main__":
    main()
