"""
PathFinder AI — interactive scholarship dashboard.

Tabs
----
* Dashboard      - live eligibility metrics, filters, charts and result cards
* All Scholarships - searchable/filterable catalogue with CSV export
* Assistant      - scholarship-only chat with profile extraction
* Profile        - full editable student profile (percentage *and* CGPA input)
* Data Health    - provenance / consistency audit of the underlying workbook
"""

import html
import re
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

# Make the project root importable when Streamlit runs this file directly.
sys.path.append(str(Path(__file__).resolve().parent))

from src.criteria import LEVEL_LABELS
from src.data_quality import audit_records, verification_overview
from src.database import get_connection, init_db, seed_database_if_empty
from src.matching import evaluate_scholarships, match_summary
from src.profile import (
    EDUCATION_LEVELS,
    get_missing_profile_fields,
    normalize_profile,
    profile_completeness,
    summarize_profile,
)

st.set_page_config(
    page_title="PathFinder AI — Scholarship Dashboard",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded",
)

try:
    init_db()
    seed_database_if_empty()
except Exception as exc:  # pragma: no cover - surfaced in the UI
    st.error(f"Database could not be initialised: {exc}")

# --------------------------------------------------------------------------- #
# Session state
# --------------------------------------------------------------------------- #

if "profile" not in st.session_state:
    st.session_state.profile = normalize_profile({})
if "chat_history" not in st.session_state:
    st.session_state.chat_history = [{
        "role": "assistant",
        "content": (
            "👋 **Welcome to PathFinder AI.**\n\n"
            "Tell me your province, degree level, percentage/CGPA and field of study — "
            "I will only show scholarships you can actually apply for, and explain "
            "why anything else was filtered out."
        ),
    }]
if "saved_scholarships" not in st.session_state:
    st.session_state.saved_scholarships = []
if "show_excluded" not in st.session_state:
    st.session_state.show_excluded = False
if "result_limit" not in st.session_state:
    st.session_state.result_limit = 24
if "profile_version" not in st.session_state:
    # Bumped whenever the profile changes outside a widget. Widget keys embed
    # this version so they are recreated with the new defaults instead of
    # keeping stale values (Streamlit forbids mutating an instantiated widget).
    st.session_state.profile_version = 0

TOKEN_BY_LEVEL_LABEL = {label: token for token, label in LEVEL_LABELS.items()}
PROVINCES = [
    "Punjab", "Sindh", "Khyber Pakhtunkhwa", "Balochistan",
    "Islamabad Capital Territory", "Gilgit-Baltistan", "Azad Jammu & Kashmir",
]
DESTINATIONS = ["Any", "Pakistan", "Abroad"]

STATUS_META = {
    "eligible": ("ELIGIBLE", "#166534", "#dcfce7", "#16a34a"),
    "potentially_eligible": ("LIKELY ELIGIBLE", "#92400e", "#fef3c7", "#f59e0b"),
    "insufficient_information": ("NEEDS INFORMATION", "#075985", "#e0f2fe", "#0284d1"),
    "ineligible": ("NOT ELIGIBLE", "#991b1b", "#fee2e2", "#dc2626"),
    "error": ("ERROR", "#7f1d1d", "#fee2e2", "#dc2626"),
}

# --------------------------------------------------------------------------- #
# Styling
# --------------------------------------------------------------------------- #

