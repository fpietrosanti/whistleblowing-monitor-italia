"""Phase 1 — Dated homepage archive (evidence + Claude gold-standard input).

For every PA: fetch the homepage, save the HTML, a compact links.json
(text/href tagged by page zone — accessibility landmarks help), and a
viewport screenshot. Everything under a dated folder so it is both the
documentary record of the site's state at capture time and the offline input
for the Claude discovery harness (so Claude need not re-crawl 23k sites).

    data/archive/<YYYY-MM-DD>/<cod_amm>/
        homepage.html
        links.json
        screenshot.png
        meta.json

Repeatable & resumable: PAs already archived for the target date are skipped
unless --force. Concurrency is capped (default 6, max 30) — screenshots are
memory-heavy, so go easy.

Usage:
    python -m tools.archive_homepages [--date YYYY-MM-DD] [--concurrency N]
                                      [--limit N] [--regione NAME] [--force]
                                      [--no-screenshot]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sqlite3
from datetime import date
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from src import browser
from src.config import DATA_DIR, DB_PATH, USER_AGENT

ARCHIVE_ROOT = DATA_DIR / "archive"
MAX_LINKS = 120
FETCH_TIMEOUT = 15.0
DEFAULT_CONCURRENCY = 6
HARD_CAP = 30  # never exceed (server-wide concurrency cap)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("archive")


def _pa_list(regione: str | None, limit: int | None) -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    sql = "SELECT cod_amm, denominazione, sito_web FROM pa WHERE sito_web != ''"
    params: list = []
    if regione:
        sql += " AND regione = ?"
        params.append(regione)
    sql += " ORDER BY regione, denominazione"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    if limit:
        rows = rows[:limit]
    return rows


def _zone_of(tag) -> str:
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


def _extract_links(html: str, base_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    seen: set = set()
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
        links.append({"text": text, "href": absolute, "zone": _zone_of(tag)})
    priority = {"nav": 0, "header": 0, "footer": 1, "sidebar": 2, "main": 3}
    links.sort(key=lambda link: priority.get(link["zone"], 3))
    return links[:MAX_LINKS]


def _norm_url(url: str) -> str:
    url = (url or "").strip()
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


async def _archive_one(
    client: httpx.AsyncClient,
    pa: dict,
    out_root: Path,
    sem: asyncio.Semaphore,
    do_screenshot: bool,
    force: bool,
) -> str:
    cod = pa["cod_amm"]
    out_dir = out_root / cod
    shot = out_dir / "screenshot.png"
    meta_path = out_dir / "meta.json"
    if not force and meta_path.exists() and (shot.exists() or not do_screenshot):
        return "skip"

    url = _norm_url(pa["sito_web"])
    meta = {
        "cod_amm": cod,
        "denominazione": pa["denominazione"],
        "sito_web": pa["sito_web"],
        "url": url,
        "fetch_ok": False,
        "screenshot_ok": False,
    }
    async with sem:
        out_dir.mkdir(parents=True, exist_ok=True)
        # --- HTML + links (httpx) ---
        try:
            resp = await client.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=FETCH_TIMEOUT,
                follow_redirects=True,
            )
            meta["http_status"] = resp.status_code
            meta["final_url"] = str(resp.url)
            if resp.status_code == 200 and resp.text:
                (out_dir / "homepage.html").write_text(resp.text, encoding="utf-8")
                links = _extract_links(resp.text, str(resp.url))
                (out_dir / "links.json").write_text(
                    json.dumps(links, ensure_ascii=False, indent=1), encoding="utf-8"
                )
                meta["fetch_ok"] = True
                meta["n_links"] = len(links)
        except Exception as exc:
            meta["error"] = f"{type(exc).__name__}: {exc}"[:200]

        # --- screenshot (Playwright) ---
        if do_screenshot and meta.get("final_url"):
            shot_res = await browser.capture_screenshot(
                meta.get("final_url", url), shot, logger
            )
            meta["screenshot_ok"] = shot_res["ok"]
            if not shot_res["ok"]:
                meta["screenshot_error"] = shot_res.get("error")

        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    return "ok" if meta["fetch_ok"] else "fail"


async def main() -> None:
    ap = argparse.ArgumentParser(description="Dated homepage archive for WB Monitor")
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--regione", default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-screenshot", action="store_true")
    args = ap.parse_args()

    concurrency = max(1, min(args.concurrency, HARD_CAP))
    do_screenshot = not args.no_screenshot
    out_root = ARCHIVE_ROOT / args.date
    out_root.mkdir(parents=True, exist_ok=True)

    pas = _pa_list(args.regione, args.limit)
    logger.info(
        "Archiving %d PAs -> %s (concurrency=%d, screenshot=%s)",
        len(pas),
        out_root,
        concurrency,
        do_screenshot,
    )

    if do_screenshot:
        await browser.init_browser()

    sem = asyncio.Semaphore(concurrency)
    counts = {"ok": 0, "fail": 0, "skip": 0}
    done = 0
    async with httpx.AsyncClient() as client:
        tasks = [
            _archive_one(client, pa, out_root, sem, do_screenshot, args.force)
            for pa in pas
        ]
        for coro in asyncio.as_completed(tasks):
            res = await coro
            counts[res] = counts.get(res, 0) + 1
            done += 1
            if done % 100 == 0:
                logger.info("Progress %d/%d — %s", done, len(pas), counts)

    if do_screenshot:
        await browser.close_browser()
    logger.info("Archive complete: %s -> %s", counts, out_root)


if __name__ == "__main__":
    asyncio.run(main())
