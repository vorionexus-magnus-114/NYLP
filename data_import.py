"""
Data import module for Scholarship Finder.

Imports scholarship records from Excel workbooks into SQLite.

Key behaviours
* The header row is detected (workbooks often have title rows above it).
* Ambiguous headers (Country, State, Marks, CGPA, Income, URL, Link, City,
  Province, ...) are NEVER mapped silently: they stay unmapped, their values are
  preserved in ``raw_data`` and a warning explains the candidates. Callers can
  resolve them explicitly with ``column_overrides``.
* Deduplication never merges records on missing data (see ``_find_duplicate``).
* Each sheet is imported in one transaction.
"""

import hashlib
import json
import re
import sqlite3
from pathlib import Path

import pandas as pd

from src.database import get_connection, init_db
from src.eligibility import _parse_date_detail


# Canonical database fields (excluding id, raw_data)
CANONICAL_FIELDS = [
    "name", "provider", "description", "education_level", "qualification",
    "field_of_study", "minimum_age", "maximum_age", "nationality",
    "eligible_provinces", "eligible_cities", "eligible_domiciles", "minimum_marks",
    "minimum_cgpa", "household_income_limit", "funding", "study_destination",
    "location", "deadline", "status", "application_url", "source_url", "verified_at",
    "external_id",
]

