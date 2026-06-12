"""Probe whistleblowing channels: accessibility, auth requirements, anonymity.

Given the WB section page HTML and extracted links, this module locates the
actual reporting channel URL, fetches it, and analyses its authentication
and anonymity properties.
"""

import re
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from src.logging_config import save_http_debug, save_raw_html

USER_AGENT = "WhistleblowingMonitorItalia/1.0 (+https://test.infosecurity.ch)"

# ---------------------------------------------------------------------------
# Known WB platform domains
# ---------------------------------------------------------------------------
KNOWN_PLATFORM_DOMAINS = [
    "globaleaks",
    "legality.it",
    "segnalazioni.net",
    "whistleblowersoftware.com",
    "whistleblowing.it",
    "iusignal.com",
    "transparency.it",
    "integrityline.com",
    "bfrcsistem.it",
]

# Link-text patterns that hint at a reporting channel
CHANNEL_TEXT_PATTERNS = re.compile(
    r"segnala|invia\s+segnalazione|accedi\s+al\s+canale|piattaforma|"
    r"canale\s+di\s+segnalazione|whistleblow|effettua.*segnalazione|"
    r"inoltra.*segnalazione",
    re.IGNORECASE,
)

# Email regex (simple, good enough for mailto: and inline addresses)
EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", re.ASCII
)


# ---------------------------------------------------------------------------
# Lightweight HTML helpers (no external dependency beyond stdlib)
# ---------------------------------------------------------------------------
class _IframeSrcExtractor(HTMLParser):
    """Extract src attributes from <iframe> tags."""

    def __init__(self):
        super().__init__()
        self.srcs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "iframe":
            for name, value in attrs:
                if name == "src" and value:
                    self.srcs.append(value)


def _extract_iframe_srcs(html: str) -> list[str]:
    parser = _IframeSrcExtractor()
    try:
        parser.feed(html)
    except Exception:
        pass
    return parser.srcs


def _is_platform_url(url: str) -> bool:
    """Check if the URL belongs to a known WB platform."""
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    return any(kp in domain for kp in KNOWN_PLATFORM_DOMAINS)


def _find_channel_url(
    wb_section_url: str,
    wb_links: list[dict],
    wb_page_html: str,
) -> str | None:
    """Identify the reporting-channel URL from page links and iframes.

    wb_links items are expected to have at least {"href": ..., "text": ...}.

    Priority:
      1. Links to known WB platform domains
      2. Iframes embedding known WB platforms
      3. Links whose anchor text matches channel patterns
      4. Iframe srcs that look like external platforms (https, different domain)
    """
    candidates_platform: list[str] = []
    candidates_text: list[str] = []

    for link in wb_links or []:
        href = (link.get("href") or "").strip()
        text = (link.get("text") or "").strip()
        if not href or href.startswith("#") or href.lower().startswith("mailto:"):
            continue
        # Resolve relative URLs
        if not href.startswith(("http://", "https://")):
            href = urljoin(wb_section_url, href)
        if _is_platform_url(href):
            candidates_platform.append(href)
        elif CHANNEL_TEXT_PATTERNS.search(text):
            candidates_text.append(href)

    # Check iframes
    iframe_srcs = _extract_iframe_srcs(wb_page_html or "")
    iframe_platform: list[str] = []
    iframe_other: list[str] = []
    section_domain = urlparse(wb_section_url).netloc.lower() if wb_section_url else ""
    for src in iframe_srcs:
        if not src.startswith(("http://", "https://")):
            src = urljoin(wb_section_url, src)
        parsed = urlparse(src)
        if _is_platform_url(src):
            iframe_platform.append(src)
        elif parsed.netloc.lower() != section_domain:
            iframe_other.append(src)

    # Return by priority
    if candidates_platform:
        return candidates_platform[0]
    if iframe_platform:
        return iframe_platform[0]
    if candidates_text:
        return candidates_text[0]
    if iframe_other:
        return iframe_other[0]
    return None


