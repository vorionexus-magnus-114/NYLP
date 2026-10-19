"""
Data-quality and source-verification audit for the scholarship database.

The workbook is a curated snapshot: every row carries a verification status,
a last-verified date, an official source URL and an evidence excerpt. This
module cross-checks those claims against the structured fields, so the UI can
show *where the data itself is uncertain* instead of presenting it as fact.
"""

from __future__ import annotations

from datetime import date, datetime

from src.criteria import derive, enrich_record


def _parse_date(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def _issue(record, severity, check, detail):
    return {
        "id": record.get("id"),
        "name": record.get("name"),
        "provider": record.get("provider"),
        "severity": severity,
        "check": check,
        "detail": detail,
    }


def audit_records(records, today=None):
    """
    Audit stored records for internal inconsistencies.

    Args:
        records: iterable of scholarship dicts (database rows)
        today: override today's date (deterministic tests)

    Returns:
        dict with ``summary`` (counts) and ``issues`` (list of dicts).
    """
    if today is None:
        today = date.today()

    issues = []
    checks = {}

    def add(record, severity, check, detail):
        issues.append(_issue(record, severity, check, detail))
        checks[check] = checks.get(check, 0) + 1

    total = 0
    seen_external = {}
    for raw_record in records:
        total += 1
        record = enrich_record(dict(raw_record))
        raw = derive(record.get("raw_data") or "")

        name = (record.get("name") or "").strip()
        source_url = (record.get("source_url") or "").strip()
        app_url = (record.get("application_url") or "").strip()
        status = (record.get("status") or "").strip().lower()
        deadline = _parse_date(record.get("deadline"))

        ext = (record.get("external_id") or "").strip()
        if ext:
            seen_external.setdefault(ext, []).append(record)

        if not name:
            add(record, "error", "missing_name", "Record has no scholarship name")

        if not source_url:
            add(record, "warning", "missing_source",
                "No official source URL; the record cannot be re-verified")
        elif not (source_url.startswith("http://") or source_url.startswith("https://")):
            add(record, "error", "invalid_source_url", f"Source URL is not HTTP(S): {source_url}")
        if app_url and not (app_url.startswith("http://") or app_url.startswith("https://")):
            add(record, "warning", "invalid_application_url",
                f"Application URL is not HTTP(S): {app_url}")

        verified = _parse_date(record.get("verified_at"))
        if verified is None:
            add(record, "warning", "missing_verification_date",
                "No last-verified date; provenance is unknown")
        else:
            age_days = (today - verified).days
            if age_days > 365:
                add(record, "warning", "stale_verification",
                    f"Last verified {verified.isoformat()} ({age_days} days ago)")

        if deadline is None:
            add(record, "info", "no_deadline", "No confirmed deadline / application window")
        elif deadline < today and status in ("open", "rolling", "ongoing", "active"):
            add(record, "error", "expired_but_open",
                f"Marked '{status}' but the deadline passed on {deadline.isoformat()}")
        elif deadline > today and status in ("closed", "expired"):
            add(record, "warning", "closed_with_future_deadline",
                f"Marked '{status}' but the deadline is {deadline.isoformat()}")

        if status in ("open", "rolling", "ongoing", "active") and deadline is None:
            add(record, "warning", "open_without_deadline",
                "Marked open but no deadline is recorded")

        if status in ("not_announced", "unknown", "upcoming"):
            add(record, "info", "unannounced_cycle",
                f"Application cycle is '{status or 'unknown'}'")

        # Narrative criteria that could not be converted are already surfaced
        # per record; flag the ones where a numeric threshold was expected.
        raw_data = record.get("raw_data")
        try:
            import json

            source = json.loads(raw_data) if isinstance(raw_data, str) and raw_data else raw_data
            source = source if isinstance(source, dict) else {}
        except Exception:
            source = {}

        verification_status = str(source.get("verification_status") or "").strip().lower()
        if verification_status and verification_status not in ("confirmed", "verified"):
            add(record, "warning", "not_fully_confirmed",
                f"Source review marked this record '{verification_status}', not 'confirmed'")

        if source.get("minimum_grade") and record.get("minimum_marks") is None \
                and record.get("minimum_cgpa") is None:
            add(record, "warning", "unparsed_grade",
                f"Grade rule could not be converted: {source.get('minimum_grade')}")

        if source.get("income_threshold") and record.get("household_income_limit") is None:
            add(record, "warning", "unparsed_income",
                f"Income rule could not be converted: {source.get('income_threshold')}")

        province_region = str(source.get("province_region") or "")
        domicile_rule = str(source.get("domicile_rule") or "")
        if province_region.lower().startswith("all pakistan") and domicile_rule:
            add(record, "info", "nationwide_with_domicile",
                f"Nationwide scope but a domicile rule is recorded: {domicile_rule}")

        if not raw["criteria"] and not raw["manual_checks"]:
            add(record, "info", "no_structured_criteria",
                "No eligibility criteria could be derived from this record")

    for ext, dupes in seen_external.items():
        if len(dupes) > 1:
            for record in dupes[1:]:
                add(record, "error", "duplicate_external_id",
                    f"external_id '{ext}' appears {len(dupes)} times")

    severity_counts = {"error": 0, "warning": 0, "info": 0}
    for issue in issues:
        severity_counts[issue["severity"]] = severity_counts.get(issue["severity"], 0) + 1

    return {
        "summary": {
            "total_records": total,
            "issues": len(issues),
            "severity": severity_counts,
            "checks": checks,
        },
        "issues": issues,
    }


def verification_overview(records):
    """Per-record provenance summary used by the data-health table."""
    rows = []
    for record in records:
        raw = derive(record.get("raw_data") or "")
        try:
            import json

            source = json.loads(record.get("raw_data")) if isinstance(record.get("raw_data"), str) \
                else (record.get("raw_data") or {})
        except Exception:
            source = {}
        rows.append({
            "id": record.get("id"),
            "name": record.get("name"),
            "provider": record.get("provider"),
            "verification_status": source.get("verification_status"),
            "last_verified": source.get("last_verified_date") or record.get("verified_at"),
            "source_publisher": source.get("official_source_publisher"),
            "source_url": record.get("source_url"),
            "criteria_found": len(raw["criteria"]),
            "manual_checks": len(raw["manual_checks"]),
        })
    return rows