st.markdown(
    """
    <style>
    .main .block-container { padding-top: 1.2rem; padding-bottom: 2.5rem; max-width: 1400px; }
    h1, h2, h3 { letter-spacing: -0.01em; }
    .pf-hero {
        background: linear-gradient(120deg, #0f172a 0%, #1d4ed8 55%, #0ea5e9 100%);
        color: #fff; border-radius: 16px; padding: 1.25rem 1.5rem; margin-bottom: 1rem;
    }
    .pf-hero h1 { font-size: 1.6rem; margin: 0 0 .25rem 0; color: #fff; }
    .pf-hero p { margin: 0; opacity: .92; font-size: .95rem; }
    .pf-metric {
        border-radius: 14px; padding: .9rem 1rem; border: 1px solid #e2e8f0;
        background: #fff; box-shadow: 0 1px 4px rgba(15,23,42,.06); height: 100%;
    }
    .pf-metric .label { font-size: .78rem; text-transform: uppercase; letter-spacing: .06em; color: #64748b; font-weight: 700; }
    .pf-metric .value { font-size: 1.9rem; font-weight: 800; line-height: 1.1; color: #0f172a; }
    .pf-metric .hint { font-size: .78rem; color: #64748b; }
    .pf-card {
        border: 1px solid #e2e8f0; border-left: 5px solid #94a3b8; border-radius: 12px;
        padding: .95rem 1.1rem; margin-bottom: .7rem; background: #fff;
        box-shadow: 0 1px 4px rgba(15,23,42,.05);
    }
    .pf-card h4 { margin: 0; font-size: 1.02rem; color: #0f172a; }
    .pf-badge {
        display: inline-block; padding: .18em .55em; border-radius: 999px;
        font-size: .68rem; font-weight: 800; letter-spacing: .04em;
    }
    .pf-meta { color: #475569; font-size: .84rem; margin: .35rem 0 .1rem 0; }
    .pf-chip {
        display: inline-block; padding: .15rem .55rem; margin: 0 .25rem .25rem 0;
        border-radius: 999px; background: #eff6ff; color: #1e40af;
        font-size: .76rem; font-weight: 600;
    }
    .pf-muted { color: #64748b; font-size: .82rem; }
    div[data-testid="stSidebar"] .block-container { padding-top: 1rem; }
    .stButton>button { border-radius: 9px; font-weight: 600; }
    </style>
    """,
    unsafe_allow_html=True,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def safe(value, default="—"):
    if value is None or (isinstance(value, str) and not value.strip()):
        return html.escape(default)
    return html.escape(str(value))


def is_valid_url(url):
    if not url or not isinstance(url, str):
        return False
    return re.match(r"^https?://\S+\.\S+", url.strip()) is not None


def short(value, limit=58, default="—"):
    """Truncate a value for a compact chip."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def level_label(token):
    return LEVEL_LABELS.get(token, token or "Not set")


def status_chip(status):
    label, fg, bg, border = STATUS_META.get(status, STATUS_META["insufficient_information"])
    return (
        f'<span class="pf-badge" style="color:{fg};background:{bg};">{label}</span>',
        border,
    )


@st.cache_data(show_spinner=False)
def load_records(db_version: float):
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM scholarships ORDER BY id").fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def db_mtime():
    from src.database import get_db_path

    try:
        return get_db_path().stat().st_mtime
    except Exception:
        return 0.0


def K(name):
    return f"w_{st.session_state.profile_version}_{name}"


def bump_profile_version():
    st.session_state.profile_version += 1


def set_profile_field(field, value):
    current = st.session_state.profile.get(field)
    if current != value:
        st.session_state.profile[field] = value


# --------------------------------------------------------------------------- #
# Sidebar — the live profile editor (not a form: every change applies at once)
# --------------------------------------------------------------------------- #

def render_sidebar():
    with st.sidebar:
        st.markdown("## 🎓 PathFinder AI")
        profile = st.session_state.profile
        completeness = profile_completeness(profile)
        st.progress(completeness / 100, text=f"Profile completeness: {completeness}%")
        st.caption(summarize_profile(profile))

        missing = get_missing_profile_fields(profile)
        if missing:
            with st.expander(f"⚠️ {len(missing)} detail(s) missing", expanded=False):
                for item in missing:
                    st.write(f"- {item}")

        st.markdown("#### 🎯 Your details")
        level_options = ["Not set"] + [LEVEL_LABELS[t] for t in EDUCATION_LEVELS]
        level_label_choice = st.selectbox(
            "Education level",
            options=level_options,
            index=(level_options.index(LEVEL_LABELS[profile["education_level"]])
                   if profile.get("education_level") in EDUCATION_LEVELS else 0),
            key=K("level_label"),
        )
        set_profile_field(
            "education_level",
            None if level_label_choice == "Not set" else TOKEN_BY_LEVEL_LABEL[level_label_choice],
        )

        grading = st.radio(
            "Result type",
            options=["Percentage", "CGPA"],
            index=0 if profile.get("marks") is not None or profile.get("cgpa") is None else 1,
            horizontal=True,
            key=K("grading"),
        )
        if grading == "Percentage":
            marks = st.number_input(
                "Percentage (%)", min_value=0.0, max_value=100.0,
                value=float(profile.get("marks") or 0.0), step=1.0, key=K("marks"),
                help="Enter 0 if you do not have a percentage result.",
            )
            set_profile_field("marks", marks if marks > 0 else None)
            if marks > 0 and profile.get("cgpa") is not None:
                set_profile_field("cgpa", None)
        else:
            cgpa = st.number_input(
                "CGPA (out of 4.0)", min_value=0.0, max_value=4.0,
                value=float(profile.get("cgpa") or 0.0), step=0.01, key=K("cgpa"),
                help="Enter 0 if you do not have a CGPA result.",
            )
            set_profile_field("cgpa", cgpa if cgpa > 0 else None)
            if cgpa > 0 and profile.get("marks") is not None:
                set_profile_field("marks", None)

        province = st.selectbox(
            "Province",
            options=[""] + PROVINCES,
            index=([""] + PROVINCES).index(profile["province"]) if profile.get("province") in PROVINCES else 0,
            key=K("province"),
            format_func=lambda v: v or "Select province…",
        )
        set_profile_field("province", province or None)

        field = st.text_input(
            "Field of study",
            value=profile.get("field_of_study") or "",
            placeholder="e.g. Computer Science",
            key=K("field"),
        )
        set_profile_field("field_of_study", field.strip() or None)

        destination = st.selectbox(
            "Study destination",
            options=DESTINATIONS,
            index=DESTINATIONS.index(profile.get("study_destination") or "Any"),
            key=K("destination"),
        )
        set_profile_field("study_destination", None if destination == "Any" else destination)

        with st.expander("➕ More details (age, income, gender, domicile)"):
            age = st.number_input(
                "Age", min_value=0, max_value=100, value=int(profile.get("age") or 0),
                step=1, key=K("age"),
            )
            set_profile_field("age", int(age) if age > 0 else None)

            gender_options = ["Not specified", "Female", "Male"]
            gender_value = {"female": "Female", "male": "Male"}.get(
                (profile.get("gender") or "").lower(), "Not specified")
            gender = st.selectbox(
                "Gender", gender_options,
                index=gender_options.index(gender_value),
                key=K("gender"),
            )
            set_profile_field("gender", None if gender == "Not specified" else gender.lower())

            domicile = st.selectbox(
                "Domicile (if different from province)",
                options=[""] + PROVINCES,
                index=([""] + PROVINCES).index(profile["domicile"]) if profile.get("domicile") in PROVINCES else 0,
                key=K("domicile"),
                format_func=lambda v: v or "Same as province",
            )
            set_profile_field("domicile", domicile or None)

            income = st.number_input(
                "Household income (PKR)", min_value=0.0, step=1000.0,
                value=float(profile.get("household_income") or 0.0), key=K("income"),
            )
            income_period = st.selectbox(
                "Income period", ["Month", "Year"],
                index=0 if (profile.get("income_period") or "month") == "month" else 1,
                key=K("income_period"),
            )
            set_profile_field("household_income", income if income > 0 else None)
            set_profile_field("income_period", income_period.lower() if income > 0 else None)

        if st.button("🧹 Clear profile", width="stretch"):
            st.session_state.profile = normalize_profile({})
            st.session_state.chat_history = [{
                "role": "assistant",
                "content": "Profile cleared. Tell me about yourself and I will start fresh.",
            }]
            bump_profile_version()
            st.rerun()

        st.divider()
        st.markdown(f"#### ⭐ Saved ({len(st.session_state.saved_scholarships)})")
        if not st.session_state.saved_scholarships:
            st.caption("Save a scholarship from the dashboard to keep it here.")
        else:
            for saved in st.session_state.saved_scholarships:
                st.markdown(f"- {safe(saved.get('name'))}")
            if st.button("Clear saved", width="stretch"):
                st.session_state.saved_scholarships = []
                st.rerun()


# --------------------------------------------------------------------------- #
# Result cards
# --------------------------------------------------------------------------- #

def save_scholarship(item):
    if not any(s.get("id") == item.get("id") for s in st.session_state.saved_scholarships):
        st.session_state.saved_scholarships.append(item)
        st.toast(f"Saved “{item.get('name')}”")


def render_card(item, key_prefix="card"):
    status = item.get("eligibility_status", "insufficient_information")
    chip, border = status_chip(status)

    name = safe(item.get("name"), "Unnamed scholarship")
    provider = safe(short(item.get("provider"), 90))
    deadline = safe(short(item.get("deadline"), 28), "not announced")
    level = level_label_from_record(item)
    province = safe(item.get("eligible_provinces"), "All Pakistan")
    funding = safe(short(item.get("funding")))
    verification = item.get("verification") or {}

    st.markdown(
        f"""
        <div class="pf-card" style="border-left-color:{border};">
            <div style="display:flex;justify-content:space-between;gap:1rem;align-items:flex-start;">
                <h4>{name}</h4>{chip}
            </div>
            <div class="pf-meta"><strong>{provider}</strong></div>
            <div class="pf-meta">
                <span class="pf-chip">🎓 {level}</span>
                <span class="pf-chip">📍 {province}</span>
                <span class="pf-chip">🗓️ {deadline}</span>
                <span class="pf-chip">💰 {funding}</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.expander("Details, eligibility reasons & how to apply"):
        left, middle, right = st.columns([1.2, 1.2, 1])

        with left:
            st.markdown("**Why this status**")
            blocking = item.get("blocking_issues") or []
            if status == "ineligible":
                for reason in blocking[:4] or item.get("reasons", [])[:2]:
                    st.markdown(f"- ❌ {reason}")
            for reason in (item.get("reasons") or [])[:4]:
                st.markdown(f"- ✅ {reason}")

            missing = item.get("missing_information") or []
            if missing:
                st.markdown("**What is still unknown**")
                for miss in missing[:5]:
                    st.markdown(f"- ⚠️ {miss}")

            manual = item.get("manual_checks") or []
            if manual:
                st.markdown("**Confirm with the sponsor**")
                for check in manual[:4]:
                    st.markdown(f"- 🔎 {check}")

        with middle:
            st.markdown("**Recorded requirements**")
            criteria = item.get("criteria") or []
            if criteria:
                for line in criteria[:8]:
                    st.markdown(f"- {line}")
            else:
                st.caption("No structured criteria recorded.")

            notes = item.get("notes") or []
            if notes:
                st.markdown("**Notes**")
                for note in notes[:3]:
                    st.caption(note)

        with right:
            st.markdown("**Verification**")
            if verification.get("status"):
                st.markdown(f"- Status: `{verification['status']}`")
            if verification.get("last_verified"):
                st.markdown(f"- Last checked: `{verification['last_verified']}`")
            if verification.get("publisher"):
                st.caption(f"Source: {verification['publisher']}")
            if verification.get("cycle"):
                st.caption(f"Cycle: {verification['cycle']}")
            evidence = verification.get("evidence")
            if evidence:
                st.caption(f"Evidence: {evidence[:280]}…" if len(evidence) > 280 else f"Evidence: {evidence}")

            app_url = item.get("application_url")
            src_url = item.get("source_url")
            if is_valid_url(app_url):
                st.link_button("Apply ↗", app_url, width="stretch")
            if is_valid_url(src_url):
                st.link_button("Official source ↗", src_url, width="stretch")
            if not is_valid_url(app_url) and not is_valid_url(src_url):
                st.caption("No link recorded.")

            if st.button("⭐ Save", key=f"{key_prefix}_save_{item.get('id')}", width="stretch"):
                save_scholarship(item)


def level_label_from_record(item):
    levels = item.get("supported_levels")
    if not levels:
        raw = item.get("education_level")
        if raw and raw in LEVEL_LABELS:
            levels = [raw]
        elif raw:
            return str(raw)
    if levels:
        return ", ".join(LEVEL_LABELS.get(lv, lv) for lv in levels)
    return "Not specified"


# --------------------------------------------------------------------------- #
# Dashboard tab
# --------------------------------------------------------------------------- #

def metric_card(label, value, hint):
    st.markdown(
        f"""
        <div class="pf-metric">
            <div class="label">{label}</div>
            <div class="value">{value}</div>
            <div class="hint">{hint}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def dashboard_tab():
    profile = st.session_state.profile
    st.markdown(
        """
        <div class="pf-hero">
            <h1>Find scholarships you can actually apply for</h1>
            <p>Eligibility is recalculated live from your profile, the province/domicile rules in the
            workbook, degree level, grades and field of study. Nothing is shown just because it exists.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if profile_completeness(profile) < 50:
        st.info(
            "**Add your details in the sidebar** to unlock accurate matching. "
            "The four fields that matter most: province, education level, percentage/CGPA and field of study."
        )

    try:
        items, errors = evaluate_scholarships(profile, include_ineligible=True)
    except Exception as exc:
        st.error(f"Could not evaluate scholarships: {exc}")
        return

    summary = match_summary(items)
    today = pd.Timestamp.today().normalize()
    upcoming = 0
    for item in items:
        if item.get("eligibility_status") not in ("eligible", "potentially_eligible"):
            continue
        parsed = pd.to_datetime(item.get("deadline"), errors="coerce")
        if not pd.isna(parsed) and parsed >= today:
            upcoming += 1

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        metric_card("Eligible now", summary["eligible"], "All recorded criteria met + open")
    with c2:
        metric_card("Likely eligible", summary["potentially_eligible"], "Criteria met, some details to confirm")
    with c3:
        metric_card("Needs information", summary["insufficient_information"], "Profile or source data incomplete")
    with c4:
        metric_card("Filtered out", summary["ineligible"], "Province, level, grades or field do not match")

    st.caption(
        f"{summary['total']} records evaluated · {upcoming} matching record(s) have a confirmed future deadline"
        + (f" · {len(errors)} unreadable row(s)" if errors else "")
    )

    if summary["eligible"] == 0 and summary["potentially_eligible"] > 0:
        st.info(
            "No scheme currently has **both** a confirmed open cycle and every criterion "
            "satisfied. The *Likely eligible* cards match your profile, but their next "
            "application window is unannounced, a deadline is missing, or the source adds a "
            "condition you must confirm. Open a card to see exactly which."
        )

    # ---------------- filters ----------------
    with st.expander("🔎 Filter results", expanded=False):
        f1, f2, f3 = st.columns([1.2, 1.2, 1.6])
        with f1:
            statuses = st.multiselect(
                "Show statuses",
                options=["eligible", "potentially_eligible", "insufficient_information"],
                default=["eligible", "potentially_eligible", "insufficient_information"],
                format_func=lambda s: STATUS_META[s][0].title(),
                key="dash_statuses",
            )
        with f2:
            sort_by = st.selectbox(
                "Sort by", ["Best match", "Deadline (soonest)", "Name (A–Z)", "Provider (A–Z)"],
                key="dash_sort",
            )
        with f3:
            query = st.text_input("Search name, provider or field", key="dash_query")

        st.checkbox(
            "Also show scholarships I am **not** eligible for (with the reason)",
            key="show_excluded",
        )

    st.divider()

    # ---------------- charts ----------------
    chart_left, chart_right = st.columns(2)
    with chart_left:
        st.markdown("**Your eligibility breakdown**")
        chart_df = pd.DataFrame(
            {"Scholarships": [summary["eligible"], summary["potentially_eligible"],
                              summary["insufficient_information"], summary["ineligible"]]},
            index=["Eligible", "Likely eligible", "Needs info", "Not eligible"],
        )
        st.bar_chart(chart_df, horizontal=True, height=220)

    with chart_right:
        st.markdown("**Where your matches are**")
        province_counts = {}
        for item in items:
            if item.get("eligibility_status") in ("eligible", "potentially_eligible"):
                key = item.get("eligible_provinces") or "All Pakistan"
                province_counts[key] = province_counts.get(key, 0) + 1
        if province_counts:
            top = sorted(province_counts.items(), key=lambda kv: -kv[1])[:6]
            st.bar_chart(
                pd.DataFrame({"Matches": [v for _, v in top]}, index=[k for k, _ in top]),
                horizontal=True, height=220,
            )
        else:
            st.caption("No matches yet — complete your profile.")

    st.divider()

    # ---------------- results ----------------
    visible = [item for item in items if item.get("eligibility_status") != "error"]
    if not st.session_state.show_excluded:
        visible = [item for item in visible if item.get("eligibility_status") != "ineligible"]
    if statuses:
        visible = [item for item in visible if item.get("eligibility_status") in statuses]
    if query:
        needle = query.lower()
        visible = [
            item for item in visible
            if needle in str(item.get("name", "")).lower()
            or needle in str(item.get("provider", "")).lower()
            or needle in str(item.get("field_of_study", "")).lower()
        ]

    if sort_by == "Deadline (soonest)":
        visible.sort(key=lambda i: (i.get("deadline") is None, str(i.get("deadline") or "")))
    elif sort_by == "Name (A–Z)":
        visible.sort(key=lambda i: str(i.get("name") or "").lower())
    elif sort_by == "Provider (A–Z)":
        visible.sort(key=lambda i: str(i.get("provider") or "").lower())

    st.markdown(f"### 🎯 Results ({len(visible)})")
    if not visible:
        st.warning(
            "No scholarship matches the current filters. Try clearing the search box, "
            "switching the status filter, or completing more of your profile."
        )
        return

    shown = visible[: st.session_state.result_limit]
    if len(visible) > len(shown):
        st.caption(
            f"Showing {len(shown)} of {len(visible)}. "
            + ("'Show more' loads another 24."
               if not st.session_state.show_excluded
               else "'Show more' also reveals the records you are not eligible for.")
        )
    for index, item in enumerate(shown):
        render_card(item, key_prefix=f"dash{index}")

    if len(visible) > len(shown):
        if st.button(f"Show more ({len(visible) - len(shown)} remaining)"):
            st.session_state.result_limit += 24
            st.rerun()

    if errors:
        with st.expander(f"⚠️ {len(errors)} unreadable database row(s)"):
            for error in errors[:20]:
                st.caption(error.get("error") or error.get("diagnostics"))


# --------------------------------------------------------------------------- #
# Catalogue tab
# --------------------------------------------------------------------------- #

def catalogue_tab():
    records = load_records(db_mtime())
    profile = st.session_state.profile
    try:
        evaluated, _ = evaluate_scholarships(profile, include_ineligible=True)
    except Exception as exc:
        st.error(f"Could not evaluate scholarships: {exc}")
        return
    status_by_id = {item["id"]: item for item in evaluated}

    st.markdown("## 🔎 All scholarships")
    st.caption(f"{len(records)} curated records from the master workbook, with provenance.")

    f1, f2, f3, f4 = st.columns([1.6, 1, 1, 1])
    with f1:
        query = st.text_input("Search", key="cat_query", placeholder="Name, provider, source title…")
    with f2:
        level_choice = st.selectbox(
            "Level", ["All"] + [LEVEL_LABELS[t] for t in EDUCATION_LEVELS], key="cat_level"
        )
    with f3:
        province_choice = st.selectbox("Province", ["All"] + PROVINCES, key="cat_province")
    with f4:
        destination_choice = st.selectbox("Destination", ["All", "Pakistan", "Abroad"], key="cat_destination")

    rows = []
    for record in records:
        match = status_by_id.get(record.get("id"), {})
        rows.append({
            "Name": record.get("name"),
            "Provider": record.get("provider"),
            "Level": level_label_from_record(match if match else record),
            "Province": record.get("eligible_provinces") or match.get("eligible_provinces") or "All Pakistan",
            "Destination": record.get("study_destination") or match.get("study_destination") or "—",
            "Deadline": record.get("deadline") or "not announced",
            "Status": record.get("status") or "—",
            "Your eligibility": STATUS_META.get(match.get("eligibility_status"), ("—",))[0].title(),
            "Apply": record.get("application_url") or "",
            "Source": record.get("source_url") or "",
            "id": record.get("id"),
        })
    frame = pd.DataFrame(rows)

    if query:
        needle = query.lower()
        mask = (
            frame["Name"].fillna("").str.lower().str.contains(needle, regex=False)
            | frame["Provider"].fillna("").str.lower().str.contains(needle, regex=False)
        )
        frame = frame[mask]
    if level_choice != "All":
        frame = frame[frame["Level"].fillna("").str.contains(level_choice, regex=False)]
    if province_choice != "All":
        frame = frame[frame["Province"].fillna("").str.contains(province_choice, regex=False)]
    if destination_choice == "Pakistan":
        frame = frame[frame["Destination"].fillna("").str.contains("Pakistan", regex=False)]
    elif destination_choice == "Abroad":
        frame = frame[~frame["Destination"].fillna("").str.contains("Pakistan", regex=False)]
        frame = frame[frame["Destination"] != "—"]

    st.markdown(f"**{len(frame)} result(s)**")
    st.dataframe(
        frame.drop(columns=["id"]),
        width="stretch",
        hide_index=True,
        height=520,
        column_config={
            "Apply": st.column_config.LinkColumn("Apply", display_text="Open"),
            "Source": st.column_config.LinkColumn("Source", display_text="Source"),
        },
    )

    st.download_button(
        "⬇️ Download filtered results as CSV",
        data=frame.drop(columns=["id"]).to_csv(index=False).encode("utf-8"),
        file_name="pathfinder_scholarships.csv",
        mime="text/csv",
    )

    st.divider()
    st.markdown("### 📄 Record detail")
    options = {f"{row['Name']} · {row['Provider']}": row["id"] for row in rows}
    if options:
        selected_label = st.selectbox("Select a record", list(options.keys()), key="cat_detail")
        selected_id = options[selected_label]
        record = next((r for r in records if r.get("id") == selected_id), None)
        if record:
            st.markdown(f"**{record.get('name')}** — {record.get('provider')}")
            meta = {
                "Level": level_label_from_record(status_by_id.get(selected_id, record)),
                "Province / domicile": record.get("eligible_provinces") or "All Pakistan",
                "Destination": record.get("study_destination") or "—",
                "Deadline": record.get("deadline") or "not announced",
                "Status": record.get("status"),
                "Funding": record.get("funding") or "—",
                "Verified at": record.get("verified_at"),
            }
            st.table(pd.DataFrame({"Field": list(meta), "Value": [str(v) for v in meta.values()]}))
            match = status_by_id.get(selected_id)
            if match:
                st.markdown(f"**Your eligibility:** {STATUS_META.get(match['eligibility_status'], ('—',))[0].title()}")
                for line in match.get("criteria") or []:
                    st.markdown(f"- {line}")
                st.markdown("**Reasons**")
                for line in (match.get("reasons") or [])[:10]:
                    st.markdown(f"- {line}")
                if match.get("blocking_issues"):
                    st.markdown("**Blocking issues**")
                    for line in match["blocking_issues"][:10]:
                        st.markdown(f"- ❌ {line}")
                if match.get("missing_information"):
                    st.markdown("**Unknown**")
                    for line in match["missing_information"][:10]:
                        st.markdown(f"- ⚠️ {line}")
                if match.get("manual_checks"):
                    st.markdown("**Confirm with the sponsor**")
                    for line in match["manual_checks"][:10]:
                        st.markdown(f"- 🔎 {line}")
                verification = match.get("verification") or {}
                if verification.get("evidence"):
                    st.caption(f"Evidence: {verification['evidence']}")
            if is_valid_url(record.get("source_url")):
                st.link_button("Open official source ↗", record["source_url"])


# --------------------------------------------------------------------------- #
# Assistant tab
# --------------------------------------------------------------------------- #

def assistant_tab():
    st.markdown("## 🤖 Scholarship assistant")
    st.caption(
        "Ask about eligibility, deadlines, documents or SOPs. Off-topic questions are declined. "
        "Anything you say about your education is used to update your profile."
    )

    for message in st.session_state.chat_history:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    with st.form("assistant_form", clear_on_submit=True):
        prompt = st.text_input(
            "Your message",
            placeholder="Ask about scholarships, or tell me your details…",
            label_visibility="collapsed",
        )
        sent = st.form_submit_button("Send ➤", type="primary")

    if not sent or not prompt.strip():
        return
    prompt = prompt.strip()

    st.session_state.chat_history.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.spinner("Checking the database and your eligibility…"):
        from src.ai_agent import process_chat_message

        result = process_chat_message(
            prompt,
            conversation_history=st.session_state.chat_history,
            current_profile=st.session_state.profile,
        )

    st.session_state.profile = result["updated_profile"]
    bump_profile_version()
    assistant_reply = result.get("response") or "I could not produce a response."
    st.session_state.chat_history.append({"role": "assistant", "content": assistant_reply})
    with st.chat_message("assistant"):
        st.markdown(assistant_reply)

    matches = [m for m in (result.get("scholarships") or []) if m.get("record_type") == "scholarship"]
    if matches:
        st.markdown("#### Matches used for this answer")
        for index, match in enumerate(matches[:4]):
            render_card(match, key_prefix=f"chat{index}")

    st.rerun()


# --------------------------------------------------------------------------- #
# Profile tab
# --------------------------------------------------------------------------- #

def profile_tab():
    profile = st.session_state.profile
    st.markdown("## 👤 Student profile")
    st.caption("Everything here is editable and is applied to matching immediately after saving.")
    st.progress(profile_completeness(profile) / 100, text=f"Completeness: {profile_completeness(profile)}%")

    with st.container(border=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            level_options = ["Not set"] + [LEVEL_LABELS[t] for t in EDUCATION_LEVELS]
            level_choice = st.selectbox(
                "Education level",
                options=level_options,
                index=(level_options.index(LEVEL_LABELS[profile["education_level"]])
                       if profile.get("education_level") in EDUCATION_LEVELS else 0),
                key=K("p_level"),
            )
            province_choice = st.selectbox(
                "Province", options=[""] + PROVINCES,
                index=([""] + PROVINCES).index(profile["province"]) if profile.get("province") in PROVINCES else 0,
                key=K("p_province"),
                format_func=lambda v: v or "Select province…",
            )
            domicile_choice = st.selectbox(
                "Domicile", options=[""] + PROVINCES,
                index=([""] + PROVINCES).index(profile["domicile"]) if profile.get("domicile") in PROVINCES else 0,
                key=K("p_domicile"),
                format_func=lambda v: v or "Same as province",
            )
            city = st.text_input("City / district", value=profile.get("city") or "", key=K("p_city"))
        with c2:
            grading = st.radio(
                "Result type", ["Percentage", "CGPA"],
                index=0 if profile.get("marks") is not None or profile.get("cgpa") is None else 1,
                horizontal=True, key=K("p_grading"),
            )
            if grading == "Percentage":
                marks = st.number_input(
                    "Percentage (%)", min_value=0.0, max_value=100.0,
                    value=float(profile.get("marks") or 0.0), step=1.0, key=K("p_marks"),
                )
                cgpa = 0.0
            else:
                cgpa = st.number_input(
                    "CGPA (out of 4.0)", min_value=0.0, max_value=4.0,
                    value=float(profile.get("cgpa") or 0.0), step=0.01, key=K("p_cgpa"),
                )
                marks = 0.0
            field = st.text_input(
                "Field of study", value=profile.get("field_of_study") or "",
                placeholder="e.g. Computer Science", key=K("p_field"),
            )
            destination = st.selectbox(
                "Study destination", DESTINATIONS,
                index=DESTINATIONS.index(profile.get("study_destination") or "Any"),
                key=K("p_destination"),
            )
        with c3:
            age = st.number_input(
                "Age", min_value=0, max_value=100, value=int(profile.get("age") or 0),
                step=1, key=K("p_age"),
            )
            gender_options = ["Not specified", "Female", "Male"]
            gender_value = {"female": "Female", "male": "Male"}.get(
                (profile.get("gender") or "").lower(), "Not specified")
            gender = st.selectbox(
                "Gender", gender_options,
                index=gender_options.index(gender_value),
                key=K("p_gender"),
            )
            income = st.number_input(
                "Household income (PKR)", min_value=0.0, step=1000.0,
                value=float(profile.get("household_income") or 0.0), key=K("p_income"),
            )
            income_period = st.selectbox(
                "Income period", ["Month", "Year"],
                index=0 if (profile.get("income_period") or "month") == "month" else 1,
                key=K("p_income_period"),
            )
            language_test = st.text_input(
                "English test (optional)", value=profile.get("language_test") or "",
                placeholder="e.g. IELTS 6.5", key=K("p_language"),
            )

        if st.button("💾 Save profile", type="primary"):
            new_profile = {
                "education_level": (None if level_choice == "Not set"
                                    else TOKEN_BY_LEVEL_LABEL[level_choice]),
                "province": province_choice or None,
                "domicile": domicile_choice or None,
                "city": city.strip() or None,
                "marks": marks if marks > 0 else None,
                "cgpa": cgpa if cgpa > 0 else None,
                "field_of_study": field.strip() or None,
                "study_destination": None if destination == "Any" else destination,
                "age": int(age) if age > 0 else None,
                "gender": None if gender == "Not specified" else gender.lower(),
                "household_income": income if income > 0 else None,
                "income_period": income_period.lower() if income > 0 else None,
                "language_test": language_test.strip() or None,
            }
            st.session_state.profile = normalize_profile(new_profile)
            bump_profile_version()
            st.success("Profile saved — dashboard recalculated.")
            st.rerun()

    st.markdown("### Current profile")
    snapshot = st.session_state.profile
    table = pd.DataFrame({
        "Field": [
            "Education level", "Province", "Domicile", "City", "Percentage", "CGPA",
            "Field of study", "Destination", "Age", "Gender", "Household income",
            "Income period", "English test",
        ],
        "Value": [
            level_label(snapshot.get("education_level")),
            snapshot.get("province") or "—",
            snapshot.get("domicile") or "—",
            snapshot.get("city") or "—",
            f"{snapshot['marks']}%" if snapshot.get("marks") is not None else "—",
            snapshot.get("cgpa") if snapshot.get("cgpa") is not None else "—",
            snapshot.get("field_of_study") or "—",
            snapshot.get("study_destination") or "Any",
            snapshot.get("age") or "—",
            snapshot.get("gender") or "—",
            f"PKR {snapshot['household_income']:,.0f}" if snapshot.get("household_income") is not None else "—",
            snapshot.get("income_period") or "—",
            snapshot.get("language_test") or "—",
        ],
    })
    st.table(table)

    st.markdown("### Missing details")
    missing = get_missing_profile_fields(snapshot)
    if missing:
        for item in missing:
            st.markdown(f"- {item}")
    else:
        st.success("Your profile is complete.")


# --------------------------------------------------------------------------- #
# Data-health tab
# --------------------------------------------------------------------------- #

def data_health_tab():
    st.markdown("## 🛡️ Data health & verification")
    st.caption(
        "The workbook is a curated snapshot, not live data. This audit cross-checks each record's "
        "provenance and flags anything internally inconsistent before you rely on it."
    )
    records = load_records(db_mtime())
    report = audit_records(records)
    summary = report["summary"]

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        metric_card("Records", summary["total_records"], "In the master workbook")
    with c2:
        metric_card("Errors", summary["severity"]["error"], "Contradictions that need a fix")
    with c3:
        metric_card("Warnings", summary["severity"]["warning"], "Stale or incomplete provenance")
    with c4:
        metric_card("Info", summary["severity"]["info"], "Expected: many cycles unannounced")

    st.markdown("### Checks")
    checks = summary["checks"]
    if checks:
        check_frame = pd.DataFrame(
            {"Check": list(checks), "Records": list(checks.values())}
        ).sort_values("Records", ascending=False)
        st.dataframe(check_frame, width="stretch", hide_index=True, height=260)

    st.markdown("### Issues")
    issues = report["issues"]
    if issues:
        issue_frame = pd.DataFrame(issues)
        severity_filter = st.multiselect(
            "Severity", ["error", "warning", "info"], default=["error", "warning"],
            key="health_severity",
        )
        view = issue_frame[issue_frame["severity"].isin(severity_filter)] if severity_filter else issue_frame
        st.dataframe(
            view[["id", "severity", "check", "name", "detail"]],
            width="stretch", hide_index=True, height=420,
        )
        st.download_button(
            "⬇️ Download audit as CSV",
            data=issue_frame.to_csv(index=False).encode("utf-8"),
            file_name="pathfinder_data_audit.csv",
            mime="text/csv",
        )
    else:
        st.success("No issues found.")

    st.markdown("### Provenance")
    overview = pd.DataFrame(verification_overview(records))
    st.dataframe(
        overview,
        width="stretch",
        hide_index=True,
        height=420,
        column_config={
            "source_url": st.column_config.LinkColumn("Source", display_text="Open"),
        },
    )

    unverified = overview[overview["verification_status"].fillna("") != "confirmed"]
    if not unverified.empty:
        st.warning(
            f"{len(unverified)} record(s) are not marked 'confirmed' by the source review — "
            "check the sponsor page before applying."
        )

    st.markdown("### Live re-verification (optional)")
    import os

    if not os.getenv("GEMINI_API_KEY"):
        st.caption(
            "Set `GEMINI_API_KEY` to enable live re-verification of a record against its "
            "official source with Gemini Search. Without a key, the workbook provenance above "
            "is the authoritative check."
        )
        return

    from src.ai_agent import get_gemini_client, get_model_name
    from src.web_verification import verify_scholarship_record

    client, client_error = get_gemini_client()
    if client is None:
        st.caption(f"Gemini is not available: {client_error}")
        return

    options = {f"{r.get('name')} · {r.get('provider')}": r for r in records}
    choice = st.selectbox("Record to verify", list(options), key="verify_record")
    if st.button("🔍 Verify against the official source"):
        with st.spinner("Searching the official source…"):
            try:
                result = verify_scholarship_record(
                    options[choice], client=client, model_name=get_model_name(),
                    force_refresh=True,
                )
            except Exception as exc:
                st.error(f"Verification failed: {exc}")
            else:
                st.json(result)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main():
    render_sidebar()

    st.title("🎓 PathFinder AI")
    st.caption("Scholarship discovery for Pakistani students · eligibility is indicative, always confirm with the sponsor")

    tab_dashboard, tab_catalogue, tab_assistant, tab_profile, tab_health = st.tabs(
        ["🏠 Dashboard", "🔎 All Scholarships", "🤖 Assistant", "👤 Profile", "🛡️ Data Health"]
    )
    with tab_dashboard:
        dashboard_tab()
    with tab_catalogue:
        catalogue_tab()
    with tab_assistant:
        assistant_tab()
    with tab_profile:
        profile_tab()
    with tab_health:
        data_health_tab()


if __name__ == "__main__":
    main()
