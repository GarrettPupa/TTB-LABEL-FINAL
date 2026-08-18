import logging
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.app.data_access import (
    ImageTooLargeError,
    InvalidImageError,
    InvalidApplicationRecordError,
    LocalImageStore,
    UnsupportedImageTypeError,
)
from backend.app.main import app
from backend.app.models import ApplicationData, ExtractedLabel
from backend.app.verification_api import (
    get_application_repository,
    get_image_store,
    get_vision_service,
)
from backend.app.vision import FakeVisionService
from backend.app.vision import VisionTimeoutError


GOVERNMENT_WARNING = "GOVERNMENT WARNING: Exact warning text."


def application() -> ApplicationData:
    return ApplicationData(
        application_id="TTB-0001",
        image_reference="TTB-0001/label.png",
        brand_name="Old Harbor",
        class_type="Bourbon Whiskey",
        producer="Old Harbor Distilling Company",
        country_of_origin="United States",
        alcohol_content="45% Alc./Vol.",
        net_contents="750 mL",
        government_warning=GOVERNMENT_WARNING,
    )


def extracted_label(**overrides: str | None) -> ExtractedLabel:
    values: dict[str, str | None] = {
        "brand_name": "OLD HARBOR",
        "class_type": "Bourbon Whiskey",
        "producer": "Old Harbor Distilling Company",
        "country_of_origin": "USA",
        "alcohol_content": "45.0%",
        "net_contents": "0.75 L",
        "government_warning": GOVERNMENT_WARNING,
    }
    values.update(overrides)
    return ExtractedLabel(**values)


def png_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (32, 32), "white").save(output, format="PNG")
    return output.getvalue()


class StubApplicationRepository:
    def __init__(self, record: ApplicationData | None) -> None:
        self.record = record
        self.requested_ids: list[str] = []

    def get(self, application_id: str) -> ApplicationData | None:
        self.requested_ids.append(application_id)
        if self.record and self.record.application_id == application_id:
            return self.record
        return None


class StubImageStore:
    def __init__(self, image: bytes) -> None:
        self.image = image
        self.references: list[str] = []

    def load(self, image_reference: str) -> bytes:
        self.references.append(image_reference)
        return self.image


class RaisingImageStore:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def load(self, image_reference: str) -> bytes:
        raise self.error


class RaisingApplicationRepository:
    def get(self, application_id: str) -> ApplicationData | None:
        raise InvalidApplicationRecordError()


class RaisingVisionService:
    def extract_label(self, image: bytes) -> ExtractedLabel:
        raise RuntimeError("internal-secret-stack-marker")


class TimeoutVisionService:
    def extract_label(self, image: bytes) -> ExtractedLabel:
        raise VisionTimeoutError("provider timed out")


@pytest.fixture(autouse=True)
def clear_dependency_overrides() -> None:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def configure_dependencies(
    *,
    record: ApplicationData | None = None,
    image_store: object | None = None,
    vision_service: object | None = None,
) -> tuple[StubApplicationRepository, StubImageStore, FakeVisionService]:
    repository = StubApplicationRepository(record if record is not None else application())
    store = image_store if image_store is not None else StubImageStore(png_bytes())
    vision = (
        vision_service
        if vision_service is not None
        else FakeVisionService(extracted_label())
    )
    app.dependency_overrides[get_application_repository] = lambda: repository
    app.dependency_overrides[get_image_store] = lambda: store
    app.dependency_overrides[get_vision_service] = lambda: vision
    return repository, store, vision


def test_verify_returns_full_verification_result_using_selected_application() -> None:
    repository, store, vision = configure_dependencies()

    response = TestClient(app).post("/verify", json={"application_id": "TTB-0001"})

    assert response.status_code == 200
    body = response.json()
    assert body["application_id"] == "TTB-0001"
    assert body["verdict"] == "PASS"
    assert isinstance(body["latency_ms"], float | int)
    assert body["latency_ms"] >= 0
    assert body["latency_ms"] < 5000
    assert len(body["fields"]) == 7
    assert all(result["outcome"] == "PASS" for result in body["fields"])
    warning = next(
        result for result in body["fields"] if result["field"] == "government_warning"
    )
    assert warning["expected_value"] == GOVERNMENT_WARNING
    assert warning["extracted_value"] == GOVERNMENT_WARNING
    assert repository.requested_ids == ["TTB-0001"]
    assert store.references == ["TTB-0001/label.png"]
    assert vision.calls == [png_bytes()]


def test_verify_returns_needs_review_when_one_field_fails() -> None:
    extracted_warning = GOVERNMENT_WARNING.replace("Exact", "Exaet")
    configure_dependencies(
        vision_service=FakeVisionService(
            extracted_label(government_warning=extracted_warning)
        )
    )

    response = TestClient(app).post("/verify", json={"application_id": "TTB-0001"})

    assert response.status_code == 200
    body = response.json()
    assert body["verdict"] == "NEEDS_REVIEW"
    warning = next(
        result for result in body["fields"] if result["field"] == "government_warning"
    )
    assert warning["outcome"] == "FAIL"
    assert warning["expected_value"] == GOVERNMENT_WARNING
    assert warning["extracted_value"] == extracted_warning


