from enum import Enum
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


ApplicationId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    ),
]


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


class ReviewStatus(str, Enum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class ReviewDecision(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class ApplicationData(BaseModel):
    """Expected application data loaded from the upstream CSV record."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    application_id: ApplicationId
    image_reference: str
    brand_name: str
    class_type: str
    producer: str
    country_of_origin: str
    alcohol_content: str
    net_contents: str
    government_warning: str

    @field_validator(
        "image_reference",
        "brand_name",
        "class_type",
        "producer",
        "country_of_origin",
        "alcohol_content",
        "net_contents",
        "government_warning",
    )
    @classmethod
    def reject_blank_required_fields(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Field must not be blank.")
        return value


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
    latency_ms: float | None = Field(default=None, ge=0)


class VerificationResponse(VerificationResult):
    """HTTP verification result, for which measured latency is mandatory."""

    latency_ms: float = Field(ge=0)


class VerifyRequest(BaseModel):
    """Browser selection used to resolve authoritative server-side source data."""

    model_config = ConfigDict(extra="forbid")

    application_id: ApplicationId


class BatchVerifyRequest(BaseModel):
    """Existing applications selected for one bounded concurrent verification."""

    model_config = ConfigDict(extra="forbid")

    application_ids: list[ApplicationId] = Field(min_length=1, max_length=25)

    @field_validator("application_ids")
    @classmethod
    def reject_duplicate_application_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("Application identifiers must be unique.")
        return value


class BatchItemError(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    message: str


class BatchVerificationItem(BaseModel):
    """One isolated batch outcome: exactly one result or readable error."""

    model_config = ConfigDict(frozen=True)

    application_id: str
    result: VerificationResponse | None = None
    error: BatchItemError | None = None

    @model_validator(mode="after")
    def require_one_outcome(self) -> "BatchVerificationItem":
        if (self.result is None) == (self.error is None):
            raise ValueError("Exactly one of result or error is required.")
        return self


class BatchVerificationSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    passed: int = Field(ge=0)
    needs_review: int = Field(ge=0)
    total: int = Field(ge=1)
    errors: int = Field(ge=0)


class BatchVerificationResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[BatchVerificationItem, ...]
    summary: BatchVerificationSummary
    latency_ms: float = Field(ge=0)
    concurrency_limit: int = Field(ge=1)


class ApplicationListItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    application_id: str
    brand_name: str
    class_type: str
    status: ReviewStatus


class ApplicationDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    application_id: str
    brand_name: str
    class_type: str
    producer: str
    country_of_origin: str
    alcohol_content: str
    net_contents: str
    government_warning: str
    status: ReviewStatus
    image_url: str


class ReviewDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: ReviewDecision
    review_note: str = Field(default="", max_length=2000)
    verification_item: BatchVerificationItem | None = None


class SavedReviewResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    application_id: str
    status: ReviewStatus
    review_note: str
    verification_item: BatchVerificationItem | None = None


class ResetStatusesResponse(BaseModel):
    reset_count: int = Field(ge=0)
