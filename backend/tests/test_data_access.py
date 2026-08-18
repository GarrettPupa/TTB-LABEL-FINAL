import csv
from io import BytesIO

import pytest
from PIL import Image

from backend.app.data_access import (
    CsvApplicationRepository,
    CsvReviewStatusRepository,
    ImageTooLargeError,
    InvalidApplicationRecordError,
    InvalidImageError,
    InvalidImageReferenceError,
    LocalImageStore,
    ReviewStatus,
    UnsupportedImageTypeError,
)


def write_csv(path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def application_row(**overrides: str) -> dict[str, str]:
    row = {
        "application_id": "TTB-0001",
        "image_reference": "TTB-0001/label.png",
        "brand_name": "Old Harbor",
        "class_type": "Bourbon Whiskey",
        "producer": "Old Harbor Distilling Company",
        "country_of_origin": "United States",
        "alcohol_content": "45%",
        "net_contents": "750 mL",
        "government_warning": "GOVERNMENT WARNING: Exact text.",
    }
    row.update(overrides)
    return row


def png_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (16, 16), "white").save(output, format="PNG")
    return output.getvalue()


def test_csv_repository_loads_selected_application(tmp_path) -> None:
    csv_path = tmp_path / "applications.csv"
    write_csv(csv_path, [application_row()])

    result = CsvApplicationRepository(csv_path).get("TTB-0001")

    assert result is not None
    assert result.application_id == "TTB-0001"
    assert result.image_reference == "TTB-0001/label.png"


def test_csv_repository_lists_all_applications(tmp_path) -> None:
    csv_path = tmp_path / "applications.csv"
    write_csv(
        csv_path,
        [application_row(), application_row(application_id="TTB-0002")],
    )

    results = CsvApplicationRepository(csv_path).list()

    assert [record.application_id for record in results] == ["TTB-0001", "TTB-0002"]


def test_csv_repository_ignores_invalid_unselected_rows(tmp_path) -> None:
    csv_path = tmp_path / "applications.csv"
    write_csv(
        csv_path,
        [application_row(application_id="BROKEN", brand_name=""), application_row()],
    )

    result = CsvApplicationRepository(csv_path).get("TTB-0001")

    assert result is not None
    assert result.brand_name == "Old Harbor"


def test_csv_repository_rejects_invalid_selected_row(tmp_path) -> None:
    csv_path = tmp_path / "applications.csv"
    write_csv(csv_path, [application_row(brand_name="")])

    with pytest.raises(InvalidApplicationRecordError):
        CsvApplicationRepository(csv_path).get("TTB-0001")


def test_local_image_store_reads_valid_image(tmp_path) -> None:
    image_path = tmp_path / "TTB-0001" / "label.png"
    image_path.parent.mkdir()
    image_path.write_bytes(png_bytes())

    result = LocalImageStore(tmp_path).load("TTB-0001/label.png")

    assert result == png_bytes()


def test_local_image_store_blocks_bucket_traversal(tmp_path) -> None:
    outside = tmp_path.parent / "outside.png"
    outside.write_bytes(png_bytes())

    with pytest.raises(InvalidImageReferenceError):
        LocalImageStore(tmp_path).load("../outside.png")


def test_local_image_store_rejects_unsupported_extension(tmp_path) -> None:
    image_path = tmp_path / "label.bmp"
    image_path.write_bytes(b"bitmap")

    with pytest.raises(UnsupportedImageTypeError):
        LocalImageStore(tmp_path).load("label.bmp")


def test_local_image_store_rejects_oversized_image(tmp_path) -> None:
    image_path = tmp_path / "label.png"
    image_path.write_bytes(png_bytes())

    with pytest.raises(ImageTooLargeError):
        LocalImageStore(tmp_path, max_image_bytes=10).load("label.png")


def test_local_image_store_rejects_spoofed_image_content(tmp_path) -> None:
    image_path = tmp_path / "label.png"
    image_path.write_bytes(b"this is not a png")

    with pytest.raises(InvalidImageError):
        LocalImageStore(tmp_path).load("label.png")


def test_local_image_store_rejects_empty_image(tmp_path) -> None:
    image_path = tmp_path / "label.png"
    image_path.write_bytes(b"")

    with pytest.raises(InvalidImageError):
        LocalImageStore(tmp_path).load("label.png")


def test_review_statuses_are_separate_and_resettable(tmp_path) -> None:
    status_path = tmp_path / "review_status.csv"
    repository = CsvReviewStatusRepository(status_path)

    repository.set("TTB-0001", ReviewStatus.ACCEPTED)
    repository.set("TTB-0002", ReviewStatus.REJECTED)

    assert repository.get_all() == {
        "TTB-0001": ReviewStatus.ACCEPTED,
        "TTB-0002": ReviewStatus.REJECTED,
    }
    assert repository.reset() == 2
    assert repository.get_all() == {}
