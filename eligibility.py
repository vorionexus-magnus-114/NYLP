"""
Eligibility evaluation for Scholarship Finder.

Deterministic, rule-based and offline. Design rules:

* A criterion is only declared FAILED when both sides are clearly understood.
  Anything ambiguous, unrecognised or malformed stays UNKNOWN (reported in
  ``missing_information``) - it is never turned into a pass.
* Percentage marks and CGPA are never compared with each other or converted.
* Aliases are limited to well-established equivalences. Where a term could
  mean two different things (e.g. "Postgraduate", "Graduate", "Aus",
  "England") it is deliberately NOT aliased.
* A scholarship with no recorded criteria is never reported as "eligible".
"""

import re
from datetime import date, datetime


# --------------------------------------------------------------------------- #
# Generic helpers
# --------------------------------------------------------------------------- #

def _normalize(value):
    """Lowercased, stripped string ('' for None)."""
    if value is None:
        return ""
    return str(value).strip().lower()


def _clean_text(value):
    """Normalise free text for comparison: lowercase, no dots/apostrophes, '&'->'and',
    separators -> spaces, collapsed whitespace."""
    n = _normalize(value)
    n = n.replace(".", "").replace("'", "").replace("\u2019", "")
    n = n.replace("&", " and ").replace("-", " ").replace("_", " ")
    return " ".join(n.split())


def _plain(value):
    """Lowercase + collapsed whitespace only. Used for numbers, where '.' is a decimal point."""
    return " ".join(str(value).strip().lower().split())


def _strip_parenthetical(text):
    """'phd (inland)' -> 'phd'. Returns the input if nothing is left."""
    stripped = re.sub(r"\([^)]*\)", " ", text)
    stripped = " ".join(stripped.split())
    return stripped or text


def _is_empty(value):
    """True for None, blank strings and NaN."""
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    if isinstance(value, float) and value != value:
        return True
    return False


def _parse_list(value):
    """Split a value on commas, semicolons, slashes, pipes and newlines."""
    if _is_empty(value):
        return []
    if isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        s = str(value)
        for sep in [";", "/", "|", "\n"]:
            s = s.replace(sep, ",")
        items = s.split(",")
    return [str(i).strip() for i in items if not _is_empty(i) and str(i).strip()]


# --------------------------------------------------------------------------- #
# Alias tables (conservative)
# --------------------------------------------------------------------------- #

_EDU_ALIASES = {
    "bachelor": "bachelor", "bachelors": "bachelor", "bachelors degree": "bachelor",
    "bachelor degree": "bachelor", "bs": "bachelor", "b s": "bachelor", "ba": "bachelor",
    "bsc": "bachelor", "bba": "bachelor", "undergraduate": "bachelor",
    "undergrad": "bachelor", "ug": "bachelor",
    "master": "master", "masters": "master", "masters degree": "master",
    "master degree": "master", "ms": "master", "ma": "master", "msc": "master",
    "phd": "phd", "ph d": "phd", "doctorate": "phd", "doctoral": "phd", "dphil": "phd",
    "intermediate": "intermediate", "hssc": "intermediate", "12th": "intermediate",
    "a level": "intermediate", "a levels": "intermediate", "fa": "intermediate",
    "fsc": "intermediate", "ics": "intermediate",
    "matric": "matric", "matriculation": "matric", "ssc": "matric", "10th": "matric",
    "o level": "matric", "o levels": "matric",
    # Deliberately NOT aliased (ambiguous): "graduate", "postgraduate", "post graduate",
    # "pg" (may or may not include PhD; "graduate" often means "holds a bachelor").
}

# Prior qualifications: education levels plus a few well-defined named degrees.
_QUAL_ALIASES = dict(_EDU_ALIASES)
_QUAL_ALIASES.update({
    "mphil": "mphil", "m phil": "mphil", "mbbs": "mbbs", "bds": "bds", "llb": "llb",
    "pharm d": "pharm d", "dpt": "dpt", "dae": "dae", "adp": "adp",
})

_NATIONALITY_PAKISTAN = {
    "pakistani", "pakistan", "pak", "pk", "pakistanis", "pakistani national",
    "pakistani nationals", "pakistani citizen", "pakistani citizens", "citizen of pakistan",
}
# Words that make a nationality phrase a different category (e.g. "Overseas Pakistani").
_NATIONALITY_QUALIFIERS = {
    "overseas", "dual", "resident", "residents", "non", "based", "living", "abroad",
    "refugee", "refugees", "or", "and", "except", "excluding", "international",
}

