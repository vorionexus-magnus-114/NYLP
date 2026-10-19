import html
import re
import sys
from pathlib import Path
import streamlit as st

# Add project root directory to sys.path
sys.path.append(str(Path(__file__).resolve().parent))

from src.database import init_db, seed_database_if_empty
from src.profile import normalize_profile, summarize_profile, get_missing_profile_fields
from src.ai_agent import process_chat_message
from src.web_verification import get_cached_verification

# Page Configuration
st.set_page_config(
    page_title="PathFinder AI — Scholarship Assistant",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Initialize DB on application launch
try:
    init_db()
    seed_database_if_empty()
except Exception:
    pass

# Initialize session state variables
if "chat_history" not in st.session_state:
    st.session_state.chat_history = [
        {
            "role": "assistant",
            "content": (
                "👋 Hello! I am **PathFinder AI**, your dedicated scholarship assistant. "
                "I am here to help Pakistani students discover and evaluate local and international scholarship opportunities.\n\n"
                "Tell me about your current education, percentage/CGPA, province, and target field or destination, "
                "and I will find verified scholarship matches for you!"
            ),
        }
    ]

if "profile" not in st.session_state:
    st.session_state.profile = normalize_profile({})

if "current_scholarships" not in st.session_state:
    st.session_state.current_scholarships = []

if "saved_scholarships" not in st.session_state:
    st.session_state.saved_scholarships = []


# Custom Styling for modern chat interface & sidebar
st.markdown(
    """
    <style>
    .main .block-container {
        padding-top: 1.5rem;
        padding-bottom: 2rem;
    }
    .stButton>button {
        background-color: #1E88E5;
        color: white;
        font-weight: bold;
        border-radius: 8px;
        border: none;
    }
    .stButton>button:hover {
        background-color: #1565C0;
        color: white;
    }
    .profile-card {
        padding: 1rem;
        border-radius: 8px;
        background-color: #f0f4f8;
        border-left: 4px solid #1E88E5;
        margin-bottom: 1rem;
    }
    .scholarship-card {
        padding: 1rem 1.2rem;
        border-radius: 8px;
        margin-bottom: 0.8rem;
        border-left: 5px solid #ccc;
        box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        background-color: #ffffff;
    }
    .status-eligible {
        border-left-color: #2E7D32 !important;
        background-color: #F1F8E9;
    }
    .status-potential {
        border-left-color: #F57F17 !important;
        background-color: #FFFDE7;
    }
    .status-insufficient {
        border-left-color: #0288D1 !important;
        background-color: #E1F5FE;
    }
    .badge {
        display: inline-block;
        padding: 0.2em 0.5em;
        font-size: 75%;
        font-weight: 700;
        border-radius: 0.25rem;
        color: white;
    }
    .badge-eligible { background-color: #2E7D32; }
    .badge-potential { background-color: #F57F17; }
    .badge-insufficient { background-color: #0288D1; }
    </style>
    """,
    unsafe_allow_html=True,
)


def safe_text(val, default="N/A"):
    if val is None or str(val).strip() == "":
        return html.escape(default)
    return html.escape(str(val))


def is_valid_url(url):
    if not url or not isinstance(url, str):
        return False
    url = url.strip()
    regex = re.compile(
        r"^https?://"
        r"(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|"
        r"localhost|"
        r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"
        r"(?::\d+)?"
        r"(?:/?|[/?]\S+)$",
        re.IGNORECASE,
    )
    return re.match(regex, url) is not None


def render_sidebar():
    with st.sidebar:
        st.title("🎓 PathFinder Dashboard")

        # 1. Active Profile Section
        st.subheader("👤 Student Profile")
        summary_str = summarize_profile(st.session_state.profile)
        st.info(summary_str)

        # Profile Missing Alerts
        missing = get_missing_profile_fields(st.session_state.profile)
        if missing:
            with st.expander("⚠️ Missing Information", expanded=False):
                st.caption("Provide these in chat to improve matching:")
                for m in missing:
                    st.write(f"- {m}")

        # Edit Profile Expander
        with st.expander("✏️ Update Profile Details", expanded=False):
            with st.form("edit_profile_form"):
                e_level = st.selectbox(
                    "Education Level",
                    [
                        "Undergraduate / Bachelors",
                        "Intermediate / A-Level",
                        "Postgraduate / Masters",
                        "Doctoral / PhD",
                        "Matriculation / O-Level",
                    ],
                    index=0,
                )
                field = st.text_input("Field of Study", value=st.session_state.profile.get("field_of_study") or "")
                grading = st.radio("Grading System", ["CGPA", "Percentage (%)"], horizontal=True)
                if grading == "CGPA":
                    cgpa_v = st.number_input("CGPA", min_value=0.0, max_value=4.0, value=float(st.session_state.profile.get("cgpa") or 0.0), step=0.01)
                    marks_v = None
                else:
                    marks_v = st.number_input("Percentage (%)", min_value=0.0, max_value=100.0, value=float(st.session_state.profile.get("marks") or 0.0), step=0.1)
                    cgpa_v = None

                prov = st.selectbox(
                    "Province",
                    ["Punjab", "Sindh", "Khyber Pakhtunkhwa", "Balochistan", "Islamabad Capital Territory", "Gilgit-Baltistan", "Azad Jammu & Kashmir"],
                )
                dest = st.selectbox("Destination", ["Pakistan", "Abroad", "Both"])

                if st.form_submit_button("Save Profile Updates"):
                    st.session_state.profile.update({
                        "education_level": e_level,
                        "field_of_study": field.strip() if field else None,
                        "cgpa": cgpa_v if cgpa_v and cgpa_v > 0 else None,
                        "marks": marks_v if marks_v and marks_v > 0 else None,
                        "province": prov,
                        "study_destination": dest,
                    })
                    st.success("Profile updated!")
                    st.rerun()

        st.markdown("---")

        # 2. Saved Matches Section
        st.subheader("⭐ Saved Scholarships")
        if not st.session_state.saved_scholarships:
            st.caption("No scholarships saved yet. Click '⭐ Save' on recommended cards.")
        else:
            for s in st.session_state.saved_scholarships:
                st.markdown(f"**{safe_text(s.get('name'))}**")
                st.caption(f"Provider: {safe_text(s.get('provider'))} | Deadline: {safe_text(s.get('deadline'))}")

        st.markdown("---")

        # Controls
        if st.button("🔄 Reset Conversation"):
            st.session_state.chat_history = [
                {
                    "role": "assistant",
                    "content": "Conversation reset. How can I help you with scholarships today?",
                }
            ]
            st.session_state.current_scholarships = []
            st.rerun()


def display_scholarship_card(item):
    status_raw = str(item.get("eligibility_status", "Unknown")).strip()
    status_lower = status_raw.lower()

    if status_lower == "eligible":
        card_class = "status-eligible"
        badge_class = "badge-eligible"
        badge_label = "ELIGIBLE"
    elif status_lower in ["potentially_eligible", "potentially eligible"]:
        card_class = "status-potential"
        badge_class = "badge-potential"
        badge_label = "POTENTIALLY ELIGIBLE"
    else:
        card_class = "status-insufficient"
        badge_class = "badge-insufficient"
        badge_label = "NEEDS INFORMATION"

    name = safe_text(item.get("name"), "Unnamed Scholarship")
    provider = safe_text(item.get("provider"))
    funding = safe_text(item.get("funding"))
    deadline = safe_text(item.get("deadline"))
    location = safe_text(item.get("location"))
    verified_at = safe_text(item.get("verified_at"), "")

    reasons = item.get("reasons") if isinstance(item.get("reasons"), list) else []
    missing_info = item.get("missing_information") if isinstance(item.get("missing_information"), list) else []

    app_link = item.get("application_url")
    src_link = item.get("source_url")
    valid_app = app_link.strip() if is_valid_url(app_link) else None
    valid_src = src_link.strip() if is_valid_url(src_link) else None

    ver_str = f" | <em>Verified: {verified_at}</em>" if verified_at else ""

    st.markdown(
        f"""
        <div class="scholarship-card {card_class}">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                <h4 style="margin:0; color: #1E88E5;">{name}</h4>
                <span class="badge {badge_class}">{badge_label}</span>
            </div>
            <p style="margin-bottom: 4px; font-size: 0.9em;">
                <strong>Provider:</strong> {provider} | <strong>Location:</strong> {location} | <strong>Funding:</strong> {funding}
            </p>
            <p style="margin-bottom: 6px; font-size: 0.85em; color: #555;">
                <strong>Deadline:</strong> {deadline} {ver_str}
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        if reasons:
            st.caption("**Match Reasons:** " + ", ".join([str(r) for r in reasons[:2]]))
        if missing_info:
            st.caption("⚠️ **Missing Info:** " + ", ".join([str(m) for m in missing_info[:2]]))
    with c2:
        if valid_app:
            st.markdown(f"🔗 [Apply]({valid_app})")
        elif valid_src:
            st.markdown(f"🌐 [Source]({valid_src})")
        else:
            st.caption("No direct link")
    with c3:
        if st.button("⭐ Save", key=f"save_{item.get('id')}_{name}"):
            if item not in st.session_state.saved_scholarships:
                st.session_state.saved_scholarships.append(item)
                st.toast(f"Saved {name}!")


def main():
    render_sidebar()

    st.title("🎓 PathFinder AI Assistant")
    st.caption("Conversational Scholarship Assistant for Pakistani & International Opportunities")

    # Display Chat History Messages
    for msg in st.session_state.chat_history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Render Active Recommendation Cards under chat if available
    if st.session_state.current_scholarships:
        st.markdown("### 🎯 Recommended Opportunities")
        for sch in st.session_state.current_scholarships[:5]:
            display_scholarship_card(sch)

    # Chat Input Box
    if user_prompt := st.chat_input("Ask about scholarships, eligibility, SOPs, or update your profile..."):
        # Display User Message
        st.session_state.chat_history.append({"role": "user", "content": user_prompt})
        with st.chat_message("user"):
            st.markdown(user_prompt)

        # Process message with AI Agent
        with st.spinner("PathFinder AI is thinking and searching database..."):
            res = process_chat_message(
                user_prompt,
                conversation_history=st.session_state.chat_history,
                current_profile=st.session_state.profile,
            )

        # Update Session State
        st.session_state.profile = res["updated_profile"]
        if res.get("scholarships"):
            st.session_state.current_scholarships = res["scholarships"]

        # Display Assistant Response
        assistant_reply = res["response"]
        st.session_state.chat_history.append({"role": "assistant", "content": assistant_reply})
        with st.chat_message("assistant"):
            st.markdown(assistant_reply)

        st.rerun()


if __name__ == "__main__":
    main()
