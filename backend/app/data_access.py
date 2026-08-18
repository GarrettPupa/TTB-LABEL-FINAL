import csv
import os
import tempfile
import threading
import warnings
from pathlib import Path
from typing import Protocol

from PIL import Image, UnidentifiedImageError
from pydantic import ValidationError

from backend.app.models import ApplicationData, ReviewStatus


MAX_IMAGE_BYTES = 10 * 1024 * 1024
_IMAGE_FORMATS_BY_SUFFIX = {
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".png": "PNG",
    ".webp": "WEBP",
}


class SourceDataError(Exception):
    status_code = 500
    code = "source_data_error"
    public_message = "The selected application data could not be loaded."

    def __init__(self) -> None:
        super().__init__(self.public_message)


class ApplicationDataUnavailableError(SourceDataError):
    status_code = 503
    code = "application_data_unavailable"
    public_message = "Application data is temporarily unavailable."


class InvalidApplicationRecordError(SourceDataError):
    status_code = 422
    code = "invalid_application_record"
    public_message = "The selected application record is incomplete or invalid."


class InvalidImageReferenceError(SourceDataError):
    status_code = 422
    code = "invalid_image_reference"
    public_message = "The selected application has an invalid label image reference."


class ImageNotFoundError(SourceDataError):
    status_code = 422
    code = "image_not_found"
    public_message = "The label image for the selected application was not found."


class UnsupportedImageTypeError(SourceDataError):
    status_code = 415
    code = "unsupported_image_type"
    public_message = "The label image must be a PNG, JPEG, or WebP file."


class ImageTooLargeError(SourceDataError):
    status_code = 413
    code = "image_too_large"
    public_message = "The label image exceeds the 10 MiB size limit."


class InvalidImageError(SourceDataError):
    status_code = 422
    code = "invalid_image"
    public_message = "The label image is unreadable or does not match its file type."


class ApplicationRepository(Protocol):
    def get(self, application_id: str) -> ApplicationData | None: ...

    def list(self) -> list[ApplicationData]: ...


class ImageStore(Protocol):
    def load(self, image_reference: str) -> bytes: ...


class ReviewStatusRepository(Protocol):
    def get_all(self) -> dict[str, ReviewStatus]: ...

    def set(self, application_id: str, status: ReviewStatus) -> None: ...

    def reset(self) -> int: ...


class CsvApplicationRepository:
    """Read one authoritative application from the mock CSV data source."""

    def __init__(self, csv_path: Path) -> None:
        self._csv_path = csv_path

    def get(self, application_id: str) -> ApplicationData | None:
        try:
            with self._csv_path.open(newline="", encoding="utf-8-sig") as csv_file:
                reader = csv.DictReader(csv_file)
                if not reader.fieldnames or "application_id" not in reader.fieldnames:
                    raise ApplicationDataUnavailableError()
                for row in reader:
                    if row.get("application_id") != application_id:
                        continue
                    try:
                        return ApplicationData.model_validate(row)
                    except ValidationError as exc:
                        raise InvalidApplicationRecordError() from exc
        except SourceDataError:
            raise
        except (OSError, UnicodeError, csv.Error) as exc:
            raise ApplicationDataUnavailableError() from exc
        return None

    def list(self) -> list[ApplicationData]:
        try:
            with self._csv_path.open(newline="", encoding="utf-8-sig") as csv_file:
                reader = csv.DictReader(csv_file)
                if not reader.fieldnames or "application_id" not in reader.fieldnames:
                    raise ApplicationDataUnavailableError()
                try:
                    return [ApplicationData.model_validate(row) for row in reader]
                except ValidationError as exc:
                    raise InvalidApplicationRecordError() from exc
        except SourceDataError:
            raise
        except (OSError, UnicodeError, csv.Error) as exc:
            raise ApplicationDataUnavailableError() from exc


