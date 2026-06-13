"""Phase A — Gold-set sampler.

Selects a sample of PAs (biased toward current discovery failures), fetches
each homepage, and extracts a COMPACT representation (page title + all links
with text/href, tagged by region of the page) so that an AI oracle can identify
the real WB/compliance/trasparenza entry point without ingesting raw HTML.

Output: data/gold/gold_candidates.json

Repeatable: re-run any time to refresh the sample against the latest scan run.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from src.config import DB_PATH, USER_AGENT

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "gold"
OUT_FILE = OUT_DIR / "gold_candidates.json"

N_FAILURES = 55   # reachable but Python found no WB section
N_SUCCESSES = 15  # reachable + WB found (regression anchors)
MAX_LINKS = 90    # cap links per site to keep the JSON compact
FETCH_TIMEOUT = 15.0
MAX_PARALLEL = 8


def _latest_run_id(conn: sqlite3.Connection) -> int:
    # Highest run id that actually has scan rows — tracks the most recent
    # (possibly still-running) scan, which uses the newest discovery code.
    row = conn.execute("SELECT MAX(scan_run_id) FROM pa_scan").fetchone()
    return row[0] if row and row[0] else 0


def _pick_sample(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    conn.row_factory = sqlite3.Row
    # Failures: reachable, no WB section found — the gold mine for missing patterns.
    failures = conn.execute(
        """
        SELECT p.cod_amm, p.denominazione, p.sito_web, p.tipologia, p.regione,
               s.discovery_method, 0 AS wb_found
        FROM pa_scan s JOIN pa p ON p.cod_amm = s.cod_amm
        WHERE s.scan_run_id = ?
          AND s.site_reachable = 1
          AND (s.wb_section_found = 0 OR s.wb_section_found IS NULL)
          AND p.sito_web != ''
        ORDER BY p.tipologia, p.regione
        """,
        (run_id,),
    ).fetchall()

    successes = conn.execute(
        """
        SELECT p.cod_amm, p.denominazione, p.sito_web, p.tipologia, p.regione,
               s.discovery_method, 1 AS wb_found
        FROM pa_scan s JOIN pa p ON p.cod_amm = s.cod_amm
        WHERE s.scan_run_id = ?
          AND s.site_reachable = 1
          AND s.wb_section_found = 1
          AND p.sito_web != ''
        ORDER BY p.tipologia, p.regione
        """,
        (run_id,),
    ).fetchall()

    def _spread(rows, n):
        """Evenly spread the pick across the ordered list for diversity."""
        rows = [dict(r) for r in rows]
        if len(rows) <= n:
            return rows
        step = len(rows) / n
        return [rows[int(i * step)] for i in range(n)]

    return _spread(failures, N_FAILURES) + _spread(successes, N_SUCCESSES)


def _region_of(tag) -> str:
    """Best-effort: is this link in nav / header / footer / sidebar / main?"""
    for parent in tag.parents:
        name = (parent.name or "").lower()
        pid = " ".join(parent.get("class", []) + [parent.get("id", "")]).lower()
        if name in ("nav", "header", "footer"):
            return name
        if any(k in pid for k in ("nav", "menu", "header")):
            return "nav"
        if any(k in pid for k in ("footer", "bottom")):
            return "footer"
        if any(k in pid for k in ("sidebar", "aside")):
            return "sidebar"
    return "main"


def _extract_links(html: str, base_url: str) -> tuple[str, list[dict]]:
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.get_text(strip=True) if soup.title else "")[:200]
    seen: set[str] = set()
    links: list[dict] = []
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        text = tag.get_text(separator=" ", strip=True)[:120]
        absolute = urljoin(base_url, href)
        key = (text.lower(), absolute)
        if key in seen:
            continue
        seen.add(key)
        links.append({"text": text, "href": absolute, "zone": _region_of(tag)})
    # Prefer nav/header/footer/sidebar links when capping
    priority = {"nav": 0, "header": 0, "footer": 1, "sidebar": 2, "main": 3}
    links.sort(key=lambda link: priority.get(link["zone"], 3))
    return title, links[:MAX_LINKS]


async def _fetch_one(client: httpx.AsyncClient, pa: dict, sem: asyncio.Semaphore) -> dict:
    url = pa["sito_web"]
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    rec = {
        "cod_amm": pa["cod_amm"],
        "denominazione": pa["denominazione"],
        "sito_web": pa["sito_web"],
        "tipologia": pa["tipologia"],
        "regione": pa["regione"],
        "python_wb_found": pa["wb_found"],
        "python_method": pa["discovery_method"],
        "fetch_ok": False,
        "title": None,
        "links": [],
    }
    async with sem:
        try:
            resp = await client.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=FETCH_TIMEOUT,
                follow_redirects=True,
            )
            if resp.status_code == 200 and resp.text:
                title, links = _extract_links(resp.text, str(resp.url))
                rec["fetch_ok"] = True
                rec["final_url"] = str(resp.url)
                rec["title"] = title
                rec["links"] = links
            else:
                rec["http_status"] = resp.status_code
        except Exception as exc:
            rec["error"] = f"{type(exc).__name__}: {exc}"[:200]
    return rec


async def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    run_id = _latest_run_id(conn)
    sample = _pick_sample(conn, run_id)
    conn.close()
    print(f"Run #{run_id}: sampled {len(sample)} PAs ({N_FAILURES} failures + {N_SUCCESSES} successes target)")

    sem = asyncio.Semaphore(MAX_PARALLEL)
    async with httpx.AsyncClient() as client:
        records = await asyncio.gather(*[_fetch_one(client, pa, sem) for pa in sample])

    ok = sum(1 for r in records if r["fetch_ok"])
    OUT_FILE.write_text(json.dumps(
        {"run_id": run_id, "count": len(records), "records": records},
        ensure_ascii=False, indent=2,
    ))
    print(f"Fetched {ok}/{len(records)} homepages -> {OUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
