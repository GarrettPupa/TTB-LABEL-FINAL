import re
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Callable

from backend.app.models import (
    ApplicationData,
    ExtractedLabel,
    FieldName,
    FieldOutcome,
    FieldResult,
    VerificationResult,
    VerificationVerdict,
)


FUZZY_MATCH_THRESHOLD = 90.0
ABV_TOLERANCE = Decimal("0.1")
VOLUME_TOLERANCE_ML = Decimal("0.5")

_NUMBER_PATTERN = re.compile(r"(?<!\w)(\d+(?:\.\d+)?)")
_PERCENT_ABV_PATTERN = re.compile(
    r"(?<!\w)(\d+(?:\.\d+)?)\s*(?:%|percent(?:age)?\b)",
    re.IGNORECASE,
)
_MARKED_ABV_PATTERN = re.compile(
    r"(?<!\w)(\d+(?:\.\d+)?)\s*(?:abv\b|alc\.?\s*/?\s*vol\.?)",
    re.IGNORECASE,
)
_PROOF_PATTERN = re.compile(
    r"(?<!\w)(\d+(?:\.\d+)?)\s*proof\b",
    re.IGNORECASE,
)
_VOLUME_PATTERN = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>fluid\s*ounces?|fl\s*oz|ounces?|oz|"
    r"millilit(?:ers?|res?)|ml|centilit(?:ers?|res?)|cl|"
    r"lit(?:ers?|res?)|l)\b"
)
_PRODUCER_ROLE = (
    r"(?:produced|distilled|bottled|imported|manufactured|distributed|"
    r"blended|vinted|cellared)"
)
_PRODUCER_PREFIX_PATTERN = re.compile(
    rf"^{_PRODUCER_ROLE}(?:\s+(?:and\s+)?{_PRODUCER_ROLE})*\s+by\s+"
)
_COUNTRY_PREFIX_PATTERN = re.compile(
    r"^(?:product\s+of|made\s+in|produced\s+in|bottled\s+in|"
    r"imported\s+from)\s+"
)

_COUNTRY_ALIASES = {
    "america": "united states",
    "u s": "united states",
    "u s a": "united states",
    "united states": "united states",
    "united states of america": "united states",
    "us": "united states",
    "usa": "united states",
    "britain": "united kingdom",
    "great britain": "united kingdom",
    "u k": "united kingdom",
    "uk": "united kingdom",
    "united kingdom": "united kingdom",
}

_VOLUME_FACTORS_ML = {
    "ml": Decimal("1"),
    "milliliter": Decimal("1"),
    "milliliters": Decimal("1"),
    "millilitre": Decimal("1"),
    "millilitres": Decimal("1"),
    "cl": Decimal("10"),
    "centiliter": Decimal("10"),
    "centiliters": Decimal("10"),
    "centilitre": Decimal("10"),
    "centilitres": Decimal("10"),
    "l": Decimal("1000"),
    "liter": Decimal("1000"),
    "liters": Decimal("1000"),
    "litre": Decimal("1000"),
    "litres": Decimal("1000"),
    "oz": Decimal("29.5735295625"),
    "ounce": Decimal("29.5735295625"),
    "ounces": Decimal("29.5735295625"),
    "fl oz": Decimal("29.5735295625"),
    "fluid ounce": Decimal("29.5735295625"),
    "fluid ounces": Decimal("29.5735295625"),
}


def _normalize_words(value: str) -> str:
    return " ".join(re.sub(r"[\W_]+", " ", value.casefold()).split())


def _collapse_whitespace(value: str) -> str:
    return " ".join(value.split())


def _normalize_producer(value: str) -> str:
    normalized = _normalize_words(value)
    return _PRODUCER_PREFIX_PATTERN.sub("", normalized, count=1)


def _missing_result(
    field: FieldName, expected: str, extracted: str | None, strategy: str
) -> FieldResult | None:
    if extracted is None or not extracted.strip():
        return FieldResult(
            field=field,
            expected_value=expected,
            extracted_value=extracted,
            strategy=strategy,
            outcome=FieldOutcome.REVIEW,
            reason="Extracted value is missing; human review is required.",
        )
    return None


