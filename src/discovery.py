"""
Crawl PA websites to locate the whistleblowing / anticorruzione section.

Multi-strategy cascade (tried in order, first success wins):
  1. guess_url        — common CMS URL patterns for AT/corruzione/WB pages
  2. menu_crawl       — homepage + footer link scan for WB/anticorruzione keywords
  3. sitemap          — parse sitemap.xml for matching URLs
  4. at_drilldown     — find Amministrazione Trasparente, drill into sub-sections
  5. deep_crawl       — follow all internal links up to depth 2, keyword scan
  6. google_fallback  — site:domain whistleblowing (last resort)

Each strategy logs its method name into discovery_method for diagnostics.
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

# URL suffixes to try directly (strategy 1) — covers common CMS patterns
GUESS_PATHS = [
    # Direct WB pages
    "/whistleblowing",
    "/it/whistleblowing",
    "/it-it/whistleblowing",
    # AT > Altri contenuti > Prevenzione corruzione (D.Lgs. 33/2013 layout)
    "/amministrazione-trasparente/altri-contenuti/prevenzione-della-corruzione",
    "/it/amministrazione-trasparente/altri-contenuti/prevenzione-della-corruzione",
    "/it-it/amministrazione-trasparente/altri-contenuti/prevenzione-della-corruzione",
    "/amministrazione-trasparente/altri-contenuti/corruzione",
    "/it/amministrazione-trasparente/altri-contenuti/corruzione",
    # AT > Altri contenuti > WB
    "/amministrazione-trasparente/altri-contenuti/prevenzione-della-corruzione/whistleblowing",
    "/amministrazione-trasparente/altri-contenuti/whistleblowing",
    "/it/amministrazione-trasparente/altri-contenuti/whistleblowing",
    # AT > Disposizioni generali > anticorruzione
    "/amministrazione-trasparente/disposizioni-generali/anticorruzione",
    # Segnalazioni
    "/segnalazioni",
    "/segnalazione-illeciti",
    "/segnala-illeciti",
    "/segnala",
    "/it/segnalazioni",
    # Common CMS variations
    "/anticorruzione",
    "/it/anticorruzione",
    "/prevenzione-corruzione",
    "/it/prevenzione-corruzione",
    "/canale-segnalazione",
    "/canale-whistleblowing",
    # Pagina del RPCT
    "/rpct",
    "/it/rpct",
    "/responsabile-anticorruzione",
    "/responsabile-prevenzione-corruzione",
]

# ── Keyword tiers ────────────────────────────────────────────────────────────
# HIGH: terms that almost always mean a WB page when found
WB_KEYWORDS_HIGH = [
    "whistleblowing",
    "whistleblower",
    "segnalazione illeciti",
    "segnalazione di illeciti",
    "segnalazione degli illeciti",
    "segnala un illecito",
    "segnala illeciti",
    "tutela del segnalante",
    "tutela dei segnalanti",
    "canale di segnalazione",
    "canale segnalazione",
    "canale segnalazioni",
    "d.lgs. 24/2023",
    "d.lgs. n. 24/2023",
    "d.lgs. 24 del 2023",
    "decreto legislativo 24/2023",
    "decreto 24/2023",
    "globaleaks",
]

# MEDIUM: terms that suggest anticorruzione/WB context
WB_KEYWORDS_MEDIUM = [
    "anticorruzione",
    "anti-corruzione",
    "anti corruzione",
    "prevenzione della corruzione",
    "prevenzione corruzione",
    "piano anticorruzione",
    "piano triennale anticorruzione",
    "ptpct",
    "rpct",
    "responsabile prevenzione corruzione",
    "responsabile anticorruzione",
    "segnalazioni",
    "segnalazione anonima",
    "segnalante",
    "illeciti",
    "condotte illecite",
    "irregolarità",
    "corruzione",
    "trasparenza e anticorruzione",
    "misure anticorruzione",
    "d.lgs. 231",
    "d.lgs. 231/2001",
    "modello 231",
]

# Combined for link matching (broader)
WB_KEYWORDS_ALL = WB_KEYWORDS_HIGH + WB_KEYWORDS_MEDIUM

# Keywords that identify the "Amministrazione Trasparente" landing page
AT_KEYWORDS = [
    "amministrazione trasparente",
    "amm. trasparente",
    "amministrazione-trasparente",
    "trasparenza",
]

# Sub-section keywords to follow inside AT page
AT_SUBSECTION_KEYWORDS = [
    "altri contenuti",
    "altri-contenuti",
    "prevenzione",
    "corruzione",
    "anticorruzione",
    "whistleblowing",
    "segnalazione",
    "segnalazioni",
    "segnala",
    "rpct",
    "responsabile",
    "piano triennale",
    "ptpct",
    "illeciti",
    "tutela",
    "disposizioni generali",
]

# ── helpers ──────────────────────────────────────────────────────────────────


def _empty_result(method: str = "none") -> dict:
    return {
        "wb_section_found": False,
        "wb_section_url": None,
        "wb_page_html": None,
        "wb_links": [],
        "render_mode": "light",
        "discovery_method": method,
    }


def _success_result(url: str, html: str, method: str) -> dict:
    return {
        "wb_section_found": True,
        "wb_section_url": url,
        "wb_page_html": html,
        "wb_links": _extract_links(html, url),
        "render_mode": "light",
        "discovery_method": method,
    }


def _extract_links(html: str, base_url: str) -> list[dict]:
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return []
    links: list[dict] = []
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        try:
            absolute = urljoin(base_url, href)
            text = tag.get_text(strip=True)
            links.append({"href": absolute, "text": text})
        except Exception:
            continue
    return links


def _normalize_site_url(site_url: str) -> str:
    url = site_url.strip().rstrip("/")
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    return url


def _text_has_keyword(text: str, keywords: list[str]) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in keywords)


def _link_matches_wb(href: str, text: str) -> bool:
    combined = (href + " " + text).lower()
    return any(kw in combined for kw in WB_KEYWORDS_ALL)


def _link_has_high_keyword(href: str, text: str) -> bool:
    combined = (href + " " + text).lower()
    return any(kw in combined for kw in WB_KEYWORDS_HIGH)


async def _fetch(
    client: httpx.AsyncClient,
    url: str,
    logger: logging.Logger,
) -> httpx.Response | None:
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


def _page_relevance_score(html: str) -> tuple[int, int]:
    """Score a page for WB relevance.

    Returns (high_hits, medium_hits) — count of distinct keyword matches.
    """
    try:
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(separator=" ", strip=True).lower()
    except Exception:
        return (0, 0)
    high = sum(1 for kw in WB_KEYWORDS_HIGH if kw in text)
    medium = sum(1 for kw in WB_KEYWORDS_MEDIUM if kw in text)
    return (high, medium)


def _page_is_wb_relevant(html: str) -> bool:
    """Is this page about WB/anticorruzione?

    Accepts if: any HIGH keyword, OR 2+ MEDIUM keywords.
    Much more permissive than the old 2-keyword requirement.
    """
    high, medium = _page_relevance_score(html)
    return high >= 1 or medium >= 2


def _is_same_domain(url: str, base_url: str) -> bool:
    try:
        return urlparse(url).netloc == urlparse(base_url).netloc
    except Exception:
        return False


# ── strategy implementations ────────────────────────────────────────────────


async def _try_guess_url(
    base_url: str,
    client: httpx.AsyncClient,
    logger: logging.Logger,
    scan_run_id: str,
    cod_amm: str,
) -> dict | None:
    """Strategy 1: try common URL patterns directly."""
    for path in GUESS_PATHS:
        url = base_url + path
        resp = await _fetch(client, url, logger)
        if resp is None or resp.status_code != 200:
            continue
        html = resp.text
        if len(html) < 500:
            continue
        if _page_is_wb_relevant(html):
            logger.info("[%s] WB found via guess_url: %s", cod_amm, path)
            save_raw_html(scan_run_id, cod_amm, "wb_page_guess.html", html)
            return _success_result(str(resp.url), html, f"guess_url:{path}")
    return None


async def _try_menu_crawl(
    base_url: str,
    client: httpx.AsyncClient,
    logger: logging.Logger,
    scan_run_id: str,
    cod_amm: str,
    homepage_html: str | None = None,
) -> dict | None:
    """Strategy 2: scan homepage (incl. nav, footer, sidebar) for WB links."""
    if homepage_html:
        html = homepage_html
        effective_url = base_url
    else:
        resp = await _fetch(client, base_url, logger)
        if resp is None or resp.status_code != 200:
            return None
        html = resp.text
        effective_url = str(resp.url)

    save_raw_html(scan_run_id, cod_amm, "homepage.html", html)

    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return None

    internal: list[tuple[str, str]] = []
    external_high: list[tuple[str, str]] = []
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        text = tag.get_text(separator=" ", strip=True)
        if not _link_matches_wb(href, text):
            continue
        absolute = urljoin(effective_url, href)
        if _is_same_domain(absolute, base_url):
            internal.append((absolute, text))
        elif _link_has_high_keyword(href, text):
            external_high.append((absolute, text))

    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for url, text in internal + external_high:
        if url not in seen:
            seen.add(url)
            unique.append((url, text))

    logger.debug(
        "[%s] menu_crawl: %d candidates (%d internal, %d external-high)",
        cod_amm,
        len(unique),
        len(internal),
        len(external_high),
    )

    for url, link_text in unique[:15]:
        resp2 = await _fetch(client, url, logger)
        if resp2 is None or resp2.status_code != 200:
            continue
        page_html = resp2.text
        is_external = not _is_same_domain(url, base_url)
        if is_external and _link_has_high_keyword(url, link_text):
            logger.info(
                "[%s] WB found via menu_crawl (external): %s ('%s')",
                cod_amm,
                url,
                link_text[:50],
            )
            save_raw_html(scan_run_id, cod_amm, "wb_page_menu.html", page_html)
            return _success_result(str(resp2.url), page_html, "menu_crawl:ext")
        if _page_is_wb_relevant(page_html):
            logger.info(
                "[%s] WB found via menu_crawl: %s ('%s')", cod_amm, url, link_text[:50]
            )
            save_raw_html(scan_run_id, cod_amm, "wb_page_menu.html", page_html)
            return _success_result(str(resp2.url), page_html, "menu_crawl")

    return None


async def _try_sitemap(
    base_url: str,
    client: httpx.AsyncClient,
    logger: logging.Logger,
    scan_run_id: str,
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

        save_http_debug(
            scan_run_id,
            cod_amm,
            "sitemap.xml",
            sitemap_url,
            "GET",
            resp.status_code,
            dict(resp.headers),
            0,
            resp.text[:2000],
        )

        locs = loc_pattern.findall(resp.text)
        logger.debug("[%s] sitemap: %d URLs total", cod_amm, len(locs))

        candidates = []
        for loc in locs:
            lower = loc.lower()
            if any(
                kw in lower
                for kw in [
                    "whistleblowing",
                    "anticorruzione",
                    "corruzione",
                    "segnalazione",
                    "segnalazioni",
                    "segnala",
                    "illeciti",
                    "rpct",
                    "prevenzione",
                    "altri-contenuti",
                    "altri_contenuti",
                ]
            ):
                candidates.append(loc)

        logger.debug("[%s] sitemap: %d WB candidates", cod_amm, len(candidates))

        for url in candidates[:10]:
            resp2 = await _fetch(client, url, logger)
            if resp2 is None or resp2.status_code != 200:
                continue
            html = resp2.text
            if _page_is_wb_relevant(html):
                logger.info("[%s] WB found via sitemap: %s", cod_amm, url)
                save_raw_html(scan_run_id, cod_amm, "wb_page_sitemap.html", html)
                return _success_result(str(resp2.url), html, "sitemap")

    return None


async def _try_at_drilldown(
    base_url: str,
    client: httpx.AsyncClient,
    logger: logging.Logger,
    scan_run_id: str,
    cod_amm: str,
    homepage_html: str | None = None,
) -> dict | None:
    """Strategy 4: find Amministrazione Trasparente, drill 2 levels deep."""
    if homepage_html:
        html = homepage_html
    else:
        resp = await _fetch(client, base_url, logger)
        if resp is None or resp.status_code != 200:
            return None
        html = resp.text

    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return None

    # Find AT page link
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

    logger.debug("[%s] AT page: %s", cod_amm, at_url)

    resp_at = await _fetch(client, at_url, logger)
    if resp_at is None or resp_at.status_code != 200:
        return None

    at_html = resp_at.text
    save_raw_html(scan_run_id, cod_amm, "amm_trasparente.html", at_html)

    # Check if AT page itself is relevant
    if _page_is_wb_relevant(at_html):
        logger.info("[%s] AT page itself is WB-relevant: %s", cod_amm, at_url)
        save_raw_html(scan_run_id, cod_amm, "wb_page_at.html", at_html)
        return _success_result(str(resp_at.url), at_html, "at_drilldown:at_page")

    try:
        soup_at = BeautifulSoup(at_html, "html.parser")
    except Exception:
        return None

    # Collect sub-links from AT page matching anticorruzione/WB keywords
    level1: list[tuple[str, str]] = []
    for tag in soup_at.find_all("a", href=True):
        href = tag["href"].strip()
        text = tag.get_text(separator=" ", strip=True)
        combined = (href + " " + text).lower()
        if any(kw in combined for kw in AT_SUBSECTION_KEYWORDS):
            absolute = urljoin(at_url, href)
            if _is_same_domain(absolute, base_url) and absolute not in {at_url}:
                level1.append((absolute, text))

    # Deduplicate
    seen: set[str] = set()
    unique_l1: list[tuple[str, str]] = []
    for url, text in level1:
        if url not in seen:
            seen.add(url)
            unique_l1.append((url, text))

    logger.debug("[%s] AT drilldown: %d level-1 sub-links", cod_amm, len(unique_l1))

    for url, link_text in unique_l1[:15]:
        resp2 = await _fetch(client, url, logger)
        if resp2 is None or resp2.status_code != 200:
            continue
        sub_html = resp2.text

        if _page_is_wb_relevant(sub_html):
            method = f"at_drilldown:L1:{link_text[:30]}"
            logger.info("[%s] WB found via %s: %s", cod_amm, method, url)
            save_raw_html(scan_run_id, cod_amm, "wb_page_at_l1.html", sub_html)
            return _success_result(str(resp2.url), sub_html, method)

        # Drill level 2
        try:
            soup_sub = BeautifulSoup(sub_html, "html.parser")
        except Exception:
            continue

        level2: list[tuple[str, str]] = []
        for tag in soup_sub.find_all("a", href=True):
            sub_href = tag["href"].strip()
            sub_text = tag.get_text(separator=" ", strip=True)
            if _link_matches_wb(sub_href, sub_text):
                sub_url = urljoin(url, sub_href)
                if _is_same_domain(sub_url, base_url) and sub_url not in seen:
                    level2.append((sub_url, sub_text))
                    seen.add(sub_url)

        for sub_url, sub_text in level2[:10]:
            resp3 = await _fetch(client, sub_url, logger)
            if resp3 is None or resp3.status_code != 200:
                continue
            sub2_html = resp3.text
            if _page_is_wb_relevant(sub2_html):
                method = f"at_drilldown:L2:{sub_text[:30]}"
                logger.info("[%s] WB found via %s: %s", cod_amm, method, sub_url)
                save_raw_html(scan_run_id, cod_amm, "wb_page_at_l2.html", sub2_html)
                return _success_result(str(resp3.url), sub2_html, method)

    return None


async def _try_deep_crawl(
    base_url: str,
    client: httpx.AsyncClient,
    logger: logging.Logger,
    scan_run_id: str,
    cod_amm: str,
    homepage_html: str | None = None,
) -> dict | None:
    """Strategy 5: follow internal links up to depth 2, looking for WB content."""
    if homepage_html:
        html = homepage_html
    else:
        resp = await _fetch(client, base_url, logger)
        if resp is None or resp.status_code != 200:
            return None
        html = resp.text

    visited: set[str] = {base_url}
    domain = urlparse(base_url).netloc

    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return None

    # Collect all internal links from homepage
    depth1_urls: list[str] = []
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absolute = urljoin(base_url, href)
        try:
            if urlparse(absolute).netloc == domain and absolute not in visited:
                depth1_urls.append(absolute)
                visited.add(absolute)
        except Exception:
            continue

    # Only follow links whose URL or text has any relevance
    relevant_urls = []
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        text = tag.get_text(separator=" ", strip=True)
        absolute = urljoin(base_url, href)
        combined = (href + " " + text).lower()
        if any(
            kw in combined
            for kw in [
                "trasparente",
                "trasparenza",
                "anticorruzione",
                "corruzione",
                "segnala",
                "whistleblowing",
                "illecit",
                "rpct",
                "integrità",
                "altri contenuti",
                "altri-contenuti",
            ]
        ):
            if _is_same_domain(absolute, base_url):
                relevant_urls.append(absolute)

    # Deduplicate
    seen: set[str] = set()
    unique_relevant: list[str] = []
    for u in relevant_urls:
        if u not in seen:
            seen.add(u)
            unique_relevant.append(u)

    logger.debug(
        "[%s] deep_crawl: %d relevant links from homepage",
        cod_amm,
        len(unique_relevant),
    )

    for url in unique_relevant[:20]:
        resp2 = await _fetch(client, url, logger)
        if resp2 is None or resp2.status_code != 200:
            continue
        page_html = resp2.text

        if _page_is_wb_relevant(page_html):
            logger.info("[%s] WB found via deep_crawl: %s", cod_amm, url)
            save_raw_html(scan_run_id, cod_amm, "wb_page_deep.html", page_html)
            return _success_result(str(resp2.url), page_html, "deep_crawl")

    return None


async def _try_google_fallback(
    base_url: str,
    client: httpx.AsyncClient,
    logger: logging.Logger,
    scan_run_id: str,
    cod_amm: str,
) -> dict | None:
    """Strategy 6: Google site: search as last resort."""
    domain = urlparse(base_url).netloc
    queries = [
        f"site:{domain} whistleblowing",
        f"site:{domain} segnalazione illeciti anticorruzione",
    ]
    for query in queries:
        search_url = f"https://www.google.com/search?q={query}&num=5"
        try:
            resp = await client.get(
                search_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                },
                timeout=10.0,
                follow_redirects=True,
            )
        except Exception as exc:
            logger.debug("[%s] Google search failed: %s", cod_amm, exc)
            continue

        if resp.status_code != 200:
            continue

        # Extract result URLs
        urls = re.findall(r'href="/url\?q=(https?://[^&"]+)', resp.text)
        if not urls:
            urls = re.findall(
                r'<a href="(https?://' + re.escape(domain) + r'[^"]*)"', resp.text
            )

        for result_url in urls[:5]:
            if domain not in result_url:
                continue
            resp2 = await _fetch(client, result_url, logger)
            if resp2 is None or resp2.status_code != 200:
                continue
            html = resp2.text
            if _page_is_wb_relevant(html):
                logger.info(
                    "[%s] WB found via google_fallback: %s", cod_amm, result_url
                )
                save_raw_html(scan_run_id, cod_amm, "wb_page_google.html", html)
                return _success_result(str(resp2.url), html, "google_fallback")

    return None


# ── public API ───────────────────────────────────────────────────────────────


async def discover_wb_section(
    cod_amm: str,
    scan_run_id: str,
    site_url: str,
    http_client: httpx.AsyncClient,
    logger: logging.Logger,
    homepage_html: str | None = None,
) -> dict:
    """Discover the whistleblowing section on a PA website.

    Tries 6 strategies in cascade order. Returns as soon as one succeeds.
    The discovery_method field records which strategy (and sub-detail) found it.
    """
    base_url = _normalize_site_url(site_url)
    if not base_url:
        logger.warning("[%s] Empty or invalid site_url: %r", cod_amm, site_url)
        return _empty_result()

    logger.info("[%s] Starting WB discovery on %s", cod_amm, base_url)

    strategies = [
        (
            "guess_url",
            lambda: _try_guess_url(base_url, http_client, logger, scan_run_id, cod_amm),
        ),
        (
            "menu_crawl",
            lambda: _try_menu_crawl(
                base_url, http_client, logger, scan_run_id, cod_amm, homepage_html
            ),
        ),
        (
            "sitemap",
            lambda: _try_sitemap(base_url, http_client, logger, scan_run_id, cod_amm),
        ),
        (
            "at_drilldown",
            lambda: _try_at_drilldown(
                base_url, http_client, logger, scan_run_id, cod_amm, homepage_html
            ),
        ),
        (
            "deep_crawl",
            lambda: _try_deep_crawl(
                base_url, http_client, logger, scan_run_id, cod_amm, homepage_html
            ),
        ),
        (
            "google_fallback",
            lambda: _try_google_fallback(
                base_url, http_client, logger, scan_run_id, cod_amm
            ),
        ),
    ]

    for name, strategy_fn in strategies:
        try:
            result = await strategy_fn()
            if result:
                return result
        except Exception as exc:
            logger.error("[%s] %s strategy failed: %s", cod_amm, name, exc)

    logger.info("[%s] No WB section found after all 6 strategies", cod_amm)
    return _empty_result()
