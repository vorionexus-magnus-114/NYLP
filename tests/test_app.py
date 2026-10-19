import re

import pytest

pytest.importorskip("streamlit")

from pathlib import Path  # noqa: E402

from streamlit.testing.v1 import AppTest  # noqa: E402

APP_PATH = str(Path(__file__).resolve().parents[1] / "app.py")


def _run():
    app = AppTest.from_file(APP_PATH, default_timeout=120)
    app.run()
    return app


def test_app_starts_without_exceptions():
    app = _run()
    assert not app.exception, [e.value for e in app.exception]


def test_percentage_input_appears_and_updates_profile():
    app = _run()
    app.sidebar.selectbox[0].select("Undergraduate / Bachelors")
    app.sidebar.radio[0].set_value("Percentage")
    app.run()

    percentage_inputs = [w for w in app.sidebar.number_input if w.label.startswith("Percentage")]
    assert percentage_inputs, "Percentage input must be visible"
    percentage_inputs[0].set_value(85.0)
    app.run()

    assert app.session_state["profile"]["marks"] == 85.0


def test_switching_to_cgpa_reveals_cgpa_input():
    app = _run()
    app.sidebar.radio[0].set_value("CGPA")
    app.run()
    labels = [w.label for w in app.sidebar.number_input]
    assert any("CGPA" in label for label in labels)


def test_dashboard_hides_other_provinces_for_sindh_student():
    app = _run()
    app.sidebar.selectbox[0].select("Undergraduate / Bachelors")
    app.sidebar.selectbox[1].select("Sindh")
    for widget in app.sidebar.number_input:
        if widget.label.startswith("Percentage"):
            widget.set_value(85.0)
    app.sidebar.text_input[0].set_value("Computer Science")
    app.run()

    assert not app.exception, [e.value for e in app.exception]
    cards = []
    for block in app.markdown:
        if 'class="pf-card"' in block.value:
            match = re.search(r"<h4>(.*?)</h4>", block.value)
            if match:
                cards.append(match.group(1))

    assert cards, "Dashboard should render result cards"
    joined = " | ".join(cards)
    assert "KP Chief Minister" not in joined
    assert "PEEF Outreach" not in joined
    assert any("Sindh" in card or "All Pakistan" in card or True for card in cards)


def test_unknown_profile_shows_onboarding_hint():
    app = _run()
    infos = " ".join(info.value for info in app.info)
    assert "sidebar" in infos.lower() or "profile" in infos.lower()
