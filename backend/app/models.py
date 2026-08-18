from enum import Enum

from pydantic import BaseModel, ConfigDict


class FieldName(str, Enum):
    BRAND_NAME = "brand_name"
    CLASS_TYPE = "class_type"
    PRODUCER = "producer"
    COUNTRY_OF_ORIGIN = "country_of_origin"
    ALCOHOL_CONTENT = "alcohol_content"
    NET_CONTENTS = "net_contents"
    GOVERNMENT_WARNING = "government_warning"


class FieldOutcome(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    REVIEW = "REVIEW"


class VerificationVerdict(str, Enum):
    PASS = "PASS"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class ApplicationData(BaseModel):
    """Expected application data loaded from the upstream CSV record."""

    model_config = ConfigDict(frozen=True)

    application_id: str
    image_reference: str
    brand_name: str
    class_type: str
    producer: str
    country_of_origin: str
    alcohol_content: str
    net_contents: str
    government_warning: str


class ExtractedLabel(BaseModel):
    """Text extracted from a label by a separate OCR component."""

    model_config = ConfigDict(frozen=True)

    brand_name: str | None = None
    class_type: str | None = None
    producer: str | None = None
    country_of_origin: str | None = None
    alcohol_content: str | None = None
    net_contents: str | None = None
    government_warning: str | None = None


class FieldResult(BaseModel):
    """Auditable result of applying one deterministic field strategy."""

    model_config = ConfigDict(frozen=True)

    field: FieldName
    expected_value: str
    extracted_value: str | None
    strategy: str
    outcome: FieldOutcome
    score: float | None = None
    reason: str


class VerificationResult(BaseModel):
    """Aggregate comparison result for one application."""

    model_config = ConfigDict(frozen=True)

    application_id: str
    verdict: VerificationVerdict
    fields: tuple[FieldResult, ...]
