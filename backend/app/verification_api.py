import asyncio
import os
from functools import lru_cache
from pathlib import Path
from time import perf_counter
from typing import Annotated

from fastapi import APIRouter, Depends
from starlette.requests import Request

from backend.app.api_errors import ApiError
from backend.app.comparison import verify_application
from backend.app.data_access import (
    ApplicationRepository,
    CsvApplicationRepository,
    ImageStore,
    LocalImageStore,
    SourceDataError,
)
from backend.app.models import (
    BatchItemError,
    BatchVerificationItem,
    BatchVerificationResponse,
    BatchVerificationSummary,
    BatchVerifyRequest,
    ExtractedLabel,
    VerificationResponse,
    VerificationVerdict,
    VerifyRequest,
)
from backend.app.vision import (
    FakeVisionService,
    OpenAIVisionService,
    VisionService,
    VisionServiceError,
    VisionTimeoutError,
)


router = APIRouter(tags=["verification"])
project_root = Path(__file__).resolve().parents[2]
DEFAULT_BATCH_CONCURRENCY = 3
MAX_BATCH_CONCURRENCY = 8


@lru_cache
def get_application_repository() -> ApplicationRepository:
    csv_path = Path(
        os.getenv("APPLICATION_CSV_PATH")
        or project_root / "backend" / "data" / "applications.csv"
    )
    return CsvApplicationRepository(csv_path)


@lru_cache
def get_image_store() -> ImageStore:
    bucket_root = Path(
        os.getenv("LABEL_IMAGE_BUCKET")
        or project_root / "backend" / "data" / "label-images"
    )
    return LocalImageStore(bucket_root)


@lru_cache
def get_vision_service() -> VisionService:
    if os.getenv("DEMO_VISION_MODE", "").casefold() in {"1", "true", "yes"}:
        return FakeVisionService(
            ExtractedLabel(
                brand_name="HARBOR & PINE",
                class_type="KENTUCKY STRAIGHT BOURBON WHISKEY",
                producer="NORTH COAST SPIRITS",
                country_of_origin="UNITED STATES",
                alcohol_content="45% Alc./Vol. (90 Proof)",
                net_contents="750 mL",
                government_warning=(
                    "GOVERNMENT WARNING: (1) ACCORDING TO THE SURGEON GENERAL, "
                    "WOMEN SHOULD NOT DRINK ALCOHOLIC BEVERAGES DURING PREGNANCY "
                    "BECAUSE OF THE RISK OF BIRTH DEFECTS. (2) CONSUMPTION OF "
                    "ALCOHOLIC BEVERAGES IMPAIRS YOUR ABILITY TO DRIVE A CAR OR "
                    "OPERATE MACHINERY, AND MAY CAUSE HEALTH PROBLEMS."
                ),
            )
        )
    return OpenAIVisionService()


def get_batch_concurrency_limit() -> int:
    try:
        configured = int(
            os.getenv("BATCH_VERIFICATION_CONCURRENCY", DEFAULT_BATCH_CONCURRENCY)
        )
    except ValueError:
        configured = DEFAULT_BATCH_CONCURRENCY
    return min(MAX_BATCH_CONCURRENCY, max(1, configured))


def _verify_application(
    application_id: str,
    applications: ApplicationRepository,
    images: ImageStore,
    vision: VisionService,
    *,
    started: float | None = None,
) -> VerificationResponse:
    started = perf_counter() if started is None else started
    try:
        application = applications.get(application_id)
        if application is None:
            raise ApiError(
                status_code=404,
                code="application_not_found",
                public_message="No application was found for the selected identifier.",
            )
        image = images.load(application.image_reference)
    except SourceDataError as exc:
        raise ApiError(
            status_code=exc.status_code,
            code=exc.code,
            public_message=exc.public_message,
        ) from None

    try:
        extracted = vision.extract_label(image)
    except VisionTimeoutError:
        raise ApiError(
            status_code=504,
            code="vision_timeout",
            public_message="Label analysis timed out. Please try again.",
        ) from None
    except VisionServiceError:
        raise ApiError(
            status_code=502,
            code="vision_unavailable",
            public_message="The label could not be analyzed. Please try again.",
        ) from None
    except Exception:
        raise ApiError(
            status_code=502,
            code="vision_unavailable",
            public_message="The label could not be analyzed. Please try again.",
        ) from None

    result = verify_application(application, extracted)
    return VerificationResponse(
        **result.model_dump(exclude={"latency_ms"}),
        latency_ms=round((perf_counter() - started) * 1000, 2),
    )


@router.post("/verify", response_model=VerificationResponse)
def verify_selected_application(
    request: VerifyRequest,
    http_request: Request,
    applications: Annotated[ApplicationRepository, Depends(get_application_repository)],
    images: Annotated[ImageStore, Depends(get_image_store)],
    vision: Annotated[VisionService, Depends(get_vision_service)],
) -> VerificationResponse:
    started = getattr(http_request.state, "verification_started_at", None)
    if started is None:
        started = perf_counter()
    http_request.state.application_id = request.application_id
    try:
        response = _verify_application(
            request.application_id,
            applications,
            images,
            vision,
            started=started,
        )
    except ApiError as exc:
        http_request.state.verification_outcome = exc.code
        raise
    http_request.state.verification_outcome = response.verdict.value
    return response


@router.post("/verify/batch", response_model=BatchVerificationResponse)
async def verify_selected_applications(
    request: BatchVerifyRequest,
    http_request: Request,
    applications: Annotated[ApplicationRepository, Depends(get_application_repository)],
    images: Annotated[ImageStore, Depends(get_image_store)],
    vision: Annotated[VisionService, Depends(get_vision_service)],
    concurrency_limit: Annotated[int, Depends(get_batch_concurrency_limit)],
) -> BatchVerificationResponse:
    """Verify selected records concurrently while isolating every item failure."""

    batch_started = getattr(http_request.state, "verification_started_at", None)
    if batch_started is None:
        batch_started = perf_counter()
    http_request.state.application_count = len(request.application_ids)
    semaphore = asyncio.Semaphore(concurrency_limit)

    async def verify_one(application_id: str) -> BatchVerificationItem:
        async with semaphore:
            try:
                result = await asyncio.to_thread(
                    _verify_application,
                    application_id,
                    applications,
                    images,
                    vision,
                )
                return BatchVerificationItem(
                    application_id=application_id,
                    result=result,
                )
            except ApiError as exc:
                return BatchVerificationItem(
                    application_id=application_id,
                    error=BatchItemError(code=exc.code, message=exc.public_message),
                )
            except Exception:
                return BatchVerificationItem(
                    application_id=application_id,
                    error=BatchItemError(
                        code="verification_failed",
                        message="This label could not be verified. Please try again.",
                    ),
                )

    items = tuple(
        await asyncio.gather(
            *(verify_one(application_id) for application_id in request.application_ids)
        )
    )
    passed = sum(
        item.result is not None
        and item.result.verdict is VerificationVerdict.PASS
        for item in items
    )
    errors = sum(item.error is not None for item in items)
    needs_review = len(items) - passed
    http_request.state.verification_outcome = (
        f"{passed}_passed_{needs_review}_needs_review"
    )
    return BatchVerificationResponse(
        items=items,
        summary=BatchVerificationSummary(
            passed=passed,
            needs_review=needs_review,
            total=len(items),
            errors=errors,
        ),
        latency_ms=round((perf_counter() - batch_started) * 1000, 2),
        concurrency_limit=concurrency_limit,
    )
