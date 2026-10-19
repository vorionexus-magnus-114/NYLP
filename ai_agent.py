"""
Gemini AI Agent integration and conversation orchestrator for PathFinder AI.
Uses official google-genai SDK.
"""

import os
import json
from src.profile import normalize_profile, summarize_profile, get_missing_profile_fields
from src.scholarship_chat import is_scholarship_related, OFF_TOPIC_REDIRECT_MESSAGE
from src.matching import find_matches
from src.web_verification import verify_scholarship_record

DEFAULT_MODEL = "gemini-2.5-flash"


def get_gemini_client():
    """
    Initialize Google GenAI client if GEMINI_API_KEY is available.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None, "GEMINI_API_KEY environment variable is not set."

    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        return client, None
    except Exception as e:
        return None, f"Failed to initialize GenAI client: {str(e)}"


def get_model_name():
    return os.getenv("GEMINI_MODEL", DEFAULT_MODEL)


SYSTEM_INSTRUCTION = """
You are PathFinder AI, an expert, encouraging, and scholarship-focused conversational assistant for Pakistani students.
Your primary role is to help students discover, evaluate, and apply for local and international scholarships.

CRITICAL DIRECTIVES:
1. STRICT SCHOLARSHIP FOCUS: You MUST NOT answer unrelated general questions (coding, jokes, trivia, weather, etc.). Directly and politely decline off-topic requests and redirect to scholarships.
2. DETERMINISTIC TRUTH: Never invent scholarship eligibility, deadlines, or URLs. Always base recommendations on the supplied database records and grounded verification search results.
3. ONBOARDING & PROFILE: Welcome new students warmly and collect missing profile details progressively through natural chat.
4. HONESTY: Never guarantee admission or scholarship award. Clearly distinguish between verified, eligible, potentially eligible, and unverified information.
"""


def process_chat_message(user_message, conversation_history=None, current_profile=None):
    """
    Process a student chat message.
    Orchestrates topic check, profile updates, scholarship matching, web research, and Gemini LLM response.
    Returns:
        dict with keys: 'response', 'updated_profile', 'scholarships', 'intent'
    """
    if conversation_history is None:
        conversation_history = []

    profile = normalize_profile(current_profile)

    # 1. Enforce scholarship-only boundary
    is_related, reason = is_scholarship_related(user_message)
    if not is_related:
        return {
            "response": OFF_TOPIC_REDIRECT_MESSAGE,
            "updated_profile": profile,
            "scholarships": [],
            "intent": "off_topic",
        }

    # 2. Extract profile updates from user input
    from src.profile import extract_profile_from_text
    profile = extract_profile_from_text(user_message, profile)

    # 3. Find candidate matches using deterministic matching engine
    matches = find_matches(profile, limit=10)

    # 4. Check missing fields for progressive onboarding
    missing_fields = get_missing_profile_fields(profile)

    # 5. Build prompt context for Gemini model
    client, err = get_gemini_client()
    model_name = get_model_name()

    profile_summary = summarize_profile(profile)

    # Prepare lightweight matching JSON for LLM prompt
    match_context = []
    for m in matches[:5]:  # Top 5 records only
        match_context.append({
            "id": m.get("id"),
            "name": m.get("name"),
            "provider": m.get("provider"),
            "eligibility_status": m.get("eligibility_status"),
            "deadline": m.get("deadline"),
            "funding": m.get("funding"),
            "reasons": m.get("reasons"),
            "missing_info": m.get("missing_information"),
            "application_url": m.get("application_url"),
        })

    prompt_context = (
        f"Student Profile Summary: {profile_summary}\n"
        f"Missing Profile Info: {', '.join(missing_fields) if missing_fields else 'None (Profile Complete)'}\n\n"
        f"Top Database Scholarship Matches:\n{json.dumps(match_context, indent=2)}\n\n"
        f"Student Message: {user_message}\n\n"
        f"Instructions:\n"
        f"- Provide a helpful, concise response.\n"
        f"- If missing crucial profile fields, gently ask for 1-2 of them naturally.\n"
        f"- Reference matching scholarships from the list above with clear reasons.\n"
        f"- If student asks about SOP or application advice, provide tailored guidance.\n"
    )

    if client:
        try:
            response_obj = client.models.generate_content(
                model=model_name,
                contents=prompt_context,
                config={
                    "system_instruction": SYSTEM_INSTRUCTION,
                    "temperature": 0.4,
                },
            )
            response_text = getattr(response_obj, "text", "")
            if not response_text:
                response_text = "I processed your request, but received an empty response. Here are your top matching opportunities below."
        except Exception as e:
            response_text = (
                f"I encountered a provider error while connecting to Gemini: {str(e)}.\n\n"
                f"However, based on your profile ({profile_summary}), I have retrieved the best matching scholarships directly from our database below!"
            )
    else:
        # Graceful Fallback when API key is not configured
        if missing_fields and len(missing_fields) > 2:
            response_text = (
                f"Welcome! I am your PathFinder AI Scholarship Assistant. "
                f"I can help you find local and international scholarships.\n\n"
                f"To give you the most accurate recommendations, could you tell me a bit more about:\n"
                f"- {missing_fields[0]}\n"
                f"- {missing_fields[1]}\n\n"
                f"Here are initial opportunities based on your current profile ({profile_summary}):"
            )
        else:
            response_text = (
                f"Thank you! Based on your profile ({profile_summary}), "
                f"I have found {len(matches)} matching scholarship opportunities in our database."
            )

    return {
        "response": response_text,
        "updated_profile": profile,
        "scholarships": matches,
        "intent": "scholarship_chat",
    }
