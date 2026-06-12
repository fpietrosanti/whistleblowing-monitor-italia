import sqlite3
from contextlib import contextmanager

from src.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS scan_run (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    total_pa        INTEGER,
    scanned_pa      INTEGER,
    errors          INTEGER DEFAULT 0,
    status          TEXT DEFAULT 'running'
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
    scan_duration_s         REAL,
    notes                   TEXT
);

CREATE INDEX IF NOT EXISTS idx_pa_scan_run ON pa_scan(scan_run_id);
CREATE INDEX IF NOT EXISTS idx_pa_scan_cod ON pa_scan(cod_amm);

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
"""


def init_db():
    with get_db() as db:
        db.executescript(SCHEMA)


@contextmanager
def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
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
