# TTB Label Verification

Phase 0 is a FastAPI + React/Vite health-check scaffold. The Vite production build is served by FastAPI from one Docker container.

## Local development

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and Node.js 22+ first.

```powershell
uv sync
Set-Location frontend
npm install
npm run build
Set-Location ..
uv run uvicorn backend.app.main:app --reload
```

Open http://127.0.0.1:8000. The page calls `/health` and displays `ok`.

Run the backend check with `uv run pytest`.

## Verify a selected application

The browser sends only the stable identifier selected from the application list. The
backend reads the authoritative CSV row and its referenced image from the configured
bucket; it does not accept application fields or image uploads from the browser.

```bash
curl -X POST http://127.0.0.1:8000/verify \
  -H "Content-Type: application/json" \
  --data '{"application_id":"TTB-0001"}'
```

Configure `APPLICATION_CSV_PATH`, `LABEL_IMAGE_BUCKET`, and `OPENAI_API_KEY` as
environment variables before using the live endpoint. Successful responses contain
the application identifier, overall verdict, seven field results, and `latency_ms`.

## Vision extraction smoke test

The vision layer uses `gpt-5.6-luna` by default and the Responses API's typed
Structured Outputs. Images are oriented, bounded to a 1600-pixel longest edge,
and JPEG-encoded in memory before the request. Override the model with
`OPENAI_VISION_MODEL` when needed.

Set `OPENAI_API_KEY` only in your shell environment, then run one local label image:

```powershell
uv run python -m backend.scripts.run_vision_sample path\to\sample-label.jpg
```

The command prints the seven extracted fields as JSON and exits successfully when
at least one field is populated. Unit tests use `FakeVisionService` and make no API
requests.

## Deploy to Render free tier

1. Push this repository to GitHub.
2. In Render, choose **New > Blueprint**, connect the repository, and apply `render.yaml`.
3. Render builds the `Dockerfile` and assigns a URL like `https://ttb-label-verification.onrender.com`.
4. Open that URL and confirm the page displays `ok`. Confirm the deployment health check at `<live-url>/health` returns `{"status":"ok"}`.

The service may sleep when idle on Render's free tier; the first request after idle can take a short time to wake it.
