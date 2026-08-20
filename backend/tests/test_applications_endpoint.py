from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.app.applications_api import get_review_status_repository
from backend.app.main import app
from backend.app.models import ApplicationData, ReviewStatus
from backend.app.verification_api import get_application_repository, get_image_store


def application(application_id: str = "TTB-0001") -> ApplicationData:
    return ApplicationData(
        application_id=application_id,
        image_reference=f"{application_id}/label.png",
        brand_name="Old Harbor",
        class_type="Bourbon Whiskey",
        producer="Old Harbor Distilling Company",
        country_of_origin="United States",
        alcohol_content="45% Alc./Vol.",
        net_contents="750 mL",
        government_warning="GOVERNMENT WARNING: Exact warning text.",
    )


def png_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (16, 16), "white").save(output, format="PNG")
    return output.getvalue()


class StubApplications:
    def __init__(self) -> None:
        self.records = [application(), application("TTB-0002")]

    def list(self) -> list[ApplicationData]:
        return self.records

    def get(self, application_id: str) -> ApplicationData | None:
        return next(
            (record for record in self.records if record.application_id == application_id),
            None,
        )


class StubImages:
    def load(self, image_reference: str) -> bytes:
        return png_bytes()


class StubStatuses:
    def __init__(self) -> None:
        self.values: dict[str, ReviewStatus] = {"TTB-0002": ReviewStatus.REJECTED}
        self.notes: dict[str, str] = {}
        self.verifications: dict[str, str] = {}

    def get_all(self) -> dict[str, ReviewStatus]:
        return dict(self.values)

    def get(self, application_id: str) -> tuple[ReviewStatus, str, str] | None:
        if application_id not in self.values:
            return None
        return (
            self.values[application_id],
            self.notes.get(application_id, ""),
            self.verifications.get(application_id, ""),
        )

    def set(
        self,
        application_id: str,
        status: ReviewStatus,
        review_note: str = "",
        verification_result: str = "",
    ) -> None:
        self.values[application_id] = status
        self.notes[application_id] = review_note
        self.verifications[application_id] = verification_result

    def reset(self) -> int:
        count = len(self.values)
        self.values.clear()
        self.notes.clear()
        self.verifications.clear()
        return count


@pytest.fixture(autouse=True)
def configure_dependencies() -> StubStatuses:
    statuses = StubStatuses()
    app.dependency_overrides[get_application_repository] = StubApplications
    app.dependency_overrides[get_image_store] = StubImages
    app.dependency_overrides[get_review_status_repository] = lambda: statuses
    yield statuses
    app.dependency_overrides.clear()


def test_list_applications_includes_review_statuses() -> None:
    response = TestClient(app).get("/applications")

    assert response.status_code == 200
    assert response.json() == [
        {
            "application_id": "TTB-0001",
            "brand_name": "Old Harbor",
            "class_type": "Bourbon Whiskey",
            "status": "PENDING",
        },
        {
            "application_id": "TTB-0002",
            "brand_name": "Old Harbor",
            "class_type": "Bourbon Whiskey",
            "status": "REJECTED",
        },
    ]


def test_application_detail_contains_seven_fields_and_image_url() -> None:
    response = TestClient(app).get("/applications/TTB-0001")

    assert response.status_code == 200
    body = response.json()
    assert body["application_id"] == "TTB-0001"
    assert body["brand_name"] == "Old Harbor"
    assert body["government_warning"].startswith("GOVERNMENT WARNING:")
    assert body["status"] == "PENDING"
    assert body["image_url"] == "/applications/TTB-0001/image"


def test_application_image_is_served_from_bucket() -> None:
    response = TestClient(app).get("/applications/TTB-0001/image")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "no-store"
    assert response.content == png_bytes()


@pytest.mark.parametrize("decision", ["ACCEPTED", "REJECTED"])
def test_save_review_decision_updates_dashboard_status(
    decision: str, configure_dependencies: StubStatuses
) -> None:
    response = TestClient(app).post(
        "/applications/TTB-0001/decision",
        json={"decision": decision, "review_note": "Checked against source record."},
    )

    assert response.status_code == 200
    assert response.json()["status"] == decision
    assert configure_dependencies.values["TTB-0001"].value == decision
    assert configure_dependencies.notes["TTB-0001"] == "Checked against source record."


def test_review_decision_rejects_note_over_character_limit() -> None:
    response = TestClient(app).post(
        "/applications/TTB-0001/decision",
        json={"decision": "ACCEPTED", "review_note": "x" * 2001},
    )

    assert response.status_code == 422


def test_reset_statuses_returns_every_application_to_pending(
    configure_dependencies: StubStatuses,
) -> None:
    response = TestClient(app).post("/applications/reset-statuses")

    assert response.status_code == 200
    assert response.json() == {"reset_count": 2}
    assert configure_dependencies.values == {}


def test_unknown_application_returns_plain_error() -> None:
    response = TestClient(app).get("/applications/TTB-9999")

    assert response.status_code == 404
    assert response.json()["error"] == {
        "code": "application_not_found",
        "message": "No application was found for the selected identifier.",
    }