def _find_email_channel(
    wb_links: list[dict], wb_page_html: str
) -> str | None:
    """Find a reporting email address from links or page HTML."""
    # Check mailto: links first
    for link in wb_links or []:
        href = (link.get("href") or "").strip()
        if href.lower().startswith("mailto:"):
            addr = href[7:].split("?")[0].strip()
            if addr:
                return addr

    # Fall back to scanning page HTML for email addresses
    if wb_page_html:
        emails = EMAIL_RE.findall(wb_page_html)
        # Filter out common non-reporting addresses
        skip = {"pec", "protocollo", "info@", "urp@", "segreteria@"}
        for email in emails:
            lower = email.lower()
            if any(s in lower for s in skip):
                continue
            return email
    return None


# ---------------------------------------------------------------------------
# Channel page analysis
# ---------------------------------------------------------------------------
_SPID_RE = re.compile(r"spid", re.IGNORECASE)
_CIE_RE = re.compile(r"\bcie\b|carta\s+d.?identit", re.IGNORECASE)
_LOGIN_RE = re.compile(
    r"login|accedi|autenticazione|registra|sign.?in|log.?in", re.IGNORECASE
)
_ANON_RE = re.compile(
    r"anon[io]m[ao]|senza\s+registrazione|anonymous", re.IGNORECASE
)