def _compare_fuzzy(
    field: FieldName,
    expected: str,
    extracted: str | None,
    *,
    normalize: Callable[[str], str] = _normalize_words,
) -> FieldResult:
    strategy = f"fuzzy_ratio>={FUZZY_MATCH_THRESHOLD:g}"
    if missing := _missing_result(field, expected, extracted, strategy):
        return missing

    assert extracted is not None
    normalized_expected = normalize(expected)
    normalized_extracted = normalize(extracted)
    score = SequenceMatcher(
        None, normalized_expected, normalized_extracted, autojunk=False
    ).ratio() * 100
    passed = score >= FUZZY_MATCH_THRESHOLD
    return FieldResult(
        field=field,
        expected_value=expected,
        extracted_value=extracted,
        strategy=strategy,
        outcome=FieldOutcome.PASS if passed else FieldOutcome.FAIL,
        score=round(score, 2),
        reason=(
            f"Fuzzy ratio {score:.2f} meets the {FUZZY_MATCH_THRESHOLD:g} threshold."
            if passed
            else f"Fuzzy ratio {score:.2f} is below the {FUZZY_MATCH_THRESHOLD:g} threshold."
        ),
    )


def compare_brand_name(expected: str, extracted: str | None) -> FieldResult:
    return _compare_fuzzy(FieldName.BRAND_NAME, expected, extracted)


def compare_class_type(expected: str, extracted: str | None) -> FieldResult:
    return _compare_fuzzy(FieldName.CLASS_TYPE, expected, extracted)


def compare_producer(expected: str, extracted: str | None) -> FieldResult:
    return _compare_fuzzy(
        FieldName.PRODUCER,
        expected,
        extracted,
        normalize=_normalize_producer,
    )


def _normalize_country(value: str) -> str:
    normalized = _normalize_words(value)
    normalized = _COUNTRY_PREFIX_PATTERN.sub("", normalized, count=1)
    return _COUNTRY_ALIASES.get(normalized, normalized)


def compare_country_of_origin(expected: str, extracted: str | None) -> FieldResult:
    strategy = "country_synonym_exact"
    if missing := _missing_result(
        FieldName.COUNTRY_OF_ORIGIN, expected, extracted, strategy
    ):
        return missing

    assert extracted is not None
    passed = _normalize_country(expected) == _normalize_country(extracted)
    return FieldResult(
        field=FieldName.COUNTRY_OF_ORIGIN,
        expected_value=expected,
        extracted_value=extracted,
        strategy=strategy,
        outcome=FieldOutcome.PASS if passed else FieldOutcome.FAIL,
        reason=(
            "Canonical country values match."
            if passed
            else "Canonical country values do not match."
        ),
    )


def _parse_abv(value: str) -> Decimal | None:
    # Prefer an explicitly marked ABV over another number such as proof.
    for pattern in (_PERCENT_ABV_PATTERN, _MARKED_ABV_PATTERN):
        if match := pattern.search(value):
            return Decimal(match.group(1))

    if proof_match := _PROOF_PATTERN.search(value):
        return Decimal(proof_match.group(1)) / Decimal("2")

    numbers = _NUMBER_PATTERN.findall(value)
    return Decimal(numbers[0]) if len(numbers) == 1 else None


def compare_alcohol_content(expected: str, extracted: str | None) -> FieldResult:
    strategy = "abv_numeric_tolerance_0.1"
    if missing := _missing_result(
        FieldName.ALCOHOL_CONTENT, expected, extracted, strategy
    ):
        return missing

    assert extracted is not None
    expected_abv = _parse_abv(expected)
    extracted_abv = _parse_abv(extracted)
    if expected_abv is None or extracted_abv is None:
        return FieldResult(
            field=FieldName.ALCOHOL_CONTENT,
            expected_value=expected,
            extracted_value=extracted,
            strategy=strategy,
            outcome=FieldOutcome.REVIEW,
            reason="One or both ABV values could not be parsed; human review is required.",
        )

    difference = abs(expected_abv - extracted_abv)
    passed = difference <= ABV_TOLERANCE
    return FieldResult(
        field=FieldName.ALCOHOL_CONTENT,
        expected_value=expected,
        extracted_value=extracted,
        strategy=strategy,
        outcome=FieldOutcome.PASS if passed else FieldOutcome.FAIL,
        reason=(
            f"ABV difference {difference} is within the {ABV_TOLERANCE} tolerance."
            if passed
            else f"ABV difference {difference} exceeds the {ABV_TOLERANCE} tolerance."
        ),
    )


