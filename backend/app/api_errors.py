from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ApiError(Exception):
    status_code: int
    code: str
    public_message: str

    def __post_init__(self) -> None:
        Exception.__init__(self, self.public_message)


def error_body(code: str, message: str) -> dict[str, dict[str, str]]:
    return {"error": {"code": code, "message": message}}
