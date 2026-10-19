from datetime import date

from src.eligibility import check_eligibility

TODAY = date(2026, 10, 19)

OPEN_PAKISTAN = {
    "name": "Sindh Education Endowment Fund",
    "provider": "Government of Sindh",
    "education_level": "Undergraduate",
    "province_region": "Sindh",
    "geographic_scope": "Sindh domicile",
    "domicile_rule": "Sindh domicile",
    "study_level": "Undergraduate",
    "application_status": "open",
    "deadline_date": "2027-01-15",
    "destination_country": "Pakistan",
    "pakistan_eligibility": "confirmed",
}

PROFILE_SINDH = {
    "nationality": "Pakistani",
    "province": "Sindh",
    "education_level": "bachelor",
    "marks": 85.0,
    "field_of_study": "Computer Science",
    "age": 20,
}


def _record(raw):
    return {"id": 1, "name": raw.get("name"), "provider": raw.get("provider"),
            "raw_data": raw}


def test_sindh_student_is_not_eligible_for_kpk_domicile():
    raw = dict(OPEN_PAKISTAN, province_region="Khyber Pakhtunkhwa",
               geographic_scope="KP domicile", domicile_rule="Khyber Pakhtunkhwa domicile required")
    result = check_eligibility(PROFILE_SINDH, _record(raw), today=TODAY)
    assert result["eligibility_status"] == "ineligible"
    assert any("province" in reason.lower() for reason in result["blocking_issues"])


def test_kpk_student_passes_kpk_province_rule():
    raw = dict(OPEN_PAKISTAN, province_region="Khyber Pakhtunkhwa",
               geographic_scope="KP domicile", domicile_rule="Khyber Pakhtunkhwa domicile required")
    profile = dict(PROFILE_SINDH, province="Khyber Pakhtunkhwa")
    result = check_eligibility(profile, _record(raw), today=TODAY)
    assert result["eligibility_status"] != "ineligible"
    assert any("Khyber Pakhtunkhwa" in reason for reason in result["reasons"])


def test_sindh_student_matches_sindh_scholarship():
    result = check_eligibility(PROFILE_SINDH, _record(OPEN_PAKISTAN), today=TODAY)
    assert result["eligibility_status"] == "eligible"


def test_nationwide_scholarship_is_open_to_every_province():
    raw = dict(OPEN_PAKISTAN, province_region="All Pakistan", geographic_scope=None,
               domicile_rule=None)
    for province in ("Sindh", "Punjab", "Khyber Pakhtunkhwa", "Balochistan"):
        result = check_eligibility(dict(PROFILE_SINDH, province=province), _record(raw), today=TODAY)
        assert result["eligibility_status"] == "eligible", province


def test_expired_or_closed_is_ineligible():
    raw = dict(OPEN_PAKISTAN, application_status="closed")
    assert check_eligibility(PROFILE_SINDH, _record(raw), today=TODAY)["eligibility_status"] == "ineligible"

    raw = dict(OPEN_PAKISTAN, deadline_date="2026-01-01")
    assert check_eligibility(PROFILE_SINDH, _record(raw), today=TODAY)["eligibility_status"] == "ineligible"


def test_rolling_status_counts_as_open():
    raw = dict(OPEN_PAKISTAN, application_status="rolling", deadline_date=None)
    result = check_eligibility(PROFILE_SINDH, _record(raw), today=TODAY)
    assert any("open: rolling" in reason for reason in result["reasons"])


def test_grade_or_requirement_accepts_cgpa_alternative():
    raw = dict(OPEN_PAKISTAN, minimum_grade="At least 60% or CGPA 3.0")
    profile = dict(PROFILE_SINDH, marks=None, cgpa=3.5)
    result = check_eligibility(profile, _record(raw), today=TODAY)
    assert result["eligibility_status"] == "eligible"


