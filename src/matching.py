"""
Matching module for Scholarship Finder.

Retrieves scholarships from SQLite, converts the workbook's narrative criteria
into structured rules (``src.criteria``), evaluates eligibility and ranks
opportunities.

Every returned item carries ``record_type``:

* ``"scholarship"`` - a real database record. ``diagnostics`` lists any problems
  found while evaluating it (e.g. the eligibility check raised an error).
* ``"error"``       - a placeholder for a database row that could not be read at
  all. It has ``eligibility_status == "error"``, no scholarship data, and an
  ``error`` message. Error items are never ranked among real scholarships: they
  are appended after them and do not count towards ``limit``.
"""

from datetime import date

from src.criteria import enrich_record, summarize as summarize_criteria
from src.database import get_connection, init_db
from src.eligibility import _parse_date_detail, check_eligibility

_RESULT_FIELDS = (
    "id", "name", "provider", "description", "education_level", "location",
    "funding", "deadline", "status", "application_url", "source_url", "verified_at",
    "eligible_provinces", "eligible_domiciles", "eligible_cities",
    "minimum_marks", "minimum_cgpa", "grade_logic", "household_income_limit",
    "income_period", "minimum_age", "maximum_age", "nationality", "gender",
    "field_of_study", "study_destination", "external_id",
)

_RANK = {"eligible": 0, "potentially_eligible": 1, "insufficient_information": 2,
         "ineligible": 3}


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


def _future_deadline(deadline, today):
    try:
        parsed, _ = _parse_date_detail(deadline)
    except Exception:
        return False
    return parsed is not None and parsed >= today


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
        "manual_checks": [],
        "notes": [],
        "criteria": [],
        "diagnostics": [f"Could not read scholarship record id={row_id}: {exc}"],
        "error": f"{type(exc).__name__}: {exc}",
    })
    return item


def _verification_info(record):
    try:
        import json

        raw = record.get("raw_data")
        data = json.loads(raw) if isinstance(raw, str) and raw else (raw if isinstance(raw, dict) else {})
    except Exception:
        data = {}
    return {
        "status": data.get("verification_status"),
        "last_verified": data.get("last_verified_date") or record.get("verified_at"),
        "source_title": data.get("official_source_title"),
        "publisher": data.get("official_source_publisher"),
        "evidence": data.get("evidence_excerpt"),
        "cycle": data.get("cycle"),
        "application_route": data.get("application_route"),
    }


def _build_item(record, eligibility):
    item = {field: record.get(field) for field in _RESULT_FIELDS}
    item["record_type"] = "scholarship"
    item["eligibility_status"] = eligibility.get("eligibility_status", "insufficient_information")
    item["reasons"] = eligibility.get("reasons", [])
    item["missing_information"] = eligibility.get("missing_information", [])
    item["manual_checks"] = eligibility.get("manual_checks", [])
    item["notes"] = eligibility.get("notes", [])
    item["blocking_issues"] = eligibility.get("blocking_issues", [])
    item["supported_levels"] = record.get("supported_levels")
    item["criteria"] = summarize_criteria(record)
    item["verification"] = _verification_info(record)
    item["diagnostics"] = []
    return item


def evaluate_scholarships(profile, today=None, include_ineligible=False):
    """
    Evaluate every stored scholarship and return enriched result items.

    Unlike :func:`find_matches` this keeps ``ineligible`` records (when
    ``include_ineligible`` is true) so a UI can explain *why* something was
    filtered out. Error items are always appended at the end.

    Raises:
        RuntimeError: if the database cannot be initialised or queried.
    """
    if profile is None:
        profile = {}
    elif not isinstance(profile, dict):
        try:
            profile = dict(profile)
        except Exception:
            profile = {}
    if today is None:
        today = date.today()

    try:
        init_db()
    except Exception as e:
        raise RuntimeError(f"Database initialization failed in evaluate_scholarships: {e}") from e

    conn = None
    try:
        conn = get_connection()
        try:
            rows = conn.execute("SELECT * FROM scholarships").fetchall()
        except Exception as e:
            raise RuntimeError(f"Database query failed in evaluate_scholarships: {e}") from e

        results, errors = [], []
        for row in rows:
            try:
                record = enrich_record(dict(row))
            except Exception as e:
                errors.append(_error_item(row, e))
                continue

            diagnostics = []
            try:
                eligibility = check_eligibility(profile, record, today=today)
            except Exception as e:
                eligibility = {
                    "eligibility_status": "insufficient_information",
                    "reasons": [],
                    "missing_information": ["eligibility evaluation failed"],
                    "manual_checks": [],
                    "notes": [],
                    "blocking_issues": ["eligibility evaluation failed"],
                }
                diagnostics.append(f"Eligibility evaluation raised {type(e).__name__}: {e}")

            try:
                item = _build_item(record, eligibility)
            except Exception as e:
                errors.append(_error_item(row, e))
                continue
            item["diagnostics"] = diagnostics

            if item["eligibility_status"] == "ineligible" and not include_ineligible:
                continue
            results.append(item)

        results.sort(key=lambda it: (
            _RANK.get(it["eligibility_status"], 2),
            0 if _future_deadline(it.get("deadline"), today) else 1,
            _deadline_sort_value(it.get("deadline")),
            _id_sort_value(it.get("id")),
        ))
        return results, errors

    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Unexpected error in evaluate_scholarships: {e}") from e
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def find_matches(profile, limit=50, today=None, include_ineligible=False):
    """
    Find matching scholarships for a student profile.

    Args:
        profile: student profile (None / non-mapping is treated as empty)
        limit: max number of real scholarships (1-500; invalid values -> 50)
        today: override today's date (deterministic tests)
        include_ineligible: keep records that the student cannot apply for
            (default False). They are ranked last.

    Returns:
        list of dicts. Real scholarships first, ranked eligible >
        potentially_eligible > insufficient_information (and ``ineligible`` last
        when requested), then by earliest parseable deadline (missing/unparseable
        last), then id. Any ``record_type == "error"`` items follow at the end.

    Raises:
        RuntimeError: if the database cannot be initialised or queried.
    """
    limit = _safe_limit(limit)
    results, errors = evaluate_scholarships(
        profile, today=today, include_ineligible=include_ineligible
    )
    return results[:limit] + errors


def match_summary(items):
    """Counts per eligibility status, used by the dashboard."""
    summary = {"eligible": 0, "potentially_eligible": 0,
               "insufficient_information": 0, "ineligible": 0, "error": 0}
    for item in items:
        status = item.get("eligibility_status")
        if status in summary:
            summary[status] += 1
    summary["total"] = sum(v for k, v in summary.items() if k != "total")
    return summary
