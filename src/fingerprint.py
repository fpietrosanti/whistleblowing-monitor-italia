"""Fingerprint the whistleblowing software behind a PA's channel URL.

Probes the page HTML and, for GlobaLeaks, its REST API to identify the
platform, version, and detection confidence.
"""

from __future__ import annotations

import re
import time
import logging
from urllib.parse import urljoin

from src.logging_config import save_http_debug


# ---------------------------------------------------------------------------
# Result helper
# ---------------------------------------------------------------------------

def _result(
    software: str | None = None,
    version: str | None = None,
    confidence: float = 0.0,
    method: str = "none",
) -> dict:
    return {
        "wb_software": software,
        "wb_software_version": version,
        "wb_software_confidence": confidence,
        "fingerprint_method": method,
    }


# ---------------------------------------------------------------------------
# Individual detectors (ordered by prevalence)
# ---------------------------------------------------------------------------

async def _check_globaleaks(
    cod_amm: str,
    scan_run_id: str,
    channel_url: str,
    channel_html: str,
    http_client,
    logger: logging.Logger,
) -> dict | None:
    """Detect GlobaLeaks via headers, API, HTML markers, and URL pattern."""

    html_lower = channel_html.lower()
    signals: list[str] = []
    version: str | None = None

    # 1. X-GlobaLeaks response header (checked via a lightweight HEAD/GET)
    try:
        t0 = time.monotonic()
        resp = await http_client.get(channel_url, follow_redirects=True)
        elapsed = (time.monotonic() - t0) * 1000
        save_http_debug(
            scan_run_id, cod_amm, "fingerprint_head.json",
            url=str(resp.url), method="GET",
            status_code=resp.status_code,
            headers_dict=dict(resp.headers),
            response_time_ms=round(elapsed, 1),
            body_preview="(fingerprint header check)",
        )
        gl_header = resp.headers.get("X-GlobaLeaks")
        if gl_header:
            signals.append("X-GlobaLeaks header present")
            logger.debug("GlobaLeaks: X-GlobaLeaks header found: %s", gl_header)
    except Exception as exc:
        logger.debug("GlobaLeaks: header probe failed: %s", exc)

    # 2. URL contains /#/
    if "/#/" in channel_url:
        signals.append("URL contains /#/")
        logger.debug("GlobaLeaks: URL contains /#/ fragment routing")

    # 3. HTML markers
    if "globaleaks" in html_lower:
        signals.append("HTML contains 'globaleaks'")
        logger.debug("GlobaLeaks: 'globaleaks' found in HTML body")

    if 'ng-app' in html_lower:
        signals.append("AngularJS ng-app attribute present")
        logger.debug("GlobaLeaks: ng-app attribute found (AngularJS SPA)")

    # CSS class prefix gl-
    if re.search(r'class="[^"]*\bgl-', channel_html, re.IGNORECASE):
        signals.append("CSS classes with gl- prefix")
        logger.debug("GlobaLeaks: gl- CSS class prefix detected")

    if not signals:
        return None

    # 4. Try /api/public for version info
    api_url = urljoin(channel_url.split("/#/")[0].rstrip("/") + "/", "api/public")
    try:
        t0 = time.monotonic()
        api_resp = await http_client.get(api_url, follow_redirects=True, timeout=15)
        elapsed = (time.monotonic() - t0) * 1000

        body_text = api_resp.text[:2000]
        save_http_debug(
            scan_run_id, cod_amm, "fingerprint_api_public.json",
            url=str(api_resp.url), method="GET",
            status_code=api_resp.status_code,
            headers_dict=dict(api_resp.headers),
            response_time_ms=round(elapsed, 1),
            body_preview=body_text,
        )

        if api_resp.status_code == 200:
            try:
                api_json = api_resp.json()
                signals.append("/api/public returned valid JSON")
                logger.debug("GlobaLeaks: /api/public responded with JSON")

                # Extract version from the node object
                node = api_json.get("node", {})
                version = node.get("version") or node.get("software_version")
                if version:
                    logger.debug("GlobaLeaks: version from API = %s", version)
            except Exception:
                logger.debug("GlobaLeaks: /api/public did not return valid JSON")
    except Exception as exc:
        logger.debug("GlobaLeaks: /api/public probe failed: %s", exc)

    # 5. Try to extract version from HTML meta tags if not found via API
    if not version:
        meta_match = re.search(
            r'<meta[^>]+globaleaks[^>]+version["\s:=]+([0-9][0-9.]+)',
            channel_html, re.IGNORECASE,
        )
        if meta_match:
            version = meta_match.group(1)
            logger.debug("GlobaLeaks: version from meta tag = %s", version)

    # Confidence based on number of signals
    confidence = min(1.0, 0.3 + 0.15 * len(signals))

    method_parts = "; ".join(signals)
    logger.info(
        "GlobaLeaks detected (confidence=%.2f): %s", confidence, method_parts
    )
    return _result("GlobaLeaks", version, confidence, f"globaleaks: {method_parts}")