def test_verify_rejects_missing_application_id_with_readable_error() -> None:
    configure_dependencies()

    response = TestClient(app).post("/verify", json={})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert "application_id" in response.json()["error"]["message"]
    assert "traceback" not in response.text.lower()


def test_verify_rejects_empty_request_body_with_readable_error() -> None:
    configure_dependencies()

    response = TestClient(app).post("/verify")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert response.json()["error"]["message"]
    assert "traceback" not in response.text.lower()


def test_verify_rejects_blank_application_id() -> None:
    configure_dependencies()

    response = TestClient(app).post("/verify", json={"application_id": "   "})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_verify_returns_readable_not_found_error() -> None:
    configure_dependencies(record=application())

    response = TestClient(app).post("/verify", json={"application_id": "TTB-9999"})

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "application_not_found",
            "message": "No application was found for the selected identifier.",
        }
    }


def test_verify_rejects_invalid_selected_csv_record() -> None:
    configure_dependencies()
    app.dependency_overrides[get_application_repository] = (
        lambda: RaisingApplicationRepository()
    )

    response = TestClient(app).post("/verify", json={"application_id": "TTB-0001"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_application_record"
    assert "traceback" not in response.text.lower()


def test_verify_rejects_empty_bucket_image(tmp_path) -> None:
    empty_image = tmp_path / "TTB-0001" / "empty.png"
    empty_image.parent.mkdir()
    empty_image.write_bytes(b"")
    record = application().model_copy(update={"image_reference": "TTB-0001/empty.png"})
    _, _, vision = configure_dependencies(
        record=record,
        image_store=LocalImageStore(tmp_path),
    )

    response = TestClient(app).post("/verify", json={"application_id": "TTB-0001"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_image"
    assert vision.calls == []


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (ImageTooLargeError(), 413, "image_too_large"),
        (UnsupportedImageTypeError(), 415, "unsupported_image_type"),
        (InvalidImageError(), 422, "invalid_image"),
    ],
)
def test_verify_shapes_bucket_image_errors(
    error: Exception, status_code: int, code: str
) -> None:
    configure_dependencies(image_store=RaisingImageStore(error))

    response = TestClient(app).post("/verify", json={"application_id": "TTB-0001"})

    assert response.status_code == status_code
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["message"]
    assert "traceback" not in response.text.lower()


def test_verify_rejects_old_multipart_upload_contract() -> None:
    configure_dependencies()

    response = TestClient(app).post(
        "/verify",
        data={"application_data": "{}"},
        files={"image": ("label.png", png_bytes(), "image/png")},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_verify_sanitizes_unexpected_vision_errors() -> None:
    configure_dependencies(vision_service=RaisingVisionService())

    response = TestClient(app).post("/verify", json={"application_id": "TTB-0001"})

    assert response.status_code == 502
    assert response.json() == {
        "error": {
            "code": "vision_unavailable",
            "message": "The label could not be analyzed. Please try again.",
        }
    }
    assert "internal-secret-stack-marker" not in response.text
    assert "traceback" not in response.text.lower()


def test_verify_returns_readable_gateway_timeout() -> None:
    configure_dependencies(vision_service=TimeoutVisionService())

    response = TestClient(app).post("/verify", json={"application_id": "TTB-0001"})

    assert response.status_code == 504
    assert response.json() == {
        "error": {
            "code": "vision_timeout",
            "message": "Label analysis timed out. Please try again.",
        }
    }
    assert "traceback" not in response.text.lower()


def test_verify_logs_latency_and_budget_without_label_text(caplog) -> None:
    configure_dependencies()

    with caplog.at_level(logging.INFO, logger="backend.verification"):
        response = TestClient(app).post(
            "/verify", json={"application_id": "TTB-0001"}
        )

    assert response.status_code == 200
    record = next(
        record
        for record in caplog.records
        if record.name == "backend.verification"
    )
    assert record.application_id == "TTB-0001"
    assert record.verification_outcome == "PASS"
    assert record.status_code == 200
    assert 0 <= record.latency_ms < record.budget_ms == 5000.0
    assert record.within_budget is True
    assert GOVERNMENT_WARNING not in record.getMessage()


def test_verify_logs_budget_overrun(monkeypatch, caplog) -> None:
    import backend.app.main as main_module
    import backend.app.verification_api as verification_api_module

    configure_dependencies()
    times = iter((0.0, 6.0, 6.1))

    def fake_perf_counter() -> float:
        return next(times)

    monkeypatch.setattr(main_module, "perf_counter", fake_perf_counter)
    monkeypatch.setattr(verification_api_module, "perf_counter", fake_perf_counter)

    with caplog.at_level(logging.INFO, logger="backend.verification"):
        response = TestClient(app).post(
            "/verify", json={"application_id": "TTB-0001"}
        )

    assert response.status_code == 200
    assert response.json()["latency_ms"] == 6000.0
    record = next(
        record
        for record in caplog.records
        if record.name == "backend.verification"
    )
    assert record.latency_ms == 6100.0
    assert record.within_budget is False


def test_verify_openapi_requires_latency_in_success_response() -> None:
    schema = app.openapi()
    response_schema = schema["components"]["schemas"]["VerificationResponse"]

    assert "latency_ms" in response_schema["required"]