# Mapping of normalized column name -> canonical field.
# Only headers whose meaning is explicit appear here. Ambiguous headers are listed
# in AMBIGUOUS_HEADERS below and are intentionally absent.
COLUMN_MAP = {
    # scholarship name variations
    "scholarship name": "name",
    "scholarship title": "name",
    "scholarship": "name",
    "name": "name",
    "title": "name",
    "scheme": "name",
    "program": "name",
    "program name": "name",
    "scheme name": "name",
    # provider
    "provider": "provider",
    "organization": "provider",
    "organisation": "provider",
    "sponsor": "provider",
    "offered by": "provider",
    "awarded by": "provider",
    "institution": "provider",
    "university": "provider",
    "donor": "provider",
    # description
    "description": "description",
    "details": "description",
    "about": "description",
    "info": "description",
    "information": "description",
    "summary": "description",
    "overview": "description",
    # education_level - specific variants
    "education level": "education_level",
    "education_level": "education_level",
    "level": "education_level",
    "education": "education_level",
    "study level": "education_level",
    "degree level": "education_level",
    "degree": "education_level",
    "study_level": "education_level",
    # qualification
    "qualification": "qualification",
    "required qualification": "qualification",
    "eligibility qualification": "qualification",
    "qualifications": "qualification",
    "required_qualification": "qualification",
    # field_of_study
    "field of study": "field_of_study",
    "field_of_study": "field_of_study",
    "field": "field_of_study",
    "discipline": "field_of_study",
    "subject": "field_of_study",
    "course": "field_of_study",
    "major": "field_of_study",
    "fields": "field_of_study",
    "area of study": "field_of_study",
    # minimum_age
    "minimum age": "minimum_age",
    "minimum_age": "minimum_age",
    "min age": "minimum_age",
    "min_age": "minimum_age",
    "age from": "minimum_age",
    "lower age": "minimum_age",
    "age lower limit": "minimum_age",
    "age min": "minimum_age",
    # maximum_age
    "maximum age": "maximum_age",
    "maximum_age": "maximum_age",
    "max age": "maximum_age",
    "max_age": "maximum_age",
    "age to": "maximum_age",
    "upper age": "maximum_age",
    "age upper limit": "maximum_age",
    "age max": "maximum_age",
    # nationality - generic "country" kept but marked ambiguous
    "nationality": "nationality",
    "citizenship": "nationality",
    "eligible nationality": "nationality",
    "eligible_nationality": "nationality",
    "nationalities": "nationality",
    # eligible_provinces
    "eligible provinces": "eligible_provinces",
    "eligible_provinces": "eligible_provinces",
    "provinces": "eligible_provinces",
    "eligible province": "eligible_provinces",
    "eligible_province": "eligible_provinces",
    # eligible_cities
    "eligible cities": "eligible_cities",
    "eligible_cities": "eligible_cities",
    "cities": "eligible_cities",
    "eligible city": "eligible_cities",
    "eligible_city": "eligible_cities",
    # eligible_domiciles
    "eligible domiciles": "eligible_domiciles",
    "eligible_domiciles": "eligible_domiciles",
    "domicile": "eligible_domiciles",
    "domiciles": "eligible_domiciles",
    "eligible domicile": "eligible_domiciles",
    "eligible_domicile": "eligible_domiciles",
    # minimum_marks - generic kept but ambiguous
    "minimum marks": "minimum_marks",
    "minimum_marks": "minimum_marks",
    "min marks": "minimum_marks",
    "min_marks": "minimum_marks",
    "min percentage": "minimum_marks",
    "minimum percentage": "minimum_marks",
    "required marks": "minimum_marks",
    "required_marks": "minimum_marks",
    "minimum marks required": "minimum_marks",
    # minimum_cgpa
    "minimum cgpa": "minimum_cgpa",
    "minimum_cgpa": "minimum_cgpa",
    "min cgpa": "minimum_cgpa",
    "min_cgpa": "minimum_cgpa",
    "minimum gpa": "minimum_cgpa",
    "minimum_gpa": "minimum_cgpa",
    # household_income_limit - generic "income" kept but ambiguous
    "household income limit": "household_income_limit",
    "household_income_limit": "household_income_limit",
    "income limit": "household_income_limit",
    "income_limit": "household_income_limit",
    "family income": "household_income_limit",
    "household income": "household_income_limit",
    "household_income": "household_income_limit",
    "family income limit": "household_income_limit",
    # funding
    "funding": "funding",
    "amount": "funding",
    "value": "funding",
    "award": "funding",
    "benefit": "funding",
    "financial support": "funding",
    "financial_support": "funding",
    "grant": "funding",
    "scholarship amount": "funding",
    "scholarship_amount": "funding",
    # study_destination
    "study destination": "study_destination",
    "study_destination": "study_destination",
    "destination": "study_destination",
    "study abroad": "study_destination",
    "country of study": "study_destination",
    "country_of_study": "study_destination",
    # location
    "location": "location",
    "place": "location",
    "where": "location",
    "study location": "location",
    "study_location": "location",
    "venue": "location",
    # deadline
    "deadline": "deadline",
    "due date": "deadline",
    "due_date": "deadline",
    "closing date": "deadline",
    "closing_date": "deadline",
    "last date": "deadline",
    "last_date": "deadline",
    "apply by": "deadline",
    "apply_by": "deadline",
    "expiry": "deadline",
    "expiry date": "deadline",
    "expiry_date": "deadline",
    "end date": "deadline",
    "end_date": "deadline",
    # status
    "status": "status",
    "availability": "status",
    "open/closed": "status",
    # application_url - generic link/url/website kept but ambiguous
    "application url": "application_url",
    "application_url": "application_url",
    "apply link": "application_url",
    "application link": "application_url",
    "apply_url": "application_url",
    "application form": "application_url",
    # source_url
    "source url": "source_url",
    "source_url": "source_url",
    "source link": "source_url",
    "source_link": "source_url",
    # verified_at
    "verified at": "verified_at",
    "verified_at": "verified_at",
    "verified": "verified_at",
    "verification date": "verified_at",
    "verification_date": "verified_at",
    "checked at": "verified_at",
    "checked_at": "verified_at",
    # --- explicit headers from the 60-field master workbook (unambiguous only) ---
    "opportunity name": "name",
    "opportunity id": "external_id",
    "external id": "external_id",
    "fields of study": "field_of_study",
    "destination country": "study_destination",
    "deadline date": "deadline",
    "application status": "status",
    "official source url": "source_url",
    "last verified date": "verified_at",
    "funding amount": "funding",
}