async def _check_legality(
    channel_url: str, channel_html: str, logger: logging.Logger,
) -> dict | None:
    """Detect Legality Whistleblowing by domain and HTML markers."""

    url_lower = channel_url.lower()
    html_lower = channel_html.lower()
    signals: list[str] = []

    if "legality.it" in url_lower or "legalitywhistleblowing.it" in url_lower:
        signals.append("domain matches legality")
        logger.debug("Legality: domain match in URL")

    if "legality" in html_lower:
        # Look for it in title or footer regions
        title_match = re.search(r"<title[^>]*>.*?legality.*?</title>", html_lower)
        if title_match:
            signals.append("title contains Legality")
            logger.debug("Legality: found in <title>")

        footer_match = re.search(
            r"<footer[\s\S]{0,3000}?legality[\s\S]{0,500}?</footer>", html_lower
        )
        if footer_match:
            signals.append("footer contains Legality")
            logger.debug("Legality: found in <footer>")

        if not title_match and not footer_match:
            signals.append("HTML body contains 'legality'")
            logger.debug("Legality: generic body reference")

    # Specific JS/CSS paths
    if re.search(r'src="[^"]*legality[^"]*\.js"', html_lower):
        signals.append("Legality JS asset")
        logger.debug("Legality: JS asset path detected")

    if re.search(r'href="[^"]*legality[^"]*\.css"', html_lower):
        signals.append("Legality CSS asset")
        logger.debug("Legality: CSS asset path detected")

    if not signals:
        return None

    confidence = min(1.0, 0.4 + 0.2 * len(signals))
    method_parts = "; ".join(signals)
    logger.info(
        "Legality Whistleblowing detected (confidence=%.2f): %s",
        confidence, method_parts,
    )
    return _result(
        "Legality Whistleblowing", None, confidence,
        f"legality: {method_parts}",
    )


async def _check_segnalazioni_net(
    channel_url: str, channel_html: str, logger: logging.Logger,
) -> dict | None:
    """Detect Segnalazioni.net by domain and HTML markers."""

    url_lower = channel_url.lower()
    html_lower = channel_html.lower()
    signals: list[str] = []

    if "segnalazioni.net" in url_lower:
        signals.append("domain matches segnalazioni.net")
        logger.debug("Segnalazioni.net: domain match in URL")

    if "segnalazioni.net" in html_lower:
        signals.append("HTML references segnalazioni.net")
        logger.debug("Segnalazioni.net: reference found in HTML")

    title_match = re.search(
        r"<title[^>]*>.*?segnalazioni\.net.*?</title>", html_lower
    )
    if title_match:
        signals.append("title contains segnalazioni.net")
        logger.debug("Segnalazioni.net: found in <title>")

    if not signals:
        return None

    confidence = min(1.0, 0.5 + 0.2 * len(signals))
    method_parts = "; ".join(signals)
    logger.info(
        "Segnalazioni.net detected (confidence=%.2f): %s",
        confidence, method_parts,
    )
    return _result(
        "Segnalazioni.net", None, confidence,
        f"segnalazioni_net: {method_parts}",
    )


async def _check_whistleblowersoftware(
    channel_url: str, channel_html: str, logger: logging.Logger,
) -> dict | None:
    """Detect WhistleblowerSoftware.com by domain and iframe patterns."""

    url_lower = channel_url.lower()
    html_lower = channel_html.lower()
    signals: list[str] = []

    if "whistleblowersoftware.com" in url_lower:
        signals.append("domain matches whistleblowersoftware.com")
        logger.debug("WhistleblowerSoftware: domain match in URL")

    if "whistleblowersoftware.com" in html_lower:
        signals.append("HTML references whistleblowersoftware.com")
        logger.debug("WhistleblowerSoftware: reference found in HTML")

    # Specific iframe embedding pattern
    iframe_match = re.search(
        r'<iframe[^>]+src="[^"]*whistleblowersoftware\.com[^"]*"',
        html_lower,
    )
    if iframe_match:
        signals.append("iframe embeds whistleblowersoftware.com")
        logger.debug("WhistleblowerSoftware: iframe embed detected")

    if not signals:
        return None

    confidence = min(1.0, 0.5 + 0.2 * len(signals))
    method_parts = "; ".join(signals)
    logger.info(
        "WhistleblowerSoftware.com detected (confidence=%.2f): %s",
        confidence, method_parts,
    )
    return _result(
        "WhistleblowerSoftware.com", None, confidence,
        f"whistleblowersoftware: {method_parts}",
    )


