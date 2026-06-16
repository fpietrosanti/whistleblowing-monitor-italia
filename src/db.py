import sqlite3
import time
from contextlib import contextmanager

from src.config import DB_PATH

# Retry connect on transient failures ("unable to open database file" under
# file-descriptor pressure during high-concurrency scans).
_CONNECT_RETRIES = 6
_CONNECT_BACKOFF = 0.25

SCHEMA = """
CREATE TABLE IF NOT EXISTS scan_run (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    total_pa        INTEGER,
    scanned_pa      INTEGER,
    errors          INTEGER DEFAULT 0,
    status          TEXT DEFAULT 'running',
    mode            TEXT DEFAULT 'browser',
    egress          TEXT DEFAULT 'datacenter',  -- datacenter | residential | vpn
    egress_ip       TEXT
);

CREATE TABLE IF NOT EXISTS pa (
    cod_amm         TEXT PRIMARY KEY,
    denominazione   TEXT NOT NULL,
    sito_web        TEXT,
    categoria       TEXT,
    regione         TEXT,
    provincia       TEXT,
    comune          TEXT,
    tipologia       TEXT,
    cf              TEXT,
    indirizzo       TEXT,
    cap             TEXT,
    mail_pec        TEXT,
    mail2           TEXT,
    resp_nome       TEXT,
    resp_cognome    TEXT,
    resp_titolo     TEXT,
    acronimo        TEXT,
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS pa_scan (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_run_id             INTEGER NOT NULL REFERENCES scan_run(id),
    cod_amm                 TEXT NOT NULL REFERENCES pa(cod_amm),
    scanned_at              TEXT NOT NULL,
    site_reachable          INTEGER,
    site_http_status        INTEGER,
    site_error              TEXT,
    render_mode             TEXT,
    wb_section_found        INTEGER,
    wb_section_url          TEXT,
    wb_digital_channel      INTEGER,
    wb_channel_url          TEXT,
    wb_channel_reachable    INTEGER,
    wb_channel_type         TEXT,
    wb_requires_auth        INTEGER,
    wb_auth_type            TEXT,
    wb_anonymous_allowed    INTEGER,
    wb_strong_auth_required INTEGER,
    wb_software             TEXT,
    wb_software_version     TEXT,
    wb_software_confidence  REAL,
    rpct_email              TEXT,
    rpct_phone              TEXT,
    rpct_name               TEXT,
    wb_email                TEXT,
    wb_phone                TEXT,
    wb_policy_visible       INTEGER,
    wb_policy_url           TEXT,
    wb_policy_pdf_path      TEXT,
    wb_policy_pdf_hash      TEXT,
    discovery_method        TEXT,
    wb_click_depth          INTEGER,
    egress                  TEXT,
    scan_duration_s         REAL,
    notes                   TEXT
);

CREATE INDEX IF NOT EXISTS idx_pa_scan_run ON pa_scan(scan_run_id);
CREATE INDEX IF NOT EXISTS idx_pa_scan_cod ON pa_scan(cod_amm);
CREATE INDEX IF NOT EXISTS idx_pa_scan_run_cod ON pa_scan(scan_run_id, cod_amm);
CREATE INDEX IF NOT EXISTS idx_pa_regione ON pa(regione);
CREATE INDEX IF NOT EXISTS idx_pa_categoria ON pa(categoria);
CREATE INDEX IF NOT EXISTS idx_pa_denominazione ON pa(denominazione);

CREATE TABLE IF NOT EXISTS pa_scan_diff (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_run_id      INTEGER NOT NULL REFERENCES scan_run(id),
    prev_scan_run_id INTEGER NOT NULL REFERENCES scan_run(id),
    cod_amm          TEXT NOT NULL REFERENCES pa(cod_amm),
    field_name       TEXT NOT NULL,
    old_value        TEXT,
    new_value        TEXT,
    detected_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pa_scan_diff_run ON pa_scan_diff(scan_run_id);

CREATE TABLE IF NOT EXISTS rpct_anac (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    anac_id             INTEGER,
    cf_ente             TEXT NOT NULL,
    denominazione_ente  TEXT,
    cod_amm             TEXT REFERENCES pa(cod_amm),
    rpct_nome           TEXT,
    rpct_cognome        TEXT,
    rpct_nome_completo  TEXT,
    link_atto_nomina    TEXT,
    data_nomina         TEXT
);

CREATE INDEX IF NOT EXISTS idx_rpct_anac_cf ON rpct_anac(cf_ente);
CREATE INDEX IF NOT EXISTS idx_rpct_anac_cod ON rpct_anac(cod_amm);

CREATE TABLE IF NOT EXISTS scan_error_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_run_id     INTEGER NOT NULL REFERENCES scan_run(id),
    cod_amm         TEXT,
    phase           TEXT,
    error_type      TEXT,
    error_message   TEXT,
    url             TEXT,
    occurred_at     TEXT NOT NULL
);

-- Per-attempt step ledger: one row per method ATTEMPTED within each step,
-- for every PA × scan. Tracks everything we manage or fail to do, per ente,
-- per phase (discovery | analysis), per step, with which method and why.
CREATE TABLE IF NOT EXISTS pa_scan_step (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_run_id     INTEGER NOT NULL REFERENCES scan_run(id),
    cod_amm         TEXT NOT NULL,
    phase           TEXT NOT NULL,   -- discovery | analysis
    step            TEXT NOT NULL,   -- site_fetch, section_discovery, channel_detect, ...
    seq             INTEGER,         -- attempt order within the step
    method          TEXT,            -- method/strategy attempted
    status          TEXT NOT NULL,   -- ok | fail | skip | partial
    reason          TEXT,            -- failure reason when status != ok
    detail          TEXT,
    occurred_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pa_scan_step_run ON pa_scan_step(scan_run_id);
CREATE INDEX IF NOT EXISTS idx_pa_scan_step_cod ON pa_scan_step(scan_run_id, cod_amm);
CREATE INDEX IF NOT EXISTS idx_pa_scan_step_step ON pa_scan_step(scan_run_id, step);

-- Claude gold-standard discovery verdicts (one per PA per archive date).
-- Authoritative result used to (a) validate the Python discovery and
-- (b) extend Python with the discovery methods Claude identifies.
CREATE TABLE IF NOT EXISTS gold_label (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    cod_amm           TEXT NOT NULL,
    archive_date      TEXT,
    source            TEXT DEFAULT 'claude',
    wb_found          INTEGER,
    wb_url            TEXT,
    entry_path        TEXT,          -- homepage -> ... -> WB page
    signal            TEXT,          -- link text / clue that revealed it
    channel_external  INTEGER,
    methods           TEXT,          -- JSON: discovery methods Claude used/suggests
    confidence        REAL,
    notes             TEXT,
    created_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_gold_label_cod ON gold_label(cod_amm);
CREATE INDEX IF NOT EXISTS idx_gold_label_date ON gold_label(archive_date);

-- Retry queue for transient connection failures (ConnectTimeout/ReadTimeout/
-- ConnectError...). Rescheduled with exponential backoff and re-attempted by
-- tools/retry_due.py (hourly cron). status: pending | recovered | exhausted.
CREATE TABLE IF NOT EXISTS retry_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_run_id     INTEGER NOT NULL,
    cod_amm         TEXT NOT NULL,
    error_type      TEXT,
    attempts        INTEGER DEFAULT 0,
    next_retry_at   TEXT,
    last_attempt_at TEXT,
    status          TEXT DEFAULT 'pending',
    created_at      TEXT NOT NULL,
    UNIQUE(scan_run_id, cod_amm)
);

CREATE INDEX IF NOT EXISTS idx_retry_queue_due ON retry_queue(status, next_retry_at);

-- Append-only per-attempt retry history: one row per retry attempt with its
-- outcome (recovered/failed), so the number of retries and whether each
-- succeeded or failed is fully recorded and auditable.
CREATE TABLE IF NOT EXISTS retry_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_run_id     INTEGER NOT NULL,
    cod_amm         TEXT NOT NULL,
    attempt         INTEGER NOT NULL,
    error_type      TEXT,
    outcome         TEXT NOT NULL,   -- recovered | failed
    http_status     INTEGER,
    new_error       TEXT,
    egress          TEXT,            -- datacenter | residential | vpn
    attempted_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_retry_log_cod ON retry_log(cod_amm);
CREATE INDEX IF NOT EXISTS idx_retry_log_run ON retry_log(scan_run_id);

-- WhistleblowingPA registry (whistleblowing.it / GlobaLeaks-hosted channels).
-- Ground truth: piat_link = the platform channel, piat_public_link = the WB
-- page on the entity's own site (the discovery target). Reconciled to pa by CF.
CREATE TABLE IF NOT EXISTS wbpa_registry (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    denominazione     TEXT,
    versione          TEXT,
    categoria         TEXT,
    regione           TEXT,
    provincia         TEXT,
    piat_stato        TEXT,
    piat_link         TEXT,
    piat_regist_data  TEXT,
    piat_disab_data   TEXT,
    piat_canc_data    TEXT,
    piat_public       TEXT,
    piat_public_link  TEXT,
    cf                TEXT,
    cod_amm           TEXT,
    note              TEXT,
    updated_at        TEXT
);

CREATE INDEX IF NOT EXISTS idx_wbpa_cf ON wbpa_registry(cf);
CREATE INDEX IF NOT EXISTS idx_wbpa_cod ON wbpa_registry(cod_amm);
CREATE INDEX IF NOT EXISTS idx_wbpa_stato ON wbpa_registry(piat_stato);

-- Live status of each WhistleblowingPA link (active vs error + content).
CREATE TABLE IF NOT EXISTS wbpa_status (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    wbpa_id       INTEGER NOT NULL REFERENCES wbpa_registry(id),
    link_type     TEXT,        -- piat | public
    url           TEXT,
    http_status   INTEGER,
    active        INTEGER,     -- 1 if reachable HTTP 200
    is_wb_content INTEGER,     -- content validation (later)
    error         TEXT,
    egress        TEXT,
    checked_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_wbpa_status_id ON wbpa_status(wbpa_id);

-- Qualitative content analysis of WhistleblowingPA pages (rubric v1.0):
-- which required/supporting elements are present, plus the outcome.
CREATE TABLE IF NOT EXISTS wbpa_quality (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    wbpa_id          INTEGER NOT NULL REFERENCES wbpa_registry(id),
    url              TEXT,
    has_tema         INTEGER,
    has_canale       INTEGER,
    has_rpct         INTEGER,
    has_anac         INTEGER,
    has_tutele       INTEGER,
    has_presupposti  INTEGER,
    has_distinzione  INTEGER,
    has_anonimato    INTEGER,
    has_procedura    INTEGER,
    has_privacy      INTEGER,
    has_legge        INTEGER,
    score            INTEGER,
    outcome          TEXT,   -- confermata | informativa | falso_positivo
    checked_at       TEXT
);

CREATE INDEX IF NOT EXISTS idx_wbpa_quality_id ON wbpa_quality(wbpa_id);
CREATE INDEX IF NOT EXISTS idx_wbpa_quality_outcome ON wbpa_quality(outcome);
"""