_PROVINCE_ALIASES = {
    "punjab": "punjab",
    "sindh": "sindh",
    "balochistan": "balochistan", "baluchistan": "balochistan",
    "khyber pakhtunkhwa": "khyber pakhtunkhwa", "khyber pukhtunkhwa": "khyber pakhtunkhwa",
    "kpk": "khyber pakhtunkhwa", "kp": "khyber pakhtunkhwa", "nwfp": "khyber pakhtunkhwa",
    "gilgit baltistan": "gilgit baltistan", "gb": "gilgit baltistan",
    "azad jammu and kashmir": "azad jammu and kashmir",
    "azad jammu kashmir": "azad jammu and kashmir", "ajk": "azad jammu and kashmir",
    "azad kashmir": "azad jammu and kashmir",
    "islamabad capital territory": "islamabad capital territory",
    "ict": "islamabad capital territory", "islamabad": "islamabad capital territory",
}

_DEST_ALIASES = {
    "usa": "usa", "us": "usa", "u s": "usa", "u s a": "usa", "united states": "usa",
    "united states of america": "usa",
    "uk": "uk", "u k": "uk", "united kingdom": "uk",
    "pakistan": "pakistan", "pak": "pakistan",
    "ksa": "saudi arabia", "saudi arabia": "saudi arabia",
    "uae": "uae", "united arab emirates": "uae",
    "turkey": "turkey", "turkiye": "turkey", "t\u00fcrkiye": "turkey",
}
# Countries that are recognised as themselves (no alias needed).
_KNOWN_COUNTRIES = {
    "australia", "canada", "germany", "china", "japan", "malaysia", "france", "italy",
    "spain", "netherlands", "sweden", "norway", "denmark", "finland", "ireland",
    "switzerland", "austria", "belgium", "south korea", "new zealand", "singapore",
    "hong kong", "qatar", "oman", "kuwait", "bahrain", "egypt", "russia", "hungary",
    "poland", "czech republic", "india", "thailand", "indonesia", "brunei",
}

_WILDCARDS = {
    "nationality": {"all", "any", "all nationalities", "any nationality", "open to all"},
    "education": {"all", "any", "all levels", "any level"},
    "field": {"all", "any", "all fields", "any field", "all disciplines", "any discipline",
              "all subjects", "any subject"},
    "province": {"all", "any", "all pakistan", "all of pakistan", "nationwide", "all provinces",
                 "any province", "pakistan"},
    "city": {"all", "any", "all cities", "any city"},
    "domicile": {"all", "any", "all domiciles", "any domicile"},
    "destination": {"all", "any", "worldwide", "global", "any country", "all countries"},
}


# --------------------------------------------------------------------------- #
# Canonicalisers: each returns (canonical_value, recognised)
# --------------------------------------------------------------------------- #

def _canon_from_table(value, table):
    n = _clean_text(value)
    if n in table:
        return table[n], True
    n2 = _strip_parenthetical(n)
    if n2 in table:
        return table[n2], True
    return n2, False


def _canon_edu(value):
    return _canon_from_table(value, _EDU_ALIASES)


def _canon_qual(value):
    return _canon_from_table(value, _QUAL_ALIASES)


def _canon_province(value):
    return _canon_from_table(value, _PROVINCE_ALIASES)


def _canon_destination(value):
    n = _clean_text(value)
    n = _strip_parenthetical(n)
    if n in _DEST_ALIASES:
        return _DEST_ALIASES[n], True
    if n in _KNOWN_COUNTRIES:
        return n, True
    return n, False


def _canon_nationality(value):
    n = _strip_parenthetical(_clean_text(value))
    if n in _NATIONALITY_PAKISTAN:
        return "pakistan", True
    if set(n.split()) & _NATIONALITY_QUALIFIERS:
        return n, False
    return n, True


def _canon_exact(value):
    """Exact text comparison (cities, qualifications handled elsewhere)."""
    return _strip_parenthetical(_clean_text(value)), True


