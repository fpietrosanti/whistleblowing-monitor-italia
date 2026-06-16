"""Claude gold-standard validation of WB-page content (rubric v1.2).

Reads each page's text and asks `claude -p` (no API; Claude Code on the server)
to apply the published methodology — does the page genuinely contain the
whistleblowing section, and which qualitative elements are present? Claude's
verdict is authoritative (gold) and used to CALIBRATE the Python analyzer
(src/wb_content.py): where Python and Claude disagree, Python is improved.

Targets default to the WhistleblowingPA disagreement set: pages that ARE
registered WB channels but whose Python outcome is not 'confermata' (likely
Python false negatives).

    python -m runners.claude.wb_validate [--limit N] [--batch 6] [--all]

Auth: source ~/.wb-discovery-env (CLAUDE_CODE_OAUTH_TOKEN) first.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import subprocess

from bs4 import BeautifulSoup

from src.config import USER_AGENT
from src.db import get_db, init_db, query_db
from src.fetcher import make_client

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("wb_validate")

DEFAULT_BATCH = 6
TEXT_CHARS = 5000
CLAUDE_TIMEOUT = 600

PROMPT_HEADER = """Sei un revisore esperto di whistleblowing nella PA italiana (D.Lgs. 24/2023, ANAC).
Per ciascuna pagina qui sotto (testo estratto), applica questa rubrica e stabilisci se è \
GENUINAMENTE la sezione/pagina di whistleblowing (segnalazione illeciti / anticorruzione), \
non un semplice riferimento testuale.

Elementi da rilevare (true/false) per ogni pagina:
- tema: spiega/cita il whistleblowing (cos'è, segnalazione illeciti, D.Lgs. 24/2023, tutela del segnalante)
- canale: c'è un canale di segnalazione REALE e utilizzabile (link a piattaforma es. GlobaLeaks/whistleblowing.it/segnalazioni.net, email/PEC dedicata, o form)
- canale_aperto: il canale è aperto anche all'ESTERNO (non solo personale interno) — per policy (cita collaboratori, fornitori, consulenti, ex dipendenti, terzi…) o per accessibilità (piattaforma pubblica senza credenziali dell'ente). NB: il canale ANAC è sempre disponibile e NON va considerato.
- rpct: indica il RPCT / soggetto gestore
- tutele: riservatezza, divieto di ritorsioni, protezione dell'identità
- presupposti: cosa si può segnalare (illeciti, violazioni, corruzione)
- distinzione: distingue segnalazione ordinaria vs whistleblowing
- anonimato: tratta la segnalazione anonima
- procedura: c'è la procedura di gestione delle segnalazioni (in pagina o PDF)
- privacy: informativa privacy / trattamento dati / GDPR
- legge: riferimenti di legge espliciti (D.Lgs. 24/2023, art. 54-bis, Dir. UE 2019/1937…)

Esito:
- "confermata" se tema E canale presenti
- "informativa" se tema e ≥2 elementi di rafforzamento ma SENZA canale reale
- "falso_positivo" se non è la sezione whistleblowing

Rispondi SOLO con un array JSON, un oggetto per pagina:
{"id": <id>, "outcome": "...", "elementi": {"tema":bool,...tutti gli 11...}, "note": "breve"}
Niente testo fuori dall'array.
"""


def _targets(all_pages: bool, limit: int | None):
    if all_pages:
        rows = query_db(
            "SELECT r.id wbpa_id, r.cod_amm, r.piat_public_link url "
            "FROM wbpa_registry r WHERE r.piat_public_link LIKE 'http%'"
        )
    else:
        # disagreement set: registered WB page but Python not 'confermata'
        rows = query_db(
            """
            SELECT r.id wbpa_id, r.cod_amm, r.piat_public_link url
            FROM wbpa_registry r
            JOIN wbpa_quality q ON q.wbpa_id = r.id
            WHERE r.piat_public_link LIKE 'http%' AND q.outcome <> 'confermata'
            """
        )
    items = [dict(r) for r in rows]
    return items[:limit] if limit else items


async def _fetch_text(client, url):
    try:
        r = await client.get(url, timeout=20.0)
        if r.status_code != 200 or not r.text:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        for t in soup(["script", "style", "noscript"]):
            t.decompose()
        return soup.get_text(" ", strip=True)[:TEXT_CHARS]
    except Exception:
        return None


def _call_claude(prompt: str) -> str:
    p = subprocess.run(
        ["claude", "-p"],
        input=prompt,
        text=True,
        capture_output=True,
        timeout=CLAUDE_TIMEOUT,
    )
    return p.stdout if p.returncode == 0 else ""


def _parse(out: str):
    s = out.strip()
    a, b = s.find("["), s.rfind("]")
    if a == -1 or b == -1:
        return []
    try:
        return json.loads(s[a : b + 1])
    except Exception:
        return []


def _save(item, verdict):
    el = verdict.get("elementi", {})
    with get_db() as db:
        db.execute(
            "INSERT INTO wb_validation (wbpa_id, cod_amm, url, source, outcome, flags, notes, created_at) "
            "VALUES (?, ?, ?, 'claude', ?, ?, ?, datetime('now'))",
            (
                item["wbpa_id"],
                item.get("cod_amm"),
                item["url"],
                verdict.get("outcome"),
                json.dumps(el, ensure_ascii=False),
                verdict.get("note"),
            ),
        )


async def run(all_pages, limit, batch):
    init_db()
    targets = _targets(all_pages, limit)
    logger.info("Validating %d pages with Claude (batch=%d)", len(targets), batch)
    saved = 0
    async with make_client(headers={"User-Agent": USER_AGENT}) as client:
        for i in range(0, len(targets), batch):
            chunk = targets[i : i + batch]
            parts = [PROMPT_HEADER]
            included = []
            for it in chunk:
                txt = await _fetch_text(client, it["url"])
                if not txt:
                    continue
                included.append(it)
                parts.append(
                    f"\n=== PAGINA id={it['wbpa_id']} ({it['url']}) ===\n{txt}"
                )
            if not included:
                continue
            out = _call_claude("\n".join(parts))
            verdicts = {v.get("id"): v for v in _parse(out)}
            for it in included:
                v = verdicts.get(it["wbpa_id"])
                if v:
                    _save(it, v)
                    saved += 1
            logger.info(
                "Batch %d: saved %d (total %d)", i // batch + 1, len(included), saved
            )
    logger.info("Done: %d Claude validations", saved)


def main():
    ap = argparse.ArgumentParser(
        description="Claude gold-standard WB content validation"
    )
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--batch", type=int, default=DEFAULT_BATCH)
    ap.add_argument(
        "--all",
        action="store_true",
        help="validate all WBPA pages, not just disagreements",
    )
    args = ap.parse_args()
    asyncio.run(run(args.all, args.limit, args.batch))


if __name__ == "__main__":
    main()
