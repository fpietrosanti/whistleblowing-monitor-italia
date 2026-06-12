"""Extract RPCT contacts and WB reporting channels from PA pages."""

import re
import time

from bs4 import BeautifulSoup

from src.logging_config import save_http_debug, save_raw_html

USER_AGENT = "WhistleblowingMonitorItalia/1.0 (+https://test.infosecurity.ch)"

# --- Regex patterns ---

_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
)

# Italian phone: +39 0XX XXXXXXX, 0XX/XXXXXXX, 0XX.XXXXXXX, 0XX-XXXXXXX
_PHONE_RE = re.compile(
    r"(?:\+39\s?)?"           # optional +39 prefix
    r"0\d{1,3}"              # area code starting with 0
    r"[\s/.\-]?"             # separator
    r"\d{4,8}",              # local number
)

# Titles preceding a name
_TITLE_PREFIX = (
    r"(?:Dott\.ssa|Dott\.|Dr\.ssa|Dr\.|Prof\.ssa|Prof\.|Ing\.|Avv\.|Arch\.)"
)

# Name pattern: optional title + capitalised words
_NAME_RE = re.compile(
    rf"(?:{_TITLE_PREFIX}\s+)?"
    r"([A-ZÀ-Ü][a-zà-ü]+(?:\s+[A-ZÀ-Ü][a-zà-ü]+)+)",
)

# RPCT label patterns
_RPCT_LABELS = re.compile(
    r"Responsabile\s+(?:della\s+)?Prevenzione\s+della\s+Corruzione"
    r"|R\.?P\.?C\.?T\.?"
    r"|Responsabile\s+Anticorruzione"
    r"|Responsabile\s+per\s+la\s+Trasparenza",
    re.IGNORECASE,
)

# WB reporting channel labels
_WB_CHANNEL_LABELS = re.compile(
    r"segnalazione|canale\s+di\s+segnalazione|inviare\s+segnalazione"
    r"|come\s+segnalare|effettuare\s+una\s+segnalazione"
    r"|canale\s+interno",
    re.IGNORECASE,
)


def _get_text_blocks(html: str) -> str:
    """Extract visible text from HTML, preserving structure."""
    soup = BeautifulSoup(html, "html.parser")
    # Remove scripts and styles
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


def _extract_near_label(
    text: str,
    label_re: re.Pattern,
    window: int = 500,
) -> tuple[str | None, str | None, str | None]:
    """Search for a name, email, and phone near a label match.

    Looks in a window of `window` characters after the label match.
    Returns (name, email, phone).
    """
    name = None
    email = None
    phone = None

    for m in label_re.finditer(text):
        start = m.start()
        # Search in a window around the label (mostly after)
        context_start = max(0, start - 100)
        context_end = min(len(text), m.end() + window)
        context = text[context_start:context_end]

        if email is None:
            email_m = _EMAIL_RE.search(context)
            if email_m:
                email = email_m.group(0).lower()

        if phone is None:
            phone_m = _PHONE_RE.search(context)
            if phone_m:
                phone = phone_m.group(0).strip()

        if name is None:
            # Search for name after the label
            after_label = text[m.end(): m.end() + window]
            name_m = _NAME_RE.search(after_label)
            if name_m:
                # Use the full match (including title prefix if present)
                name = name_m.group(0).strip()

    return name, email, phone


def _extract_wb_contacts(text: str) -> tuple[str | None, str | None]:
    """Extract dedicated WB reporting email and phone."""
    wb_email = None
    wb_phone = None

    for m in _WB_CHANNEL_LABELS.finditer(text):
        context_start = max(0, m.start() - 100)
        context_end = min(len(text), m.end() + 500)
        context = text[context_start:context_end]

        if wb_email is None:
            email_m = _EMAIL_RE.search(context)
            if email_m:
                wb_email = email_m.group(0).lower()

        if wb_phone is None:
            phone_m = _PHONE_RE.search(context)
            if phone_m:
                wb_phone = phone_m.group(0).strip()

        if wb_email and wb_phone:
            break

    return wb_email, wb_phone