# Headers that are NOT mapped because their meaning is not clear from the header
# alone. value = the plausible interpretations, used in the warning text.
AMBIGUOUS_HEADERS = {
    "country": ["nationality", "study_destination"],
    "state": ["eligible_provinces", "status"],
    "marks": ["minimum_marks (requirement)", "the student's achieved marks"],
    "percentage": ["minimum_marks (requirement)", "the student's achieved marks"],
    "%": ["minimum_marks (requirement)", "the student's achieved marks"],
    "cgpa": ["minimum_cgpa (requirement)", "the student's achieved CGPA"],
    "gpa": ["minimum_cgpa (requirement)", "the student's achieved CGPA"],
    "minimum grade": ["minimum_marks (percentage)", "minimum_cgpa (grade-point)"],
    "income": ["household_income_limit", "the applicant's own income"],
    "income threshold": ["household_income_limit (period and currency unclear)"],
    "url": ["application_url", "source_url"],
    "link": ["application_url", "source_url"],
    "website": ["application_url", "source_url"],
    "source": ["source_url", "a source name/citation"],
    "reference": ["source_url", "a citation"],
    "origin": ["source_url", "a country/source of origin"],
    "city": ["eligible_cities", "the provider's or institution's city"],
    "province": ["eligible_provinces", "the provider's or institution's province"],
    "province region": ["eligible_provinces", "location", "a free-text region"],
    "district": ["eligible_domiciles", "the institution's district"],
    "region": ["eligible_provinces", "location"],
}

# Mapped, but generic enough that we still ask the user to double-check.
SOFT_GENERIC_HEADERS = {
    "scholarship", "level", "education", "degree", "field", "amount", "value",
    "award", "benefit", "grant", "destination", "location", "place", "where", "venue",
}

# Sheets to skip (exact normalized names)
SKIP_SHEET_KEYWORDS = {
    "table of contents", "toc", "contents", "index", "cover", "instructions",
    "readme", "overview", "regional index", "summary",
}
# Substrings that mark metadata sheets, never scholarship rows
SKIP_SHEET_SUBSTRINGS = (
    "table of contents", "start here", "dictionary", "exclusion", "verify queue",
    "region index",
)
SKIP_SHEET_SHORT_SUBSTRINGS = ("toc", "contents", "index", "summary")

HEADER_SCAN_ROWS = 15


def _normalize_col(name):
    """Normalize column name for mapping."""
    if not isinstance(name, str):
        name = str(name)
    n = name.strip().lower()
    n = n.replace("_", " ").replace("-", " ")
    return " ".join(n.split())


def _is_toc_sheet(sheet_name):
    """True for TOC / index / summary / metadata sheets that hold no scholarship rows."""
    norm = _normalize_col(sheet_name)
    if norm in SKIP_SHEET_KEYWORDS:
        return True
    if any(s in norm for s in SKIP_SHEET_SUBSTRINGS):
        return True
    if len(norm) < 30 and any(re.search(rf"\b{kw}\b", norm) for kw in SKIP_SHEET_SHORT_SUBSTRINGS):
        return True
    return False


def _is_blank(value):
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except Exception:
        pass
    return isinstance(value, str) and value.strip() == ""


def _is_empty(value):
    return _is_blank(value)


def _clean_value(value):
    """Clean value for DB insertion, preserve NULL for empty."""
    if _is_blank(value):
        return None
    if isinstance(value, str):
        s = value.strip()
        if s.lower() in ("nan", "nat", "none", "null", "-"):
            return None
        return s
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return value


def _json_safe(value):
    cleaned = _clean_value(value)
    try:
        json.dumps(cleaned)
        return cleaned
    except Exception:
        return str(cleaned)


def _is_valid_date(value):
    """True when the value is an unambiguous, valid date."""
    if _is_blank(value):
        return False
    return _parse_date_detail(value)[0] is not None


def _resolve_columns(columns, overrides=None):
    """
    Map columns to canonical fields.

    Returns (mapping, ambiguous, duplicates):
      mapping    {column label: canonical field}
      ambiguous  [(column label, [candidate fields])] - left unmapped on purpose
      duplicates [column label] - second header mapping to an already-mapped field
    """
    overrides = overrides or {}
    mapping, ambiguous, duplicates = {}, [], []
    used = set()
    for col in columns:
        norm = _normalize_col(col)
        canonical = overrides.get(norm)
        if canonical is None and norm in AMBIGUOUS_HEADERS:
            ambiguous.append((col, AMBIGUOUS_HEADERS[norm]))
            continue
        if canonical is None:
            canonical = COLUMN_MAP.get(norm)
        if canonical is None:
            continue
        if canonical in used:
            duplicates.append(col)
            continue
        mapping[col] = canonical
        used.add(canonical)
    return mapping, ambiguous, duplicates


def _map_columns(columns):
    """Backwards-compatible wrapper returning just the mapping."""
    return _resolve_columns(columns)[0]