def init_db():
    with get_db() as db:
        db.executescript(SCHEMA)


def _connect() -> sqlite3.Connection:
    """Open a SQLite connection, retrying transient open failures."""
    last_exc = None
    for attempt in range(_CONNECT_RETRIES):
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA foreign_keys=ON")
            return conn
        except sqlite3.OperationalError as exc:
            last_exc = exc
            time.sleep(_CONNECT_BACKOFF * (attempt + 1))
    raise last_exc


@contextmanager
def get_db():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def query_db(sql, params=(), one=False):
    with get_db() as db:
        cur = db.execute(sql, params)
        rows = cur.fetchall()
        return rows[0] if one and rows else rows if not one else None


def save_pa_steps(scan_run_id, cod_amm, steps):
    """Bulk-insert the per-attempt step ledger for one PA in a single tx.

    steps: list of dicts with keys phase, step, method, status, reason, detail.
    `seq` is assigned automatically from list order.
    """
    if not steps:
        return
    rows = [
        (
            scan_run_id,
            cod_amm,
            s.get("phase"),
            s.get("step"),
            i,
            s.get("method"),
            s.get("status"),
            s.get("reason"),
            s.get("detail"),
        )
        for i, s in enumerate(steps)
    ]
    with get_db() as db:
        db.executemany(
            """INSERT INTO pa_scan_step
                   (scan_run_id, cod_amm, phase, step, seq, method, status,
                    reason, detail, occurred_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            rows,
        )


def save_gold_label(cod_amm, archive_date, verdict):
    """Upsert one Claude gold-standard verdict for a PA.

    verdict: dict with keys wb_found, wb_url, entry_path, signal,
    channel_external, methods (list|str), confidence, notes.
    """
    import json as _json

    methods = verdict.get("methods")
    if isinstance(methods, (list, dict)):
        methods = _json.dumps(methods, ensure_ascii=False)
    with get_db() as db:
        db.execute(
            "DELETE FROM gold_label WHERE cod_amm = ? AND archive_date = ?",
            (cod_amm, archive_date),
        )
        db.execute(
            """INSERT INTO gold_label
                   (cod_amm, archive_date, source, wb_found, wb_url, entry_path,
                    signal, channel_external, methods, confidence, notes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (
                cod_amm,
                archive_date,
                verdict.get("source", "claude"),
                1 if verdict.get("wb_found") else 0,
                verdict.get("wb_url"),
                verdict.get("entry_path"),
                verdict.get("signal"),
                1 if verdict.get("channel_external") else 0,
                methods,
                verdict.get("confidence"),
                verdict.get("notes"),
            ),
        )