async def extract_rpct_contacts(
    cod_amm: str,
    scan_run_id: str,
    wb_page_html: str,
    site_url: str,
    http_client,
    logger,
) -> dict:
    """Extract RPCT contact details and WB reporting channels.

    Parameters
    ----------
    cod_amm : str
        PA identifier code.
    scan_run_id : str
        Current scan run identifier.
    wb_page_html : str
        Raw HTML of the whistleblowing page.
    site_url : str
        Base URL of the PA website (for fallback navigation).
    http_client : httpx.AsyncClient
        Shared async HTTP client.
    logger : logging.Logger
        Logger for this PA scan.

    Returns
    -------
    dict
        rpct_name, rpct_email, rpct_phone, wb_email, wb_phone
    """
    result = {
        "rpct_name": None,
        "rpct_email": None,
        "rpct_phone": None,
        "wb_email": None,
        "wb_phone": None,
    }

    if not wb_page_html:
        logger.info("rpct: no HTML available for %s, skipping", cod_amm)
        return result

    try:
        text = _get_text_blocks(wb_page_html)
    except Exception as exc:
        logger.error("rpct: error parsing HTML: %s", exc)
        return result

    # --- Extract RPCT info ---
    try:
        rpct_name, rpct_email, rpct_phone = _extract_near_label(
            text, _RPCT_LABELS
        )
        result["rpct_name"] = rpct_name
        result["rpct_email"] = rpct_email
        result["rpct_phone"] = rpct_phone
    except Exception as exc:
        logger.error("rpct: error extracting RPCT contacts: %s", exc)

    # --- Extract WB-specific channels ---
    try:
        wb_email, wb_phone = _extract_wb_contacts(text)
        result["wb_email"] = wb_email
        result["wb_phone"] = wb_phone
    except Exception as exc:
        logger.error("rpct: error extracting WB channels: %s", exc)

    # --- Fallback: try "Amministrazione Trasparente" main page ---
    rpct_found = result["rpct_name"] or result["rpct_email"]
    if not rpct_found and site_url:
        logger.info(
            "rpct: no RPCT info on WB page, trying Amministrazione Trasparente"
        )
        try:
            at_url = site_url.rstrip("/") + "/amministrazione-trasparente"
            t0 = time.monotonic()
            resp = await http_client.get(
                at_url,
                headers={"User-Agent": USER_AGENT},
                timeout=30,
                follow_redirects=True,
            )
            elapsed_ms = (time.monotonic() - t0) * 1000

            save_http_debug(
                scan_run_id,
                cod_amm,
                "rpct_at_fallback_debug.json",
                url=at_url,
                method="GET",
                status_code=resp.status_code,
                headers_dict=dict(resp.headers),
                response_time_ms=elapsed_ms,
                body_preview=resp.text[:2000] if resp.text else "",
            )

            if resp.status_code == 200:
                save_raw_html(
                    scan_run_id, cod_amm,
                    "at_page_fallback.html", resp.text,
                )

                at_text = _get_text_blocks(resp.text)
                name, email, phone = _extract_near_label(
                    at_text, _RPCT_LABELS
                )

                if name and not result["rpct_name"]:
                    result["rpct_name"] = name
                if email and not result["rpct_email"]:
                    result["rpct_email"] = email
                if phone and not result["rpct_phone"]:
                    result["rpct_phone"] = phone

                logger.info(
                    "rpct: AT fallback found name=%s email=%s phone=%s",
                    name, email, phone,
                )
            else:
                logger.info(
                    "rpct: AT fallback HTTP %d for %s", resp.status_code, at_url
                )

        except Exception as exc:
            logger.error("rpct: error fetching AT fallback page: %s", exc)

    logger.info(
        "rpct: results for %s — name=%s, email=%s, phone=%s, "
        "wb_email=%s, wb_phone=%s",
        cod_amm,
        result["rpct_name"],
        result["rpct_email"],
        result["rpct_phone"],
        result["wb_email"],
        result["wb_phone"],
    )

    return result
