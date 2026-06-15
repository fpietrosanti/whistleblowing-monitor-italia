"""Claude gold-standard discovery driver (no API — Claude Code CLI on server).

Reads the dated homepage archive (links.json per PA) and asks `claude -p` to
identify, for each ente, the whistleblowing / segnalazione-illeciti /
anticorruzione page — AND the discovery method/signal it used, so those
methods can be back-ported into the Python heuristics (the virtuous loop).

Verdicts are stored in the gold_label table (authoritative), used to validate
and extend the Python discovery.

Resumable: PAs already in gold_label for the date are skipped. Bounded by
--max-batches / --limit so a 5h run-window can stop and resume next window.

Auth: relies on CLAUDE_CODE_OAUTH_TOKEN + CLAUDE_CONFIG_DIR in the environment
(source ~/.wb-discovery-env before running — done by run-window.sh).

Usage:
    python -m runners.claude.wb_discover [--date YYYY-MM-DD] [--batch 15]
                                         [--limit N] [--max-batches N]
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import subprocess
from datetime import date
from pathlib import Path

from src.config import DATA_DIR, DB_PATH
from src.db import init_db, save_gold_label

ARCHIVE_ROOT = DATA_DIR / "archive"
# Rich agentic mode: Claude reads each PA's full HTML + screenshot, so keep
# batches small (more files per session = more tokens/latency).
DEFAULT_BATCH = 5
CLAUDE_TIMEOUT = 600

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("wb_discover")

CANONICAL_METHODS = (
    "direct_link",  # link diretto a /whistleblowing o simile
    "menu_label",  # voce di menu (es. 'Whistleblowing', 'Segnalazione illeciti')
    "container_section",  # dentro una macro-sezione (Compliance, Governance, Etica)
    "amministrazione_trasparente",
    "societa_trasparente",  # portale Società Trasparente (SpA/Srl)
    "external_platform",  # canale su dominio esterno (segnalazioni.net, globaleaks)
    "footer_link",
    "anticorruzione_section",
    "inference",  # dedotto senza un link chiaro
)

PROMPT_HEADER = """Sei un revisore esperto di siti della Pubblica Amministrazione italiana e \
della normativa whistleblowing (D.Lgs. 24/2023, anticorruzione, RPCT).