def test_grade_or_requirement_fails_only_when_both_fail():
    raw = dict(OPEN_PAKISTAN, minimum_grade="At least 60% or CGPA 3.0")
    profile = dict(PROFILE_SINDH, marks=50.0, cgpa=2.5)
    result = check_eligibility(profile, _record(raw), today=TODAY)
    assert result["eligibility_status"] == "ineligible"
    assert any("Grade requirement" in issue for issue in result["blocking_issues"])


def test_grade_or_requirement_is_unknown_when_only_one_side_is_known():
    raw = dict(OPEN_PAKISTAN, minimum_grade="At least 60% or CGPA 3.0")
    profile = dict(PROFILE_SINDH, marks=50.0, cgpa=None)
    result = check_eligibility(profile, _record(raw), today=TODAY)
    assert result["eligibility_status"] == "potentially_eligible"


def test_destination_abroad_excludes_pakistan_only_scheme():
    profile = dict(PROFILE_SINDH, study_destination="Abroad")
    result = check_eligibility(profile, _record(OPEN_PAKISTAN), today=TODAY)
    assert result["eligibility_status"] == "ineligible"
    assert any("Pakistan" in issue for issue in result["blocking_issues"])


def test_destination_pakistan_excludes_foreign_scheme():
    raw = dict(OPEN_PAKISTAN, destination_country="United Kingdom")
    profile = dict(PROFILE_SINDH, study_destination="Pakistan")
    result = check_eligibility(profile, _record(raw), today=TODAY)
    assert result["eligibility_status"] == "ineligible"


def test_destination_any_accepts_both():
    for destination in (None, "Both"):
        raw = dict(OPEN_PAKISTAN, destination_country="United Kingdom")
        profile = dict(PROFILE_SINDH, study_destination=destination)
        result = check_eligibility(profile, _record(raw), today=TODAY)
        assert result["eligibility_status"] != "ineligible"


def test_domicile_falls_back_to_province():
    raw = dict(OPEN_PAKISTAN, province_region="All Pakistan", geographic_scope=None,
               domicile_rule="Punjab domicile")
    profile = dict(PROFILE_SINDH, province="Sindh")
    result = check_eligibility(profile, _record(raw), today=TODAY)
    assert result["eligibility_status"] == "ineligible"


def test_unsupported_level_is_not_claimed_eligible():
    raw = dict(OPEN_PAKISTAN, study_level="Research fellowship")
    result = check_eligibility(PROFILE_SINDH, _record(raw), today=TODAY)
    assert result["eligibility_status"] != "eligible"
    assert result["manual_checks"]


def test_manual_target_group_caps_status_at_potentially_eligible():
    raw = dict(OPEN_PAKISTAN, target_group="Children of KP education employees")
    result = check_eligibility(PROFILE_SINDH, _record(raw), today=TODAY)
    assert result["eligibility_status"] == "potentially_eligible"
    assert result["manual_checks"]


def test_unannounced_cycle_is_potentially_eligible_not_eligible():
    raw = dict(OPEN_PAKISTAN, application_status="not_announced", deadline_date=None)
    result = check_eligibility(PROFILE_SINDH, _record(raw), today=TODAY)
    assert result["eligibility_status"] == "potentially_eligible"
    assert any("not announced" in miss for miss in result["missing_information"])


def test_female_only_scheme_checks_gender():
    raw = dict(OPEN_PAKISTAN, gender_rule="Female")
    male = dict(PROFILE_SINDH, gender="male")
    result = check_eligibility(male, _record(raw), today=TODAY)
    assert result["eligibility_status"] == "ineligible"

    female = dict(PROFILE_SINDH, gender="female")
    assert check_eligibility(female, _record(raw), today=TODAY)["eligibility_status"] == "eligible"


def test_missing_profile_bits_are_reported_not_passed():
    raw = dict(OPEN_PAKISTAN, minimum_grade="At least 60%")
    result = check_eligibility({"province": "Sindh"}, _record(raw), today=TODAY)
    assert result["eligibility_status"] != "eligible"
    assert any("marks" in miss for miss in result["missing_information"])
