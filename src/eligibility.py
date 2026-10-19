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

from src.criteria import enrich_record, match_field


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
    "vocational": "vocational", "dae": "vocational", "diploma": "vocational",
    "diploma of associate engineering": "vocational", "associate": "vocational",
    "associate degree": "vocational", "technical diploma": "vocational",
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
    # Labels such as "Undergraduate / Bachelors" or "Postgraduate / Masters"
    # describe one level; accept them when every recognised part agrees.
    parts = [p.strip() for p in re.split(r"[/,;|]", n2) if p.strip()]
    if len(parts) > 1:
        found = {table[p] for p in parts if p in table}
        if len(found) == 1:
            return found.pop(), True
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
                  "accepting applications", "rolling", "open rolling",
                  "rolling applications", "open and rolling"}
_CLOSED_STATUSES = {"closed", "expired", "inactive", "disabled", "archived",
                    "applications closed", "cancelled", "canceled", "discontinued",
                    "suspended"}
_NOT_YET_OPEN_STATUSES = {"upcoming", "coming soon", "not yet open", "opening soon",
                          "not announced", "not announced yet", "unannounced",
                          "not yet announced"}


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
    verdict = match_field(sch_raw, prof_raw)
    if verdict == "pass":
        passes.append(f"Field of study matches: {prof_raw}")
    elif verdict == "fail":
        fails.append(
            f"Field of study requirement not met: scholarship is for '{sch_raw}', profile has '{prof_raw}'"
        )
    else:
        if _is_empty(prof_raw):
            missing.append("field_of_study is required but not provided in profile")
        else:
            missing.append(
                f"field_of_study could not be confirmed: scholarship says '{sch_raw}', profile says '{prof_raw}'"
            )


def _is_pakistan(value):
    return _canon_destination(value)[0] == "pakistan"


def _check_destination(sch_raw, prof_raw, passes, fails, missing):
    """
    Compare the student's destination preference with the scholarship's country.

    Profile semantics: ``Pakistan`` / ``Abroad`` / ``Both`` / ``None`` (any).
    A scholarship destination may be a country, a comma/semicolon list, or a
    non-country scope such as ``International``. Wildcards mean unrestricted.
    """
    if _is_empty(prof_raw):
        return
    pref = _clean_text(prof_raw)
    if pref in ("", "both", "any", "all", "anywhere"):
        return
    wants_abroad = pref in ("abroad", "international", "foreign", "overseas")
    wants_pakistan = pref in ("pakistan", "pakistani", "pak")
    if not wants_abroad and not wants_pakistan:
        return

    if _is_empty(sch_raw):
        missing.append(
            "Study destination is not stated for this scholarship; cannot confirm it "
            f"matches your preference ({prof_raw})"
        )
        return

    raw_norm = _clean_text(sch_raw)
    if any(w in raw_norm for w in _WILDCARDS["destination"]):
        passes.append(f"Destination has no restriction ({sch_raw})")
        return
    if raw_norm in ("international", "overseas", "foreign", "abroad"):
        countries = {"abroad"}
    else:
        countries = {c for c, _ in (_canon_destination(v) for v in _parse_list(sch_raw))}

    if not countries:
        missing.append(f"Destination '{sch_raw}' could not be interpreted")
        return

    only_pakistan = countries == {"pakistan"}
    has_abroad = any(c != "pakistan" for c in countries)

    if wants_pakistan:
        if only_pakistan:
            passes.append(f"Destination matches: {sch_raw}")
        elif has_abroad and "pakistan" not in countries:
            fails.append(f"Scholarship is for study abroad ({sch_raw}); your destination is Pakistan")
        elif has_abroad:
            passes.append(f"Scholarship includes study in Pakistan ({sch_raw})")
        else:
            missing.append(f"Destination '{sch_raw}' could not be interpreted")
    else:  # wants_abroad
        if has_abroad:
            passes.append(f"Destination matches: {sch_raw}")
        elif only_pakistan:
            fails.append(f"Scholarship is for study in Pakistan ({sch_raw}); your destination is Abroad")
        else:
            missing.append(f"Destination '{sch_raw}' could not be interpreted")