def _canon_domicile(value):
    """Provinces are recognised; district-level values compare only by equality."""
    return _canon_province(value)


# Backwards-compatible helpers (kept for callers that imported them)
def _canonical_edu(value):
    return _canon_edu(value)[0]


def _canonical_destination(value):
    return _canon_destination(value)[0]


# --------------------------------------------------------------------------- #
# Numeric parsing (strict; returns (number, note))
# --------------------------------------------------------------------------- #

_NUM = r"\d+(?:\.\d+)?"


def _to_float(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        f = float(value)
        if f != f or f in (float("inf"), float("-inf")):
            return None
        return f
    return None


def _parse_age(value):
    """Return (age, note)."""
    f = _to_float(value)
    if f is None:
        s = _plain(value).replace(",", "")
        m = re.fullmatch(rf"({_NUM})\s*(?:years old|years?|yrs?|y)?", s)
        if not m:
            return None, f"unrecognised age value '{value}'"
        f = float(m.group(1))
    if f < 0 or f > 120:
        return None, f"age value '{value}' is out of range"
    return f, None


def _parse_percentage(value):
    """Percentage marks 0-100. Never accepts something that looks like a GPA."""
    f = _to_float(value)
    if f is None:
        s = _plain(value).replace(",", "")
        m = re.fullmatch(rf"({_NUM})\s*(?:%|percent|percentage)?", s)
        if m:
            f = float(m.group(1))
        else:
            m = re.fullmatch(rf"({_NUM})\s*(?:/|out of)\s*({_NUM})", s)
            if not m:
                return None, f"unrecognised marks value '{value}'"
            num, den = float(m.group(1)), float(m.group(2))
            if den <= 10:
                return None, f"'{value}' looks like a GPA, not percentage marks"
            if num > den:
                return None, f"marks value '{value}' exceeds total"
            f = num / den * 100.0
    if f < 0 or f > 100:
        return None, f"marks value '{value}' is not a percentage between 0 and 100"
    if 0 < f <= 4.5:
        return None, f"marks value '{value}' looks like a GPA, not percentage marks"
    return f, None


def _parse_cgpa(value):
    """CGPA on a 4.0 scale only."""
    f = _to_float(value)
    if f is None:
        s = _plain(value)
        m = re.fullmatch(rf"({_NUM})\s*(?:/|out of)\s*({_NUM})", s)
        if m:
            if float(m.group(2)) != 4.0:
                return None, f"CGPA '{value}' is not on a 4.0 scale"
            f = float(m.group(1))
        else:
            m = re.fullmatch(_NUM, s)
            if not m:
                return None, f"unrecognised CGPA value '{value}'"
            f = float(s)
    if f < 0 or f > 4.0:
        return None, f"CGPA value '{value}' is outside a 4.0 scale (scale unclear or percentage supplied)"
    return f, None


def _parse_income(value):
    """Return (amount, period, note); period in {None, 'month', 'year'}."""
    period = None
    f = _to_float(value)
    if f is None:
        s = _plain(value).replace("p.a.", "pa")
        if re.search(r"\b(usd|gbp|eur|dollars?|pounds?|euros?)\b|[$£€]", str(value).lower()):
            return None, None, f"income '{value}' is not in PKR"
        if re.search(r"\b(per month|monthly|a month|pm|month)\b", s):
            period = "month"
        elif re.search(r"\b(per year|yearly|annual|annually|per annum|pa|year)\b", s):
            period = "year"
        s = re.sub(r"\b(per month|monthly|a month|pm|month|per year|yearly|annual|annually|per annum|pa|year)\b", " ", s)
        s = re.sub(r"\b(rs|pkr|rupees?)\b|\u20a8", " ", s)
        s = s.replace(",", "").replace("/", " ")
        s = " ".join(s.replace("-", " ").split()).strip(" .")
        if not re.fullmatch(_NUM, s):
            return None, None, f"unrecognised income value '{value}'"
        f = float(s)
    if f < 0:
        return None, None, f"income '{value}' is negative"
    return f, period, None


# --------------------------------------------------------------------------- #
# Date parsing
# --------------------------------------------------------------------------- #

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4,
    "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8,
    "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10,
    "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_RE_ISO = re.compile(r"(?<!\d)(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)")
_RE_NUM = re.compile(r"(?<!\d)(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})(?!\d)")
_RE_DMY_NAME = re.compile(rf"(?<!\d)(\d{{1,2}})[\s\-/]*{_MON}[\s\-/,]*(\d{{4}})(?!\d)", re.I)
_RE_MDY_NAME = re.compile(rf"\b{_MON}\.?\s+(\d{{1,2}}),?\s+(\d{{4}})(?!\d)", re.I)


def _mk(y, m, d):
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _parse_date_detail(value):
    """Return (date | None, note). Ambiguous or multi-date strings yield None."""
    if _is_empty(value):
        return None, "empty"
    if isinstance(value, datetime):
        return value.date(), None
    if isinstance(value, date):
        return value, None
    if hasattr(value, "to_pydatetime"):
        try:
            return value.to_pydatetime().date(), None
        except Exception:
            return None, f"unreadable date object '{value}'"

    s = re.sub(r"(?<=\d)(st|nd|rd|th)\b", "", str(value).strip(), flags=re.I)
    found = []
    for m in _RE_ISO.finditer(s):
        found.append(("iso", m.groups()))
    for m in _RE_NUM.finditer(s):
        found.append(("num", m.groups()))
    for m in _RE_DMY_NAME.finditer(s):
        found.append(("dmy", m.groups()))
    for m in _RE_MDY_NAME.finditer(s):
        found.append(("mdy", m.groups()))
    # A text month can also match numerically nested patterns; de-duplicate by result later.
    if not found:
        return None, f"unrecognised date '{value}'"

    results = set()
    ambiguous = False
    for kind, g in found:
        if kind == "iso":
            d = _mk(int(g[0]), int(g[1]), int(g[2]))
        elif kind == "num":
            a, b, y = int(g[0]), int(g[1]), int(g[2])
            if a > 12:
                d = _mk(y, b, a)
            elif b > 12:
                d = _mk(y, a, b)
            elif a == b:
                d = _mk(y, a, b)
            else:
                ambiguous = True
                d = None
        elif kind == "dmy":
            d = _mk(int(g[2]), _MONTHS[g[1].lower()], int(g[0]))
        else:
            d = _mk(int(g[2]), _MONTHS[g[0].lower().rstrip(".")], int(g[1]))
        if d is not None:
            results.add(d)
    if ambiguous and not results:
        return None, f"date '{value}' is ambiguous (day/month order unclear)"
    if ambiguous or len(results) > 1:
        return None, f"'{value}' contains more than one date; cannot tell which is the deadline"
    if not results:
        return None, f"invalid calendar date '{value}'"
    return results.pop(), None


def _parse_date(value):
    """Return a date or None (backwards-compatible wrapper)."""
    return _parse_date_detail(value)[0]


# --------------------------------------------------------------------------- #
# Set-membership criteria
# --------------------------------------------------------------------------- #

_OPEN_STATUSES = {"open", "active", "available", "ongoing", "applications open",
                  "accepting applications"}
_CLOSED_STATUSES = {"closed", "expired", "inactive", "disabled", "archived",
                    "applications closed", "cancelled", "canceled", "discontinued",
                    "suspended"}
_NOT_YET_OPEN_STATUSES = {"upcoming", "coming soon", "not yet open", "opening soon"}


def _check_set(label, sch_raw, prof_raw, canon, wildcards, passes, fails, missing,
               sch_name, prof_name=None):
    """
    Generic membership check with conservative failure semantics.

    pass    - a recognised/equal value on both sides matches, or scholarship is unrestricted
    fail    - every value on both sides is recognised and none match
    unknown - anything else
    """
    prof_name = prof_name or label
    if _is_empty(sch_raw):
        return
    if _is_empty(prof_raw):
        missing.append(f"{prof_name} is required but not provided in profile")
        return

    sch_vals = _parse_list(sch_raw)
    prof_vals = _parse_list(prof_raw)
    if not sch_vals or not prof_vals:
        missing.append(f"{sch_name} value could not be read")
        return

    if any(_clean_text(v) in wildcards for v in sch_vals):
        passes.append(f"{sch_name} has no restriction ({sch_raw})")
        return

    sch_c = [canon(v) for v in sch_vals]
    prof_c = [canon(v) for v in prof_vals]
    sch_set = {c for c, _ in sch_c}
    prof_set = {c for c, _ in prof_c}
    if sch_set & prof_set:
        passes.append(f"{sch_name} matches: {prof_raw}")
        return
    if all(r for _, r in sch_c) and all(r for _, r in prof_c):
        fails.append(f"{sch_name} requirement not met: required {sch_raw}, profile has {prof_raw}")
    else:
        missing.append(
            f"{sch_name} could not be confirmed: scholarship says '{sch_raw}', profile says "
            f"'{prof_raw}' (value not clearly comparable)"
        )


def _check_nationality(sch_raw, prof_raw, passes, fails, missing):
    if _is_empty(sch_raw):
        return
    if _is_empty(prof_raw):
        missing.append("nationality is required but not provided in profile")
        return
    sch_vals = _parse_list(sch_raw)
    prof_vals = _parse_list(prof_raw)
    if any(_clean_text(v) in _WILDCARDS["nationality"] for v in sch_vals):
        passes.append(f"Nationality has no restriction ({sch_raw})")
        return
    sch_c = [_canon_nationality(v) for v in sch_vals]
    prof_c = [_canon_nationality(v) for v in prof_vals]
    sch_set = {c for c, _ in sch_c}
    prof_set = {c for c, _ in prof_c}
    if sch_set & prof_set:
        passes.append(f"Nationality matches: {prof_raw}")
        return
    sch_all_known = all(r for _, r in sch_c)
    prof_all_known = all(r for _, r in prof_c)
    # Only declare a mismatch when Pakistan is on one side and both sides are plain values.
    pakistani_profile = prof_set == {"pakistan"}
    pakistan_only_scholarship = sch_set == {"pakistan"}
    if sch_all_known and prof_all_known and (pakistani_profile or pakistan_only_scholarship):
        fails.append(f"Nationality requirement not met: required {sch_raw}, profile has {prof_raw}")
    else:
        missing.append(
            f"nationality could not be confirmed: scholarship says '{sch_raw}', profile says '{prof_raw}'"
        )


def _check_domicile(sch_raw, prof_raw, passes, fails, missing):
    # Domicile is deliberately NOT inferred from province.
    _check_set("domicile", sch_raw, prof_raw, _canon_domicile, _WILDCARDS["domicile"],
               passes, fails, missing, "Domicile")


def _check_field(sch_raw, prof_raw, passes, fails, missing):
    if _is_empty(sch_raw):
        return
    if _is_empty(prof_raw):
        missing.append("field_of_study is required but not provided in profile")
        return
    sch_vals = [_clean_text(v) for v in _parse_list(sch_raw)]
    prof_vals = [_clean_text(v) for v in _parse_list(prof_raw)]
    if any(v in _WILDCARDS["field"] for v in sch_vals):
        passes.append(f"Field of study has no restriction ({sch_raw})")
        return
    if set(sch_vals) & set(prof_vals):
        passes.append(f"Field of study matches: {prof_raw}")
        return
    # Partial overlap ("engineering" vs "mechanical engineering") is uncertain, not a match.
    for a in sch_vals:
        for b in prof_vals:
            if a and b and (re.search(rf"\b{re.escape(a)}\b", b) or re.search(rf"\b{re.escape(b)}\b", a)):
                missing.append(
                    f"field_of_study could not be confirmed: scholarship says '{sch_raw}', profile says '{prof_raw}'"
                )
                return
    fails.append(f"Field of study requirement not met: required {sch_raw}, profile has {prof_raw}")


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #

def _as_dict(obj):
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    try:
        return dict(obj)
    except Exception:
        return {}


def check_eligibility(profile, scholarship, today=None):
    """
    Evaluate whether a student meets a scholarship's eligibility.

    Args:
        profile (dict): student data
        scholarship (dict): scholarship record
        today (date, optional): override "today" (used for deterministic tests)

    Returns:
        dict with eligibility_status ('eligible', 'potentially_eligible',
        'insufficient_information', 'ineligible'), reasons, missing_information.
    """
    profile = _as_dict(profile)
    scholarship = _as_dict(scholarship)
    if today is None:
        today = date.today()
    elif isinstance(today, datetime):
        today = today.date()

    missing = []
    hard_fails = []
    criteria_passes = []      # student-specific checks that passed
    availability_passes = []  # status/deadline checks (say nothing about the student)

    def get_any(source, keys):
        for k in keys:
            if k in source:
                return source[k]
        lowered = {_normalize(sk): sk for sk in source.keys()}
        for k in keys:
            if _normalize(k) in lowered:
                return source[lowered[_normalize(k)]]
        return None

    # --- Nationality ---
    _check_nationality(get_any(scholarship, ["nationality"]), get_any(profile, ["nationality"]),
                       criteria_passes, hard_fails, missing)

    # --- Age ---
    sch_min_age = get_any(scholarship, ["minimum_age", "min_age"])
    sch_max_age = get_any(scholarship, ["maximum_age", "max_age"])
    prof_age = get_any(profile, ["age"])
    if not _is_empty(sch_min_age) or not _is_empty(sch_max_age):
        if _is_empty(prof_age):
            missing.append("age is required but not provided in profile")
        else:
            age, age_note = _parse_age(prof_age)
            lo, lo_note = (None, None) if _is_empty(sch_min_age) else _parse_age(sch_min_age)
            hi, hi_note = (None, None) if _is_empty(sch_max_age) else _parse_age(sch_max_age)
            if lo_note or hi_note:
                missing.append("scholarship age criteria malformed or invalid")
            elif age is None:
                missing.append("profile age is malformed or invalid")
            elif lo is not None and age < lo:
                hard_fails.append(f"Age {age:g} is below minimum required {lo:g}")
            elif hi is not None and age > hi:
                hard_fails.append(f"Age {age:g} exceeds maximum allowed {hi:g}")
            else:
                criteria_passes.append(f"Age {age:g} meets age requirement")

    # --- Education level / qualification / field ---
    _check_set("education_level",
               get_any(scholarship, ["education_level", "education level", "level"]),
               get_any(profile, ["education_level", "education level", "level"]),
               _canon_edu, _WILDCARDS["education"], criteria_passes, hard_fails, missing,
               "Education level", "education_level")
    _check_set("qualification", get_any(scholarship, ["qualification"]),
               get_any(profile, ["qualification"]), _canon_qual, _WILDCARDS["education"],
               criteria_passes, hard_fails, missing, "Qualification", "qualification")
    prof_field = get_any(profile, ["field_of_study", "field", "discipline", "major", "subject"])
    _check_field(get_any(scholarship, ["field_of_study", "field", "fields"]), prof_field,
                 criteria_passes, hard_fails, missing)

    # --- Minimum marks (percentage only) ---
    sch_marks = get_any(scholarship, ["minimum_marks", "min_marks", "minimum_marks_required"])
    if not _is_empty(sch_marks):
        prof_marks = get_any(profile, ["marks", "percentage", "marks_percentage"])
        if _is_empty(prof_marks):
            missing.append("academic marks are required but not provided in profile (minimum_marks); "
                           "CGPA cannot be used in place of percentage marks")
        else:
            need, need_note = _parse_percentage(sch_marks)
            have, have_note = _parse_percentage(prof_marks)
            if need is None:
                missing.append(f"scholarship minimum_marks criterion malformed or invalid: {need_note}")
            elif have is None:
                missing.append(f"profile marks value is malformed or invalid: {have_note}")
            elif have < need:
                hard_fails.append(f"Academic marks {have:g} below minimum required {need:g}")
            else:
                criteria_passes.append(f"Academic marks {have:g} meet minimum {need:g}")

    # --- Minimum CGPA (4.0 scale only) ---
    sch_cgpa = get_any(scholarship, ["minimum_cgpa", "min_cgpa", "minimum_gpa", "cgpa"])
    if not _is_empty(sch_cgpa):
        prof_cgpa = get_any(profile, ["cgpa", "gpa"])
        if _is_empty(prof_cgpa):
            missing.append("CGPA/GPA is required but not provided in profile (minimum_cgpa); "
                           "percentage marks cannot be used in place of CGPA")
        else:
            need, need_note = _parse_cgpa(sch_cgpa)
            have, have_note = _parse_cgpa(prof_cgpa)
            if need is None:
                missing.append(f"scholarship minimum_cgpa criterion malformed or invalid: {need_note}")
            elif have is None:
                missing.append(f"profile CGPA value is malformed or invalid: {have_note}")
            elif have < need:
                hard_fails.append(f"CGPA {have:g} below minimum required {need:g}")
            else:
                criteria_passes.append(f"CGPA {have:g} meets minimum {need:g}")

    # --- Household income ---
    sch_income = get_any(scholarship, ["household_income_limit", "income_limit", "household_income"])
    if not _is_empty(sch_income):
        prof_income = get_any(profile, ["household_income", "income", "family_income"])
        if _is_empty(prof_income):
            missing.append("household_income is required but not provided in profile")
        else:
            limit, lim_period, lim_note = _parse_income(sch_income)
            have, have_period, have_note = _parse_income(prof_income)
            if limit is None:
                missing.append(f"scholarship household_income_limit criterion malformed or invalid: {lim_note}")
            elif have is None:
                missing.append(f"profile household_income value is malformed or invalid: {have_note}")
            elif lim_period != have_period and (lim_period or have_period):
                missing.append("household income could not be compared: monthly vs annual period is unclear")
            elif have > limit:
                hard_fails.append(f"Household income {have:g} exceeds limit {limit:g}")
            else:
                criteria_passes.append(f"Household income {have:g} within limit {limit:g}")

    # --- Geography ---
    _check_set("province", get_any(scholarship, ["eligible_provinces", "eligible_province", "province"]),
               get_any(profile, ["province"]), _canon_province, _WILDCARDS["province"],
               criteria_passes, hard_fails, missing, "Province", "province")
    _check_set("city", get_any(scholarship, ["eligible_cities", "eligible_city", "cities", "city"]),
               get_any(profile, ["city"]), _canon_exact, _WILDCARDS["city"],
               criteria_passes, hard_fails, missing, "City", "city")
    _check_domicile(get_any(scholarship, ["eligible_domiciles", "eligible_domicile", "domiciles", "domicile"]),
                    get_any(profile, ["domicile"]), criteria_passes, hard_fails, missing)

    # --- Study destination ---
    _check_set("destination", get_any(scholarship, ["study_destination", "destination"]),
               get_any(profile, ["study_destination", "destination"]), _canon_destination,
               _WILDCARDS["destination"], criteria_passes, hard_fails, missing,
               "Study destination", "study_destination")

    # --- Status ---
    sch_status = get_any(scholarship, ["status"])
    if not _is_empty(sch_status):
        ns = _clean_text(sch_status)
        if ns in _CLOSED_STATUSES:
            hard_fails.append(f"Scholarship status indicates not open: {sch_status}")
        elif ns in _OPEN_STATUSES:
            availability_passes.append(f"Scholarship status is open: {sch_status}")
        elif ns in _NOT_YET_OPEN_STATUSES:
            missing.append(f"scholarship is not yet open (status: {sch_status})")
        else:
            missing.append(f"scholarship status '{sch_status}' is not recognised; cannot confirm applications are open")

    # --- Deadline ---
    sch_deadline = get_any(scholarship, ["deadline", "due_date", "closing_date", "last_date"])
    if _is_empty(sch_deadline):
        missing.append("deadline not specified or unknown")
    else:
        parsed, note = _parse_date_detail(sch_deadline)
        if parsed is None:
            missing.append(f"deadline value is invalid or unparseable: {sch_deadline} ({note})")
        elif parsed < today:
            hard_fails.append(f"Scholarship deadline has expired on {parsed.isoformat()}")
        else:
            availability_passes.append(f"Deadline is still open: {parsed.isoformat()}")

    # --- Final status ---
    if hard_fails:
        status = "ineligible"
        reasons = hard_fails + criteria_passes + availability_passes
    elif missing:
        status = "potentially_eligible" if criteria_passes else "insufficient_information"
        reasons = criteria_passes + availability_passes
    elif not criteria_passes:
        # Nothing about the student was actually verified: never claim eligibility.
        status = "insufficient_information"
        reasons = availability_passes
        missing.append("no eligibility criteria are recorded for this scholarship; eligibility cannot be confirmed")
    else:
        status = "eligible"
        reasons = criteria_passes + availability_passes

    seen = set()
    uniq_missing = []
    for m in missing:
        if m not in seen:
            seen.add(m)
            uniq_missing.append(m)

    return {
        "eligibility_status": status,
        "reasons": reasons,
        "missing_information": uniq_missing,
    }
