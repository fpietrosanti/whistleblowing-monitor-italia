"""
Crawl PA websites to locate the whistleblowing (segnalazione illeciti) section.

Strategy (tried in order, first success wins):
  1. guess_url   - common URL patterns appended to the site root
  2. menu_crawl  - fetch homepage, scan links for WB keywords
  3. sitemap     - parse sitemap.xml for matching URLs
  4. keyword_search - find "Amministrazione Trasparente" page, then drill into sub-links
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from src.logging_config import save_raw_html, save_http_debug

# ── constants ────────────────────────────────────────────────────────────────

USER_AGENT = "WhistleblowingMonitorItalia/1.0 (+https://test.infosecurity.ch)"
REQUEST_TIMEOUT = 15.0
MAX_REDIRECTS = 5

# URL suffixes to try directly (strategy 1)
GUESS_PATHS = [
    "/amministrazione-trasparente/altri-contenuti/prevenzione-della-corruzione",
    "/it/amministrazione-trasparente/altri-contenuti/prevenzione-della-corruzione",
    "/amministrazione-trasparente/altri-contenuti/corruzione",
    "/it/amministrazione-trasparente/altri-contenuti/corruzione",
    "/amministrazione-trasparente/altri-contenuti/prevenzione-della-corruzione/whistleblowing",
    "/amministrazione-trasparente/altri-contenuti/whistleblowing",
]

# Keywords that signal a whistleblowing section (case-insensitive search)
WB_KEYWORDS = [
    "whistleblowing",
    "segnalazione illeciti",
    "segnalazione di illeciti",
    "segnalazioni",
    "anticorruzione",
    "rpct",
    "tutela del segnalante",
    "prevenzione corruzione",
    "prevenzione della corruzione",
]

# Keywords that identify the "Amministrazione Trasparente" landing page
AT_KEYWORDS = [
    "amministrazione trasparente",
    "amm. trasparente",
    "amministrazione-trasparente",
]

# ── helpers ──────────────────────────────────────────────────────────────────


def _empty_result(method: str = "none") -> dict:
    """Return a result dict indicating failure."""
    return {
        "wb_section_found": False,
        "wb_section_url": None,
        "wb_page_html": None,
        "wb_links": [],
        "render_mode": "light",
        "discovery_method": method,
    }


def _success_result(url: str, html: str, method: str) -> dict:
    """Return a result dict for a successfully discovered WB page."""
    return {
        "wb_section_found": True,
        "wb_section_url": url,
        "wb_page_html": html,
        "wb_links": _extract_links(html, url),
        "render_mode": "light",
        "discovery_method": method,
    }


def _extract_links(html: str, base_url: str) -> list[str]:
    """Extract all href links from *html*, resolved against *base_url*."""
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return []
    links: list[str] = []
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        try:
            absolute = urljoin(base_url, href)
            links.append(absolute)
        except Exception:
            continue
    return links


def _normalize_site_url(site_url: str) -> str:
    """Ensure the URL has a scheme and no trailing slash."""
    url = site_url.strip().rstrip("/")
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def _text_matches_keywords(text: str, keywords: list[str]) -> bool:
    """Check whether *text* contains any of the *keywords* (case-insensitive)."""
    lower = text.lower()
    return any(kw in lower for kw in keywords)


def _link_matches_wb(href: str, text: str) -> bool:
    """Return True if a link (href + visible text) looks like a WB section."""
    combined = (href + " " + text).lower()
    return any(kw in combined for kw in WB_KEYWORDS)


async def _fetch(
    client: httpx.AsyncClient,
    url: str,
    logger: logging.Logger,
) -> httpx.Response | None:
    """GET *url* with standard headers/timeout, return response or None."""
    try:
        resp = await client.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
        )
        logger.debug("GET %s -> %s", url, resp.status_code)
        return resp
    except httpx.TimeoutException:
        logger.warning("Timeout fetching %s", url)
    except httpx.TooManyRedirects:
        logger.warning("Too many redirects for %s", url)
    except httpx.HTTPError as exc:
        logger.warning("HTTP error fetching %s: %s", url, exc)
    except Exception as exc:
        logger.warning("Unexpected error fetching %s: %s", url, exc)
    return None


def _page_looks_like_wb(html: str) -> bool:
    """Heuristic: does the page body mention WB-related terms enough to be relevant?"""
    try:
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(separator=" ", strip=True).lower()
    except Exception:
        return False
    # Require at least two distinct keyword matches to reduce false positives
    hits = sum(1 for kw in WB_KEYWORDS if kw in text)
    return hits >= 2


# ── strategy implementations ────────────────────────────────────────────────


async def _try_guess_url(
    base_url: str,
    client: httpx.AsyncClient,
    logger: logging.Logger,
    scan_run_id: int,
    cod_amm: str,
) -> dict | None:
    """Strategy 1: try common URL patterns directly."""
    for path in GUESS_PATHS:
        url = base_url + path
        resp = await _fetch(client, url, logger)
        if resp is None or resp.status_code != 200:
            continue
        html = resp.text
        if _page_looks_like_wb(html):
            logger.info("[%s] WB section found via guess_url: %s", cod_amm, url)
            save_raw_html(scan_run_id, cod_amm, "wb_page_guess.html", html)
            return _success_result(str(resp.url), html, "guess_url")
    return None


async def _try_menu_crawl(
    base_url: str,
    client: httpx.AsyncClient,
    logger: logging.Logger,
    scan_run_id: int,
    cod_amm: str,
) -> dict | None:
    """Strategy 2: fetch homepage, scan for WB-related links."""
    resp = await _fetch(client, base_url, logger)
    if resp is None or resp.status_code != 200:
        return None

    homepage_html = resp.text
    save_raw_html(scan_run_id, cod_amm, "homepage.html", homepage_html)

    try:
        soup = BeautifulSoup(homepage_html, "html.parser")
    except Exception as exc:
        logger.warning("[%s] Failed to parse homepage: %s", cod_amm, exc)
        return None

    # Collect candidate links
    candidates: list[str] = []
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        text = tag.get_text(separator=" ", strip=True)
        if _link_matches_wb(href, text):
            absolute = urljoin(base_url, href)
            candidates.append(absolute)

    # De-duplicate, preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique.append(c)

    logger.debug("[%s] menu_crawl found %d candidate links", cod_amm, len(unique))

    for url in unique:
        resp2 = await _fetch(client, url, logger)
        if resp2 is None or resp2.status_code != 200:
            continue
        html = resp2.text
        if _page_looks_like_wb(html):
            logger.info("[%s] WB section found via menu_crawl: %s", cod_amm, url)
            save_raw_html(scan_run_id, cod_amm, "wb_page_menu.html", html)
            return _success_result(str(resp2.url), html, "menu_crawl")

    return None


async def _try_sitemap(
    base_url: str,
    client: httpx.AsyncClient,
    logger: logging.Logger,
    scan_run_id: int,
    cod_amm: str,
) -> dict | None:
    """Strategy 3: parse sitemap.xml for WB-related URLs."""
    sitemap_urls = [
        base_url + "/sitemap.xml",
        base_url + "/sitemap_index.xml",
    ]

    loc_pattern = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.IGNORECASE)

    for sitemap_url in sitemap_urls:
        resp = await _fetch(client, sitemap_url, logger)
        if resp is None or resp.status_code != 200:
            continue

        save_http_debug(scan_run_id, cod_amm, "sitemap.xml", resp.text)

        # Extract all <loc> entries
        locs = loc_pattern.findall(resp.text)
        logger.debug("[%s] sitemap has %d URLs", cod_amm, len(locs))

        # Filter for WB-related URLs
        candidates = [
            loc for loc in locs if _text_matches_keywords(loc, WB_KEYWORDS)
        ]

        # Also look for prevenzione-corruzione paths
        for loc in locs:
            lower = loc.lower()
            if "prevenzione" in lower and "corruzione" in lower:
                if loc not in candidates:
                    candidates.append(loc)

        for url in candidates:
            resp2 = await _fetch(client, url, logger)
            if resp2 is None or resp2.status_code != 200:
                continue
            html = resp2.text
            if _page_looks_like_wb(html):
                logger.info("[%s] WB section found via sitemap: %s", cod_amm, url)
                save_raw_html(scan_run_id, cod_amm, "wb_page_sitemap.html", html)
                return _success_result(str(resp2.url), html, "sitemap")

    return None


async def _try_keyword_search(
    base_url: str,
    client: httpx.AsyncClient,
    logger: logging.Logger,
    scan_run_id: int,
    cod_amm: str,
) -> dict | None:
    """Strategy 4: find the Amministrazione Trasparente page, then drill into sub-links."""
    # First, fetch homepage to find the AT page
    resp = await _fetch(client, base_url, logger)
    if resp is None or resp.status_code != 200:
        return None

    try:
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception:
        return None

    # Find the AT page link
    at_url: str | None = None
    for tag in soup.find_all("a", href=True):
        text = tag.get_text(separator=" ", strip=True).lower()
        href = tag["href"].strip().lower()
        if any(kw in text or kw in href for kw in AT_KEYWORDS):
            at_url = urljoin(base_url, tag["href"].strip())
            break

    if not at_url:
        logger.debug("[%s] No Amministrazione Trasparente link found", cod_amm)
        return None

    logger.debug("[%s] Found AT page: %s", cod_amm, at_url)

    # Fetch the AT page
    resp_at = await _fetch(client, at_url, logger)
    if resp_at is None or resp_at.status_code != 200:
        return None

    at_html = resp_at.text
    save_raw_html(scan_run_id, cod_amm, "amm_trasparente.html", at_html)

    try:
        soup_at = BeautifulSoup(at_html, "html.parser")
    except Exception:
        return None

    # Search AT page for sub-links mentioning WB/anticorruzione keywords
    candidates: list[str] = []
    for tag in soup_at.find_all("a", href=True):
        href = tag["href"].strip()
        text = tag.get_text(separator=" ", strip=True)
        combined = (href + " " + text).lower()
        if any(kw in combined for kw in [
            "altri contenuti",
            "prevenzione",
            "corruzione",
            "anticorruzione",
            "whistleblowing",
            "segnalazione",
            "segnalazioni",
            "rpct",
            "tutela del segnalante",
        ]):
            absolute = urljoin(at_url, href)
            if absolute not in candidates:
                candidates.append(absolute)

    logger.debug(
        "[%s] keyword_search found %d sub-links in AT page", cod_amm, len(candidates)
    )

    for url in candidates:
        resp2 = await _fetch(client, url, logger)
        if resp2 is None or resp2.status_code != 200:
            continue
        html = resp2.text
        if _page_looks_like_wb(html):
            logger.info(
                "[%s] WB section found via keyword_search: %s", cod_amm, url
            )
            save_raw_html(scan_run_id, cod_amm, "wb_page_keyword.html", html)
            return _success_result(str(resp2.url), html, "keyword_search")

        # The sub-page might itself be an intermediate "Altri contenuti" page.
        # Drill one level deeper.
        try:
            soup_sub = BeautifulSoup(html, "html.parser")
        except Exception:
            continue
        for tag in soup_sub.find_all("a", href=True):
            sub_href = tag["href"].strip()
            sub_text = tag.get_text(separator=" ", strip=True)
            if _link_matches_wb(sub_href, sub_text):
                sub_url = urljoin(url, sub_href)
                resp3 = await _fetch(client, sub_url, logger)
                if resp3 is None or resp3.status_code != 200:
                    continue
                sub_html = resp3.text
                if _page_looks_like_wb(sub_html):
                    logger.info(
                        "[%s] WB section found via keyword_search (drill): %s",
                        cod_amm,
                        sub_url,
                    )
                    save_raw_html(
                        scan_run_id, cod_amm, "wb_page_keyword_drill.html", sub_html
                    )
                    return _success_result(
                        str(resp3.url), sub_html, "keyword_search"
                    )

    return None


# ── public API ───────────────────────────────────────────────────────────────


async def discover_wb_section(
    cod_amm: str,
    scan_run_id: str,
    site_url: str,
    http_client: httpx.AsyncClient,
    logger: logging.Logger,
) -> dict:
    """
    Discover the whistleblowing section on a PA website.

    Tries multiple strategies in order and returns as soon as one succeeds.

    Parameters
    ----------
    cod_amm : str
        PA code from IndicePA.
    scan_run_id : str
        Current scan run ID, used for saving raw artefacts.
    site_url : str
        Base URL of the PA website.
    http_client : httpx.AsyncClient
        Shared async HTTP client (caller manages its lifecycle).
    logger : logging.Logger
        Logger instance for debug/info/warning output.

    Returns
    -------
    dict
        Discovery result with keys: wb_section_found, wb_section_url,
        wb_page_html, wb_links, render_mode, discovery_method.
    """
    base_url = _normalize_site_url(site_url)
    if not base_url:
        logger.warning("[%s] Empty or invalid site_url: %r", cod_amm, site_url)
        return _empty_result()

    logger.info("[%s] Starting WB discovery on %s", cod_amm, base_url)

    # Strategy 1: guess common URL patterns
    try:
        result = await _try_guess_url(
            base_url, http_client, logger, scan_run_id, cod_amm
        )
        if result:
            return result
    except Exception as exc:
        logger.error("[%s] guess_url strategy failed: %s", cod_amm, exc)

    # Strategy 2: crawl homepage menu links
    try:
        result = await _try_menu_crawl(
            base_url, http_client, logger, scan_run_id, cod_amm
        )
        if result:
            return result
    except Exception as exc:
        logger.error("[%s] menu_crawl strategy failed: %s", cod_amm, exc)

    # Strategy 3: sitemap.xml
    try:
        result = await _try_sitemap(
            base_url, http_client, logger, scan_run_id, cod_amm
        )
        if result:
            return result
    except Exception as exc:
        logger.error("[%s] sitemap strategy failed: %s", cod_amm, exc)

    # Strategy 4: find Amministrazione Trasparente, drill into sub-links
    try:
        result = await _try_keyword_search(
            base_url, http_client, logger, scan_run_id, cod_amm
        )
        if result:
            return result
    except Exception as exc:
        logger.error("[%s] keyword_search strategy failed: %s", cod_amm, exc)

    logger.info("[%s] No WB section found after all strategies", cod_amm)
    return _empty_result()
