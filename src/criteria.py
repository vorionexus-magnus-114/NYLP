"""
Derive machine-checkable eligibility criteria from the curated workbook.

The master workbook stores most eligibility rules as prose (``province_region``,
``domicile_rule``, ``minimum_grade``, ``income_threshold``, ``target_group`` …).
The importer keeps every source field in ``raw_data``; this module converts the
parts that can be checked reliably into structured values, and records the
remainder as *manual checks* so the student is told what to confirm with the
sponsor instead of being silently told "eligible".

Design rules
------------
* Only well-understood prose is converted. Anything ambiguous stays a manual
  check / note - it is never guessed into a pass.
* A derived value never overwrites a value that the workbook supplied in a
  structured column, except ``education_level`` where the raw ``study_level``
  text ("Nursery through BS") is not comparable on its own.
* Province and domicile are kept as separate criteria. A domicile rule is never
  silently ignored, and it is never invented when the source says nothing.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache

# --------------------------------------------------------------------------- #
# Canonical vocabulary
# --------------------------------------------------------------------------- #

PROVINCES = (
    "Punjab",
    "Sindh",
    "Khyber Pakhtunkhwa",
    "Balochistan",
    "Islamabad Capital Territory",
    "Gilgit-Baltistan",
    "Azad Jammu & Kashmir",
)

LEVEL_ORDER = ("matric", "intermediate", "vocational", "bachelor", "master", "phd")

LEVEL_LABELS = {
    "matric": "Matriculation / O-Level",
    "intermediate": "Intermediate / A-Level",
    "vocational": "Diploma / Vocational (DAE)",
    "bachelor": "Undergraduate / Bachelors",
    "master": "Postgraduate / Masters",
    "phd": "Doctoral / PhD",
}

_PROVINCE_PATTERNS = (
    ("Sindh", (r"\bsindh\b",)),
    ("Punjab", (r"\bpunjab\b",)),
    (
        "Khyber Pakhtunkhwa",
        (
            r"\bkhyber[\s-]*p(?:a|u)khtunkhwa\b",
            r"\bkpk\b",
            r"\bkp\b",
            r"\bnwfp\b",
            r"\bformer\s+fata\b",
            r"\bfata\b",
            r"\bmerged districts\b",
        ),
    ),
    ("Balochistan", (r"\bbalochistan\b", r"\bbaluchistan\b")),
    ("Gilgit-Baltistan", (r"\bgilgit[\s-]*baltistan\b", r"\bgb\b")),
    ("Azad Jammu & Kashmir", (r"\bajk\b", r"azad\s+jammu", r"azad\s+kashmir")),
    ("Islamabad Capital Territory", (r"\bict\b", r"\bislamabad\b")),
)

_ALL_PAKISTAN_RE = re.compile(
    r"\ball[\s-]*pakistan\b|\bnationwide\b|\ball\s+provinces\b|\bany\s+province\b|"
    r"\ball\s+of\s+pakistan\b|\bpan[\s-]*pakistan\b",
    re.I,
)

_LEVEL_PATTERNS = (
    ("phd", (r"\bph\.?\s?d\b", r"\bdphil\b", r"\bdoctorate?\b", r"\bdoctoral\b")),
    (
        "master",
        (
            r"\bmasters?'?\b",
            r"\bmaster['\u2019]?s\b",
            r"\bmaster\b",
            r"\bms\b",
            r"\bmsc\b",
            r"\bm\.?phil\b",
            r"\bmphil\b",
            r"\bllm\b",
            r"\bmst\b",
            r"\bmba\b",
            r"\bm\.?a\b",
            r"\bpostgraduate\b",
            r"\bgraduate\b",
        ),
    ),
    ("bachelor", (r"\bbachelors?'?\b", r"\bundergraduate\b", r"\bundergrad\b", r"\bbs\b", r"\bbsc\b", r"\bbba\b", r"\bb\.?a\b")),
    (
        "intermediate",
        (r"\bintermediate\b", r"\bhssc\b", r"\bf\.?sc\b", r"\ba[\s-]?levels?\b", r"\bhigher secondary\b"),
    ),
    ("matric", (r"\bmatric(?:ulation)?\b", r"\bssc\b", r"\bo[\s-]?levels?\b", r"\bsecondary\b")),
    ("vocational", (r"\bvocational\b", r"\bdae\b", r"\bdiploma of associate\b", r"\bassociate degree\b", r"\bdiploma\b")),
)

_GRADE_PCT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*(?:%|percent\b)", re.I)
_GRADE_GPA_RE = re.compile(
    r"(?:cgpa|gpa)\s*(?:≥|>=|>|of|:|=|at\s+least|min(?:imum)?)?\s*(\d(?:\.\d{1,2})?)",
    re.I,
)
_GRADE_GPA_SUFFIX_RE = re.compile(r"(\d\.\d{1,2})\s*(?:cgpa|gpa)\b", re.I)

_INCOME_RE = re.compile(
    r"(?:pkr|rs\.?|rupees?|\u20a8)\s*([\d][\d,]*(?:\.\d+)?)\s*(m|million|lakh|lac|k)?\b",
    re.I,
)

_TARGET_MARKERS = (
    "children of",
    "transgender",
    "disabilit",
    "orphan",
    "martyred",
    "martyr",
    "employees",
    "workers",
    "lawyers",
    "quota",
    "reserved",
    "category",
)

_OTHER_MANUAL_MARKERS = (
    "qs top",
    "top-100",
    "top-50",
    "top-300",
    "unconditional offer",
    "government school",
    "top ten",
    "top three",
    "first-class",
    "first class",
    "aptitude",
)

_DEGREE_NOISE = (
    "master of science",
    "master of arts",
    "master of engineering",
    "master of laws",
    "bachelor of science",
    "bachelor of arts",
    "bachelor of engineering",
    "doctor of philosophy",
    "joint master",
    "joint masters",
    "master's",
    "masters",
    "master",
    "bachelor's",
    "bachelors",
    "bachelor",
    "msc",
    "bsc",
    "mphil",
    "m.phil",
    "phd",
    "dphil",
    "programme",
    "program",
    "degree",
    "scholarship",
    "studies",
    "study",
    "course",
    "courses",
    "joint",
    "european",
    "international",
    "fellowship",
    "grant",
    "award",
)

# Words that identify a *field of study* group. A profile field expands to the
# whole group so that e.g. "computer science" also matches "cybersecurity".
_FIELD_GROUPS = {
    "computing": (
        "computer", "computing", "software", "information technology", "it",
        "artificial intelligence", "data science", "cyber", "informatics",
        "machine learning", "programming", "digital",
    ),
    "engineering": (
        "engineering", "engineer", "mechanical", "electrical", "civil",
        "chemical", "industrial", "mechatronics", "aerospace", "materials",
        "nanotechnology", "nanomaterials", "robotics", "manufacturing",
        "nuclear", "structural", "electric", "electronic", "photonics",
        "mechanic", "transportation", "power system", "telecommunication",
    ),
    "health": (
        "medicine", "medical", "health", "mbbs", "nursing", "pharmacy",
        "public health", "physiology", "dental", "bds", "psychology",
        "neuroscience",
    ),
    "business": (
        "business", "management", "commerce", "economics", "finance",
        "accounting", "mba", "entrepreneurship", "marketing",
    ),
    "law": ("law", "legal", "llb", "llm", "jurisprudence"),
    "environment": (
        "environment", "environmental", "climate", "energy",
        "forestry", "agriculture", "food security", "groundwater",
    ),
    "earth": ("geology", "geospatial", "georesources", "geography", "earth", "mining", "petroleum"),
    "media": ("media", "communication", "journalism", "film", "broadcasting"),
    "design": ("architecture", "design", "urban", "planning", "heritage"),
    "education": ("education", "teaching", "pedagogy"),
    "islamic": ("islamic", "islam", "religious", "sharia"),
    "science": (
        "science", "physics", "chemistry", "biology", "mathematics",
        "statistics", "astronomy", "astrophysics", "biochemistry",
        "catalysis",
    ),
    "arts": (
        "arts", "humanities", "literature", "history", "philosophy",
        "languages", "linguistics", "creative writing", "music", "culture",
        "anthropology",
    ),
    "social": (
        "social", "political", "international relations", "public policy",
        "sociology", "migration", "development",
    ),
}

# Generic groups: a match on one of these alone is weak evidence (many
# programmes say "science" or "arts" without meaning the student's subject).
_BROAD_FIELD_GROUPS = {"science", "arts", "social"}

# Words too generic to prove a subject match on their own.
_GENERIC_FIELD_WORDS = {
    "science", "sciences", "arts", "social", "studies", "study", "general",
    "applied", "basic", "research", "technology", "technologies",
}

_FIELD_WILDCARDS = (
    "all", "any", "all fields", "any field", "all disciplines", "any discipline",
    "all subjects", "any subject", "open to all", "no restriction",
)


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

def _text(value):
    if value is None:
        return ""
    return str(value).strip()


def _norm(value):
    s = _text(value).lower()
    s = s.replace("\u2019", "'").replace("\u2018", "'")
    s = s.replace("&", " and ")
    s = re.sub(r"[_\-]+", " ", s)
    return " ".join(s.split())


def _is_blank(value):
    if value is None:
        return True
    if isinstance(value, float) and value != value:  # NaN
        return True
    return isinstance(value, str) and value.strip() == ""


def _load_raw(record):
    raw = record.get("raw_data") if isinstance(record, dict) else None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _uniq(items):
    seen, out = set(), []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


# --------------------------------------------------------------------------- #
# Province / domicile
# --------------------------------------------------------------------------- #

def provinces_in(text):
    """Return the canonical provinces named in ``text`` (order preserved)."""
    t = _norm(text)
    found = []
    for name, patterns in _PROVINCE_PATTERNS:
        if any(re.search(p, t, re.I) for p in patterns):
            found.append(name)
    return found


def is_all_pakistan(text):
    return bool(_ALL_PAKISTAN_RE.search(_text(text)))


def _derive_provinces(raw):
    """(provinces, note) - empty list means nationwide / no restriction."""
    region = _text(raw.get("province_region"))
    geo = _text(raw.get("geographic_scope"))
    domicile = _text(raw.get("domicile_rule"))

    notes = []
    if region and is_all_pakistan(region):
        # "All Pakistan" is an explicit nationwide scope; still honour a
        # province named elsewhere in the row (e.g. all-Pakistan scheme with a
        # domicile restriction).
        provs = provinces_in(domicile) or provinces_in(geo)
        if "preference" in _norm(region):
            notes.append(f"Geographic preference noted in source: {region}")
        return provs, notes

    provs = provinces_in(region)
    if not provs:
        provs = provinces_in(domicile) or provinces_in(geo)
    return provs, notes


def _derive_domicile(raw):
    return provinces_in(raw.get("domicile_rule"))


# --------------------------------------------------------------------------- #
# Education level
# --------------------------------------------------------------------------- #

def _levels_in(text):
    """Infer the set of education levels a ``study_level`` phrase covers."""
    t = _norm(text)
    if not t:
        return []

    # "higher secondary" is an intermediate qualification, not matric.
    without_higher_secondary = t.replace("higher secondary", " ")
    levels = []
    for name, patterns in _LEVEL_PATTERNS:
        haystack = without_higher_secondary if name == "matric" else t
        if any(re.search(p, haystack, re.I) for p in patterns):
            levels.append(name)

    # Explicit school stages imply school-level eligibility.
    if re.search(r"\b(nursery|kindergarten|post primary|under matric|school|college)\b", t):
        levels.extend(["matric", "intermediate"])
    if re.search(r"post[\s-]*matric", t):
        levels.extend(["intermediate"])
    # A bare "degree" programme is an undergraduate/postgraduate degree.
    if not levels and "non degree" not in t and re.search(r"\bdegrees?\b", t):
        levels.extend(["bachelor", "master"])

    # "16 years of education" is a master's degree in Pakistan.
    if re.search(r"\b16\s*years?\b", t):
        levels.append("master")

    # US usage: "graduate" courses/students include doctoral study. A plain
    # word-boundary match deliberately excludes "undergraduate"/"postgraduate".
    if re.search(r"\bgraduate\b", t):
        levels.append("phd")
    # Postdoctoral / PhD-holder awards require a doctorate, never a bachelor's.
    if re.search(r"postdoc|phd[\s-]?holder", t):
        levels.append("phd")
    if "oxford" in t and re.search(r"postgraduate|graduate", t):
        levels.append("phd")

    levels = [lv for lv in LEVEL_ORDER if lv in set(levels)]

    # Ranges: "Nursery through BS", "Matric through MPhil". Vocational awards
    # are never inferred from a range - they must be named explicitly.
    range_order = ["matric", "intermediate", "bachelor", "master", "phd"]
    if re.search(r"\bthrough\b|\bup\s+to\b|\bto\b", t):
        idx = [range_order.index(lv) for lv in levels if lv in range_order]
        if len(idx) >= 2:
            inferred = range_order[min(idx): max(idx) + 1]
            levels = [lv for lv in LEVEL_ORDER if lv in set(levels) | set(inferred)]

    return levels


def _derive_levels(raw):
    text = _text(raw.get("study_level")) or _text(raw.get("education_level"))
    levels = _levels_in(text)
    if levels:
        return levels
    # "First-year BS / MS" style already handled by keywords; anything left is
    # genuinely not a level we can check (research fellowships, visiting
    # scholar awards, "continuing students" and similar).
    return []


# --------------------------------------------------------------------------- #
# Grades and income
# --------------------------------------------------------------------------- #

def _derive_grade(text):
    """Return (minimum_marks, minimum_cgpa, logic) from prose."""
    t = _text(text)
    if not t:
        return None, None, None

    pcts = []
    for m in _GRADE_PCT_RE.finditer(t):
        try:
            v = float(m.group(1))
        except ValueError:
            continue
        if 0 < v <= 100:
            pcts.append(v)

    gpas = []
    for regex in (_GRADE_GPA_RE, _GRADE_GPA_SUFFIX_RE):
        for m in regex.finditer(t):
            try:
                v = float(m.group(1))
            except ValueError:
                continue
            if 0 < v <= 4.0:
                gpas.append(v)

    pct = max(pcts) if pcts else None
    cgpa = max(gpas) if gpas else None

    logic = None
    if pct is not None and cgpa is not None:
        logic = "and" if re.search(r"\band\b", _norm(t)) and not re.search(r"\bor\b|/", t) else "or"
    return pct, cgpa, logic


def _derive_income(text):
    """Return (amount, period) - period is 'month', 'year' or None."""
    t = _text(text)
    if not t:
        return None, None
    m = _INCOME_RE.search(t)
    if not m:
        return None, None
    try:
        amount = float(m.group(1).replace(",", ""))
    except ValueError:
        return None, None
    unit = (m.group(2) or "").lower()
    if unit in ("m", "million"):
        amount *= 1_000_000
    elif unit in ("lakh", "lac"):
        amount *= 100_000
    elif unit == "k":
        amount *= 1_000
    period = None
    n = _norm(t)
    if re.search(r"\bper month\b|\bmonthly\b|\ba month\b|\bp\.?m\.?\b", n):
        period = "month"
    elif re.search(r"\bper year\b|\byearly\b|\bannual(?:ly)?\b|\bper annum\b|\bp\.?a\.?\b|\byear\b", n):
        period = "year"
    return amount, period


_AGE_MAX_RE = re.compile(
    r"(?:age[^.;]{0,25}?)?(?:under|below|less than|younger than|max(?:imum)?|not more than)\s*(\d{2})\s*(?:years?|yrs?)?\b",
    re.I,
)
_AGE_MIN_RE = re.compile(r"(?:at least|minimum|above|older than|over)\s*(\d{2})\s*(?:years?|yrs?)\b", re.I)


def _to_number(value):
    try:
        f = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if f != f:
        return None
    return f


def _derive_age(raw):
    age_min = _to_number(raw.get("age_min"))
    age_max = _to_number(raw.get("age_max"))
    extra = " ".join(_text(raw.get(k)) for k in ("other_eligibility", "prior_qualification"))
    if age_max is None:
        m = _AGE_MAX_RE.search(extra)
        if m:
            age_max = float(m.group(1))
    if age_min is None:
        m = _AGE_MIN_RE.search(extra)
        if m:
            age_min = float(m.group(1))
    return age_min, age_max


# --------------------------------------------------------------------------- #
# Field of study matching
# --------------------------------------------------------------------------- #

def _clean_field_text(text):
    t = _norm(text)
    for noise in _DEGREE_NOISE:
        t = re.sub(rf"\b{re.escape(noise)}\b", " ", t)
    return " ".join(t.split())


def field_groups(text):
    """Field groups mentioned in ``text``."""
    t = _norm(text)
    return {
        name
        for name, words in _FIELD_GROUPS.items()
        if any(re.search(rf"\b{re.escape(w)}", t) for w in words)
    }


def _is_broad_field_list(text):
    """True when the source lists many eligible fields (an inclusive list)."""
    t = _norm(text)
    if re.search(r"\bstem\b", t):
        return True
    items = [i for i in re.split(r"[,;]", t) if i.strip()]
    return len(items) >= 4


def match_field(scholarship_field, profile_field):
    """Compare a scholarship's field requirement with the student's field.

    Returns ``"pass"``, ``"fail"`` or ``"unknown"``. A mismatch is only declared
    when both sides name a field group we recognise; vague prose stays unknown.
    """
    if _is_blank(scholarship_field):
        return "unknown"
    sch_raw = _norm(scholarship_field)
    if any(w in sch_raw for w in _FIELD_WILDCARDS):
        return "pass"

    sch_clean = _clean_field_text(scholarship_field)
    sch_groups = field_groups(sch_clean)

    if _is_blank(profile_field):
        return "unknown"

    prof_clean = _clean_field_text(profile_field)
    prof_groups = field_groups(prof_clean)
    prof_specific = prof_groups - _BROAD_FIELD_GROUPS
    effective_prof = prof_specific or prof_groups

    # An inclusive list of fields ("IT, engineering, medicine, business") is
    # open to any recognised field.
    if _is_broad_field_list(scholarship_field):
        return "pass" if effective_prof else "unknown"

    if effective_prof and sch_groups and (effective_prof & sch_groups):
        return "pass"

    # Direct word overlap on meaningful words (>=4 chars) is strong evidence.
    prof_words = {w for w in prof_clean.split()
                  if len(w) >= 4 and w not in _GENERIC_FIELD_WORDS}
    sch_words = {w for w in sch_clean.split()
                 if len(w) >= 4 and w not in _GENERIC_FIELD_WORDS}
    if prof_words & sch_words:
        return "pass"

    if effective_prof and sch_groups:
        return "fail"
    return "unknown"


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #

def _derive_destination(raw):
    """Infer a study destination when ``destination_country`` is blank."""
    dest = _text(raw.get("destination_country"))
    if dest:
        return dest

    name = _norm(raw.get("opportunity_name"))
    sponsor_type = _norm(raw.get("sponsor_type"))
    route = _norm(raw.get("application_route"))
    source = _norm(raw.get("official_source_url"))

    if re.search(r"\boverseas\b|\bforeign\b|\babroad\b", name):
        return "International"
    if "erasmus" in name or "mundus" in name or "fame" in name:
        return "International"
    if "consortium" in sponsor_type or "consortium" in route:
        return "International"
    if source.endswith(".pk") or ".pk/" in source or ".gov.pk" in source:
        return "Pakistan"
    return None


def _manual_checks(raw, levels):
    checks = []

    target = _text(raw.get("target_group"))
    if target:
        checks.append(f"Restricted beneficiary group - confirm you qualify: {target}")

    employment = _text(raw.get("employment_requirement"))
    if employment:
        checks.append(f"Employment condition - confirm you qualify: {employment}")

    language = _text(raw.get("language_requirement"))
    if language:
        checks.append(f"Language requirement - provide evidence: {language}")

    geo = _text(raw.get("geographic_scope"))
    if re.search(r"\b(aiou|virtual university|\bvu\b|habib students|kellogg students)\b", _norm(geo)):
        checks.append(f"Institution-specific eligibility - confirm you study there: {geo}")
    elif re.search(r"children of|employees|workers|martyrs|martyred|disabilit|transgender", _norm(geo)):
        checks.append(f"Restricted beneficiary group - confirm you qualify: {geo}")

    grade_text = _text(raw.get("minimum_grade"))
    if grade_text and re.search(
        r"top ten|top three|top 10|top 3|aptitude|all courses passed|first class|first-class|"
        r"per semester|equivalent",
        _norm(grade_text),
    ):
        checks.append(f"Additional academic condition in the source - confirm: {grade_text}")

    domicile_text = _text(raw.get("domicile_rule"))
    if domicile_text and re.search(
        r"government school|cnic|resident|local|examination", _norm(domicile_text)
    ):
        short = domicile_text if len(domicile_text) <= 160 else domicile_text[:157].rstrip() + "..."
        checks.append(f"Domicile/local conditions - confirm you meet them: {short}")

    other = _text(raw.get("other_eligibility"))
    if other:
        low = _norm(other)
        if any(marker in low for marker in _OTHER_MANUAL_MARKERS):
            short = other if len(other) <= 180 else other[:177].rstrip() + "..."
            checks.append(f"Additional condition in the source - confirm: {short}")

    if not levels:
        level_text = _text(raw.get("study_level"))
        if level_text:
            checks.append(
                f"The source describes the level as '{level_text}' which cannot be "
                "matched to a degree level automatically - confirm with the sponsor"
            )

    return _uniq(checks)


def _notes(raw):
    out = []
    status = _norm(raw.get("verification_status"))
    if status and status not in ("confirmed", "verified"):
        out.append(
            f"Source verification status is '{_text(raw.get('verification_status'))}' - re-check the sponsor page."
        )
    exclusions = _text(raw.get("exclusions"))
    if exclusions and re.search(
        r"concurrent|another scholarship|no other scholarship|not permitted|ineligible|excluded",
        _norm(exclusions),
    ):
        out.append(f"Exclusions noted by the source: {exclusions}")
    return _uniq(out)


@lru_cache(maxsize=4096)
def _derive_cached(raw_json):
    try:
        raw = json.loads(raw_json) if raw_json else {}
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}

    provs, prov_notes = _derive_provinces(raw)
    domicile = _derive_domicile(raw)
    levels = _derive_levels(raw)
    marks, cgpa, grade_logic = _derive_grade(raw.get("minimum_grade"))
    income, income_period = _derive_income(raw.get("income_threshold"))
    age_min, age_max = _derive_age(raw)
    gender = _norm(raw.get("gender_rule"))
    gender_value = None
    if "female" in gender or "women" in gender or "girl" in gender:
        gender_value = "female"
    elif "male" in gender or "men" in gender or "boy" in gender:
        gender_value = "male"

    criteria = {}
    if provs:
        criteria["eligible_provinces"] = ", ".join(provs)
    if domicile:
        criteria["eligible_domiciles"] = ", ".join(domicile)
    districts = [
        d.strip()
        for d in re.split(r"[;,]", _text(raw.get("district")))
        if d.strip()
    ]
    if districts:
        criteria["eligible_cities"] = ", ".join(districts)
    if levels:
        criteria["education_level"] = ", ".join(levels)
    if marks is not None:
        criteria["minimum_marks"] = marks
    if cgpa is not None:
        criteria["minimum_cgpa"] = cgpa
    if grade_logic:
        criteria["grade_logic"] = grade_logic
    if income is not None:
        criteria["household_income_limit"] = income
    if income_period:
        criteria["income_period"] = income_period
    if age_min is not None:
        criteria["minimum_age"] = age_min
    if age_max is not None:
        criteria["maximum_age"] = age_max
    if gender_value:
        criteria["gender"] = gender_value
    destination = _derive_destination(raw)
    if destination:
        criteria["study_destination"] = destination

    # Direct passthroughs keep a raw row usable even when the importer did not
    # map the column (e.g. when check_eligibility is called with raw_data only).
    passthrough = {
        "status": "application_status",
        "deadline": "deadline_date",
        "application_url": "application_url",
        "source_url": "official_source_url",
        "funding": "funding_amount",
    }
    for key, raw_key in passthrough.items():
        value = _text(raw.get(raw_key))
        if value:
            criteria[key] = value
    if _text(raw.get("sponsor")):
        criteria["provider"] = _text(raw.get("sponsor"))

    return {
        "criteria": criteria,
        "manual_checks": _manual_checks(raw, levels),
        "notes": _uniq(prov_notes + _notes(raw)),
        "levels": levels,
    }


def derive(raw_data):
    """Public wrapper: accept raw_data as JSON text, dict or None."""
    if isinstance(raw_data, dict):
        payload = json.dumps(raw_data, sort_keys=True, default=str)
    elif isinstance(raw_data, str):
        payload = raw_data
    else:
        return {"criteria": {}, "manual_checks": [], "notes": [], "levels": []}
    result = _derive_cached(payload)
    return {
        "criteria": dict(result["criteria"]),
        "manual_checks": list(result["manual_checks"]),
        "notes": list(result["notes"]),
        "levels": list(result["levels"]),
    }


def enrich_record(record):
    """Return a copy of ``record`` with derived criteria filled in."""
    if not isinstance(record, dict):
        return record
    raw = _load_raw(record)
    if not raw:
        return record
    derived = derive(raw)

    out = dict(record)
    authoritative = {
        "education_level", "eligible_provinces", "eligible_domiciles",
        "eligible_cities", "minimum_marks", "minimum_cgpa",
        "household_income_limit", "income_period", "minimum_age",
        "maximum_age", "gender", "grade_logic", "study_destination",
    }
    for key, value in derived["criteria"].items():
        if key in authoritative:
            # Derived values are authoritative: the raw structured columns for
            # these fields are prose that the generic checker cannot read.
            out[key] = value
        elif _is_blank(out.get(key)):
            out[key] = value

    out["manual_checks"] = derived["manual_checks"]
    out["criteria_notes"] = derived["notes"]
    out["supported_levels"] = derived["levels"] or None
    return out


def _fmt_money(amount):
    if amount is None:
        return ""
    try:
        return f"{amount:,.0f}"
    except (TypeError, ValueError):
        return str(amount)


def summarize(record):
    """Human-readable requirement lines for the UI."""
    rec = record
    lines = []
    levels = rec.get("supported_levels") or _levels_in(_text(rec.get("education_level")))
    if levels:
        lines.append("Level: " + ", ".join(LEVEL_LABELS.get(lv, lv) for lv in levels))
    province = _text(rec.get("eligible_provinces"))
    if province:
        lines.append("Province: " + province)
    domicile = _text(rec.get("eligible_domiciles"))
    if domicile:
        lines.append("Domicile: " + domicile)
    cities = _text(rec.get("eligible_cities"))
    if cities:
        lines.append("Districts/cities: " + cities)

    marks = rec.get("minimum_marks")
    cgpa = rec.get("minimum_cgpa")
    logic = _text(rec.get("grade_logic"))
    if marks is not None and cgpa is not None:
        joiner = " and " if logic == "and" else " or "
        lines.append(f"Grade: at least {marks:g}%{joiner}CGPA {cgpa:g}")
    elif marks is not None:
        lines.append(f"Grade: at least {marks:g}%")
    elif cgpa is not None:
        lines.append(f"Grade: at least CGPA {cgpa:g}")

    income = rec.get("household_income_limit")
    if income is not None:
        period = _text(rec.get("income_period"))
        suffix = f" per {period}" if period in ("month", "year") else ""
        lines.append(f"Household income: up to PKR {_fmt_money(income)}{suffix}")

    age_min, age_max = rec.get("minimum_age"), rec.get("maximum_age")
    if age_min is not None and age_max is not None:
        lines.append(f"Age: {age_min:g}-{age_max:g}")
    elif age_max is not None:
        lines.append(f"Age: up to {age_max:g}")
    elif age_min is not None:
        lines.append(f"Age: at least {age_min:g}")

    gender = _text(rec.get("gender"))
    if gender:
        lines.append("Gender: " + gender)

    field = _text(rec.get("field_of_study"))
    if field:
        lines.append("Field: " + (field if len(field) <= 120 else field[:117] + "..."))

    destination = _text(rec.get("study_destination"))
    if destination:
        lines.append("Destination: " + destination)
    return lines
