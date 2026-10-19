from src.profile import (
    canonical_destination,
    canonical_education,
    extract_profile_from_text,
    normalize_profile,
    summarize_profile,
)


def test_education_labels_normalize_to_canonical_tokens():
    assert canonical_education("Undergraduate / Bachelors") == "bachelor"
    assert canonical_education("Postgraduate / Masters") == "master"
    assert canonical_education("Doctoral / PhD") == "phd"
    assert canonical_education("Matriculation / O-Level") == "matric"
    assert canonical_education("Intermediate / A-Level") == "intermediate"


def test_normalize_profile_keeps_marks_and_cgpa_separate():
    profile = normalize_profile({"marks": 85, "cgpa": 3.5})
    assert profile["marks"] == 85.0
    assert profile["cgpa"] == 3.5


def test_destination_normalization():
    assert canonical_destination("Any") is None
    assert canonical_destination("Both") is None
    assert canonical_destination("Pakistan") == "Pakistan"
    assert canonical_destination("Abroad") == "Abroad"
    assert canonical_destination("UK") == "Abroad"


def test_extract_profile_from_a_sentence():
    profile = extract_profile_from_text("I am from Sindh, doing BS Computer Science with 85%")
    assert profile["province"] == "Sindh"
    assert profile["education_level"] == "bachelor"
    assert profile["field_of_study"] == "Computer Science"
    assert profile["marks"] == 85.0


def test_extract_cgpa_and_destination():
    profile = extract_profile_from_text("I want to study abroad, my cgpa is 3.6")
    assert profile["cgpa"] == 3.6
    assert profile["study_destination"] == "Abroad"


def test_extract_fraction_marks():
    profile = extract_profile_from_text("I got 900/1100 in FSc from Punjab")
    assert profile["marks"] == 81.82
    assert profile["province"] == "Punjab"
    assert profile["education_level"] == "intermediate"


def test_questions_do_not_overwrite_the_profile():
    profile = extract_profile_from_text("Is the KPK scholarship open?")
    assert profile["province"] is None
    assert profile["education_level"] is None


def test_summary_mentions_any_destination():
    profile = normalize_profile({"province": "Sindh"})
    assert "Destination: Any" in summarize_profile(profile)


def test_normalize_accepts_percentage_strings():
    profile = normalize_profile({"marks": "85%", "cgpa": "3.50"})
    assert profile["marks"] == 85.0
    assert profile["cgpa"] == 3.5
