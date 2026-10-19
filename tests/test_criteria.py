from src.criteria import (
    _derive_grade,
    _derive_income,
    _levels_in,
    derive,
    is_all_pakistan,
    match_field,
    provinces_in,
)


def test_all_pakistan_detection():
    assert is_all_pakistan("All Pakistan")
    assert is_all_pakistan("Nationwide")
    assert not is_all_pakistan("Khyber Pakhtunkhwa")


def test_province_parsing():
    assert provinces_in("Sindh domicile") == ["Sindh"]
    assert provinces_in("Khyber Pakhtunkhwa") == ["Khyber Pakhtunkhwa"]
    assert set(provinces_in("Balochistan; Gilgit-Baltistan; former FATA (KP)")) == {
        "Khyber Pakhtunkhwa", "Balochistan", "Gilgit-Baltistan",
    }
    assert provinces_in("All Pakistan") == []


def test_all_pakistan_is_not_narrowed_by_preference():
    result = derive({
        "province_region": "All Pakistan; South Punjab preference",
        "geographic_scope": None,
        "domicile_rule": None,
    })
    assert "eligible_provinces" not in result["criteria"]
    assert result["notes"]


def test_province_derived_from_domicile_when_scope_is_nationwide():
    result = derive({
        "province_region": "All Pakistan",
        "domicile_rule": "Sindh domicile",
    })
    assert result["criteria"]["eligible_provinces"] == "Sindh"
    assert result["criteria"]["eligible_domiciles"] == "Sindh"


def test_levels_for_messy_workbook_values():
    assert _levels_in("Undergraduate") == ["bachelor"]
    assert _levels_in("MS / MPhil / PhD (inland)") == ["master", "phd"]
    assert _levels_in("Master’s") == ["master"]
    assert _levels_in("Intermediate / DAE") == ["intermediate", "vocational"]
    assert _levels_in("Nursery through BS") == ["matric", "intermediate", "bachelor"]
    assert _levels_in("Postdoctoral / lectureship") == ["phd"]
    assert _levels_in("Research fellowship") == []
    assert _levels_in("School / college") == ["matric", "intermediate"]


def test_grade_alternatives():
    assert _derive_grade("At least 60% or 3.0 GPA/CGPA") == (60.0, 3.0, "or")
    assert _derive_grade("CGPA ≥3.0 in last exam") == (None, 3.0, None)
    assert _derive_grade("At least 80% Matric/O-level equivalent") == (80.0, None, None)
    assert _derive_grade("No numeric requirement") == (None, None, None)


def test_income_period_and_units():
    assert _derive_income("Parent/guardian monthly income ≤ PKR 100,000") == (100000.0, "month")
    assert _derive_income("Annual income under PKR 400,000") == (400000.0, "year")
    assert _derive_income("annual parental net income ≤ PKR 1.5m") == (1500000.0, "year")


def test_destination_inference_without_country():
    erasmus = derive({
        "opportunity_name": "Erasmus Mundus Joint Master in Cybersecurity",
        "sponsor_type": "supranational / university consortium",
        "official_source_url": "https://erasmus.example.edu",
    })
    assert erasmus["criteria"]["study_destination"] == "International"

    domestic = derive({
        "opportunity_name": "Habib Merit Scholarship",
        "sponsor_type": "university",
        "official_source_url": "https://habib.edu.pk/scholarships",
    })
    assert domestic["criteria"]["study_destination"] == "Pakistan"

    overseas = derive({
        "opportunity_name": "KP Chief Minister Educational Endowment Fund — overseas PhD",
        "sponsor_type": "provincial government",
        "official_source_url": "https://hed.gkp.pk/content/cmeef",
    })
    assert overseas["criteria"]["study_destination"] == "International"


def test_field_matching():
    cs = "Computer Science"
    assert match_field("IT, engineering, medicine, business administration", cs) == "pass"
    assert match_field("Master's Programme in Cybersecurity", cs) == "pass"
    assert match_field("European Master in European Forestry", cs) == "fail"
    assert match_field("Partner-specified eligible fields; programme list differs", cs) == "unknown"
    assert match_field("Biological Sciences; Engineering; Medicine; Physical Sciences; Technology", cs) == "pass"
    assert match_field("", cs) == "unknown"


def test_manual_checks_for_target_groups():
    result = derive({
        "study_level": "Undergraduate",
        "target_group": "Children of KP education employees",
    })
    assert any("beneficiary group" in check for check in result["manual_checks"])


def test_gender_rule_derived():
    result = derive({"gender_rule": "Female", "study_level": "Undergraduate"})
    assert result["criteria"]["gender"] == "female"