Per ciascun ente elencato sotto hai accesso a una cartella d'archivio offline che \
contiene:
  - homepage.html  : l'HTML INTEGRALE della homepage (leggilo con Read)
  - screenshot.png : screenshot della homepage (guardalo: è un'immagine)
  - links.json     : i link estratti (zona, testo, URL)
  - meta.json      : denominazione, url, stato

USA gli strumenti Read per leggere DAVVERO homepage.html e per VEDERE screenshot.png, \
non limitarti a links.json. Se utile, puoi anche fare una WebFetch del sito o di una \
sotto-pagina candidata per confermare (es. la pagina Compliance/Trasparente/canale).

Obiettivo: stabilire se l'ente espone una pagina/sezione di **whistleblowing / \
segnalazione illeciti / anticorruzione**, ANCHE se il canale è su dominio esterno \
(segnalazioni.net, globaleaks, portaletrasparenza...) o annidato in una macro-sezione \
(Compliance, Governance, Etica, Amministrazione/Società Trasparente).

Per OGNI ente restituisci un oggetto JSON con ESATTAMENTE queste chiavi:
- "cod_amm": codice dell'ente
- "wb_found": true/false
- "wb_url": URL della pagina WB o del canale (o null)
- "entry_path": percorso dalla homepage (es. "Compliance > Whistleblowing"), o null
- "signal": il testo del link/indizio che lo rivela (o null)
- "channel_external": true se il canale è su un dominio diverso da quello dell'ente
- "method": il metodo di discovery usato; scegli tra: {methods}; se nessuno calza, \
proponine uno nuovo con una breve etichetta snake_case
- "confidence": 0.0-1.0
- "notes": breve nota (o null), incluse osservazioni utili a migliorare un detector \
automatico Python

Rispondi SOLO con un array JSON di questi oggetti, senza testo prima o dopo, senza \
markdown.
"""


def _pending(target_date: str, limit: int | None) -> list[tuple[str, Path]]:
    base = ARCHIVE_ROOT / target_date
    if not base.exists():
        logger.error("No archive for date %s at %s", target_date, base)
        return []
    conn = sqlite3.connect(str(DB_PATH))
    done = {
        r[0]
        for r in conn.execute(
            "SELECT cod_amm FROM gold_label WHERE archive_date = ?", (target_date,)
        )
    }
    conn.close()
    items: list[tuple[str, Path]] = []
    for d in sorted(base.iterdir()):
        if not d.is_dir() or d.name in done:
            continue
        if (d / "links.json").exists():
            items.append((d.name, d))
        if limit and len(items) >= limit:
            break
    return items


def _meta(d: Path) -> dict:
    try:
        return json.loads((d / "meta.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def _build_prompt(batch: list[tuple[str, Path]]) -> str:
    parts = [PROMPT_HEADER.format(methods=", ".join(CANONICAL_METHODS))]
    parts.append("\nEnti da analizzare (cartella d'archivio fra parentesi):")
    for cod, d in batch:
        meta = _meta(d)
        parts.append(
            f"- {cod} — {meta.get('denominazione', '')} "
            f"({meta.get('final_url') or meta.get('url', '')}) "
            f"-> cartella: {d.resolve()}"
        )
    return "\n".join(parts)


def _call_claude(prompt: str, dirs: list[Path]) -> str:
    cmd = ["claude", "-p"]
    for d in dirs:
        cmd += ["--add-dir", str(d.resolve())]
    proc = subprocess.run(
        cmd,
        input=prompt,
        text=True,
        capture_output=True,
        timeout=CLAUDE_TIMEOUT,
    )
    if proc.returncode != 0:
        logger.error("claude -p failed (rc=%s): %s", proc.returncode, proc.stderr[:300])
        return ""
    return proc.stdout


def _parse(out: str) -> list[dict]:
    s = out.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1] if "```" in s[3:] else s.strip("`")
        s = s.lstrip("json").strip()
    # find the JSON array
    start, end = s.find("["), s.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        data = json.loads(s[start : end + 1])
        return data if isinstance(data, list) else []
    except Exception as exc:
        logger.error("Could not parse Claude JSON: %s", exc)
        return []


def main() -> None:
    ap = argparse.ArgumentParser(description="Claude gold-standard WB discovery")
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--batch", type=int, default=DEFAULT_BATCH)
    ap.add_argument("--limit", type=int, default=None, help="max PAs this run")
    ap.add_argument("--max-batches", type=int, default=None)
    args = ap.parse_args()

    init_db()
    pend = _pending(args.date, args.limit)
    logger.info("Pending PAs for %s: %d (batch=%d)", args.date, len(pend), args.batch)
    if not pend:
        return

    saved = 0
    batches = 0
    for i in range(0, len(pend), args.batch):
        if args.max_batches and batches >= args.max_batches:
            logger.info("Reached --max-batches=%d, stopping", args.max_batches)
            break
        batch = pend[i : i + args.batch]
        batches += 1
        codes = {c for c, _ in batch}
        prompt = _build_prompt(batch)
        out = _call_claude(prompt, [d for _, d in batch])
        if not out:
            logger.warning("Empty response for batch %d, skipping", batches)
            continue
        verdicts = _parse(out)
        got = 0
        for v in verdicts:
            cod = v.get("cod_amm")
            if cod not in codes:
                continue
            method = v.get("method")
            v["methods"] = [method] if method else []
            save_gold_label(cod, args.date, v)
            got += 1
            saved += 1
        logger.info(
            "Batch %d: %d/%d verdicts saved (total %d)", batches, got, len(batch), saved
        )

    logger.info(
        "Done: %d gold verdicts for %s across %d batches", saved, args.date, batches
    )


if __name__ == "__main__":
    main()
