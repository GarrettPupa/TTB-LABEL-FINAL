import base64
import os
import warnings
from collections.abc import Mapping
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, Protocol

from openai import APITimeoutError, OpenAI, OpenAIError
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import ValidationError

from backend.app.models import ExtractedLabel


DEFAULT_VISION_MODEL = "gpt-5.6-luna"
MAX_IMAGE_DIMENSION = 1600
JPEG_QUALITY = 82
API_TIMEOUT_SECONDS = 4.0

EXTRACTION_PROMPT = """\
Extract visible alcohol-label text into exactly these seven fields:
- brand_name
- class_type
- producer
- country_of_origin
- alcohol_content
- net_contents
- government_warning

Copy visible wording instead of interpreting or correcting it. Return null for every
field that is absent, obscured, unreadable, or uncertain. If the image is blurry,
angled, affected by glare, or only partly readable, return all fields you can read
confidently and leave the rest null; do not fail the entire extraction.
If the image is not an alcohol label, return all seven fields as null.

For government_warning, copy the warning verbatim exactly as visible. Preserve its
capitalization, punctuation, spelling, and wording. Do not repair OCR-like errors,
paraphrase, complete missing text, or substitute a standard warning statement.
"""


class VisionService(Protocol):
    """Extraction boundary shared by real and deterministic test implementations."""

    def extract_label(self, image: bytes) -> ExtractedLabel:
        """Extract all confidently readable fields from one image."""


class VisionServiceError(Exception):
    """Controlled operational failure from the external vision provider."""


class VisionTimeoutError(VisionServiceError):
    """The vision provider did not respond within the configured deadline."""


class VisionStructuredOutputError(VisionServiceError):
    """The provider response could not be validated against ExtractedLabel."""


@dataclass(frozen=True, slots=True)
class PreparedImage:
    data: bytes
    media_type: str
    width: int
    height: int


def preprocess_image(
    image: bytes,
    *,
    max_dimension: int = MAX_IMAGE_DIMENSION,
    jpeg_quality: int = JPEG_QUALITY,
) -> PreparedImage:
    """Orient, bound, and JPEG-encode an image without persisting it."""

    if not image:
        raise ValueError("Image data is empty.")
    if max_dimension < 1:
        raise ValueError("max_dimension must be positive.")
    if not 1 <= jpeg_quality <= 95:
        raise ValueError("jpeg_quality must be between 1 and 95.")

    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(BytesIO(image)) as source:
            source.load()
            oriented = ImageOps.exif_transpose(source)
            if oriented.mode in {"RGBA", "LA"} or "transparency" in oriented.info:
                rgba = oriented.convert("RGBA")
                rgb = Image.new("RGB", rgba.size, "white")
                rgb.paste(rgba, mask=rgba.getchannel("A"))
            else:
                rgb = oriented.convert("RGB")

            rgb.thumbnail(
                (max_dimension, max_dimension),
                resample=Image.Resampling.LANCZOS,
            )
            output = BytesIO()
            rgb.save(
                output,
                format="JPEG",
                quality=jpeg_quality,
                optimize=True,
            )
            width, height = rgb.size

    return PreparedImage(
        data=output.getvalue(),
        media_type="image/jpeg",
        width=width,
        height=height,
    )


def _defensive_extracted_label(payload: object) -> ExtractedLabel:
    if isinstance(payload, ExtractedLabel):
        return payload
    if not isinstance(payload, Mapping):
        return ExtractedLabel()

    # Structured Outputs should already provide the Pydantic type. This fallback
    # salvages valid partial fields from a mapping without ever parsing JSON text.
    valid_values = {
        name: value
        for name in ExtractedLabel.model_fields
        if isinstance((value := payload.get(name)), str) or value is None
    }
    return ExtractedLabel(**valid_values)


class OpenAIVisionService:
    """OpenAI Responses API implementation with typed Structured Outputs."""

    def __init__(
        self,
        *,
        client: Any | None = None,
        model: str | None = None,
        api_timeout_seconds: float = API_TIMEOUT_SECONDS,
    ) -> None:
        self._model = model or os.getenv("OPENAI_VISION_MODEL", DEFAULT_VISION_MODEL)
        self._api_timeout_seconds = api_timeout_seconds
        self._client = client or OpenAI(
            timeout=api_timeout_seconds,
            max_retries=0,
        )

    def extract_label(self, image: bytes) -> ExtractedLabel:
        try:
            prepared = preprocess_image(image)
        except (
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
            UnidentifiedImageError,
            OSError,
            ValueError,
        ):
            return ExtractedLabel()

        encoded = base64.b64encode(prepared.data).decode("ascii")
        try:
            response = self._client.responses.parse(
                model=self._model,
                reasoning={"effort": "none"},
                input=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": EXTRACTION_PROMPT},
                            {
                                "type": "input_image",
                                "image_url": (
                                    f"data:{prepared.media_type};base64,{encoded}"
                                ),
                                "detail": "high",
                            },
                        ],
                    }
                ],
                text_format=ExtractedLabel,
                max_output_tokens=400,
                store=False,
                timeout=self._api_timeout_seconds,
            )
        except APITimeoutError as exc:
            raise VisionTimeoutError("The vision provider timed out.") from exc
        except ValidationError as exc:
            raise VisionStructuredOutputError(
                "The vision provider returned invalid structured output."
            ) from exc
        except OpenAIError as exc:
            raise VisionServiceError("The vision provider request failed.") from exc
        return _defensive_extracted_label(getattr(response, "output_parsed", None))


@dataclass(slots=True)
class FakeVisionService:
    """Deterministic VisionService for orchestration and route tests."""

    result: ExtractedLabel
    calls: list[bytes] = field(default_factory=list, init=False)

    def extract_label(self, image: bytes) -> ExtractedLabel:
        self.calls.append(image)
        return self.result
