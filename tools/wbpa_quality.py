"""Fetch each WhistleblowingPA public WB page once and record:
  - link status (active/error) into wbpa_status (link_type='public')
  - qualitative content analysis (rubric v1.0) into wbpa_quality

Serves the private dashboard: which pages are online and which qualitative
elements they contain, for the WhistleblowingPA administrator.

    python -m tools.wbpa_quality [--limit N] [--max-parallel 8] [--egress datacenter]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime, timezone

from src.config import USER_AGENT
from src.db import get_db, init_db, query_db
from src.fetcher import make_client
from src.wb_content import analyze_wb_content

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("wbpa_quality")

_QFIELDS = (
    "has_tema",
    "has_canale",
    "has_rpct",
    "has_anac",
    "has_tutele",
    "has_presupposti",
    "has_distinzione",
    "has_anonimato",
    "has_procedura",
    "has_privacy",
    "has_legge",
)


def _targets(limit):
    rows = query_db(
        "SELECT id, piat_public_link FROM wbpa_registry "
        "WHERE piat_public_link LIKE 'http%'"
    )
    out = [{"wbpa_id": r["id"], "url": r["piat_public_link"]} for r in rows]
    return out[:limit] if limit else out


def _now():
    return datetime.now(timezone.utc).isoformat()


def _save_status(t, active, http_status, error, egress):
    with get_db() as db:
        db.execute(
            "INSERT INTO wbpa_status (wbpa_id, link_type, url, http_status, active, error, egress, checked_at) "
            "VALUES (?, 'public', ?, ?, ?, ?, ?, ?)",
            (
                t["wbpa_id"],
                t["url"],
                http_status,
                1 if active else 0,
                error,
                egress,
                _now(),
            ),
        )


def _save_quality(t, q):
    with get_db() as db:
        db.execute(
            f"""INSERT INTO wbpa_quality
                   (wbpa_id, url, {", ".join(_QFIELDS)}, score, outcome, checked_at)
               VALUES (?, ?, {", ".join("?" for _ in _QFIELDS)}, ?, ?, ?)""",
            (
                t["wbpa_id"],
                t["url"],
                *[1 if q[f] else 0 for f in _QFIELDS],
                q["score"],
                q["outcome"],
                _now(),
            ),
        )


async def _one(client, t, sem, egress):
    async with sem:
        try:
            r = await client.get(t["url"], timeout=20.0)
            active = r.status_code == 200
            _save_status(t, active, r.status_code, None, egress)
            if active and r.text:
                q = analyze_wb_content(r.text, t["url"])
                _save_quality(t, q)
                return q["outcome"]
        except Exception as exc:
            _save_status(t, False, None, f"{type(exc).__name__}: {exc}"[:200], egress)
        return None


async def run(limit, max_parallel, egress):
    init_db()
    targets = _targets(limit)
    logger.info("Analyzing %d public WB pages", len(targets))
    with get_db() as db:
        db.execute("DELETE FROM wbpa_status WHERE link_type='public'")
        db.execute("DELETE FROM wbpa_quality")
    sem = asyncio.Semaphore(max_parallel)
    async with make_client(timeout=20.0, headers={"User-Agent": USER_AGENT}) as client:
        outcomes = await asyncio.gather(
            *[_one(client, t, sem, egress) for t in targets]
        )
    from collections import Counter

    logger.info("Done: outcomes=%s", dict(Counter(o for o in outcomes if o)))


def main():
    ap = argparse.ArgumentParser(
        description="WhistleblowingPA public-page quality analysis"
    )
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--max-parallel", type=int, default=8)
    ap.add_argument("--egress", default="datacenter")
    args = ap.parse_args()
    asyncio.run(run(args.limit, max(1, min(args.max_parallel, 30)), args.egress))


if __name__ == "__main__":
    main()
