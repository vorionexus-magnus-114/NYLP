"""
Student profile extraction, validation, normalization and presentation.

The profile is the single source of truth for matching. Values are stored in a
small canonical vocabulary (the same one the eligibility engine understands) so
that a value chosen in the UI, typed in chat or read from a workbook all mean
the same thing.
"""

import re

from src.criteria import LEVEL_LABELS

VALID_PROVINCES = [
    "Punjab",
    "Sindh",
    "Khyber Pakhtunkhwa",
    "Balochistan",
    "Islamabad Capital Territory",
    "Gilgit-Baltistan",
    "Azad Jammu & Kashmir",
]

PROVINCE_ALIAS_MAP = {
    "punjab": "Punjab",
    "sindh": "Sindh",
    "sind": "Sindh",
    "kpk": "Khyber Pakhtunkhwa",
    "kp": "Khyber Pakhtunkhwa",
    "khyber pakhtunkhwa": "Khyber Pakhtunkhwa",
    "khyber pukhtunkhwa": "Khyber Pakhtunkhwa",
    "balochistan": "Balochistan",
    "baluchistan": "Balochistan",
    "ict": "Islamabad Capital Territory",
    "islamabad": "Islamabad Capital Territory",
    "gb": "Gilgit-Baltistan",
    "gilgit baltistan": "Gilgit-Baltistan",
    "gilgit": "Gilgit-Baltistan",
    "ajk": "Azad Jammu & Kashmir",
    "azad kashmir": "Azad Jammu & Kashmir",
    "azad jammu & kashmir": "Azad Jammu & Kashmir",
    "azad jammu and kashmir": "Azad Jammu & Kashmir",
}

CITY_PROVINCE_MAP = {
    "karachi": "Sindh",
    "hyderabad": "Sindh",
    "sukkur": "Sindh",
    "larkana": "Sindh",
    "nawabshah": "Sindh",
    "mirpur khas": "Sindh",
    "lahore": "Punjab",
    "faisalabad": "Punjab",
    "multan": "Punjab",
    "rawalpindi": "Punjab",
    "gujranwala": "Punjab",
    "sialkot": "Punjab",
    "bahawalpur": "Punjab",
    "peshawar": "Khyber Pakhtunkhwa",
    "abbottabad": "Khyber Pakhtunkhwa",
    "mardan": "Khyber Pakhtunkhwa",
    "swat": "Khyber Pakhtunkhwa",
    "kohat": "Khyber Pakhtunkhwa",
    "quetta": "Balochistan",
    "gwadar": "Balochistan",
    "turbat": "Balochistan",
    "khuzdar": "Balochistan",
    "gilgit": "Gilgit-Baltistan",
    "skardu": "Gilgit-Baltistan",
    "muzaffarabad": "Azad Jammu & Kashmir",
    "mirpur": "Azad Jammu & Kashmir",
}

# Canonical education levels, friendly labels and accepted spellings. The
# canonical tokens are what the matcher stores and compares.
EDUCATION_LEVELS = ["matric", "intermediate", "vocational", "bachelor", "master", "phd"]

EDUCATION_ALIASES = {
    "matriculation / o-level": "matric",
    "matriculation": "matric",
    "matric": "matric",
    "o level": "matric",
    "o-level": "matric",
    "o levels": "matric",
    "ssc": "matric",
    "10th": "matric",
    "intermediate / a-level": "intermediate",
    "intermediate": "intermediate",
    "fsc": "intermediate",
    "f.sc": "intermediate",
    "fa": "intermediate",
    "ics": "intermediate",
    "hssc": "intermediate",
    "a level": "intermediate",
    "a-level": "intermediate",
    "12th": "intermediate",
    "diploma / vocational (dae)": "vocational",
    "vocational": "vocational",
    "dae": "vocational",
    "diploma": "vocational",
    "undergraduate / bachelors": "bachelor",
    "undergraduate": "bachelor",
    "bachelors": "bachelor",
    "bachelor": "bachelor",
    "bs": "bachelor",
    "bsc": "bachelor",
    "ba": "bachelor",
    "bba": "bachelor",
    "postgraduate / masters": "master",
    "postgraduate": "master",
    "masters": "master",
    "master": "master",
    "ms": "master",
    "msc": "master",
    "mphil": "master",
    "m.phil": "master",
    "ma": "master",
    "mba": "master",
    "doctoral / phd": "phd",
    "doctoral": "phd",
    "doctorate": "phd",
    "phd": "phd",
    "ph.d": "phd",
}

DESTINATION_ALIASES = {
    "pakistan": "Pakistan",
    "pakistani": "Pakistan",
    "pak": "Pakistan",
    "pk": "Pakistan",
    "abroad": "Abroad",
    "foreign": "Abroad",
    "international": "Abroad",
    "overseas": "Abroad",
    "both": None,
    "any": None,
    "anywhere": None,
    "all": None,
    "no preference": None,
}