def _check_gender(sch_gender, prof_gender, passes, fails, missing):
    sch = _clean_text(sch_gender)
    if not sch:
        return
    if _is_empty(prof_gender):
        missing.append(f"gender is required but not provided in profile (scholarship is for {sch_gender})")
        return
    prof = _clean_text(prof_gender)
    sch_tokens = set(re.findall(r"[a-z]+", sch))
    prof_tokens = set(re.findall(r"[a-z]+", prof))
    if (sch_tokens & {"female", "women", "woman", "girl"}) and \
            (prof_tokens & {"female", "women", "woman", "girl"}):
        passes.append(f"Gender matches: {sch_gender}")
    elif (sch_tokens & {"male", "men", "man", "boy"}) and \
            (prof_tokens & {"male", "men", "man", "boy"}):
        passes.append(f"Gender matches: {sch_gender}")
    else:
        fails.append(f"Gender requirement not met: scholarship is for {sch_gender}, profile says {prof_gender}")


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


def get_value(source, keys):
    """Return the first present key (case/space-insensitive) from ``source``."""
    for key in keys:
        if key in source:
            return source[key]
    lowered = {_normalize(sk): sk for sk in source.keys()}
    for key in keys:
        if _normalize(key) in lowered:
            return source[lowered[_normalize(key)]]
    return None


def _eval_grade_requirement(scholarship, profile, criteria_passes, hard_fails, missing):
    """Evaluate percentage / CGPA requirements, honouring OR alternatives."""
    sch_marks = get_value(scholarship, ["minimum_marks", "min_marks", "minimum_marks_required"])
    sch_cgpa = get_value(scholarship, ["minimum_cgpa", "min_cgpa", "minimum_gpa"])
    if _is_empty(sch_marks) and _is_empty(sch_cgpa):
        return

    logic = _clean_text(scholarship.get("grade_logic")) or ("or" if not _is_empty(sch_marks)
                                                           and not _is_empty(sch_cgpa) else "and")
    prof_marks = get_value(profile, ["marks", "percentage", "marks_percentage"])
    prof_cgpa = get_value(profile, ["cgpa", "gpa"])

    need_marks = need_cgpa = None
    if not _is_empty(sch_marks):
        need_marks, note = _parse_percentage(sch_marks)
        if need_marks is None:
            missing.append(f"scholarship minimum_marks criterion malformed or invalid: {note}")
    if not _is_empty(sch_cgpa):
        need_cgpa, note = _parse_cgpa(sch_cgpa)
        if need_cgpa is None:
            missing.append(f"scholarship minimum_cgpa criterion malformed or invalid: {note}")

    have_marks = have_cgpa = None
    marks_note = cgpa_note = None
    if not _is_empty(prof_marks):
        have_marks, marks_note = _parse_percentage(prof_marks)
    if not _is_empty(prof_cgpa):
        have_cgpa, cgpa_note = _parse_cgpa(prof_cgpa)

    def mark_result():
        if need_marks is None:
            return "unknown"
        if _is_empty(prof_marks):
            return "missing"
        if have_marks is None:
            return "unknown"
        return "pass" if have_marks >= need_marks else "fail"

    def cgpa_result():
        if need_cgpa is None:
            return "unknown"
        if _is_empty(prof_cgpa):
            return "missing"
        if have_cgpa is None:
            return "unknown"
        return "pass" if have_cgpa >= need_cgpa else "fail"

    if logic == "or" and need_marks is not None and need_cgpa is not None:
        results = [mark_result(), cgpa_result()]
        if "pass" in results:
            parts = []
            if have_marks is not None and need_marks is not None and have_marks >= need_marks:
                parts.append(f"marks {have_marks:g}%")
            if have_cgpa is not None and need_cgpa is not None and have_cgpa >= need_cgpa:
                parts.append(f"CGPA {have_cgpa:g}")
            criteria_passes.append(
                f"Grade requirement met ({', '.join(parts)}; required {need_marks:g}% or CGPA {need_cgpa:g})"
            )
        elif all(r == "fail" for r in results):
            hard_fails.append(
                f"Grade requirement not met: required {need_marks:g}% or CGPA {need_cgpa:g}; "
                f"profile has marks {prof_marks if have_marks is not None else 'invalid'} and "
                f"CGPA {prof_cgpa if have_cgpa is not None else 'invalid'}"
            )
        else:
            if "missing" in results:
                if mark_result() == "missing":
                    missing.append("academic marks (percentage) are not provided in profile")
                if cgpa_result() == "missing":
                    missing.append("CGPA/GPA is not provided in profile")
            if marks_note and not _is_empty(prof_marks) and have_marks is None:
                missing.append(f"profile marks value is malformed or invalid: {marks_note}")
            if cgpa_note and not _is_empty(prof_cgpa) and have_cgpa is None:
                missing.append(f"profile CGPA value is malformed or invalid: {cgpa_note}")
        return

    # AND (or a single requirement)
    if need_marks is not None:
        if _is_empty(prof_marks):
            missing.append("academic marks are required but not provided in profile (minimum_marks)")
        elif have_marks is None:
            missing.append(f"profile marks value is malformed or invalid: {marks_note}")
        elif have_marks < need_marks:
            hard_fails.append(f"Academic marks {have_marks:g} below minimum required {need_marks:g}")
        else:
            criteria_passes.append(f"Academic marks {have_marks:g} meet minimum {need_marks:g}")

    if need_cgpa is not None:
        if _is_empty(prof_cgpa):
            missing.append("CGPA/GPA is required but not provided in profile (minimum_cgpa)")
        elif have_cgpa is None:
            missing.append(f"profile CGPA value is malformed or invalid: {cgpa_note}")
        elif have_cgpa < need_cgpa:
            hard_fails.append(f"CGPA {have_cgpa:g} below minimum required {need_cgpa:g}")
        else:
            criteria_passes.append(f"CGPA {have_cgpa:g} meets minimum {need_cgpa:g}")


