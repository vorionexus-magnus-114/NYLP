"""
Student Profile extraction, validation, normalization, and state management.
"""

import re

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
    "ajk": "Azad Jammu & Kashmir",
    "azad kashmir": "Azad Jammu & Kashmir",
    "azad jammu & kashmir": "Azad Jammu & Kashmir",
    "azad jammu and kashmir": "Azad Jammu & Kashmir",
}

EDUCATION_LEVEL_MAP = {
    "matric": "Matriculation / O-Level",
    "matriculation": "Matriculation / O-Level",
    "o level": "Matriculation / O-Level",
    "o-level": "Matriculation / O-Level",
    "ssc": "Matriculation / O-Level",
    "intermediate": "Intermediate / A-Level",
    "fsc": "Intermediate / A-Level",
    "fa": "Intermediate / A-Level",
    "ics": "Intermediate / A-Level",
    "a level": "Intermediate / A-Level",
    "a-level": "Intermediate / A-Level",
    "hssc": "Intermediate / A-Level",
    "undergraduate": "Undergraduate / Bachelors",
    "bachelors": "Undergraduate / Bachelors",
    "bachelor": "Undergraduate / Bachelors",
    "bs": "Undergraduate / Bachelors",
    "bsc": "Undergraduate / Bachelors",
    "bba": "Undergraduate / Bachelors",
    "postgraduate": "Postgraduate / Masters",
    "masters": "Postgraduate / Masters",
    "master": "Postgraduate / Masters",
    "ms": "Postgraduate / Masters",
    "msc": "Postgraduate / Masters",
    "mphil": "Postgraduate / Masters",
    "doctoral": "Doctoral / PhD",
    "phd": "Doctoral / PhD",
    "doctorate": "Doctoral / PhD",
}


def normalize_profile(raw_profile):
    """
    Validate and normalize profile fields into a clean canonical dictionary.
    """
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
        "study_destination": "Pakistan",
        "household_income": None,
        "language_test": None,
    }

    # Helper cleaners
    def str_val(key):
        val = raw_profile.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
        return None

    # Age
    age_raw = raw_profile.get("age")
    if age_raw is not None:
        try:
            m = re.search(r"\d+", str(age_raw))
            if m:
                age_val = int(m.group(0))
                if 10 <= age_val <= 100:
                    profile["age"] = age_val
        except (ValueError, TypeError):
            pass

    # Nationality
    nat = str_val("nationality")
    if nat:
        profile["nationality"] = nat

    # Country
    cc = str_val("current_country")
    if cc:
        profile["current_country"] = cc

    # Province
    prov = str_val("province")
    if prov:
        prov_norm = prov.lower().strip()
        if prov_norm in PROVINCE_ALIAS_MAP:
            profile["province"] = PROVINCE_ALIAS_MAP[prov_norm]
        elif prov in VALID_PROVINCES:
            profile["province"] = prov
        else:
            profile["province"] = prov

    # Domicile & City
    dom = str_val("domicile")
    if dom:
        profile["domicile"] = dom
    city = str_val("city")
    if city:
        profile["city"] = city

    # Education Level
    edu = str_val("education_level")
    if edu:
        edu_norm = edu.lower().strip()
        matched = None
        for key, canonical in EDUCATION_LEVEL_MAP.items():
            if key in edu_norm:
                matched = canonical
                break
        profile["education_level"] = matched or edu

    # Qualification
    qual = str_val("qualification")
    if qual:
        profile["qualification"] = qual

    # Marks vs CGPA
    marks_raw = raw_profile.get("marks")
    cgpa_raw = raw_profile.get("cgpa")

    if marks_raw is not None:
        try:
            m_val = float(marks_raw)
            if 0 <= m_val <= 100:
                profile["marks"] = m_val
        except (ValueError, TypeError):
            pass

    if cgpa_raw is not None:
        try:
            c_val = float(cgpa_raw)
            if 0 <= c_val <= 4.0:
                profile["cgpa"] = c_val
        except (ValueError, TypeError):
            pass

    # Field of study
    field = str_val("field_of_study")
    if field:
        profile["field_of_study"] = field

    # Destination
    dest = str_val("study_destination")
    if dest:
        d_lower = dest.lower()
        if "abroad" in d_lower and "pakistan" in d_lower:
            profile["study_destination"] = "Both"
        elif "abroad" in d_lower or "international" in d_lower:
            profile["study_destination"] = "Abroad"
        elif "both" in d_lower:
            profile["study_destination"] = "Both"
        else:
            profile["study_destination"] = "Pakistan"

    # Household Income
    inc_raw = raw_profile.get("household_income")
    if inc_raw is not None:
        try:
            inc_val = float(inc_raw)
            if inc_val >= 0:
                profile["household_income"] = inc_val
        except (ValueError, TypeError):
            pass

    # Language test
    lang = str_val("language_test")
    if lang:
        profile["language_test"] = lang

    return profile


