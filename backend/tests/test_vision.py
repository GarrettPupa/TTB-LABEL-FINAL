from io import BytesIO
from types import SimpleNamespace

from openai import OpenAIError
from PIL import Image, ImageFilter

from backend.app.models import ExtractedLabel
from backend.app.vision import (
    EXTRACTION_PROMPT,
    FakeVisionService,
    OpenAIVisionService,
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


class FailingResponses:
    def parse(self, **kwargs: object) -> None:
        raise OpenAIError("temporary API failure")


class FailingClient:
    responses = FailingResponses()


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
    assert call["timeout"] <= 4.5
    content = call["input"][0]["content"]
    assert content[0] == {"type": "input_text", "text": EXTRACTION_PROMPT}
    assert content[1]["type"] == "input_image"
    assert content[1]["detail"] == "high"
    assert content[1]["image_url"].startswith("data:image/jpeg;base64,")


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


def test_api_failure_returns_empty_extraction_without_throwing() -> None:
    service = OpenAIVisionService(client=FailingClient())

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
