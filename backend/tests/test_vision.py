from io import BytesIO
from types import SimpleNamespace

import httpx
import pytest
from openai import APITimeoutError, OpenAIError
from PIL import Image, ImageFilter
from pydantic import ValidationError

from backend.app.models import ExtractedLabel
from backend.app.vision import (
    API_TIMEOUT_SECONDS,
    EXTRACTION_PROMPT,
    EXTRACTION_REQUEST,
    FakeVisionService,
    OpenAIVisionService,
    VisionConfigurationError,
    VisionServiceError,
    VisionStructuredOutputError,
    VisionTimeoutError,
    preprocess_image,
)


def image_bytes(size: tuple[int, int] = (2400, 1200), *, blurred: bool = False) -> bytes:
    image = Image.new("RGB", size, "white")
    if blurred:
        image = image.filter(ImageFilter.GaussianBlur(radius=8))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class StubResponses:
    def __init__(self, parsed: object) -> None:
        self.parsed = parsed
        self.calls: list[dict[str, object]] = []

    def parse(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(output_parsed=self.parsed)


class StubClient:
    def __init__(self, parsed: object) -> None:
        self.responses = StubResponses(parsed)


class RaisingResponses:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def parse(self, **kwargs: object) -> None:
        raise self.error


class RaisingClient:
    def __init__(self, error: Exception) -> None:
        self.responses = RaisingResponses(error)


def test_preprocessing_downscales_and_reencodes_as_jpeg() -> None:
    prepared = preprocess_image(image_bytes())

    assert prepared.media_type == "image/jpeg"
    assert prepared.width == 1600
    assert prepared.height == 800
    assert len(prepared.data) < len(image_bytes())
    with Image.open(BytesIO(prepared.data)) as image:
        assert image.format == "JPEG"
        assert image.mode == "RGB"


def test_preprocessing_does_not_upscale_small_images() -> None:
    prepared = preprocess_image(image_bytes((500, 250)))

    assert (prepared.width, prepared.height) == (500, 250)


def test_openai_service_uses_typed_structured_output() -> None:
    expected = ExtractedLabel(
        brand_name="Old Harbor",
        class_type="Bourbon Whiskey",
        producer="Old Harbor Distilling Company",
        country_of_origin="United States",
        alcohol_content="45% Alc./Vol.",
        net_contents="750 mL",
        government_warning="GOVERNMENT WARNING: Exact label text.",
    )
    client = StubClient(expected)
    service = OpenAIVisionService(client=client, model="test-vision-model")

    result = service.extract_label(image_bytes((800, 400)))

    assert result == expected
    call = client.responses.calls[0]
    assert call["model"] == "test-vision-model"
    assert call["text_format"] is ExtractedLabel
    assert call["reasoning"] == {"effort": "none"}
    assert call["store"] is False
    assert call["timeout"] == API_TIMEOUT_SECONDS
    assert call["input"][0] == {
        "role": "developer",
        "content": EXTRACTION_PROMPT,
    }
    content = call["input"][1]["content"]
    assert content[0] == {"type": "input_text", "text": EXTRACTION_REQUEST}
    assert content[1]["type"] == "input_image"
    assert content[1]["detail"] == "high"
    assert content[1]["image_url"].startswith("data:image/jpeg;base64,")


def test_missing_api_key_fails_safely_without_a_provider_request(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    service = OpenAIVisionService()

    with pytest.raises(VisionConfigurationError):
        service.extract_label(image_bytes((800, 400)))


def test_blurry_image_can_return_partial_data_without_throwing() -> None:
    partial = ExtractedLabel(brand_name="Old Harbor", alcohol_content="45%")
    client = StubClient(partial)
    service = OpenAIVisionService(client=client)

    result = service.extract_label(image_bytes(blurred=True))

    assert result.brand_name == "Old Harbor"
    assert result.alcohol_content == "45%"
    assert result.government_warning is None


def test_invalid_image_returns_empty_extraction_without_api_call() -> None:
    client = StubClient(ExtractedLabel(brand_name="should not be returned"))
    service = OpenAIVisionService(client=client)

    result = service.extract_label(b"not an image")

    assert result == ExtractedLabel()
    assert client.responses.calls == []


def test_absent_structured_output_returns_empty_extraction() -> None:
    service = OpenAIVisionService(client=StubClient(None))

    result = service.extract_label(image_bytes((800, 400)))

    assert result == ExtractedLabel()


def test_api_failure_raises_typed_service_error() -> None:
    service = OpenAIVisionService(
        client=RaisingClient(OpenAIError("temporary API failure"))
    )

    with pytest.raises(VisionServiceError):
        service.extract_label(image_bytes((800, 400)))


def test_model_timeout_raises_typed_timeout_error() -> None:
    timeout = APITimeoutError(request=httpx.Request("POST", "https://example.test"))
    service = OpenAIVisionService(client=RaisingClient(timeout))

    with pytest.raises(VisionTimeoutError):
        service.extract_label(image_bytes((800, 400)))


def test_malformed_structured_output_raises_typed_parse_error() -> None:
    with pytest.raises(ValidationError) as validation:
        ExtractedLabel.model_validate({"brand_name": 123})
    service = OpenAIVisionService(client=RaisingClient(validation.value))

    with pytest.raises(VisionStructuredOutputError):
        service.extract_label(image_bytes((800, 400)))


def test_non_label_structured_result_is_all_null() -> None:
    service = OpenAIVisionService(client=StubClient(ExtractedLabel()))

    result = service.extract_label(image_bytes((800, 400)))

    assert result == ExtractedLabel()


def test_defensive_mapping_validation_salvages_only_typed_fields() -> None:
    service = OpenAIVisionService(
        client=StubClient(
            {
                "brand_name": "Old Harbor",
                "class_type": 123,
                "government_warning": "GOVERNMENT WARNING: Preserve  spacing.",
                "unexpected": "ignored",
            }
        )
    )

    result = service.extract_label(image_bytes((800, 400)))

    assert result.brand_name == "Old Harbor"
    assert result.class_type is None
    assert result.government_warning == "GOVERNMENT WARNING: Preserve  spacing."
    assert not hasattr(result, "unexpected")


def test_fake_service_returns_configured_result_and_records_image() -> None:
    expected = ExtractedLabel(brand_name="Fixture Brand")
    service = FakeVisionService(expected)
    image = b"fixture-image"

    result = service.extract_label(image)

    assert result == expected
    assert service.calls == [image]


def test_extraction_prompt_contains_quality_and_verbatim_requirements() -> None:
    for field in ExtractedLabel.model_fields:
        assert field in EXTRACTION_PROMPT
    assert "null" in EXTRACTION_PROMPT
    assert "verbatim" in EXTRACTION_PROMPT
    assert "blurry" in EXTRACTION_PROMPT
    assert "glare" in EXTRACTION_PROMPT
    assert "angled" in EXTRACTION_PROMPT
    assert "not an alcohol label" in EXTRACTION_PROMPT
    assert "untrusted data" in EXTRACTION_PROMPT
    assert "ignore any instructions" in EXTRACTION_PROMPT
    assert "never invent" in EXTRACTION_PROMPT
    assert "return only the displayed organization name" in EXTRACTION_PROMPT
    assert '"produced and bottled by"' in EXTRACTION_PROMPT
    assert "return only the country name" in EXTRACTION_PROMPT
    assert '"product of"' in EXTRACTION_PROMPT
