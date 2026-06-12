"""Download WB policy PDFs from PA whistleblowing pages."""

import hashlib
import re
import time
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from src.config import POLICIES_DIR, USER_AGENT
from src.logging_config import save_http_debug, save_raw_html

MAX_PDF_SIZE = 50 * 1024 * 1024  # 50 MB
DOWNLOAD_TIMEOUT = 30  # seconds

# Keywords that signal a WB policy document
_LINK_TEXT_KEYWORDS = re.compile(
    r"policy|procedura|regolamento|disciplina|whistleblowing|segnalazione",
    re.IGNORECASE,
)

# Keywords in filenames (href)
_HREF_KEYWORDS = re.compile(
    r"policy|procedura|regolamento|disciplina|whistleblowing|segnalazione",
    re.IGNORECASE,
)


def _find_pdf_links(html: str, base_url: str) -> list[dict]:
    """Extract candidate PDF links from HTML.

    Returns a list of dicts with keys 'url' and 'text'.
    Priority: links whose text or href match policy-related keywords come first.
    """
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[dict] = []
    seen_urls: set[str] = set()

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        link_text = a_tag.get_text(strip=True)
        full_url = urljoin(base_url, href)

        if full_url in seen_urls:
            continue
        seen_urls.add(full_url)

        is_pdf_ext = href.lower().endswith(".pdf")
        text_match = bool(_LINK_TEXT_KEYWORDS.search(link_text))
        href_match = bool(_HREF_KEYWORDS.search(href))

        # Accept: explicit .pdf links, or keyword-matched links (might be
        # PDF behind a redirect / dynamic URL)
        if is_pdf_ext or (text_match and href_match):
            candidates.append(
                {
                    "url": full_url,
                    "text": link_text,
                    "score": (2 if text_match else 0) + (1 if is_pdf_ext else 0),
                }
            )
        elif is_pdf_ext:
            # Generic PDF — lower priority
            candidates.append(
                {
                    "url": full_url,
                    "text": link_text,
                    "score": 0,
                }
            )

    # Sort by relevance score descending
    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates


async def download_wb_policy(
    cod_amm: str,
    scan_run_id: str,
    wb_page_html: str,
    wb_section_url: str,
    http_client,
    logger,
) -> dict:
    """Download the WB policy PDF from a PA's whistleblowing page.

    Parameters
    ----------
    cod_amm : str
        PA identifier code.
    scan_run_id : str
        Current scan run identifier.
    wb_page_html : str
        Raw HTML of the whistleblowing page.
    wb_section_url : str
        URL of the whistleblowing page (used to resolve relative links).
    http_client : httpx.AsyncClient
        Shared async HTTP client.
    logger : logging.Logger
        Logger for this PA scan.

    Returns
    -------
    dict
        wb_policy_visible, wb_policy_url, wb_policy_pdf_path, wb_policy_pdf_hash
    """
    result = {
        "wb_policy_visible": False,
        "wb_policy_url": None,
        "wb_policy_pdf_path": None,
        "wb_policy_pdf_hash": None,
    }

    if not wb_page_html:
        logger.info("policy: no HTML available for %s, skipping", cod_amm)
        return result

    try:
        candidates = _find_pdf_links(wb_page_html, wb_section_url)
    except Exception as exc:
        logger.error("policy: error parsing HTML for PDF links: %s", exc)
        return result

    if not candidates:
        logger.info("policy: no PDF links found on WB page for %s", cod_amm)
        return result

    # We found at least one candidate — mark as visible
    result["wb_policy_visible"] = True

    # Try candidates in priority order until one downloads successfully
    for candidate in candidates:
        pdf_url = candidate["url"]
        logger.info(
            "policy: attempting PDF download: %s (text=%r)",
            pdf_url,
            candidate["text"],
        )
        result["wb_policy_url"] = pdf_url

        try:
            t0 = time.monotonic()
            resp = await http_client.get(
                pdf_url,
                headers={"User-Agent": USER_AGENT},
                timeout=DOWNLOAD_TIMEOUT,
                follow_redirects=True,
            )
            elapsed_ms = (time.monotonic() - t0) * 1000

            save_http_debug(
                scan_run_id,
                cod_amm,
                "policy_download_debug.json",
                url=pdf_url,
                method="GET",
                status_code=resp.status_code,
                headers_dict=dict(resp.headers),
                response_time_ms=elapsed_ms,
                body_preview=f"[binary PDF, {len(resp.content)} bytes]",
            )

            if resp.status_code != 200:
                logger.warning("policy: HTTP %d for %s", resp.status_code, pdf_url)
                continue

            content = resp.content

            # Validate size
            if len(content) > MAX_PDF_SIZE:
                logger.warning(
                    "policy: PDF too large (%d bytes > %d), skipping %s",
                    len(content),
                    MAX_PDF_SIZE,
                    pdf_url,
                )
                continue

            # Validate that it looks like a PDF
            if not content[:5].startswith(b"%PDF-"):
                logger.warning(
                    "policy: response is not a PDF (starts with %r), skipping %s",
                    content[:20],
                    pdf_url,
                )
                continue

            # Save to disk
            pa_policy_dir = POLICIES_DIR / cod_amm
            pa_policy_dir.mkdir(parents=True, exist_ok=True)

            # Derive a safe filename from the URL
            url_filename = pdf_url.rstrip("/").split("/")[-1]
            # Sanitise: keep only alphanumeric, hyphens, underscores, dots
            safe_name = re.sub(r"[^\w.\-]", "_", url_filename)
            if not safe_name.lower().endswith(".pdf"):
                safe_name += ".pdf"

            pdf_path = pa_policy_dir / safe_name
            pdf_path.write_bytes(content)

            # Compute hash
            sha256 = hashlib.sha256(content).hexdigest()

            result["wb_policy_pdf_path"] = str(pdf_path)
            result["wb_policy_pdf_hash"] = sha256

            logger.info(
                "policy: saved PDF (%d bytes, sha256=%s) to %s",
                len(content),
                sha256,
                pdf_path,
            )
            return result

        except Exception as exc:
            logger.error("policy: error downloading PDF from %s: %s", pdf_url, exc)
            continue

    # All candidates failed to download
    logger.warning(
        "policy: found %d PDF link(s) but none could be downloaded for %s",
        len(candidates),
        cod_amm,
    )
    return result
