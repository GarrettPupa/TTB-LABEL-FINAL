import pytest

from backend.app.comparison import (
    FUZZY_MATCH_THRESHOLD,
    compare_alcohol_content,
    compare_brand_name,
    compare_class_type,
    compare_country_of_origin,
    compare_government_warning,
    compare_net_contents,
    compare_producer,
    verify_application,
)
from backend.app.models import (
    ApplicationData,
    ExtractedLabel,
    FieldOutcome,
    VerificationVerdict,
)


GOVERNMENT_WARNING = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, women should not "
    "drink alcoholic beverages during pregnancy because of the risk of birth "
    "defects. (2) Consumption of alcoholic beverages impairs your ability to "
    "drive a car or operate machinery, and may cause health problems."
)


def make_application(**overrides: str) -> ApplicationData:
    values = {
        "application_id": "TTB-0001",
        "image_reference": "labels/TTB-0001.png",
        "brand_name": "Old Harbor",
        "class_type": "Kentucky Straight Bourbon Whiskey",
        "producer": "Old Harbor Distilling Company",
        "country_of_origin": "United States",
        "alcohol_content": "45% Alc./Vol.",
        "net_contents": "750 mL",
        "government_warning": GOVERNMENT_WARNING,
    }
    values.update(overrides)
    return ApplicationData(**values)


def make_extracted(**overrides: str | None) -> ExtractedLabel:
    values: dict[str, str | None] = {
        "brand_name": "OLD HARBOR",
        "class_type": "Kentucky Straight Bourbon Whiskey",
        "producer": "Old Harbor Distilling Co.",
        "country_of_origin": "U.S.A.",
        "alcohol_content": "45.0%",
        "net_contents": "0.75 L",
        "government_warning": GOVERNMENT_WARNING,
    }
    values.update(overrides)
    return ExtractedLabel(**values)


@pytest.mark.parametrize(
    ("comparison", "expected", "extracted"),
    [
        (compare_brand_name, "Old-Harbor!", " old harbor "),
        (
            compare_class_type,
            "Kentucky Straight Bourbon Whiskey",
            "KENTUCKY, STRAIGHT BOURBON WHISKEY",
        ),
        (
            compare_producer,
            "Old Harbor Distilling Company",
            "Old Harbor Distilling Co.",
        ),
    ],
)
def test_fuzzy_fields_normalize_and_match(comparison, expected, extracted) -> None:
    result = comparison(expected, extracted)

    assert result.outcome is FieldOutcome.PASS
    assert result.strategy == f"fuzzy_ratio>={FUZZY_MATCH_THRESHOLD:g}"
    assert result.score is not None
    assert result.score >= FUZZY_MATCH_THRESHOLD


def test_fuzzy_field_below_threshold_fails() -> None:
    result = compare_brand_name("Old Harbor", "Completely Different Brand")

    assert result.outcome is FieldOutcome.FAIL
    assert result.score is not None
    assert result.score < FUZZY_MATCH_THRESHOLD


@pytest.mark.parametrize(
    ("expected", "extracted"),
    [
        ("United States", "USA"),
        ("United States of America", "U.S.A."),
        ("United Kingdom", "Great Britain"),
        ("Mexico", " mexico "),
    ],
)
def test_country_normalizes_known_synonyms(expected: str, extracted: str) -> None:
    result = compare_country_of_origin(expected, extracted)

    assert result.outcome is FieldOutcome.PASS
    assert result.strategy == "country_synonym_exact"


def test_country_does_not_use_fuzzy_matching() -> None:
    result = compare_country_of_origin("United States", "United Kingdom")

    assert result.outcome is FieldOutcome.FAIL
    assert result.score is None


@pytest.mark.parametrize(
    ("expected", "extracted"),
    [
        ("45%", "45.0% Alc./Vol."),
        ("Alcohol 45.0 percent by volume", "44.9%"),
        ("12.5", "12.59% ABV"),
    ],
)
def test_abv_normalizes_numeric_values_with_tolerance(
    expected: str, extracted: str
) -> None:
    result = compare_alcohol_content(expected, extracted)

    assert result.outcome is FieldOutcome.PASS
    assert result.strategy == "abv_numeric_tolerance_0.1"


def test_abv_outside_tolerance_fails() -> None:
    result = compare_alcohol_content("45%", "44.89%")

    assert result.outcome is FieldOutcome.FAIL


def test_abv_unparseable_value_needs_review() -> None:
    result = compare_alcohol_content("45%", "forty-five percent")

    assert result.outcome is FieldOutcome.REVIEW
    assert "parse" in result.reason.lower()


@pytest.mark.parametrize(
    ("expected", "extracted"),
    [
        ("750 mL", "750ml"),
        ("750 mL", "0.75 L"),
        ("1 L", "1000 milliliters"),
        ("12 fl oz", "354.88235475 mL"),
    ],
)
def test_net_contents_converts_supported_units(expected: str, extracted: str) -> None:
    result = compare_net_contents(expected, extracted)

    assert result.outcome is FieldOutcome.PASS
    assert result.strategy == "volume_ml_tolerance_0.5"


def test_net_contents_mismatch_fails() -> None:
    result = compare_net_contents("750 mL", "700 mL")

    assert result.outcome is FieldOutcome.FAIL


def test_net_contents_unparseable_value_needs_review() -> None:
    result = compare_net_contents("750 mL", "one bottle")

    assert result.outcome is FieldOutcome.REVIEW


def test_government_warning_collapses_whitespace_only() -> None:
    extracted = GOVERNMENT_WARNING.replace(" ", "\n  ")

    result = compare_government_warning(GOVERNMENT_WARNING, extracted)

    assert result.outcome is FieldOutcome.PASS
    assert result.strategy == "exact_case_sensitive_whitespace_collapsed"
    assert result.expected_value == GOVERNMENT_WARNING
    assert result.extracted_value == extracted


def test_government_warning_is_strictly_case_sensitive() -> None:
    extracted = GOVERNMENT_WARNING.replace("GOVERNMENT WARNING", "Government Warning")

    result = compare_government_warning(GOVERNMENT_WARNING, extracted)

    assert result.outcome is FieldOutcome.FAIL
    assert result.score is None
    assert result.extracted_value == extracted


def test_missing_extracted_value_is_review_not_match() -> None:
    result = compare_brand_name("Old Harbor", None)

    assert result.outcome is FieldOutcome.REVIEW
    assert result.expected_value == "Old Harbor"
    assert result.extracted_value is None


def test_verification_passes_when_every_field_passes() -> None:
    result = verify_application(make_application(), make_extracted())

    assert result.application_id == "TTB-0001"
    assert result.verdict is VerificationVerdict.PASS
    assert len(result.fields) == 7
    assert all(field.outcome is FieldOutcome.PASS for field in result.fields)


def test_any_field_failure_makes_verification_need_review() -> None:
    result = verify_application(
        make_application(),
        make_extracted(government_warning=GOVERNMENT_WARNING.lower()),
    )

    assert result.verdict is VerificationVerdict.NEEDS_REVIEW
    warning = next(
        field for field in result.fields if field.field == "government_warning"
    )
    assert warning.outcome is FieldOutcome.FAIL


def test_review_field_makes_verification_need_review() -> None:
    result = verify_application(make_application(), make_extracted(brand_name=None))

    assert result.verdict is VerificationVerdict.NEEDS_REVIEW
    brand = next(field for field in result.fields if field.field == "brand_name")
    assert brand.outcome is FieldOutcome.REVIEW