class LocalImageStore:
    """Read and validate images confined to a local bucket-style directory."""

    def __init__(
        self,
        bucket_root: Path,
        *,
        max_image_bytes: int = MAX_IMAGE_BYTES,
    ) -> None:
        self._bucket_root = bucket_root
        self._max_image_bytes = max_image_bytes

    def load(self, image_reference: str) -> bytes:
        if not image_reference.strip():
            raise InvalidImageReferenceError()

        try:
            bucket_root = self._bucket_root.resolve(strict=True)
        except OSError as exc:
            raise ApplicationDataUnavailableError() from exc

        reference = Path(image_reference)
        if reference.is_absolute():
            raise InvalidImageReferenceError()

        try:
            image_path = (bucket_root / reference).resolve(strict=True)
        except FileNotFoundError as exc:
            raise ImageNotFoundError() from exc
        except OSError as exc:
            raise InvalidImageReferenceError() from exc

        try:
            image_path.relative_to(bucket_root)
        except ValueError as exc:
            raise InvalidImageReferenceError() from exc
        if not image_path.is_file():
            raise ImageNotFoundError()

        expected_format = _IMAGE_FORMATS_BY_SUFFIX.get(image_path.suffix.casefold())
        if expected_format is None:
            raise UnsupportedImageTypeError()

        try:
            if image_path.stat().st_size > self._max_image_bytes:
                raise ImageTooLargeError()
            with image_path.open("rb") as image_file:
                image = image_file.read(self._max_image_bytes + 1)
        except SourceDataError:
            raise
        except OSError as exc:
            raise ImageNotFoundError() from exc
        if len(image) > self._max_image_bytes:
            raise ImageTooLargeError()
        if not image:
            raise InvalidImageError()

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(Path(image_path)) as decoded:
                    actual_format = decoded.format
                    decoded.verify()
        except (
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
            UnidentifiedImageError,
            OSError,
            SyntaxError,
        ) as exc:
            raise InvalidImageError() from exc
        if actual_format != expected_format:
            raise InvalidImageError()
        return image


class CsvReviewStatusRepository:
    """Persist demo review decisions separately from the read-only source CSV."""

    def __init__(self, status_path: Path) -> None:
        self._status_path = status_path
        self._lock = threading.Lock()

    def get_all(self) -> dict[str, ReviewStatus]:
        with self._lock:
            return self._read_unlocked()

    def set(self, application_id: str, status: ReviewStatus) -> None:
        with self._lock:
            statuses = self._read_unlocked()
            statuses[application_id] = status
            self._write_unlocked(statuses)

    def reset(self) -> int:
        with self._lock:
            count = len(self._read_unlocked())
            self._write_unlocked({})
            return count

    def _read_unlocked(self) -> dict[str, ReviewStatus]:
        if not self._status_path.exists():
            return {}
        try:
            with self._status_path.open(newline="", encoding="utf-8-sig") as csv_file:
                reader = csv.DictReader(csv_file)
                if reader.fieldnames != ["application_id", "status"]:
                    raise ApplicationDataUnavailableError()
                statuses: dict[str, ReviewStatus] = {}
                for row in reader:
                    try:
                        statuses[row["application_id"]] = ReviewStatus(row["status"])
                    except (KeyError, ValueError) as exc:
                        raise ApplicationDataUnavailableError() from exc
                return statuses
        except SourceDataError:
            raise
        except (OSError, UnicodeError, csv.Error) as exc:
            raise ApplicationDataUnavailableError() from exc

    def _write_unlocked(self, statuses: dict[str, ReviewStatus]) -> None:
        temporary_path: Path | None = None
        try:
            self._status_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                newline="",
                encoding="utf-8",
                dir=self._status_path.parent,
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                writer = csv.DictWriter(
                    temporary_file,
                    fieldnames=["application_id", "status"],
                )
                writer.writeheader()
                for application_id in sorted(statuses):
                    writer.writerow(
                        {
                            "application_id": application_id,
                            "status": statuses[application_id].value,
                        }
                    )
            os.replace(temporary_path, self._status_path)
        except OSError as exc:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise ApplicationDataUnavailableError() from exc
