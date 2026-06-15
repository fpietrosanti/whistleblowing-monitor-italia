"""Playwright-based browser fallback for JS-heavy PA websites.

When a simple HTTP fetch returns a JS-rendered shell (empty body, SPA markers,
meta-refresh redirects), this module re-fetches the page with a headless
Chromium instance so the final rendered HTML is available for analysis.

The browser is a module-level lazy singleton: call ``init_browser()`` once at
the start of a scan run and ``close_browser()`` at the end.
"""

from __future__ import annotations

import logging
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

from src.config import BASE_DIR, USER_AGENT

# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
_browser = None
_playwright_ctx = None
VIEWPORT = {"width": 1280, "height": 720}

# Resource types to block for speed
_BLOCKED_RESOURCE_TYPES = {"image", "font", "media", "stylesheet"}

LOGS_DIR = BASE_DIR / "data" / "logs"


# ---------------------------------------------------------------------------
# Lightweight HTML helpers
# ---------------------------------------------------------------------------
class _BodyTextExtractor(HTMLParser):
    """Extract visible text length inside <body>, ignoring <script>/<style>."""

    def __init__(self) -> None:
        super().__init__()
        self._in_body = False
        self._skip_depth = 0
        self.text_chars = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "body":
            self._in_body = True
        if tag in ("script", "style"):
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag == "body":
            self._in_body = False

    def handle_data(self, data: str) -> None:
        if self._in_body and self._skip_depth == 0:
            stripped = data.strip()
            if stripped:
                self.text_chars += len(stripped)


def _body_text_length(html: str) -> int:
    """Return approximate visible-text character count inside <body>."""
    extractor = _BodyTextExtractor()
    try:
        extractor.feed(html)
    except Exception:
        return 0
    return extractor.text_chars


def _has_noscript_content(html: str) -> bool:
    """True if a <noscript> tag contains substantial content (>100 chars)."""
    pattern = re.compile(r"<noscript[^>]*>(.*?)</noscript>", re.DOTALL | re.IGNORECASE)
    for match in pattern.finditer(html):
        inner = match.group(1).strip()
        # Strip HTML tags to measure actual text
        text_only = re.sub(r"<[^>]+>", "", inner).strip()
        if len(text_only) > 100:
            return True
    return False


def _has_spa_markers_with_empty_body(html: str) -> bool:
    """True if the HTML has SPA framework markers and a nearly-empty body."""
    spa_patterns = [
        r'id\s*=\s*["\']root["\']',
        r'id\s*=\s*["\']app["\']',
        r"ng-app",
        r"data-reactroot",
        r'id\s*=\s*["\']__next["\']',
        r'id\s*=\s*["\']__nuxt["\']',
    ]
    has_marker = any(re.search(p, html, re.IGNORECASE) for p in spa_patterns)
    if not has_marker:
        return False
    return _body_text_length(html) < 200


def _has_js_redirect(html: str) -> bool:
    """True if the page relies on a JS or meta-refresh redirect."""
    # <meta http-equiv="refresh" content="0;url=...">
    if re.search(
        r'<meta[^>]+http-equiv\s*=\s*["\']refresh["\']',
        html,
        re.IGNORECASE,
    ):
        return True
    # window.location / document.location assignments
    if re.search(
        r"(window|document)\.location\s*[=.]",
        html,
        re.IGNORECASE,
    ):
        return True
    return False


# ---------------------------------------------------------------------------
# Public API: heuristic
# ---------------------------------------------------------------------------
async def should_use_browser(html_content: str, http_status: int) -> bool:
    """Decide whether *html_content* needs a full browser render.

    Heuristics (any match -> True):
    1. Body text content < 500 chars (likely JS-rendered page).
    2. Contains <noscript> with substantial content (>100 chars).
    3. Contains React/Angular/Vue/Next markers with an empty body.
    4. Page uses a JS redirect (meta refresh or ``window.location``).

    The function is async for interface consistency with the rest of the
    module; all checks are synchronous and fast.
    """
    # 0. A page with many anchor links is a real rendered page, not a JS shell —
    #    PA homepages are menu-heavy (little prose) and would otherwise be
    #    misclassified as empty and sent to the browser (which strips links).
    if html_content and html_content.count("<a ") >= 10:
        return False

    # 1. Very short visible body text
    if _body_text_length(html_content) < 500:
        return True

    # 2. Meaningful noscript block
    if _has_noscript_content(html_content):
        return True

    # 3. SPA markers with near-empty body
    if _has_spa_markers_with_empty_body(html_content):
        return True

    # 4. JS / meta redirect
    if _has_js_redirect(html_content):
        return True

    return False


