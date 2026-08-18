from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


app = FastAPI(title="TTB Label Verification")
frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


if frontend_dist.exists():
    app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="assets")

    @app.get("/{path:path}")
    async def frontend(path: str) -> FileResponse:
        requested_file = frontend_dist / path
        if path and requested_file.is_file():
            return FileResponse(requested_file)
        return FileResponse(frontend_dist / "index.html")