EDUCATION_DISPLAY = dict(LEVEL_LABELS)


def _norm_text(value):
    s = str(value).strip().lower()
    s = s.replace("\u2019", "'").replace("_", " ").replace("-", " ")
    return " ".join(s.split())


def _has_word(text, word):
    if len(word) <= 3:
        return re.search(rf"\b{re.escape(word)}\b", text) is not None
    return re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", text) is not None


def canonical_education(value):
    """Map a label or alias to a canonical level token (or None)."""
    if value is None:
        return None
    norm = _norm_text(value)
    if not norm:
        return None
    if norm in EDUCATION_ALIASES:
        return EDUCATION_ALIASES[norm]
    for alias, canonical in sorted(EDUCATION_ALIASES.items(), key=lambda kv: -len(kv[0])):
        if _has_word(norm, alias):
            return canonical
    return None


def education_label(value):
    token = canonical_education(value) or value
    return EDUCATION_DISPLAY.get(token, str(value) if value else "—")


def canonical_destination(value):
    if value is None:
        return None
    norm = _norm_text(value)
    if not norm:
        return None
    if norm in DESTINATION_ALIASES:
        return DESTINATION_ALIASES[norm]
    if "both" in norm or "any" in norm:
        return None
    if "pakistan" in norm and "abroad" not in norm:
        return "Pakistan"
    if any(word in norm for word in ("abroad", "foreign", "international", "overseas")):
        return "Abroad"
    # A named country means study outside Pakistan.
    return "Abroad"


def canonical_income_period(value):
    norm = _norm_text(value) if value is not None else ""
    if norm in ("month", "monthly", "per month", "pm"):
        return "month"
    if norm in ("year", "yearly", "annual", "annually", "per year", "pa"):
        return "year"
    return None