def _detect_header_row(raw, overrides):
    """
    Index of the header row in a header-less frame, or 0 if none looks like one.

    A header row has at least 2 cells that are known headers (mapped or ambiguous).
    Title/blurb rows above the real header (very common in curated workbooks) are
    skipped this way.
    """
    known = set(COLUMN_MAP) | set(AMBIGUOUS_HEADERS) | set(overrides)
    for i in range(min(len(raw), HEADER_SCAN_ROWS)):
        cells = [_normalize_col(c) for c in raw.iloc[i].tolist() if not _is_blank(c)]
        if sum(1 for c in cells if c in known) >= 2:
            return i
    return 0


def _norm_key_text(value):
    return " ".join(str(value).strip().lower().split()) if not _is_blank(value) else ""


def _norm_key_deadline(value):
    """ISO date when the deadline is parseable, else '' (treated as unknown)."""
    if _is_blank(value):
        return ""
    parsed, _ = _parse_date_detail(value)
    return parsed.isoformat() if parsed else ""


def _fp_value(value):
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        try:
            f = float(s)
            return int(f) if f.is_integer() else f
        except ValueError:
            return " ".join(s.split())
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _fingerprint(values):
    """Stable hash of a record's canonical fields and raw row."""
    payload = json.dumps(
        {k: _fp_value(values.get(k)) for k in CANONICAL_FIELDS + ["raw_data"]},
        sort_keys=True, default=str, ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _find_duplicate(cursor, record):
    """
    Decide whether ``record`` is already stored. Returns a reason string or None.

    Identity rules, strongest first. Missing data NEVER causes a merge.
      1. external_id (e.g. 'PKSF-0001'): same id => same record; different ids =>
         always distinct records.
      2. Complete key: name + provider + deadline all present (deadline parseable):
         same normalised key => duplicate. A different deadline is a different cycle.
      3. Incomplete key (provider or deadline missing/unparseable): duplicate ONLY if
         every stored field and the raw source row are identical (an exact re-import).
         Otherwise it is a distinct record and is inserted.
    Raises on database errors so the caller can refuse to insert.
    """
    name = _norm_key_text(record.get("name"))
    ext = _norm_key_text(record.get("external_id"))

    if ext:
        cursor.execute(
            "SELECT 1 FROM scholarships WHERE LOWER(TRIM(external_id)) = ? LIMIT 1", (ext,)
        )
        if cursor.fetchone():
            return "same external_id"

    provider = _norm_key_text(record.get("provider"))
    deadline = _norm_key_deadline(record.get("deadline"))
    complete = bool(name and provider and deadline)

    cursor.execute(
        "SELECT * FROM scholarships WHERE LOWER(TRIM(name)) = LOWER(TRIM(?))",
        (record.get("name"),),
    )
    candidates = cursor.fetchall()

    new_fp = None
    for row in candidates:
        cand = dict(row)
        cand_ext = _norm_key_text(cand.get("external_id"))
        if ext and cand_ext and ext != cand_ext:
            continue  # explicitly different rows
        if complete and not (ext and cand_ext):
            if (
                _norm_key_text(cand.get("name")) == name
                and _norm_key_text(cand.get("provider")) == provider
                and _norm_key_deadline(cand.get("deadline")) == deadline
            ):
                return "same name + provider + deadline"
        if not complete:
            if new_fp is None:
                new_fp = _fingerprint(record)
            if _fingerprint(cand) == new_fp:
                return "identical record (key incomplete, exact re-import)"
    return None


def _row_to_raw_json(raw_by_label):
    try:
        return json.dumps({k: _json_safe(v) for k, v in raw_by_label.items()},
                          ensure_ascii=False, default=str)
    except Exception:
        return json.dumps({}, ensure_ascii=False)


def _new_summary():
    return {
        "records_inserted": 0,
        "records_skipped": 0,
        "records_duplicate": 0,
        "records_incomplete_key": 0,
        "sheets_processed": 0,
        "errors": [],
        "warnings": [],
    }


def import_workbook(path, column_overrides=None):
    """
    Import scholarship records from an Excel workbook.

    Args:
        path: Path to the Excel workbook (opened read-only; never modified).
        column_overrides: optional {header text: canonical field} used to resolve an
            ambiguous header explicitly, e.g. {"Country": "study_destination"}.

    Returns:
        dict with records_inserted, records_skipped, records_duplicate,
        records_incomplete_key, sheets_processed, errors, warnings.
    """
    summary = _new_summary()

    overrides = {}
    for header, field in (column_overrides or {}).items():
        if field not in CANONICAL_FIELDS:
            summary["errors"].append(
                f"Invalid column_overrides target '{field}' for header '{header}'; "
                f"must be one of the canonical fields"
            )
            return summary
        overrides[_normalize_col(header)] = field

    try:
        init_db()
    except Exception as e:
        summary["errors"].append(f"Failed to initialize database: {e}")
        return summary

    p = Path(path)
    if not p.exists():
        summary["errors"].append(f"Workbook not found: {path}")
        return summary
    if not p.is_file():
        summary["errors"].append(f"Path is not a file: {path}")
        return summary

    try:
        xls = pd.ExcelFile(str(p), engine="openpyxl")
        sheet_names = xls.sheet_names
    except Exception as e:
        summary["errors"].append(f"Failed to read workbook: {e}")
        return summary

    if not sheet_names:
        summary["warnings"].append("Workbook contains no sheets")
        return summary

    try:
        conn = get_connection()
    except Exception as e:
        summary["errors"].append(f"Failed to open database: {e}")
        return summary

    try:
        for sheet in sheet_names:
            if _is_toc_sheet(sheet):
                summary["warnings"].append(f"Skipped TOC/index sheet: {sheet}")
                continue
            try:
                raw = xls.parse(sheet_name=sheet, header=None, dtype=object)
            except Exception as e:
                summary["errors"].append(f"Failed to read sheet '{sheet}': {e}")
                continue
            _import_sheet(conn, sheet, raw, overrides, summary)

        if summary["records_inserted"] == 0 and not summary["errors"]:
            if summary["sheets_processed"] == 0:
                summary["warnings"].append(
                    "Workbook produced no inserts and no sheets were processed - check sheet names and column headers"
                )
            elif summary["records_skipped"] == 0:
                summary["warnings"].append(
                    "Ambiguous workbook structure - no records inserted; verify headers match expected scholarship fields"
                )
        return summary
    except Exception as e:
        summary["errors"].append(f"Unexpected error during import: {e}")
        return summary
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _import_sheet(conn, sheet, raw, overrides, summary):
    """Import one sheet inside ONE transaction; roll the whole sheet back on failure."""
    if raw is None or raw.empty:
        summary["warnings"].append(f"Sheet '{sheet}' is empty or has no data")
        return

    header_idx = _detect_header_row(raw, overrides)
    if header_idx > 0:
        summary["warnings"].append(
            f"Sheet '{sheet}': header row detected at row {header_idx + 1} "
            f"({header_idx} title row(s) above it ignored)"
        )

    # Build unique column labels from the header row
    labels, positions = [], []
    blank_cols = 0
    seen_norm = {}
    dup_headers = []
    for pos, cell in enumerate(raw.iloc[header_idx].tolist()):
        if _is_blank(cell) or str(cell).strip().lower().startswith("unnamed"):
            blank_cols += 1
            continue
        label = str(cell).strip()
        norm = _normalize_col(label)
        if norm in seen_norm:
            seen_norm[norm] += 1
            if norm not in dup_headers:
                dup_headers.append(norm)
            label = f"{label} (duplicate {seen_norm[norm]})"
        else:
            seen_norm[norm] = 1
        labels.append(label)
        positions.append(pos)

    if not labels:
        summary["warnings"].append(f"Sheet '{sheet}' has no valid column headers, skipped")
        return
    if dup_headers:
        summary["warnings"].append(
            f"Sheet '{sheet}' has duplicate headers: {dup_headers} - keeping first occurrence"
        )
    if blank_cols:
        summary["warnings"].append(
            f"Sheet '{sheet}' has {blank_cols} unnamed/blank columns (possible merged-cell artifacts) - ignored"
        )

    mapping, ambiguous, _dups = _resolve_columns(labels, overrides)
    for col, candidates in ambiguous:
        summary["warnings"].append(
            f"Ambiguous header '{col}' in sheet '{sheet}' was left unmapped (could be "
            f"{' or '.join(candidates)}); original values preserved in raw_data. "
            f"Pass column_overrides={{'{col}': '<field>'}} to map it explicitly."
        )
    for col, canonical in mapping.items():
        if _normalize_col(col) in SOFT_GENERIC_HEADERS:
            summary["warnings"].append(
                f"Generic header '{col}' in sheet '{sheet}' mapped to '{canonical}' - verify meaning; "
                f"original preserved in raw_data"
            )
    if not mapping:
        summary["warnings"].append(f"Sheet '{sheet}' has no recognizable scholarship columns, skipped")
        return
    if len(mapping) < 2:
        summary["warnings"].append(
            f"Sheet '{sheet}' has ambiguous structure (only {len(mapping)} recognized columns), processed with caution"
        )
    unmapped = [c for c in labels if c not in mapping]
    if len(unmapped) > len(mapping):
        summary["warnings"].append(
            f"Sheet '{sheet}' has {len(unmapped)} unmapped columns {unmapped[:5]} - "
            f"preserved in raw_data but not imported as canonical fields"
        )

    col_pos = dict(zip(labels, positions))
    data = raw.iloc[header_idx + 1:]
    if data.empty:
        summary["warnings"].append(f"Sheet '{sheet}' has no data rows after cleaning")
        return

    # Local counters; merged into the summary only if the sheet commits.
    local = {"inserted": 0, "skipped": 0, "duplicate": 0, "incomplete": 0}
    local_warnings, local_errors = [], []

    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.cursor()
        for idx, row in data.iterrows():
            excel_row = int(idx) + 1
            cells = row.tolist()
            rowvals = {label: cells[col_pos[label]] for label in labels}

            if all(_clean_value(rowvals[c]) is None for c in mapping):
                local["skipped"] += 1
                continue

            record = {}
            for col, canonical in mapping.items():
                record[canonical] = _clean_value(rowvals[col])
            if record.get("deadline") is not None:
                parsed, note = _parse_date_detail(record["deadline"])
                if parsed is None:
                    local_warnings.append(
                        f"Sheet '{sheet}' row {excel_row} has malformed deadline '{record['deadline']}' "
                        f"({note}) - stored as-is, treated as unknown for matching"
                    )
            record["raw_data"] = _row_to_raw_json(rowvals)

            if _is_blank(record.get("name")):
                local["skipped"] += 1
                local_warnings.append(f"Sheet '{sheet}' row {excel_row} skipped: missing scholarship name")
                continue

            try:
                reason = _find_duplicate(cursor, record)
            except Exception as e:
                # Unsafe to insert without a working duplicate check.
                local["skipped"] += 1
                local_errors.append(
                    f"Duplicate check failed for row {excel_row} in sheet '{sheet}'; row NOT inserted: {e}"
                )
                continue
            if reason:
                local["skipped"] += 1
                local["duplicate"] += 1
                continue

            insert_data = {f: record.get(f) for f in CANONICAL_FIELDS}
            insert_data["raw_data"] = record["raw_data"]
            cols = list(insert_data)
            try:
                cursor.execute(
                    f"INSERT INTO scholarships ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                    [insert_data[c] for c in cols],
                )
                local["inserted"] += 1
                if not (_norm_key_text(record.get("provider")) and _norm_key_deadline(record.get("deadline"))
                        or _norm_key_text(record.get("external_id"))):
                    local["incomplete"] += 1
            except Exception as e:
                local["skipped"] += 1
                local_errors.append(f"Failed to insert row {excel_row} in sheet '{sheet}': {e}")

        if local["incomplete"]:
            local_warnings.append(
                f"Sheet '{sheet}': {local['incomplete']} record(s) lack provider and/or a parseable deadline "
                f"(and have no external id). They were inserted as distinct records; re-importing "
                f"the same file skips only exact repeats."
            )
        conn.commit()
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        summary["errors"].append(f"Sheet '{sheet}' rolled back; nothing from it was imported: {e}")
        summary["errors"].extend(local_errors)
        return

    summary["sheets_processed"] += 1
    summary["records_inserted"] += local["inserted"]
    summary["records_skipped"] += local["skipped"]
    summary["records_duplicate"] += local["duplicate"]
    summary["records_incomplete_key"] += local["incomplete"]
    summary["warnings"].extend(local_warnings)
    summary["errors"].extend(local_errors)
