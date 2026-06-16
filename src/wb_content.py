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

VERSION = "v1.3"

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
# Procedure described on-page (not only as a PDF)
_PROC_TEXT = re.compile(
    r"gestione\s+delle\s+segnalazioni|procedura\s+(?:per\s+)?(?:la\s+)?segnalazion|"
    r"come\s+(?:effettuare\s+una\s+|si\s+)?segnala|iter\s+della\s+segnalazione|"
    r"fasi\s+della\s+segnalazione",
    re.IGNORECASE,
)
# Privacy notice / data-protection information
_PRIVACY = re.compile(
    r"informativa\s+(?:sulla\s+)?privacy|informativa\s+(?:sul\s+)?trattamento|"
    r"trattamento\s+dei\s+dati|protezione\s+dei\s+dati\s+personali|\bgdpr\b|"
    r"reg(?:olamento)?\.?\s*(?:ue\s*)?(?:n\.?\s*)?2016/679|privacy\s+policy",
    re.IGNORECASE,
)
# Explicit legal references
_LEGGE = re.compile(
    r"d\.?\s?lgs\.?\s*(?:n\.?\s*)?24/2023|decreto\s+legislativo\s+(?:n\.?\s*)?24|"
    r"art\.?\s*54[\-\s]?bis|l\.?\s*(?:n\.?\s*)?190/2012|legge\s+190|"
    r"riferimenti\s+normativi|direttiva\s+\(?ue\)?\s*2019/1937",
    re.IGNORECASE,
)
# Channel open also to EXTERNAL reporters (not only internal staff): the page
# names non-employee categories of segnalanti.
_AUDIENCE_EXT = re.compile(
    r"collaborator|fornitor|consulent|lavorator\w*\s+autonom|tirocinant|volontari|"
    r"liberi\s+professionisti|soggetti\s+terzi|anche.{0,20}estern|chiunque|stagista|"
    r"ex\s+dipendent|candidat|appaltator|subappaltator",
    re.IGNORECASE,
)
# Channel restricted to internal staff only (intranet / entity credentials).
_INTERNAL_ONLY = re.compile(
    r"riservat\w*\s+(?:ai|al)\s+(?:dipendenti|personale)|solo.{0,10}dipendenti|"
    r"credenziali\s+aziendali|area\s+riservata|accesso\s+riservato|intranet",
    re.IGNORECASE,
)


# A dedicated mailbox local-part signals a real WB email channel (vs generic
# info@/urp@). Calibrated against Claude gold (v1.3): generic mailto/form no
# longer count as a channel.
_DEDICATED_BOX = re.compile(
    r"(whistleblow|segnalazion|anticorruzione|rpct|odv|illecit)", re.IGNORECASE
)


def _platform_link(soup: BeautifulSoup) -> bool:
    """True if the page links/embeds a known public WB platform."""
    for a in soup.find_all("a", href=True):
        if any(d in a["href"].lower() for d in PLATFORM_DOMAINS):
            return True
    for ifr in soup.find_all("iframe", src=True):
        if any(d in ifr["src"].lower() for d in PLATFORM_DOMAINS):
            return True
    return False


def _has_tema_strong(soup: BeautifulSoup, text: str) -> bool:
    """Topic must be the page's SUBJECT (title/heading or repeated), not a lone
    footer/menu link — kills homepage/index false positives (v1.3)."""
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    if _TEMA.search(title):
        return True
    for h in soup.find_all(["h1", "h2", "h3"]):
        if _TEMA.search(h.get_text(" ", strip=True)):
            return True
    return len(_TEMA.findall(text)) >= 2


def _has_real_channel(soup: BeautifulSoup, text: str) -> bool:
    # Public WB platform link/iframe — the strong signal.
    if _platform_link(soup):
        return True
    # Dedicated WB mailbox (not a generic info@/urp@).
    for a in soup.find_all("a", href=True):
        href = a["href"].lower()
        if href.startswith("mailto:") and _DEDICATED_BOX.search(href.split("@")[0]):
            return True
    # A form that is plausibly a reporting form (WB context in/around it).
    for f in soup.find_all("form"):
        if _TEMA.search(f.get_text(" ", strip=True)):
            return True
    return False


def _has_canale_aperto(soup: BeautifulSoup, text: str) -> bool:
    """True if the channel appears open also to EXTERNAL reporters (not only
    internal staff): a public platform (no entity-credential gate) or the page
    names non-employee categories of segnalanti."""
    if _platform_link(soup):
        return True
    if _AUDIENCE_EXT.search(text) and not _INTERNAL_ONLY.search(text):
        return True
    return False


def _has_procedura(soup: BeautifulSoup, text: str) -> bool:
    # Procedure described on the page itself...
    if _PROC_TEXT.search(text):
        return True
    # ...or linked as a PDF.
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
        "has_tema": _has_tema_strong(soup, text),
        "has_canale": _has_real_channel(soup, text),
        "has_canale_aperto": _has_canale_aperto(soup, text),
        "has_rpct": bool(_RPCT.search(text)),
        "has_tutele": bool(_TUTELE.search(text)),
        "has_presupposti": bool(_PRESUPPOSTI.search(text)),
        "has_distinzione": bool(_DISTINZIONE.search(text)),
        "has_anonimato": bool(_ANONIMATO.search(text)),
        "has_procedura": _has_procedura(soup, text),
        "has_privacy": bool(_PRIVACY.search(text)),
        "has_legge": bool(_LEGGE.search(text)),
    }
    supporting = sum(
        flags[k]
        for k in (
            "has_canale_aperto",
            "has_tutele",
            "has_presupposti",
            "has_distinzione",
            "has_anonimato",
            "has_procedura",
            "has_privacy",
            "has_legge",
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
