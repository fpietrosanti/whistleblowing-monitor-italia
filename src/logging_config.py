"""Structured logging for the WB Monitor scraping pipeline.

Each PA scan gets its own log directory under data/logs/{scan_run_id}/{cod_amm}/
with per-PA and global log files, plus raw HTML and HTTP debug snapshots.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from src.config import BASE_DIR

LOGS_DIR = BASE_DIR / "data" / "logs"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"


def get_scan_log_dir(scan_run_id: str, cod_amm: str) -> Path:
    """Return the Path to the log directory for a specific PA scan."""
    return LOGS_DIR / scan_run_id / cod_amm


def setup_scan_logging(scan_run_id: str, cod_amm: str) -> logging.Logger:
    """Create a logger that writes to both a per-PA file and a global file.

    Returns the configured logger after creating the necessary directories.
    """
    pa_log_dir = get_scan_log_dir(scan_run_id, cod_amm)
    pa_log_dir.mkdir(parents=True, exist_ok=True)

    global_log_dir = LOGS_DIR / scan_run_id
    global_log_dir.mkdir(parents=True, exist_ok=True)

    logger_name = f"wbmonitor.{scan_run_id}.{cod_amm}"
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.DEBUG)

    # Avoid duplicate handlers if called multiple times for the same PA
    if logger.handlers:
        return logger

    formatter = logging.Formatter(LOG_FORMAT)

    # Per-PA log file
    pa_handler = logging.FileHandler(pa_log_dir / "scan.log", encoding="utf-8")
    pa_handler.setLevel(logging.DEBUG)
    pa_handler.setFormatter(formatter)
    logger.addHandler(pa_handler)

    # Global log file (shared across all PAs in the same scan run)
    global_handler = logging.FileHandler(
        global_log_dir / "scan_global.log", encoding="utf-8"
    )
    global_handler.setLevel(logging.DEBUG)
    global_handler.setFormatter(formatter)
    logger.addHandler(global_handler)

    return logger


def teardown_scan_logging(logger: logging.Logger) -> None:
    """Close and detach a per-PA logger's file handlers.

    Each PA gets its own uniquely-named logger with two FileHandlers; if they
    are never closed the open descriptors accumulate across thousands of PAs
    and exhaust the process FD limit ("Too many open files"). Call once the PA
    scan is done.
    """
    for handler in list(logger.handlers):
        try:
            handler.close()
        except Exception:
            pass
        logger.removeHandler(handler)


def save_raw_html(
    scan_run_id: str, cod_amm: str, filename: str, html_content: str
) -> Path:
    """Save a raw HTML snapshot to the PA's log directory.

    Returns the Path to the saved file.
    """
    pa_log_dir = get_scan_log_dir(scan_run_id, cod_amm)
    pa_log_dir.mkdir(parents=True, exist_ok=True)

    filepath = pa_log_dir / filename
    filepath.write_text(html_content, encoding="utf-8")
    return filepath


def save_http_debug(
    scan_run_id: str,
    cod_amm: str,
    filename: str,
    url: str,
    method: str,
    status_code: int,
    headers_dict: dict,
    response_time_ms: float,
    body_preview: str,
) -> Path:
    """Save HTTP request/response debug info as a JSON file.

    Returns the Path to the saved file.
    """
    pa_log_dir = get_scan_log_dir(scan_run_id, cod_amm)
    pa_log_dir.mkdir(parents=True, exist_ok=True)

    debug_data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "url": url,
        "method": method,
        "status_code": status_code,
        "headers": headers_dict,
        "response_time_ms": response_time_ms,
        "body_preview": body_preview,
    }

    filepath = pa_log_dir / filename
    filepath.write_text(
        json.dumps(debug_data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return filepath


def save_scan_summary(scan_run_id: str, cod_amm: str, summary_dict: dict) -> Path:
    """Save the final scan summary as scan_summary.json.

    Returns the Path to the saved file.
    """
    pa_log_dir = get_scan_log_dir(scan_run_id, cod_amm)
    pa_log_dir.mkdir(parents=True, exist_ok=True)

    filepath = pa_log_dir / "scan_summary.json"
    filepath.write_text(
        json.dumps(summary_dict, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return filepath