def _analyse_channel_page(html: str) -> dict:
    """Analyse the channel page HTML for auth and anonymity signals.

    Returns a dict with:
      - requires_auth: bool
      - auth_type: str  ("none", "spid", "cie", "internal", "other")
      - anonymous_allowed: bool|None
      - strong_auth_required: bool
    """
    html_lower = html.lower() if html else ""

    has_spid = bool(_SPID_RE.search(html_lower))
    has_cie = bool(_CIE_RE.search(html_lower))
    has_login = bool(_LOGIN_RE.search(html_lower))
    has_anon = bool(_ANON_RE.search(html_lower))

    # Determine auth_type
    if has_spid and has_cie:
        auth_type = "spid"  # SPID takes precedence as strongest
        requires_auth = True
        strong_auth = True
    elif has_spid:
        auth_type = "spid"
        requires_auth = True
        strong_auth = True
    elif has_cie:
        auth_type = "cie"
        requires_auth = True
        strong_auth = True
    elif has_login:
        auth_type = "internal"
        requires_auth = True
        strong_auth = False
    else:
        auth_type = "none"
        requires_auth = False
        strong_auth = False

    # Anonymous allowed: explicit signal in page
    if has_anon:
        anonymous_allowed = True
    elif requires_auth and not has_anon:
        anonymous_allowed = False
    else:
        anonymous_allowed = None  # Cannot determine

    return {
        "requires_auth": requires_auth,
        "auth_type": auth_type,
        "anonymous_allowed": anonymous_allowed,
        "strong_auth_required": strong_auth,
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
async def probe_wb_channel(
    cod_amm: str,
    scan_run_id: str,
    wb_section_url: str | None,
    wb_links: list[dict],
    wb_page_html: str | None,
    http_client,
    logger,
) -> dict:
    """Probe the whistleblowing channel for a given PA.

    Parameters
    ----------
    cod_amm : str
        PA code from IndicePA.
    scan_run_id : str
        Identifier for the current scan run (used for logging paths).
    wb_section_url : str | None
        URL of the WB section page on the PA website.
    wb_links : list[dict]
        Links extracted from the WB section page, each with "href" and "text".
    wb_page_html : str | None
        Raw HTML of the WB section page.
    http_client : httpx.AsyncClient
        Shared async HTTP client for making requests.
    logger : logging.Logger
        Logger instance for this PA scan.

    Returns
    -------
    dict
        Channel probe results; see module docstring for schema.
    """
    result = {
        "wb_digital_channel": False,
        "wb_channel_url": None,
        "wb_channel_reachable": False,
        "wb_channel_type": None,
        "wb_requires_auth": False,
        "wb_auth_type": None,
        "wb_anonymous_allowed": None,
        "wb_strong_auth_required": False,
        "channel_page_html": None,
    }

    if not wb_section_url and not wb_page_html:
        logger.info("No WB section URL or HTML provided — skipping probe")
        return result

    # Step 1: Find the channel URL
    try:
        channel_url = _find_channel_url(
            wb_section_url or "", wb_links or [], wb_page_html or ""
        )
    except Exception as exc:
        logger.error("Error searching for channel URL: %s", exc)
        channel_url = None

    if channel_url:
        logger.info("Found digital channel URL: %s", channel_url)
        result["wb_digital_channel"] = True
        result["wb_channel_url"] = channel_url
        result["wb_channel_type"] = (
            "platform" if _is_platform_url(channel_url) else "form"
        )
    else:
        # Check for email-only channel
        try:
            email = _find_email_channel(wb_links or [], wb_page_html or "")
        except Exception as exc:
            logger.error("Error searching for email channel: %s", exc)
            email = None

        if email:
            logger.info("No digital platform found; email channel: %s", email)
            result["wb_digital_channel"] = False
            result["wb_channel_type"] = "email_only"
            result["wb_channel_url"] = f"mailto:{email}"
            # Analyse the WB section page itself for auth signals
            if wb_page_html:
                analysis = _analyse_channel_page(wb_page_html)
                result["wb_requires_auth"] = analysis["requires_auth"]
                result["wb_auth_type"] = analysis["auth_type"]
                result["wb_anonymous_allowed"] = analysis["anonymous_allowed"]
                result["wb_strong_auth_required"] = analysis["strong_auth_required"]
            return result
        else:
            logger.info("No digital channel or email found")
            return result

    # Step 2: Fetch the channel URL and analyse
    try:
        t0 = time.monotonic()
        resp = await http_client.get(
            channel_url,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
            timeout=30,
        )
        elapsed_ms = (time.monotonic() - t0) * 1000

        result["wb_channel_reachable"] = 200 <= resp.status_code < 400

        logger.info(
            "Channel fetch: %s → HTTP %d (%.0f ms)",
            channel_url,
            resp.status_code,
            elapsed_ms,
        )

        # Save HTTP debug info
        try:
            save_http_debug(
                scan_run_id=str(scan_run_id),
                cod_amm=cod_amm,
                filename="channel_http_debug.json",
                url=str(resp.url),
                method="GET",
                status_code=resp.status_code,
                headers_dict=dict(resp.headers),
                response_time_ms=elapsed_ms,
                body_preview=resp.text[:2000] if resp.text else "",
            )
        except Exception as exc:
            logger.warning("Failed to save HTTP debug: %s", exc)

        channel_html = resp.text if resp.status_code < 400 else None

        if channel_html:
            result["channel_page_html"] = channel_html

            # Save raw HTML
            try:
                save_raw_html(
                    scan_run_id=str(scan_run_id),
                    cod_amm=cod_amm,
                    filename="channel_page.html",
                    html_content=channel_html,
                )
            except Exception as exc:
                logger.warning("Failed to save channel HTML: %s", exc)

            # Analyse auth/anonymity
            analysis = _analyse_channel_page(channel_html)
            result["wb_requires_auth"] = analysis["requires_auth"]
            result["wb_auth_type"] = analysis["auth_type"]
            result["wb_anonymous_allowed"] = analysis["anonymous_allowed"]
            result["wb_strong_auth_required"] = analysis["strong_auth_required"]

            logger.info(
                "Channel analysis: auth=%s, auth_type=%s, anon=%s, strong=%s",
                analysis["requires_auth"],
                analysis["auth_type"],
                analysis["anonymous_allowed"],
                analysis["strong_auth_required"],
            )
        else:
            logger.warning(
                "Channel page returned HTTP %d — cannot analyse",
                resp.status_code,
            )

    except Exception as exc:
        logger.error("Error fetching channel URL %s: %s", channel_url, exc)
        result["wb_channel_reachable"] = False

    return result
