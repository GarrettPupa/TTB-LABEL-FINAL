import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated, NoReturn
from urllib.parse import quote

from fastapi import APIRouter, Depends, Response

from backend.app.api_errors import ApiError
from backend.app.data_access import (
    ApplicationRepository,
    CsvReviewStatusRepository,
    ImageStore,
    ReviewStatusRepository,
    SourceDataError,
)
from backend.app.models import (
    ApplicationData,
    ApplicationDetail,
    ApplicationListItem,
    BatchVerificationItem,
    ResetStatusesResponse,
    ReviewDecisionRequest,
    ReviewStatus,
    SavedReviewResponse,
)
from backend.app.verification_api import get_application_repository, get_image_store


router = APIRouter(prefix="/applications", tags=["applications"])
project_root = Path(__file__).resolve().parents[2]
_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


@lru_cache
def get_review_status_repository() -> ReviewStatusRepository:
    status_path = Path(
        os.getenv("REVIEW_STATUS_CSV_PATH")
        or project_root / "backend" / "data" / "review_status.csv"
    )
    return CsvReviewStatusRepository(status_path)


def _raise_source_error(exc: SourceDataError) -> NoReturn:
    raise ApiError(exc.status_code, exc.code, exc.public_message) from None


def _require_application(
    applications: ApplicationRepository, application_id: str
) -> ApplicationData:
    try:
        application = applications.get(application_id)
    except SourceDataError as exc:
        _raise_source_error(exc)
    if application is None:
        raise ApiError(
            404,
            "application_not_found",
            "No application was found for the selected identifier.",
        )
    return application


def _list_item(application: ApplicationData, status: ReviewStatus) -> ApplicationListItem:
    return ApplicationListItem(
        application_id=application.application_id,
        brand_name=application.brand_name,
        class_type=application.class_type,
        status=status,
    )


@router.get("", response_model=list[ApplicationListItem])
def list_applications(
    applications: Annotated[ApplicationRepository, Depends(get_application_repository)],
    statuses: Annotated[ReviewStatusRepository, Depends(get_review_status_repository)],
) -> list[ApplicationListItem]:
    try:
        records = applications.list()
        decisions = statuses.get_all()
    except SourceDataError as exc:
        _raise_source_error(exc)
    return [
        _list_item(record, decisions.get(record.application_id, ReviewStatus.PENDING))
        for record in records
    ]


@router.post("/reset-statuses", response_model=ResetStatusesResponse)
def reset_statuses(
    applications: Annotated[ApplicationRepository, Depends(get_application_repository)],
    statuses: Annotated[ReviewStatusRepository, Depends(get_review_status_repository)],
) -> ResetStatusesResponse:
    try:
        record_count = len(applications.list())
        statuses.reset()
    except SourceDataError as exc:
        _raise_source_error(exc)
    return ResetStatusesResponse(reset_count=record_count)


@router.get("/{application_id}", response_model=ApplicationDetail)
def get_application_detail(
    application_id: str,
    applications: Annotated[ApplicationRepository, Depends(get_application_repository)],
    statuses: Annotated[ReviewStatusRepository, Depends(get_review_status_repository)],
) -> ApplicationDetail:
    application = _require_application(applications, application_id)
    try:
        status = statuses.get_all().get(application_id, ReviewStatus.PENDING)
    except SourceDataError as exc:
        _raise_source_error(exc)
    return ApplicationDetail(
        **application.model_dump(exclude={"image_reference"}),
        status=status,
        image_url=f"/applications/{quote(application_id, safe='')}/image",
    )


@router.get("/{application_id}/image")
def get_application_image(
    application_id: str,
    applications: Annotated[ApplicationRepository, Depends(get_application_repository)],
    images: Annotated[ImageStore, Depends(get_image_store)],
) -> Response:
    application = _require_application(applications, application_id)
    try:
        image = images.load(application.image_reference)
    except SourceDataError as exc:
        _raise_source_error(exc)
    media_type = _MEDIA_TYPES[Path(application.image_reference).suffix.casefold()]
    return Response(
        content=image,
        media_type=media_type,
        headers={"Cache-Control": "no-store"},
    )


@router.post("/{application_id}/decision", response_model=ApplicationListItem)
def save_review_decision(
    application_id: str,
    request: ReviewDecisionRequest,
    applications: Annotated[ApplicationRepository, Depends(get_application_repository)],
    statuses: Annotated[ReviewStatusRepository, Depends(get_review_status_repository)],
) -> ApplicationListItem:
    application = _require_application(applications, application_id)
    status = ReviewStatus(request.decision.value)
    if (
        request.verification_item is not None
        and request.verification_item.application_id != application_id
    ):
        raise ApiError(
            422,
            "verification_application_mismatch",
            "The saved verification does not match this application.",
        )
    try:
        statuses.set(
            application_id,
            status,
            request.review_note,
            request.verification_item.model_dump_json()
            if request.verification_item is not None
            else "",
        )
    except SourceDataError as exc:
        _raise_source_error(exc)
    return _list_item(application, status)


@router.get("/{application_id}/review", response_model=SavedReviewResponse)
def get_saved_review(
    application_id: str,
    applications: Annotated[ApplicationRepository, Depends(get_application_repository)],
    statuses: Annotated[ReviewStatusRepository, Depends(get_review_status_repository)],
) -> SavedReviewResponse:
    _require_application(applications, application_id)
    try:
        saved = statuses.get(application_id)
    except SourceDataError as exc:
        _raise_source_error(exc)
    if saved is None:
        raise ApiError(
            404,
            "review_not_found",
            "No completed review was found for this application.",
        )
    status, review_note, verification_json = saved
    verification_item = (
        BatchVerificationItem.model_validate_json(verification_json)
        if verification_json
        else None
    )
    return SavedReviewResponse(
        application_id=application_id,
        status=status,
        review_note=review_note,
        verification_item=verification_item,
    )
