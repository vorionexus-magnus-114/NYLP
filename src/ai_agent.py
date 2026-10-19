"""
Conversation orchestrator for PathFinder AI.

Gemini is optional: when ``GEMINI_API_KEY`` is not configured the assistant
falls back to a deterministic, database-grounded reply so the application is
fully usable offline.
"""

import json
import os

from src.criteria import LEVEL_LABELS
from src.matching import find_matches, match_summary
from src.profile import (
    get_missing_profile_fields,
    extract_profile_from_text,
    normalize_profile,
    summarize_profile,
)
from src.scholarship_chat import OFF_TOPIC_REDIRECT_MESSAGE, is_scholarship_related

DEFAULT_MODEL = "gemini-2.5-flash"

_PROFILE_LABELS = {
    "province": "Province",
    "domicile": "Domicile",
    "city": "City",
    "education_level": "Education level",
    "field_of_study": "Field of study",
    "marks": "Percentage",
    "cgpa": "CGPA",
    "age": "Age",
    "gender": "Gender",
    "study_destination": "Destination",
    "household_income": "Household income",
    "income_period": "Income period",
    "language_test": "English test",
}


def get_gemini_client():
    """Initialise the Google GenAI client when an API key is available."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None, "GEMINI_API_KEY environment variable is not set."
    try:
        from google import genai

        return genai.Client(api_key=api_key), None
    except Exception as exc:  # pragma: no cover - depends on optional package
        return None, f"Failed to initialise GenAI client: {exc}"


def get_model_name():
    return os.getenv("GEMINI_MODEL", DEFAULT_MODEL)


SYSTEM_INSTRUCTION = """
You are PathFinder AI, a scholarship advisor for Pakistani students.

CRITICAL RULES
1. Scholarship focus only. Politely decline unrelated requests and redirect.
2. Never invent scholarships, deadlines, funding or URLs. Use only the supplied
   database matches. If the database has nothing suitable, say so.
3. Respect the eligibility engine's verdicts and quote its reasons. A record
   marked "ineligible" must not be recommended. Province and domicile
   restrictions are real: never suggest a Khyber Pakhtunkhwa or Punjab scheme to
   a Sindh student.
4. Distinguish clearly between "eligible", "potentially eligible" (criteria met
   but something must be confirmed) and "needs information".
