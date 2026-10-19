from datetime import date

from src.matching import evaluate_scholarships, find_matches, match_summary

TODAY = date(2026, 10, 19)

BASE_RAW = {
    "study_level": "Undergraduate",
    "application_status": "open",
    "deadline_date": "2027-01-15",
    "destination_country": "Pakistan",
    "pakistan_eligibility": "confirmed",
}

SINDH_PROFILE = {
    "nationality": "Pakistani",
    "province": "Sindh",
    "education_level": "bachelor",
    "marks": 85.0,
    "field_of_study": "Computer Science",
    "age": 20,
}


def _seed(make_scholarship):
    make_scholarship(
        {"name": "Sindh Talent Scholarship", "provider": "Govt of Sindh",
         "education_level": "Undergraduate", "status": "open", "deadline": "2027-01-15"},
        dict(BASE_RAW, province_region="Sindh"),
    )
    make_scholarship(
        {"name": "KP Merit Award", "provider": "Govt of KP",
         "education_level": "Undergraduate", "status": "open", "deadline": "2027-02-15"},
        dict(BASE_RAW, province_region="Khyber Pakhtunkhwa",
             geographic_scope="KP domicile", domicile_rule="Khyber Pakhtunkhwa domicile required"),
    )
    make_scholarship(
        {"name": "National Merit Award", "provider": "HEC",
         "education_level": "Undergraduate", "status": "open", "deadline": "2027-03-15"},
        dict(BASE_RAW, province_region="All Pakistan"),
    )
    make_scholarship(
        {"name": "Punjab Outreach", "provider": "Govt of Punjab",
         "education_level": "Undergraduate", "status": "open", "deadline": "2027-04-15"},
        dict(BASE_RAW, province_region="Punjab"),
    )


def test_sindh_student_only_gets_sindh_and_national(temp_db, make_scholarship):
    _seed(make_scholarship)
    names = [item["name"] for item in find_matches(SINDH_PROFILE, today=TODAY)]
    assert "Sindh Talent Scholarship" in names
    assert "National Merit Award" in names
    assert "KP Merit Award" not in names
    assert "Punjab Outreach" not in names


def test_ineligible_records_are_available_on_request(temp_db, make_scholarship):
    _seed(make_scholarship)
    items, errors = evaluate_scholarships(SINDH_PROFILE, today=TODAY, include_ineligible=True)
    assert not errors
    summary = match_summary(items)
    assert summary["eligible"] == 2
    assert summary["ineligible"] == 2
    kp = next(item for item in items if item["name"] == "KP Merit Award")
    assert kp["eligibility_status"] == "ineligible"
    assert kp["blocking_issues"]


def test_results_are_ranked_by_status_then_deadline(temp_db, make_scholarship):
    _seed(make_scholarship)
    items = find_matches(SINDH_PROFILE, today=TODAY)
    assert items[0]["eligibility_status"] == "eligible"
    assert items[0]["name"] == "Sindh Talent Scholarship"


def test_include_ineligible_flag_ranks_them_last(temp_db, make_scholarship):
    _seed(make_scholarship)
    items = find_matches(SINDH_PROFILE, today=TODAY, include_ineligible=True)
    statuses = [item["eligibility_status"] for item in items]
    assert statuses[-1] == "ineligible"


def test_limit_is_applied_to_real_records(temp_db, make_scholarship):
    _seed(make_scholarship)
    items = find_matches(SINDH_PROFILE, limit=1, today=TODAY)
    assert len(items) == 1


def test_empty_profile_never_claims_eligibility(temp_db, make_scholarship):
    _seed(make_scholarship)
    items = find_matches({}, today=TODAY)
    assert all(item["eligibility_status"] != "eligible" for item in items)


def test_enriched_fields_are_returned(temp_db, make_scholarship):
    _seed(make_scholarship)
    items = find_matches(SINDH_PROFILE, today=TODAY)
    sindh = next(item for item in items if item["name"] == "Sindh Talent Scholarship")
    assert sindh["eligible_provinces"] == "Sindh"
    assert sindh["criteria"]
    assert sindh["verification"]