def _parse_volume_ml(value: str) -> Decimal | None:
    # Remove label-style unit punctuation ("FL. OZ.") without destroying
    # decimal points that have digits on both sides ("0.75 L").
    without_unit_periods = re.sub(r"(?<!\d)\.|\.(?!\d)", "", value.casefold())
    normalized = _collapse_whitespace(without_unit_periods)
    match = _VOLUME_PATTERN.search(normalized)
    if not match:
        return None
    unit = _collapse_whitespace(match.group("unit"))
    factor = _VOLUME_FACTORS_ML.get(unit)
    if factor is None:
        return None
    return Decimal(match.group("value")) * factor


def compare_net_contents(expected: str, extracted: str | None) -> FieldResult:
    strategy = "volume_ml_tolerance_0.5"
    if missing := _missing_result(FieldName.NET_CONTENTS, expected, extracted, strategy):
        return missing

    assert extracted is not None
    expected_ml = _parse_volume_ml(expected)
    extracted_ml = _parse_volume_ml(extracted)
    if expected_ml is None or extracted_ml is None:
        return FieldResult(
            field=FieldName.NET_CONTENTS,
            expected_value=expected,
            extracted_value=extracted,
            strategy=strategy,
            outcome=FieldOutcome.REVIEW,
            reason=(
                "One or both volume values could not be parsed; human review is required."
            ),
        )

    difference = abs(expected_ml - extracted_ml)
    passed = difference <= VOLUME_TOLERANCE_ML
    return FieldResult(
        field=FieldName.NET_CONTENTS,
        expected_value=expected,
        extracted_value=extracted,
        strategy=strategy,
        outcome=FieldOutcome.PASS if passed else FieldOutcome.FAIL,
        reason=(
            f"Volume difference {difference} mL is within the "
            f"{VOLUME_TOLERANCE_ML} mL tolerance."
            if passed
            else f"Volume difference {difference} mL exceeds the "
            f"{VOLUME_TOLERANCE_ML} mL tolerance."
        ),
    )


def compare_government_warning(expected: str, extracted: str | None) -> FieldResult:
    strategy = "exact_case_sensitive_whitespace_collapsed"
    if missing := _missing_result(
        FieldName.GOVERNMENT_WARNING, expected, extracted, strategy
    ):
        return missing

    assert extracted is not None
    passed = _collapse_whitespace(expected) == _collapse_whitespace(extracted)
    return FieldResult(
        field=FieldName.GOVERNMENT_WARNING,
        expected_value=expected,
        extracted_value=extracted,
        strategy=strategy,
        outcome=FieldOutcome.PASS if passed else FieldOutcome.FAIL,
        reason=(
            "Warning text matches exactly after whitespace collapse."
            if passed
            else "Warning text differs after whitespace collapse; comparison is case-sensitive."
        ),
    )


_COMPARISONS: tuple[
    tuple[FieldName, Callable[[str, str | None], FieldResult]], ...
] = (
    (FieldName.BRAND_NAME, compare_brand_name),
    (FieldName.CLASS_TYPE, compare_class_type),
    (FieldName.PRODUCER, compare_producer),
    (FieldName.COUNTRY_OF_ORIGIN, compare_country_of_origin),
    (FieldName.ALCOHOL_CONTENT, compare_alcohol_content),
    (FieldName.NET_CONTENTS, compare_net_contents),
    (FieldName.GOVERNMENT_WARNING, compare_government_warning),
)


def verify_application(
    application: ApplicationData, extracted: ExtractedLabel
) -> VerificationResult:
    """Apply every deterministic comparison strategy to one application."""

    results = tuple(
        comparison(
            getattr(application, field.value),
            getattr(extracted, field.value),
        )
        for field, comparison in _COMPARISONS
    )
    verdict = (
        VerificationVerdict.PASS
        if all(result.outcome is FieldOutcome.PASS for result in results)
        else VerificationVerdict.NEEDS_REVIEW
    )
    return VerificationResult(
        application_id=application.application_id,
        verdict=verdict,
        fields=results,
    )
