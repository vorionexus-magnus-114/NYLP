"""
Matching module for Scholarship Finder.

Retrieves scholarships from SQLite, evaluates eligibility and ranks opportunities.

Every returned item carries ``record_type``:

* ``"scholarship"`` - a real database record. ``diagnostics`` lists any problems
  found while evaluating it (e.g. the eligibility check raised an error).
* ``"error"``       - a placeholder for a database row that could not be read at
  all. It has ``eligibility_status == "error"``, no scholarship data, and an
  ``error`` message. Error items are never ranked among real scholarships: they
  are appended after them and do not count towards ``limit``.
"""

import sqlite3
from datetime import date

from src.database import get_connection, init_db
from src.eligibility import _parse_date_detail, check_eligibility

_RESULT_FIELDS = (
    "id", "name", "provider", "description", "education_level", "location",
    "funding", "deadline", "status", "application_url", "source_url", "verified_at",
)
_RANK = {"eligible": 0, "potentially_eligible": 1, "insufficient_information": 2}


def _safe_limit(limit):
    """Clamp to 1..500; anything invalid falls back to 50."""
    if isinstance(limit, bool):
        return 50
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return 50
    if limit <= 0:
        return 50
    return min(limit, 500)


def _deadline_sort_value(deadline):
    """(0, ordinal) for parseable deadlines (earliest first); (1, 0) for missing/unparseable."""
    if deadline is None:
        return (1, 0)
    try:
        parsed, _ = _parse_date_detail(deadline)
    except Exception:
        return (1, 0)
    if parsed is None:
        return (1, 0)
    return (0, parsed.toordinal())


def _id_sort_value(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else 10 ** 12


def _error_item(row, exc):
    """Placeholder for an unreadable row: unmistakably not a scholarship."""
    row_id = None
    try:
        row_id = row["id"]
    except Exception:
        pass
    item = {field: None for field in _RESULT_FIELDS}
    item.update({
        "id": row_id,
        "record_type": "error",
        "eligibility_status": "error",
        "reasons": [],
        "missing_information": [],
        "diagnostics": [f"Could not read scholarship record id={row_id}: {exc}"],
        "error": f"{type(exc).__name__}: {exc}",
    })
    return item


def find_matches(profile, limit=50, today=None):
    """
    Find matching scholarships for a student profile.

    Args:
        profile (dict): student profile (None / non-mapping is treated as empty)
        limit (int): max number of real scholarships (1-500; invalid values -> 50)
        today (date, optional): override today's date (deterministic tests)

    Returns:
        list of dicts. Real scholarships first, ranked eligible >
        potentially_eligible > insufficient_information (ineligible excluded), then by
        earliest parseable deadline (missing/unparseable last), then id. Any
        ``record_type == "error"`` items follow at the end.

    Raises:
        RuntimeError: if the database cannot be initialised or queried. Database
        failures are never converted into an empty result.
    """
    if profile is None:
        profile = {}
    elif not isinstance(profile, dict):
        try:
            profile = dict(profile)
        except Exception:
            profile = {}
    limit = _safe_limit(limit)
    if today is None:
        today = date.today()

    try:
        init_db()
    except Exception as e:
        raise RuntimeError(f"Database initialization failed in find_matches: {e}") from e

    conn = None
    try:
        conn = get_connection()
        try:
            rows = conn.execute("SELECT * FROM scholarships").fetchall()
        except Exception as e:
            raise RuntimeError(f"Database query failed in find_matches: {e}") from e

        results, errors = [], []
        for row in rows:
            try:
                scholarship = dict(row)
            except Exception as e:
                errors.append(_error_item(row, e))
                continue

            diagnostics = []
            try:
                eligibility = check_eligibility(profile, scholarship, today=today)
            except Exception as e:
                eligibility = {
                    "eligibility_status": "insufficient_information",
                    "reasons": [],
                    "missing_information": ["eligibility evaluation failed"],
                }
                diagnostics.append(f"Eligibility evaluation raised {type(e).__name__}: {e}")

            status = eligibility.get("eligibility_status", "insufficient_information")
            if status == "ineligible":
                continue

            item = {field: scholarship.get(field) for field in _RESULT_FIELDS}
            item.update({
                "record_type": "scholarship",
                "eligibility_status": status,
                "reasons": eligibility.get("reasons", []),
                "missing_information": eligibility.get("missing_information", []),
                "diagnostics": diagnostics,
            })
            results.append(item)

        results.sort(key=lambda it: (
            _RANK.get(it["eligibility_status"], 2),
            _deadline_sort_value(it.get("deadline")),
            _id_sort_value(it.get("id")),
        ))
        return results[:limit] + errors

    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Unexpected error in find_matches: {e}") from e
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