def check_eligibility(profile, scholarship, today=None):
    """
    Evaluate whether a student meets a scholarship's eligibility.

    ``scholarship`` may be a raw database row: narrative workbook fields in
    ``raw_data`` are converted to structured criteria first (see
    ``src.criteria``).

    Returns:
        dict with eligibility_status ('eligible', 'potentially_eligible',
        'insufficient_information', 'ineligible'), reasons,
        missing_information, manual_checks, notes.
    """
    profile = _as_dict(profile)
    scholarship = enrich_record(_as_dict(scholarship))
    if today is None:
        today = date.today()
    elif isinstance(today, datetime):
        today = today.date()

    missing = []
    hard_fails = []
    criteria_passes = []      # student-specific checks that passed
    availability_passes = []  # status/deadline checks (say nothing about the student)
    manual_checks = [str(c) for c in (scholarship.get("manual_checks") or []) if str(c).strip()]
    notes = [str(n) for n in (scholarship.get("criteria_notes") or []) if str(n).strip()]

    # --- Nationality ---
    _check_nationality(get_value(scholarship, ["nationality"]), get_value(profile, ["nationality"]),
                       criteria_passes, hard_fails, missing)

    # --- Age ---
    sch_min_age = get_value(scholarship, ["minimum_age", "min_age"])
    sch_max_age = get_value(scholarship, ["maximum_age", "max_age"])
    prof_age = get_value(profile, ["age"])
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
               get_value(scholarship, ["education_level", "education level", "level"]),
               get_value(profile, ["education_level", "education level", "level"]),
               _canon_edu, _WILDCARDS["education"], criteria_passes, hard_fails, missing,
               "Education level", "education_level")
    _check_set("qualification", get_value(scholarship, ["qualification"]),
               get_value(profile, ["qualification"]), _canon_qual, _WILDCARDS["education"],
               criteria_passes, hard_fails, missing, "Qualification", "qualification")
    prof_field = get_value(profile, ["field_of_study", "field", "discipline", "major", "subject"])
    _check_field(get_value(scholarship, ["field_of_study", "field", "fields"]), prof_field,
                 criteria_passes, hard_fails, missing)

    # --- Minimum marks / CGPA (OR alternatives supported) ---
    _eval_grade_requirement(scholarship, profile, criteria_passes, hard_fails, missing)

    # --- Household income ---
    sch_income = get_value(scholarship, ["household_income_limit", "income_limit", "household_income"])
    if not _is_empty(sch_income):
        prof_income = get_value(profile, ["household_income", "income", "family_income"])
        if _is_empty(prof_income):
            missing.append("household_income is required but not provided in profile")
        else:
            limit, lim_period, lim_note = _parse_income(sch_income)
            have, have_period, have_note = _parse_income(prof_income)
            if lim_period is None:
                lim_period = _clean_text(scholarship.get("income_period")) or None
            if have_period is None:
                have_period = _clean_text(profile.get("income_period")) or None
            if limit is None:
                missing.append(f"scholarship household_income_limit criterion malformed or invalid: {lim_note}")
            elif have is None:
                missing.append(f"profile household_income value is malformed or invalid: {have_note}")
            elif lim_period and have_period and lim_period != have_period:
                # Compare on a monthly basis to keep the check useful.
                factor = {"month": 12, "year": 1}
                monthly_limit = limit / factor[lim_period]
                monthly_have = have / factor[have_period]
                if monthly_have > monthly_limit:
                    hard_fails.append(
                        f"Household income {have:g}/{have_period} exceeds limit "
                        f"{limit:g}/{lim_period}"
                    )
                else:
                    criteria_passes.append(
                        f"Household income {have:g}/{have_period} within limit {limit:g}/{lim_period}"
                    )
            elif lim_period and not have_period:
                missing.append("household income could not be compared: monthly vs annual period is unclear")
            elif have > limit:
                hard_fails.append(f"Household income {have:g} exceeds limit {limit:g}")
            else:
                criteria_passes.append(f"Household income {have:g} within limit {limit:g}")

    # --- Geography ---
    _check_set("province", get_value(scholarship, ["eligible_provinces", "eligible_province", "province"]),
               get_value(profile, ["province"]), _canon_province, _WILDCARDS["province"],
               criteria_passes, hard_fails, missing, "Province", "province")
    _check_set("city", get_value(scholarship, ["eligible_cities", "eligible_city", "cities", "city"]),
               get_value(profile, ["city"]), _canon_exact, _WILDCARDS["city"],
               criteria_passes, hard_fails, missing, "City", "city")

    # Domicile: use an explicit domicile when given, otherwise fall back to the
    # student's province (a Sindh student cannot hold a KP domicile). The
    # fallback is reported in ``notes`` so nothing is silently assumed.
    sch_dom = get_value(scholarship, ["eligible_domiciles", "eligible_domicile", "domiciles", "domicile"])
    prof_dom = get_value(profile, ["domicile"])
    prof_prov = get_value(profile, ["province"])
    if not _is_empty(sch_dom):
        if not _is_empty(prof_dom):
            _check_set("domicile", sch_dom, prof_dom, _canon_domicile, _WILDCARDS["domicile"],
                       criteria_passes, hard_fails, missing, "Domicile", "domicile")
        elif not _is_empty(prof_prov):
            before_fail = len(hard_fails)
            before_pass = len(criteria_passes)
            _check_set("domicile", sch_dom, prof_prov, _canon_domicile, _WILDCARDS["domicile"],
                       criteria_passes, hard_fails, missing, "Domicile", "domicile")
            if len(hard_fails) > before_fail:
                notes.append(
                    "Domicile was assumed to be your province. If you hold the required "
                    "domicile of another province, update your profile."
                )
            elif len(criteria_passes) > before_pass:
                criteria_passes.append(
                    f"Domicile requirement ({sch_dom}) matches your province {prof_prov} "
                    "(add a separate domicile for precision)"
                )
        else:
            missing.append("domicile/province is required but not provided in profile")

    # --- Study destination ---
    _check_destination(get_value(scholarship, ["study_destination", "destination"]),
                       get_value(profile, ["study_destination", "destination"]),
                       criteria_passes, hard_fails, missing)

    # --- Gender ---
    _check_gender(get_value(scholarship, ["gender"]), get_value(profile, ["gender"]),
                  criteria_passes, hard_fails, missing)

    # --- Status ---
    sch_status = get_value(scholarship, ["status"])
    if not _is_empty(sch_status):
        ns = _clean_text(sch_status)
        if ns in _CLOSED_STATUSES:
            hard_fails.append(f"Scholarship status indicates not open: {sch_status}")
        elif ns in _OPEN_STATUSES:
            availability_passes.append(f"Scholarship status is open: {sch_status}")
        elif ns in _NOT_YET_OPEN_STATUSES:
            missing.append(f"application cycle is not announced yet (status: {sch_status})")
        else:
            missing.append(f"scholarship status '{sch_status}' is not recognised; cannot confirm applications are open")

    # --- Deadline ---
    sch_deadline = get_value(scholarship, ["deadline", "due_date", "closing_date", "last_date"])
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
    elif manual_checks:
        # Everything we can check passed, but the source also has a condition
        # that only the student can confirm. Never claim full eligibility.
        status = "potentially_eligible"
        reasons = criteria_passes + availability_passes
    elif not criteria_passes:
        status = "insufficient_information"
        reasons = availability_passes
        missing.append("no eligibility criteria are recorded for this scholarship; eligibility cannot be confirmed")
    else:
        status = "eligible"
        reasons = criteria_passes + availability_passes

    return {
        "eligibility_status": status,
        "reasons": _dedupe(reasons),
        "missing_information": _dedupe(missing),
        "manual_checks": _dedupe(manual_checks),
        "notes": _dedupe(notes),
        "blocking_issues": _dedupe(hard_fails),
    }


def _dedupe(items):
    seen, out = set(), []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