async def _check_isweb(
    channel_html: str, logger: logging.Logger,
) -> dict | None:
    """Detect ISWEB by footer/meta references."""

    html_lower = channel_html.lower()
    signals: list[str] = []

    # Footer
    footer_match = re.search(
        r"<footer[\s\S]{0,3000}?isweb[\s\S]{0,500}?</footer>", html_lower
    )
    if footer_match:
        signals.append("footer contains ISWEB")
        logger.debug("ISWEB: found in <footer>")

    # Meta tags
    meta_match = re.search(r'<meta[^>]+isweb[^>]*>', html_lower)
    if meta_match:
        signals.append("meta tag references ISWEB")
        logger.debug("ISWEB: found in <meta>")

    # Generator or author meta
    gen_match = re.search(
        r'<meta[^>]+(?:generator|author)[^>]+isweb[^>]*>', html_lower
    )
    if gen_match:
        signals.append("generator/author meta = ISWEB")
        logger.debug("ISWEB: generator/author meta tag")

    # Generic body reference (lower confidence)
    if not signals and "isweb" in html_lower:
        signals.append("HTML body contains 'isweb'")
        logger.debug("ISWEB: generic body reference")

    if not signals:
        return None

    confidence = min(1.0, 0.35 + 0.2 * len(signals))
    method_parts = "; ".join(signals)
    logger.info("ISWEB detected (confidence=%.2f): %s", confidence, method_parts)
    return _result("ISWEB", None, confidence, f"isweb: {method_parts}")


async def _check_comunica_wb(
    channel_html: str, logger: logging.Logger,
) -> dict | None:
    """Detect Comunica WB by specific patterns."""

    html_lower = channel_html.lower()
    signals: list[str] = []

    if "comunicawb" in html_lower:
        signals.append("HTML contains 'ComunicaWB'")
        logger.debug("ComunicaWB: literal reference found")

    # Pattern: "comunica" near "whistleblowing" in the same region
    comunica_wb_match = re.search(
        r"comunica[\s\S]{0,100}?whistleblowing|whistleblowing[\s\S]{0,100}?comunica",
        html_lower,
    )
    if comunica_wb_match:
        signals.append("'comunica' near 'whistleblowing' in HTML")
        logger.debug("ComunicaWB: proximity match for comunica + whistleblowing")

    # Title match
    title_match = re.search(
        r"<title[^>]*>.*?comunica.*?</title>", html_lower
    )
    if title_match:
        signals.append("title contains 'comunica'")
        logger.debug("ComunicaWB: found in <title>")

    if not signals:
        return None

    confidence = min(1.0, 0.4 + 0.2 * len(signals))
    method_parts = "; ".join(signals)
    logger.info(
        "Comunica WB detected (confidence=%.2f): %s", confidence, method_parts
    )
    return _result("Comunica WB", None, confidence, f"comunica_wb: {method_parts}")


async def _check_custom_form(
    channel_html: str, logger: logging.Logger,
) -> dict | None:
    """Fallback: detect a generic custom/internal form with file upload + textarea."""

    html_lower = channel_html.lower()

    has_file_input = bool(re.search(r'<input[^>]+type=["\']file["\']', html_lower))
    has_textarea = "<textarea" in html_lower
    has_form = "<form" in html_lower

    if has_form and has_file_input and has_textarea:
        logger.info(
            "Custom/Interno: HTML form with file upload + textarea detected "
            "(no known software signature matched)"
        )
        return _result(
            "Custom/Interno", None, 0.3,
            "custom_form: HTML form with file upload and textarea, "
            "no known software signature",
        )

    return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def fingerprint_software(
    cod_amm: str,
    scan_run_id: str,
    channel_url: str,
    channel_html: str,
    http_client,
    logger: logging.Logger,
) -> dict:
    """Identify which whistleblowing software a PA channel uses.

    Tests signatures in order of prevalence (GlobaLeaks first) and returns
    the first confident match.  Falls back to a generic custom-form check.

    Parameters
    ----------
    cod_amm : str
        PA code (e.g. "c_h501").
    scan_run_id : str
        Unique identifier for this scan batch.
    channel_url : str
        The URL of the whistleblowing channel.
    channel_html : str
        Pre-fetched HTML content of the channel page.
    http_client :
        An httpx.AsyncClient (or compatible) for additional probes.
    logger : logging.Logger
        Logger instance for this PA's scan.

    Returns
    -------
    dict with keys wb_software, wb_software_version,
    wb_software_confidence, fingerprint_method.
    """

    logger.info("Fingerprinting software for %s at %s", cod_amm, channel_url)

    # Ordered by prevalence -- return on first match
    detectors = [
        ("GlobaLeaks", _check_globaleaks(
            cod_amm, scan_run_id, channel_url, channel_html, http_client, logger,
        )),
        ("Legality Whistleblowing", _check_legality(
            channel_url, channel_html, logger,
        )),
        ("Segnalazioni.net", _check_segnalazioni_net(
            channel_url, channel_html, logger,
        )),
        ("WhistleblowerSoftware.com", _check_whistleblowersoftware(
            channel_url, channel_html, logger,
        )),
        ("ISWEB", _check_isweb(channel_html, logger)),
        ("Comunica WB", _check_comunica_wb(channel_html, logger)),
        ("Custom/Interno", _check_custom_form(channel_html, logger)),
    ]

    for name, coro in detectors:
        try:
            result = await coro
            if result is not None:
                logger.info(
                    "Fingerprint result for %s: %s (confidence=%.2f)",
                    cod_amm, result["wb_software"],
                    result["wb_software_confidence"],
                )
                return result
        except Exception as exc:
            logger.warning(
                "Fingerprint check '%s' raised an error for %s: %s",
                name, cod_amm, exc,
            )
            continue

    logger.info("No software fingerprint matched for %s", cod_amm)
    return _result(method="no_match")