def extract_profile_from_text(text, current_profile=None):
    """
    Rule-based heuristic profile extraction from natural text input.
    Can be merged with previous current_profile.
    """
    updated = normalize_profile(current_profile)
    text_lower = text.lower()

    # Extract Age
    age_match = re.search(r"\b(i am|i'm|age is|age)\s*(\d{2})\s*(years|yrs|years old)?\b", text_lower)
    if age_match:
        age_val = int(age_match.group(2))
        if 10 <= age_val <= 100:
            updated["age"] = age_val

    # Extract Province
    for key, canonical in PROVINCE_ALIAS_MAP.items():
        if re.search(rf"\b{re.escape(key)}\b", text_lower):
            updated["province"] = canonical
            break

    # Extract Education Level / Qualification
    for key, canonical in EDUCATION_LEVEL_MAP.items():
        if re.search(rf"\b{re.escape(key)}\b", text_lower):
            updated["education_level"] = canonical
            break

    # Extract Marks (% vs CGPA)
    marks_match = re.search(r"(\d{2}(?:\.\d+)?)\s*(%|percent|percentage)", text_lower)
    if marks_match:
        m_val = float(marks_match.group(1))
        if 0 <= m_val <= 100:
            updated["marks"] = m_val

    cgpa_match = re.search(r"\b(cgpa|gpa)\s*(?:is|=|:)?\s*([0-3]\.\d{1,2}|4\.0)\b", text_lower)
    if cgpa_match:
        c_val = float(cgpa_match.group(2))
        if 0 <= c_val <= 4.0:
            updated["cgpa"] = c_val

    # Extract Destination
    if "abroad" in text_lower or "foreign" in text_lower or "international" in text_lower:
        if "pakistan" in text_lower:
            updated["study_destination"] = "Both"
        else:
            updated["study_destination"] = "Abroad"
    elif "pakistan" in text_lower and "both" not in text_lower:
        updated["study_destination"] = "Pakistan"

    return updated


def get_missing_profile_fields(profile):
    """
    Return list of key fields that are missing and would improve matching.
    """
    p = normalize_profile(profile)
    missing = []

    if not p.get("education_level"):
        missing.append("Education Level (e.g. Bachelors, Masters, Intermediate)")
    if p.get("marks") is None and p.get("cgpa") is None:
        missing.append("Academic Grade (Percentage % or CGPA)")
    if not p.get("field_of_study"):
        missing.append("Field of Study / Subject (e.g. Computer Science, Engineering)")
    if not p.get("province"):
        missing.append("Province / Region (e.g. Punjab, Sindh, KPK)")
    if p.get("age") is None:
        missing.append("Age")

    return missing


def summarize_profile(profile):
    """
    Return a clear human-readable string summarizing the current profile.
    """
    p = normalize_profile(profile)
    parts = []

    if p.get("education_level"):
        parts.append(f"Level: {p['education_level']}")
    if p.get("qualification"):
        parts.append(f"Qualification: {p['qualification']}")
    if p.get("field_of_study"):
        parts.append(f"Field: {p['field_of_study']}")
    if p.get("cgpa") is not None:
        parts.append(f"CGPA: {p['cgpa']}")
    elif p.get("marks") is not None:
        parts.append(f"Marks: {p['marks']}%")

    if p.get("province"):
        parts.append(f"Province: {p['province']}")
    if p.get("city"):
        parts.append(f"City: {p['city']}")
    if p.get("age"):
        parts.append(f"Age: {p['age']}")

    parts.append(f"Destination: {p.get('study_destination', 'Pakistan')}")

    if not parts:
        return "No profile details collected yet."

    return " | ".join(parts)