def _to_float(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    text = str(value).replace(",", "").strip().rstrip("%").strip()
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def normalize_profile(raw_profile):
    """Validate and normalize profile fields into a clean canonical dictionary."""
    if not raw_profile or not isinstance(raw_profile, dict):
        raw_profile = {}

    profile = {
        "nationality": "Pakistani",
        "current_country": "Pakistan",
        "age": None,
        "province": None,
        "domicile": None,
        "city": None,
        "education_level": None,
        "qualification": None,
        "marks": None,
        "cgpa": None,
        "field_of_study": None,
        "study_destination": None,
        "household_income": None,
        "income_period": None,
        "language_test": None,
        "gender": None,
    }

    def str_val(key):
        val = raw_profile.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
        return None

    age_raw = raw_profile.get("age")
    if age_raw is not None:
        m = re.search(r"\d+", str(age_raw))
        if m:
            age_val = int(m.group(0))
            if 10 <= age_val <= 100:
                profile["age"] = age_val

    nationality = str_val("nationality")
    if nationality:
        profile["nationality"] = nationality

    country = str_val("current_country")
    if country:
        profile["current_country"] = country

    province = str_val("province")
    if province:
        profile["province"] = PROVINCE_ALIAS_MAP.get(province.lower().strip(), province)

    for key in ("domicile", "city"):
        value = str_val(key)
        if value:
            profile[key] = (
                PROVINCE_ALIAS_MAP.get(value.lower().strip(), value)
                if key == "domicile"
                else value
            )
    if profile["province"] is None and profile["city"]:
        inferred = CITY_PROVINCE_MAP.get(_norm_text(profile["city"]))
        if inferred:
            profile["province"] = inferred

    education = str_val("education_level")
    if education:
        profile["education_level"] = canonical_education(education) or education

    qualification = str_val("qualification")
    if qualification:
        profile["qualification"] = canonical_education(qualification) or qualification

    marks = _to_float(raw_profile.get("marks"))
    if marks is not None and 0 <= marks <= 100:
        profile["marks"] = marks

    cgpa = _to_float(raw_profile.get("cgpa"))
    if cgpa is not None and 0 <= cgpa <= 4.0:
        profile["cgpa"] = cgpa

    field = str_val("field_of_study")
    if field:
        profile["field_of_study"] = field

    destination = canonical_destination(str_val("study_destination"))
    if destination:
        profile["study_destination"] = destination

    income = _to_float(raw_profile.get("household_income"))
    if income is not None and income >= 0:
        profile["household_income"] = income
        period = canonical_income_period(raw_profile.get("income_period"))
        if period:
            profile["income_period"] = period

    language = str_val("language_test")
    if language:
        profile["language_test"] = language

    gender = _norm_text(raw_profile.get("gender") or "")
    if gender in ("female", "f", "woman", "girl"):
        profile["gender"] = "female"
    elif gender in ("male", "m", "man", "boy"):
        profile["gender"] = "male"

    return profile


_MARKS_PERCENT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*(?:%|percent\b|percentage\b)", re.I)
_MARKS_LABEL_RE = re.compile(
    r"\b(?:percentage|marks|score|obtained\s+marks|result)\s*(?:is|are|was|:|=)?\s*(\d{1,3}(?:\.\d+)?)\b",
    re.I,
)
_MARKS_FRACTION_RE = re.compile(r"\b(\d{2,4})\s*(?:/|out\s+of)\s*(\d{2,4})\b", re.I)
_CGPA_LABEL_RE = re.compile(r"\b(?:cgpa|gpa)\s*(?:is|was|:|=|of)?\s*(\d(?:\.\d{1,2})?)\b", re.I)
_CGPA_SUFFIX_RE = re.compile(r"\b(\d(?:\.\d{1,2}))\s*(?:cgpa|gpa)\b", re.I)
_AGE_RE = re.compile(
    r"\b(?:i\s+am|i'm|age(?:\s+is)?|aged)\s*[:=]?\s*(\d{1,2})\s*(?:years?|yrs?|y\.?o\.?)?\b",
    re.I,
)
_FIELD_RE = re.compile(
    r"\b(?:field\s+of\s+study|field|major(?:ing)?\s+in|major|discipline|studying|study|subject)\s*"
    r"(?:is|:|=|in)?\s*([a-z][a-z &/+.-]{2,45})",
    re.I,
)
_DEGREE_FIELD_RE = re.compile(
    r"\b(?:bs|bsc|ba|bba|ms|msc|mba|mphil|phd|be|btech)\s+(?:in\s+)?([a-z][a-z &/+.-]{2,45})",
    re.I,
)
_INCOME_RE = re.compile(
    r"\b(?:household\s+)?(?:income|salary|earning[s]?)\s*(?:is|:|=|of)?\s*"
    r"(?:pkr|rs\.?|rupees?)?\s*([\d][\d,]{2,})\s*(k|lakh|lac|m|million)?\b",
    re.I,
)


def _clean_capture(text):
    text = re.split(r"[,.;|]|\band\b|\bwith\b|\bfrom\b|\bat\b", text, maxsplit=1, flags=re.I)[0]
    return " ".join(text.split()).strip()


def extract_profile_from_text(text, current_profile=None):
    """Rule-based heuristic profile extraction from natural language."""
    updated = normalize_profile(current_profile)
    if not text or not isinstance(text, str):
        return updated
    text_lower = text.lower()

    age_match = _AGE_RE.search(text_lower)
    if age_match:
        age_val = int(age_match.group(1))
        if 10 <= age_val <= 100:
            updated["age"] = age_val

    # A province is only taken from a self-description ("I am from Sindh",
    # "my domicile is Punjab") or from a short statement - never from a
    # question such as "is the KPK scholarship open?".
    self_describing = bool(re.search(
        r"(?:i\s+am|i'm|i\s+live|live\s+in|living\s+in|from|belong\s+to|"
        r"domicile|resident\s+of|province\s+(?:is|:)|based\s+in|hail\s+from)",
        text_lower,
    ))
    is_question = "?" in text_lower or bool(re.search(
        r"\b(scholarship|scholarships|eligible|eligibility|deadline|apply|application|"
        r"requirements?|which|show|find|list)\b", text_lower,
    ))
    allow_location = self_describing or (len(text_lower) <= 60 and not is_question)

    if allow_location:
        for key, canonical in PROVINCE_ALIAS_MAP.items():
            if _has_word(text_lower, key):
                updated["province"] = canonical
                break

        for city, province in CITY_PROVINCE_MAP.items():
            if _has_word(text_lower, city):
                updated["city"] = city.title()
                if not updated.get("province"):
                    updated["province"] = province
                break

    for alias, canonical in sorted(EDUCATION_ALIASES.items(), key=lambda kv: -len(kv[0])):
        if _has_word(text_lower, alias):
            updated["education_level"] = canonical
            break

    # Grades are only taken from a statement about the student, not from a
    # question about a scholarship's requirement.
    first_person = bool(re.search(
        r"\b(i\s+got|i\s+have|i\s+scored|i\s+obtained|i\s+secured|"
        r"my\s+(?:marks|percentage|cgpa|gpa|result)|\bi\b|i'm|\bmy\b|\bme\b)",
        text_lower,
    ))
    if first_person or not is_question:
        cgpa = _CGPA_LABEL_RE.search(text_lower) or _CGPA_SUFFIX_RE.search(text_lower)
        if cgpa:
            value = float(cgpa.group(1))
            if 0 < value <= 4.0:
                updated["cgpa"] = value

        marks = _MARKS_PERCENT_RE.search(text_lower) or _MARKS_LABEL_RE.search(text_lower)
        if marks:
            value = float(marks.group(1))
            if 0 < value <= 100:
                updated["marks"] = value
        else:
            frac = _MARKS_FRACTION_RE.search(text_lower)
            if frac:
                got, total = float(frac.group(1)), float(frac.group(2))
                if total > 10 and got <= total:
                    updated["marks"] = round(got / total * 100, 2)

    field_match = _FIELD_RE.search(text_lower) or _DEGREE_FIELD_RE.search(text_lower)
    if field_match:
        candidate = _clean_capture(field_match.group(1))
        candidate = re.sub(r"\b(from|in|at)\s+\w+$", "", candidate).strip()
        if (
            candidate
            and len(candidate) >= 3
            and canonical_education(candidate) is None
            and candidate.lower() not in {
                "abroad", "overseas", "foreign", "international", "pakistan",
                "in pakistan", "in the pakistan", "the pakistan",
            }
        ):
            updated["field_of_study"] = candidate.title()

    if re.search(r"\bfemale\b|\bwoman\b|\bgirl\b", text_lower):
        updated["gender"] = "female"
    elif re.search(r"\bmale\b|\bman\b|\bboy\b", text_lower):
        updated["gender"] = "male"

    income = _INCOME_RE.search(text_lower)
    if income:
        amount = float(income.group(1).replace(",", ""))
        unit = (income.group(2) or "").lower()
        if unit == "k":
            amount *= 1_000
        elif unit in ("lakh", "lac"):
            amount *= 100_000
        elif unit in ("m", "million"):
            amount *= 1_000_000
        updated["household_income"] = amount
        if re.search(r"\bmonthly\b|\bper month\b|\ba month\b", text_lower):
            updated["income_period"] = "month"
        elif re.search(r"\bannual\b|\byearly\b|\bper year\b", text_lower):
            updated["income_period"] = "year"

    if re.search(
        r"(?:want|wish|plan|prefer|interested|looking|hope)\s+to\s+(?:study|go|apply)\s+"
        r"(?:abroad|overseas|foreign)|study\s+(?:abroad|overseas)|destination\s+(?:is\s+)?(?:abroad|overseas)",
        text_lower,
    ):
        updated["study_destination"] = "Abroad"
    elif re.search(
        r"(?:want|wish|plan|prefer|interested|looking|hope)\s+to\s+study\s+in\s+pakistan|"
        r"study\s+in\s+pakistan|destination\s+(?:is\s+)?pakistan",
        text_lower,
    ):
        updated["study_destination"] = "Pakistan"

    return updated


def get_missing_profile_fields(profile):
    """Key fields that are missing and would improve matching."""
    p = normalize_profile(profile)
    missing = []

    if not p.get("education_level"):
        missing.append("Education Level (e.g. Bachelors, Intermediate, Masters)")
    if p.get("marks") is None and p.get("cgpa") is None:
        missing.append("Academic Grade (Percentage % or CGPA)")
    if not p.get("field_of_study"):
        missing.append("Field of Study / Subject (e.g. Computer Science, Engineering)")
    if not p.get("province"):
        missing.append("Province / Region (e.g. Punjab, Sindh, KPK)")
    if p.get("age") is None:
        missing.append("Age")

    return missing


def profile_completeness(profile):
    """0-100 completeness score for the profile card."""
    checks = [
        bool(profile.get("education_level")),
        profile.get("marks") is not None or profile.get("cgpa") is not None,
        bool(profile.get("field_of_study")),
        bool(profile.get("province")),
        profile.get("age") is not None,
        bool(profile.get("study_destination")),
    ]
    return int(round(sum(1 for c in checks if c) / len(checks) * 100))


def summarize_profile(profile):
    """Clear human-readable summary of the current profile."""
    p = normalize_profile(profile)
    parts = []

    if p.get("education_level"):
        parts.append(f"Level: {education_label(p['education_level'])}")
    if p.get("qualification"):
        parts.append(f"Qualification: {p['qualification']}")
    if p.get("field_of_study"):
        parts.append(f"Field: {p['field_of_study']}")
    if p.get("cgpa") is not None:
        parts.append(f"CGPA: {p['cgpa']}")
    if p.get("marks") is not None:
        parts.append(f"Marks: {p['marks']}%")

    if p.get("province"):
        parts.append(f"Province: {p['province']}")
    if p.get("city"):
        parts.append(f"City: {p['city']}")
    if p.get("age"):
        parts.append(f"Age: {p['age']}")
    if p.get("gender"):
        parts.append(f"Gender: {p['gender']}")

    parts.append(f"Destination: {p.get('study_destination') or 'Any'}")

    if p.get("household_income") is not None:
        period = p.get("income_period")
        suffix = f"/{period}" if period else ""
        parts.append(f"Income: PKR {p['household_income']:,.0f}{suffix}")

    return " | ".join(parts) if parts else "No profile details collected yet."