# ---------------------------------------------------------------------------
# Public API: browser lifecycle
# ---------------------------------------------------------------------------
async def init_browser() -> None:
    """Launch a headless Chromium instance (lazy singleton).

    Safe to call multiple times; subsequent calls are no-ops if the browser
    is already running.
    """
    global _browser, _playwright_ctx

    if _browser is not None:
        return

    from playwright.async_api import async_playwright

    _playwright_ctx = await async_playwright().start()
    _browser = await _playwright_ctx.chromium.launch(headless=True)


async def capture_screenshot(url: str, out_path, logger, timeout_s: int = 30) -> dict:
    """Navigate to *url* and save a viewport screenshot to *out_path*.

    Returns {ok, status, final_url, error}. Images are NOT blocked here (unlike
    the scan fetch) since the point is a faithful visual snapshot of the page.
    Uses the shared browser singleton; init_browser() must be running.
    """
    result = {"ok": False, "status": 0, "final_url": url, "error": None}
    await init_browser()
    context = None
    try:
        context = await _browser.new_context(
            user_agent=USER_AGENT,
            viewport=VIEWPORT,
            ignore_https_errors=True,
        )
        page = await context.new_page()
        response = await page.goto(
            url, wait_until="domcontentloaded", timeout=timeout_s * 1000
        )
        if response is not None:
            result["status"] = response.status
        result["final_url"] = page.url
        out_path.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(out_path), full_page=False)
        result["ok"] = True
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"[:200]
        logger.warning("screenshot failed for %s: %s", url, result["error"])
    finally:
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass
    return result


async def close_browser() -> None:
    """Shut down the singleton browser and release Playwright resources."""
    global _browser, _playwright_ctx

    if _browser is not None:
        try:
            await _browser.close()
        except Exception:
            pass
        _browser = None

    if _playwright_ctx is not None:
        try:
            await _playwright_ctx.stop()
        except Exception:
            pass
        _playwright_ctx = None


# ---------------------------------------------------------------------------
# Public API: fetch with browser
# ---------------------------------------------------------------------------
async def fetch_with_browser(
    url: str,
    logger: logging.Logger,
    timeout_s: int = 30,
    scan_run_id: Optional[str] = None,
    cod_amm: Optional[str] = None,
) -> dict:
    """Navigate to *url* with headless Chromium and return rendered HTML.

    Returns a dict with keys:
        html          – rendered HTML after JS execution
        status        – HTTP status code (of the main document)
        final_url     – URL after any redirects
        error         – error message or None
        screenshot_path – path to saved PNG screenshot or None
    """
    result: dict = {
        "html": "",
        "status": 0,
        "final_url": url,
        "error": None,
        "screenshot_path": None,
    }

    # Ensure the browser is initialised (lazy)
    await init_browser()

    context = None
    try:
        context = await _browser.new_context(
            user_agent=USER_AGENT,
            viewport=VIEWPORT,
            ignore_https_errors=True,
        )
        page = await context.new_page()

        # Block heavy resources for speed
        async def _block_resources(route):
            if route.request.resource_type in _BLOCKED_RESOURCE_TYPES:
                await route.abort()
            else:
                await route.continue_()

        await page.route("**/*", _block_resources)

        logger.info("browser: navigating to %s", url)

        timeout_ms = timeout_s * 1000
        response = await page.goto(
            url,
            wait_until="networkidle",
            timeout=timeout_ms,
        )

        if response is not None:
            result["status"] = response.status
        result["final_url"] = page.url
        result["html"] = await page.content()

        # Save screenshot
        screenshot_path = _screenshot_path(scan_run_id, cod_amm)
        if screenshot_path is not None:
            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(screenshot_path), full_page=False)
            result["screenshot_path"] = str(screenshot_path)
            logger.debug("browser: screenshot saved to %s", screenshot_path)

        logger.info(
            "browser: loaded %s (status=%s, final_url=%s, html_len=%d)",
            url,
            result["status"],
            result["final_url"],
            len(result["html"]),
        )

    except Exception as exc:
        error_type = type(exc).__name__
        error_msg = f"{error_type}: {exc}"
        result["error"] = error_msg
        logger.error("browser: error fetching %s — %s", url, error_msg)

    finally:
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _screenshot_path(
    scan_run_id: Optional[str],
    cod_amm: Optional[str],
) -> Optional[Path]:
    """Build the screenshot file path, or None if identifiers are missing."""
    if scan_run_id is None or cod_amm is None:
        return None
    return LOGS_DIR / scan_run_id / cod_amm / "screenshot.png"
