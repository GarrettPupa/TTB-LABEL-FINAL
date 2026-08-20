import logging
from pathlib import Path
from time import perf_counter

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request

from backend.app.api_errors import ApiError, error_body
from backend.app.applications_api import router as applications_router
from backend.app.verification_api import router as verification_router


app = FastAPI(title="TTB Label Verification")
frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
verification_logger = logging.getLogger("backend.verification")
VERIFICATION_BUDGET_MS = 5000.0


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
    )
    if request.url.path.startswith(("/verify", "/applications")):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.middleware("http")
async def measure_verification_request(request: Request, call_next):
    if request.url.path not in {"/verify", "/verify/batch"}:
        return await call_next(request)

    started = perf_counter()
    request.state.verification_started_at = started
    response = await call_next(request)
    latency_ms = round((perf_counter() - started) * 1000, 2)
    verification_logger.info(
        "verification request completed",
        extra={
            "application_id": getattr(request.state, "application_id", None),
            "application_count": getattr(request.state, "application_count", 1),
            "verification_outcome": getattr(
                request.state, "verification_outcome", "validation_error"
            ),
            "status_code": response.status_code,
            "latency_ms": latency_ms,
            "budget_ms": VERIFICATION_BUDGET_MS,
            "within_budget": latency_ms < VERIFICATION_BUDGET_MS,
        },
    )
    return response


@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(exc.code, exc.public_message),
    )


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    descriptions: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"] if part != "body")
        field = location or "request body"
        descriptions.append(f"{field}: {error['msg']}")
    message = "Invalid request: " + "; ".join(descriptions)
    return JSONResponse(
        status_code=422,
        content=error_body("validation_error", message),
    )


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content=error_body(
            "internal_error",
            "The request could not be completed. Please try again.",
        ),
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(verification_router)
app.include_router(applications_router)


if frontend_dist.exists():
    app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="assets")

    @app.get("/{path:path}")
    async def frontend(path: str) -> FileResponse:
        resolved_dist = frontend_dist.resolve()
        requested_file = (resolved_dist / path).resolve()
        try:
            requested_file.relative_to(resolved_dist)
        except ValueError:
            requested_file = resolved_dist / "index.html"
        if path and requested_file.is_file():
            return FileResponse(requested_file)
        return FileResponse(frontend_dist / "index.html")
