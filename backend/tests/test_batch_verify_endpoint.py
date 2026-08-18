import threading
import time

import pytest
from fastapi.testclient import TestClient

from backend.app.data_access import ImageNotFoundError
from backend.app.main import app
from backend.app.models import ApplicationData, ExtractedLabel
from backend.app.verification_api import (
    get_application_repository,
    get_batch_concurrency_limit,
    get_image_store,
    get_vision_service,
)


WARNING = "GOVERNMENT WARNING: Exact warning text."


def make_application(application_id: str, *, warning: str = WARNING) -> ApplicationData:
    return ApplicationData(
        application_id=application_id,
        image_reference=f"{application_id}.png",
        brand_name="Old Harbor",
        class_type="Bourbon Whiskey",
        producer="Old Harbor Distilling Company",
        country_of_origin="United States",
        alcohol_content="45% Alc./Vol.",
        net_contents="750 mL",
        government_warning=warning,
    )


def extracted_label() -> ExtractedLabel:
    return ExtractedLabel(
        brand_name="OLD HARBOR",
        class_type="Bourbon Whiskey",
        producer="Old Harbor Distilling Company",
        country_of_origin="USA",
        alcohol_content="45.0%",
        net_contents="0.75 L",
        government_warning=WARNING,
    )


class MappingApplicationRepository:
    def __init__(self, records: list[ApplicationData]) -> None:
        self.records = {record.application_id: record for record in records}

    def get(self, application_id: str) -> ApplicationData | None:
        return self.records.get(application_id)


class IsolatedImageStore:
    def __init__(self, *, bad_id: str | None = None) -> None:
        self.bad_id = bad_id

    def load(self, image_reference: str) -> bytes:
        application_id = image_reference.removesuffix(".png")
        if application_id == self.bad_id:
            raise ImageNotFoundError()
        return application_id.encode()


class TrackingVisionService:
    def __init__(self, delay_seconds: float = 0.0) -> None:
        self.delay_seconds = delay_seconds
        self.active = 0
        self.max_active = 0
        self.calls: list[str] = []
        self.lock = threading.Lock()

    def extract_label(self, image: bytes) -> ExtractedLabel:
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.calls.append(image.decode())
        try:
            if self.delay_seconds:
                time.sleep(self.delay_seconds)
            return extracted_label()
        finally:
            with self.lock:
                self.active -= 1


@pytest.fixture(autouse=True)
def clear_dependency_overrides() -> None:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def configure_batch(
    records: list[ApplicationData],
    *,
    images: object | None = None,
    vision: object | None = None,
    concurrency: int = 2,
) -> TrackingVisionService:
    tracker = vision if vision is not None else TrackingVisionService()
    app.dependency_overrides[get_application_repository] = lambda: MappingApplicationRepository(records)
    app.dependency_overrides[get_image_store] = lambda: images or IsolatedImageStore()
    app.dependency_overrides[get_vision_service] = lambda: tracker
    app.dependency_overrides[get_batch_concurrency_limit] = lambda: concurrency
    return tracker  # type: ignore[return-value]


def test_batch_processes_three_labels_concurrently_with_a_bound() -> None:
    records = [make_application(f"TTB-000{number}") for number in range(1, 4)]
    vision = TrackingVisionService(delay_seconds=0.08)
    configure_batch(records, vision=vision, concurrency=2)

    response = TestClient(app).post(
        "/verify/batch",
        json={"application_ids": [record.application_id for record in records]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["concurrency_limit"] == 2
    assert body["summary"] == {
        "passed": 3,
        "needs_review": 0,
        "total": 3,
        "errors": 0,
    }
    assert [item["application_id"] for item in body["items"]] == [
        record.application_id for record in records
    ]
    assert all(item["result"]["verdict"] == "PASS" for item in body["items"])
    assert vision.max_active == 2
    assert sorted(vision.calls) == sorted(record.application_id for record in records)


def test_batch_isolates_bad_label_and_counts_every_outcome() -> None:
    records = [
        make_application("TTB-0001"),
        make_application("TTB-0002", warning="GOVERNMENT WARNING: Different text."),
        make_application("TTB-0003"),
    ]
    configure_batch(records, images=IsolatedImageStore(bad_id="TTB-0003"))

    response = TestClient(app).post(
        "/verify/batch",
        json={"application_ids": [record.application_id for record in records]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == {
        "passed": 1,
        "needs_review": 2,
        "total": 3,
        "errors": 1,
    }
    assert body["items"][0]["result"]["verdict"] == "PASS"
    assert body["items"][1]["result"]["verdict"] == "NEEDS_REVIEW"
    assert body["items"][2] == {
        "application_id": "TTB-0003",
        "result": None,
        "error": {
            "code": "image_not_found",
            "message": "The label image for the selected application was not found.",
        },
    }
    assert "traceback" not in response.text.lower()


def test_batch_isolates_unknown_application() -> None:
    records = [make_application("TTB-0001"), make_application("TTB-0002")]
    configure_batch(records)

    response = TestClient(app).post(
        "/verify/batch",
        json={"application_ids": ["TTB-0001", "TTB-9999", "TTB-0002"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["passed"] == 2
    assert body["summary"]["needs_review"] == 1
    assert body["summary"]["errors"] == 1
    assert body["items"][1]["error"]["code"] == "application_not_found"


@pytest.mark.parametrize(
    "application_ids",
    [[], ["TTB-0001", "TTB-0001"]],
)
def test_batch_rejects_empty_or_duplicate_selection(application_ids: list[str]) -> None:
    configure_batch([make_application("TTB-0001")])

    response = TestClient(app).post(
        "/verify/batch",
        json={"application_ids": application_ids},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert response.json()["error"]["message"]
    assert "traceback" not in response.text.lower()