5. Never guarantee an award. Tell the student to confirm with the sponsor.
6. Keep answers concise and practical.
"""


def _profile_changes(before, after):
    changes = []
    for key, label in _PROFILE_LABELS.items():
        old, new = before.get(key), after.get(key)
        if old != new and new not in (None, ""):
            if key in ("marks", "cgpa", "household_income"):
                changes.append(f"{label}: {new:g}")
            elif key == "education_level":
                changes.append(f"{label}: {LEVEL_LABELS.get(new, new)}")
            else:
                changes.append(f"{label}: {new}")
    return changes


def _fallback_response(profile, matches, missing_fields, changes, summary=None):
    summary = summary or match_summary(matches)
    lines = []

    if changes:
        lines.append("✅ **Updated from your message:** " + " · ".join(changes))
        lines.append("")

    if summary["eligible"] or summary["potentially_eligible"]:
        lines.append(
            f"Based on your profile ({summarize_profile(profile)}), I found "
            f"**{summary['eligible']} eligible** and "
            f"**{summary['potentially_eligible']} likely eligible** scholarship(s) "
            f"you can apply for."
        )
        if matches:
            top = matches[0]
            deadline = top.get("deadline") or "deadline not announced"
            lines.append(
                f"\nTop match: **{top.get('name')}** — {top.get('provider')} "
                f"({deadline})."
            )
            reasons = top.get("reasons") or []
            if reasons:
                lines.append("Why: " + "; ".join(str(r) for r in reasons[:3]))
            missing = top.get("missing_information") or []
            if missing:
                lines.append("Still to confirm: " + "; ".join(str(m) for m in missing[:2]))
    elif matches:
        lines.append(
            "I found records that may fit, but some details are still unknown "
            "and I cannot confirm eligibility yet."
        )
    else:
        lines.append(
            "No scholarship in the database matches your current profile yet. "
            "Nothing is hidden: the province, degree level, grades and field "
            "rules are applied strictly."
        )

    if missing_fields:
        lines.append("")
        lines.append(
            "To improve the matches, tell me: "
            + "; ".join(missing_fields[:2])
            + "."
        )

    lines.append("")
    lines.append(
        "_Eligibility is indicative — always confirm requirements and deadlines "
        "on the official sponsor page._"
    )
    return "\n".join(lines)


def process_chat_message(user_message, conversation_history=None, current_profile=None):
    """
    Process a student chat message.

    Returns a dict with keys: ``response``, ``updated_profile``, ``scholarships``,
    ``intent``.
    """
    if conversation_history is None:
        conversation_history = []

    profile = normalize_profile(current_profile)
    before = dict(profile)

    is_related, _reason = is_scholarship_related(user_message)
    if not is_related:
        return {
            "response": OFF_TOPIC_REDIRECT_MESSAGE,
            "updated_profile": profile,
            "scholarships": [],
            "intent": "off_topic",
        }

    profile = extract_profile_from_text(user_message, profile)
    changes = _profile_changes(before, profile)

    all_matches = find_matches(profile, limit=500)
    summary = match_summary(all_matches)
    matches = all_matches[:10]
    missing_fields = get_missing_profile_fields(profile)

    match_context = []
    for match in matches[:5]:
        match_context.append({
            "id": match.get("id"),
            "name": match.get("name"),
            "provider": match.get("provider"),
            "eligibility_status": match.get("eligibility_status"),
            "deadline": match.get("deadline"),
            "funding": match.get("funding"),
            "provinces": match.get("eligible_provinces") or "All Pakistan",
            "levels": match.get("education_level"),
            "reasons": (match.get("reasons") or [])[:4],
            "missing_info": (match.get("missing_information") or [])[:4],
            "manual_checks": (match.get("manual_checks") or [])[:3],
            "application_url": match.get("application_url"),
            "source_url": match.get("source_url"),
        })

    client, client_error = get_gemini_client()
    if client is None:
        return {
            "response": _fallback_response(profile, matches, missing_fields, changes, summary),
            "updated_profile": profile,
            "scholarships": matches,
            "intent": "scholarship_chat",
            "client_error": client_error,
        }

    prompt_context = (
        f"Student profile: {summarize_profile(profile)}\n"
        f"Profile fields updated by this message: {', '.join(changes) or 'none'}\n"
        f"Missing profile fields: {', '.join(missing_fields) if missing_fields else 'none'}\n"
        f"Eligibility counts: {json.dumps(summary)}\n\n"
        f"Database matches (eligible > likely eligible > needs information):\n"
        f"{json.dumps(match_context, indent=2)}\n\n"
        f"Student message: {user_message}\n\n"
        "Instructions: reply concisely, reference only the listed scholarships, "
        "repeat the eligibility reasons, mention anything the student must confirm, "
        "and never claim a scheme is open unless its status/deadline supports it."
    )

    try:
        response_obj = client.models.generate_content(
            model=get_model_name(),
            contents=prompt_context,
            config={
                "system_instruction": SYSTEM_INSTRUCTION,
                "temperature": 0.3,
            },
        )
        response_text = getattr(response_obj, "text", "") or ""
        if not response_text.strip():
            response_text = _fallback_response(profile, matches, missing_fields, changes, summary)
    except Exception as exc:
        response_text = (
            _fallback_response(profile, matches, missing_fields, changes, summary)
            + f"\n\n_(Gemini was unavailable: {type(exc).__name__})_"
        )

    return {
        "response": response_text,
        "updated_profile": profile,
        "scholarships": matches,
        "intent": "scholarship_chat",
    }
