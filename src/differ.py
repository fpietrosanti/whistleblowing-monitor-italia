"""Compute differences between consecutive scan runs.

Compares pa_scan records field-by-field across two runs and persists
every change into pa_scan_diff for historical tracking and reporting.
"""

import logging
from datetime import datetime, timezone

from src.db import get_db, query_db

logger = logging.getLogger("wbmonitor.differ")

COMPARED_FIELDS = [
    "site_reachable",
    "wb_section_found",
    "wb_digital_channel",
    "wb_channel_reachable",
    "wb_anonymous_allowed",
    "wb_strong_auth_required",
    "wb_software",
    "wb_policy_visible",
    "rpct_email",
    "wb_email",
]


def _find_previous_run(current_run_id: int) -> int | None:
    """Return the most recent completed scan_run_id before *current_run_id*."""
    row = query_db(
        """
        SELECT id FROM scan_run
        WHERE id < ? AND status = 'finished'
        ORDER BY id DESC
        LIMIT 1
        """,
        (current_run_id,),
        one=True,
    )
    return row["id"] if row else None


def _normalise(value) -> str | None:
    """Convert a value to its string form for comparison and storage."""
    if value is None:
        return None
    return str(value)


def compute_diff(
    current_run_id: int, previous_run_id: int | None = None
) -> dict:
    """Compare every PA between two scan runs and record changes.

    Parameters
    ----------
    current_run_id:
        The scan_run whose results are treated as *current*.
    previous_run_id:
        The scan_run to compare against.  When ``None`` the most recent
        completed run before *current_run_id* is used automatically.

    Returns
    -------
    dict with keys ``new_channels``, ``lost_channels``,
    ``software_changes``, ``total_changes``.
    """
    if previous_run_id is None:
        previous_run_id = _find_previous_run(current_run_id)
        if previous_run_id is None:
            logger.warning(
                "No previous scan run found for run %s – nothing to diff",
                current_run_id,
            )
            return {
                "new_channels": 0,
                "lost_channels": 0,
                "software_changes": 0,
                "total_changes": 0,
            }

    logger.info(
        "Computing diff: run %s vs run %s", current_run_id, previous_run_id
    )

    # Build lookup of previous-run results keyed by cod_amm
    prev_rows = query_db(
        "SELECT * FROM pa_scan WHERE scan_run_id = ?", (previous_run_id,)
    )
    prev_by_cod = {row["cod_amm"]: row for row in prev_rows}

    curr_rows = query_db(
        "SELECT * FROM pa_scan WHERE scan_run_id = ?", (current_run_id,)
    )

    now = datetime.now(timezone.utc).isoformat()
    new_channels = 0
    lost_channels = 0
    software_changes = 0
    total_changes = 0

    with get_db() as db:
        for idx, curr in enumerate(curr_rows, 1):
            cod_amm = curr["cod_amm"]
            prev = prev_by_cod.get(cod_amm)

            if prev is None:
                # PA not present in previous run – skip field-level diff
                continue

            for field in COMPARED_FIELDS:
                old_val = _normalise(prev[field])
                new_val = _normalise(curr[field])

                if old_val == new_val:
                    continue

                db.execute(
                    """
                    INSERT INTO pa_scan_diff
                        (scan_run_id, prev_scan_run_id, cod_amm,
                         field_name, old_value, new_value, detected_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        current_run_id,
                        previous_run_id,
                        cod_amm,
                        field,
                        old_val,
                        new_val,
                        now,
                    ),
                )

                total_changes += 1

                # Track high-level summary counters
                if field == "wb_digital_channel":
                    if new_val == "1" and old_val != "1":
                        new_channels += 1
                    elif old_val == "1" and new_val != "1":
                        lost_channels += 1

                if field == "wb_software":
                    software_changes += 1

            if idx % 5000 == 0:
                logger.info("Diff progress: %d / %d PAs", idx, len(curr_rows))

    logger.info(
        "Diff complete: %d total changes (%d new channels, %d lost, "
        "%d software changes)",
        total_changes,
        new_channels,
        lost_channels,
        software_changes,
    )

    return {
        "new_channels": new_channels,
        "lost_channels": lost_channels,
        "software_changes": software_changes,
        "total_changes": total_changes,
    }


def get_diff_summary(scan_run_id: int) -> dict:
    """Return a structured summary of changes recorded for *scan_run_id*.

    Returns
    -------
    dict mapping each ``field_name`` to a sub-dict with ``count``,
    ``examples`` (up to 5 sample changes), and the top-level key
    ``total_changes``.
    """
    rows = query_db(
        """
        SELECT field_name, COUNT(*) as cnt
        FROM pa_scan_diff
        WHERE scan_run_id = ?
        GROUP BY field_name
        ORDER BY cnt DESC
        """,
        (scan_run_id,),
    )

    total = 0
    by_field: dict[str, dict] = {}

    for row in rows:
        field = row["field_name"]
        count = row["cnt"]
        total += count

        examples = query_db(
            """
            SELECT cod_amm, old_value, new_value
            FROM pa_scan_diff
            WHERE scan_run_id = ? AND field_name = ?
            LIMIT 5
            """,
            (scan_run_id, field),
        )

        by_field[field] = {
            "count": count,
            "examples": [
                {
                    "cod_amm": ex["cod_amm"],
                    "old_value": ex["old_value"],
                    "new_value": ex["new_value"],
                }
                for ex in examples
            ],
        }

    return {
        "total_changes": total,
        "by_field": by_field,
    }
