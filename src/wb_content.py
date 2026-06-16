"""Qualitative content analysis of a whistleblowing page (rubric v1.0).

Confirms a page is genuinely the WB section by checking for the required and
supporting elements defined in the public methodology (Documentazione §3):

  CORE       tema (explicit topic) + canale reale (usable reporting channel) + RPCT
  SUPPORTING ANAC, tutele, presupposti, ordinaria-vs-WB, anonimato, procedura/PDF
  OUTCOME    confermata | informativa (senza canale) | falso_positivo

Reused by both the WhistleblowingPA quality dashboard and (later) the scraper's
own validation — the same definition on both sides of the virtuous loop.

Methodology version: v1.0 (2026-06-16).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

VERSION = "v1.0"

# Known WB platform domains: a link here = a real, usable reporting channel.
PLATFORM_DOMAINS = (
    "whistleblowing.it",
    "globaleaks",
    "segnalazioni.net",
    "whistleblowersoftware.com",
    "iusignal",
    "integrityline",
    "trusty.report",
    "whistlelink",
    "legality.it",
    "transparency.it",
)

_TEMA = re.compile(
    r"whistleblow|segnalazione\s+(?:di\s+)?illeciti|segnalazione\s+degli\s+illeciti|"
    r"tutela\s+del\s+segnalante|d\.?\s?lgs\.?\s*24/2023|decreto\s+legislativo\s+24/2023",
    re.IGNORECASE,
)
_RPCT = re.compile(
    r"\brpct\b|responsabile\s+(?:della\s+)?prevenzione\s+(?:della\s+)?corruzione|"
    r"responsabile\s+anticorruzione",
    re.IGNORECASE,
)
_ANAC = re.compile(r"\banac\b|anticorruzione\.it", re.IGNORECASE)
_TUTELE = re.compile(
    r"riservatezza|ritorsion|tutela\s+dell.identit|protezione\s+dell.identit|confidenzial",
    re.IGNORECASE,
)
_PRESUPPOSTI = re.compile(
    r"illeciti|violazion|irregolarit|condotte\s+illecite|abusi", re.IGNORECASE
)
_DISTINZIONE = re.compile(
    r"segnalazione\s+ordinaria|differenza\s+tra|non\s+costituisce\s+una\s+segnalazione|"
    r"reclam[io]\b",
    re.IGNORECASE,
)
_ANONIMATO = re.compile(r"anonim", re.IGNORECASE)
_PROC_KW = re.compile(
    r"procedura|regolamento|disciplina|policy|istruzion|linee\s+guida", re.IGNORECASE
)


def _has_real_channel(soup: BeautifulSoup, text: str) -> bool:
    # A link to a known WB platform...
    for a in soup.find_all("a", href=True):
        href = a["href"].lower()
        if any(d in href for d in PLATFORM_DOMAINS):
            return True
        if href.startswith("mailto:"):
            return True
    # ...or an on-page reporting form.
    if soup.find("form"):
        return True
    # ...or an embedded platform iframe.
    for ifr in soup.find_all("iframe", src=True):
        if any(d in ifr["src"].lower() for d in PLATFORM_DOMAINS):
            return True
    return False


def _has_procedura(soup: BeautifulSoup) -> bool:
    for a in soup.find_all("a", href=True):
        href = a["href"].lower()
        txt = a.get_text(" ", strip=True)
        if href.endswith(".pdf") and (_PROC_KW.search(txt) or _PROC_KW.search(href)):
            return True
    return False


def analyze_wb_content(html: str, base_url: str = "") -> dict:
    """Return the rubric flags, a score and the outcome for a page's HTML."""
    try:
        soup = BeautifulSoup(html or "", "html.parser")
        for t in soup(["script", "style", "noscript"]):
            t.decompose()
        text = soup.get_text(" ", strip=True)
    except Exception:
        soup = BeautifulSoup("", "html.parser")
        text = ""

    flags = {
        "has_tema": bool(_TEMA.search(text)),
        "has_canale": _has_real_channel(soup, text),
        "has_rpct": bool(_RPCT.search(text)),
        "has_anac": bool(_ANAC.search(text)),
        "has_tutele": bool(_TUTELE.search(text)),
        "has_presupposti": bool(_PRESUPPOSTI.search(text)),
        "has_distinzione": bool(_DISTINZIONE.search(text)),
        "has_anonimato": bool(_ANONIMATO.search(text)),
        "has_procedura": _has_procedura(soup),
    }
    supporting = sum(
        flags[k]
        for k in (
            "has_anac",
            "has_tutele",
            "has_presupposti",
            "has_distinzione",
            "has_anonimato",
            "has_procedura",
        )
    )
    score = sum(1 for v in flags.values() if v)

    if flags["has_tema"] and flags["has_canale"]:
        outcome = "confermata"
    elif flags["has_tema"] and supporting >= 2:
        outcome = "informativa"
    else:
        outcome = "falso_positivo"

    return {**flags, "score": score, "outcome": outcome, "version": VERSION}
