"""
Scholarship-only topic guardrails and intent classification.
Enforces domain boundaries so PathFinder AI remains scholarship-focused.
"""

import re

# Domain keywords that clearly indicate scholarship / study opportunity intent
SCHOLARSHIP_KEYWORDS = [
    "scholarship", "scholarships", "grant", "grants", "fellowship", "fellowships",
    "bursary", "bursaries", "financial aid", "funding", "stipend", "tuition",
    "eligibility", "eligible", "deadline", "deadlines", "application", "apply",
    "sop", "statement of purpose", "motivation letter", "recommendation letter",
    "hec", "ehsaas", "peef", "fulbright", "chevening", "rhodes", "dadd", "turkiye burslari",
    "study in", "university", "college", "degree", "qualification", "gpa", "cgpa",
    "marks", "percentage", "domicile", "province", "requirements", "documents",
    "recommend", "find scholarship", "matches", "profile", "education",
]

# Off-topic keywords/topics that should be redirected
OFF_TOPIC_PATTERNS = [
    r"\b(write a code|python script|write python|coding|javascript|java|c\+\+|html|css|python code|write a python)\b",
    r"\b(tell me a joke|weather in|recipe for|cook|movie recommendation|cricket score|football)\b",
    r"\b(who is the president|capital of|explain quantum|solve math equation)\b",
    r"\b(crypto|bitcoin|stock market|investing|make money online)\b",
]

OFF_TOPIC_REDIRECT_MESSAGE = (
    "I am your PathFinder AI Scholarship Assistant, dedicated exclusively to helping "
    "students find, evaluate, and apply for local and international scholarships. "
    "I cannot assist with unrelated general queries. Please ask me about scholarships, "
    "eligibility criteria, application deadlines, required documents, or SOP guidance!"
)


def is_scholarship_related(text):
    """
    Evaluate whether text input is related to scholarships and educational opportunities.
    Returns (is_related: bool, reason: str).
    """
    if not text or not isinstance(text, str):
        return True, "empty or non-string input"

    t_lower = text.strip().lower()

    # If message is short conversational greeting or profile info update, allow it
    if len(t_lower) <= 30 and any(g in t_lower for g in ["hi", "hello", "hey", "assalam", "aoa", "yes", "no", "thanks", "thank you", "my name", "i am", "help"]):
        return True, "greeting or brief conversational response"

    # Check off-topic negative patterns first
    for pattern in OFF_TOPIC_PATTERNS:
        if re.search(pattern, t_lower):
            return False, f"Matched off-topic pattern: {pattern}"

    # Check for presence of scholarship domain keywords
    for kw in SCHOLARSHIP_KEYWORDS:
        if re.search(rf"\b{re.escape(kw)}\b", t_lower):
            return True, f"Matched domain keyword: {kw}"

    # If input looks like student profile sharing (e.g. contains numbers, grades, locations)
    if re.search(r"\b(\d{2}%|\d\.\d+|fsc|matric|bs|ms|phd|punjab|sindh|kpk|balochistan|lahore|karachi|islamabad)\b", t_lower):
        return True, "contains student profile details"

    # Default heuristic: if message is open-ended or ambiguous follow-up, pass to agent but enforce system prompt
    return True, "ambiguous query allowed under general prompt guardrails"


def classify_user_intent(text):
    """
    Classify student query intent:
    - 'off_topic': non-scholarship request
    - 'profile_update': student updating demographic/academic profile
    - 'search_request': student asking to find or recommend scholarships
    - 'follow_up': asking question about a specific scholarship, eligibility, deadline, or SOP
    - 'general_scholarship': general query about scholarship processes
    """
    related, _ = is_scholarship_related(text)
    if not related:
        return "off_topic"

    t_lower = text.lower()

    if any(k in t_lower for k in ["find", "search", "recommend", "matches", "show scholarships", "suggest"]):
        return "search_request"

    if any(k in t_lower for k in ["sop", "statement of purpose", "motivation letter", "essay", "draft"]):
        return "sop_assistance"

    if any(k in t_lower for k in ["why am i eligible", "why eligible", "requirements missing", "is deadline open", "documents needed"]):
        return "follow_up"

    if re.search(r"\b(i am|my age|my marks|my cgpa|fsc|bs|ms|punjab|sindh|kpk)\b", t_lower):
        return "profile_update"

    return "general_scholarship"
