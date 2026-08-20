# TTB Label Verification

TTB Label Verification is a standalone proof of concept for screening alcohol
label images against expected application data. It loads applications from a
mock CSV data source, resolves each application's existing label image, extracts
visible text with an AI vision model, and applies deterministic field-by-field
comparison rules.

The results are review aids only. They are not legal determinations, regulatory
decisions, TTB approvals, or substitutes for human review.

## What it does

- Lists existing applications from `backend/data/applications.csv`.
- Displays each application's expected values and associated label image.
- Verifies one application on demand; selecting an application does not run AI.
- Verifies up to 25 selected applications in one batch while isolating item errors.
- Shows expected and extracted values with `PASS`, `FAIL`, or `REVIEW` outcomes.
- Compares government warning text exactly and case-sensitively.
- Keeps AI extraction separate from deterministic comparison logic.
- Requires no form entry or image upload.

## Architecture

The application uses a FastAPI backend and a React/TypeScript/Vite frontend. In
production, FastAPI serves the compiled frontend and API from one Docker
container. See [DESIGN.md](DESIGN.md) for the component design, data flow,
comparison strategies, tools, assumptions, and tradeoffs.

```text
Mock application CSV --+
                       +--> FastAPI --> AI extraction --> deterministic comparison
Local image bucket ----+                                  +--> verification result
```

Source application records and label images are read-only during verification.
The local CSV and image directory represent upstream systems and can later be
replaced behind the repository and image-store interfaces.

## Approach, tools, and assumptions

The backend treats the CSV record as authoritative, resolves its existing image,
uses the OpenAI Responses API only to transcribe visible fields, and then applies
Python comparison rules to determine field outcomes. Single and batch requests
share the same orchestration and comparison code.

The main tools are Python 3.12, FastAPI, Pydantic, Pillow, React, TypeScript,
Vite, `uv`, pytest, and Docker. The proof of concept assumes stable application
identifiers, valid administrative CSV records, server-side access to the image
bucket, and a human reviewer who makes the final decision. It also assumes local
CSV review storage is acceptable for a demo; there is no authentication,
production database, or durable audit store.

See [DESIGN.md](DESIGN.md) for the detailed flow, component responsibilities,
comparison strategies, security boundaries, and future-production changes.

## Requirements

- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Node.js 22+
- An OpenAI API key for live extraction

Python 3.12 is declared by the project and can be installed automatically with
`uv python install 3.12`. Docker is optional.

## Platform setup

Run the commands for your operating system from the repository root.

### Windows (PowerShell)

Install Node.js 22 or newer from [nodejs.org](https://nodejs.org/), then install
`uv` and the project dependencies:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
uv python install 3.12
uv sync
Set-Location frontend
npm ci
Set-Location ..
New-Item -ItemType File -Path .env -Force
```

Close and reopen PowerShell if `uv` is not immediately available after
installation.

### macOS

Install Node.js 22 or newer from [nodejs.org](https://nodejs.org/) or with your
preferred package manager, then run:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv python install 3.12
uv sync
cd frontend
npm ci
cd ..
touch .env
```

Open a new terminal if `uv` is not immediately on your `PATH`.

### Linux

Install Node.js 22 or newer using the instructions for your distribution or from
[nodejs.org](https://nodejs.org/), then run:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv python install 3.12
uv sync
cd frontend
npm ci
cd ..
touch .env
```

Open a new shell if `uv` is not immediately on your `PATH`.

### Environment configuration

Open the repository-root `.env` file and add:

```dotenv
OPENAI_API_KEY=your_key_from_the_OpenAI_API_dashboard
OPENAI_VISION_MODEL=gpt-5.6-luna
DEMO_VISION_MODE=false
BATCH_VERIFICATION_CONCURRENCY=3
```

Use `DEMO_VISION_MODE=true` and leave `OPENAI_API_KEY` empty if you only want
to explore the interface without a live AI request. The `.env` file is ignored
by Git and Docker.

## Run the application

### Single-server mode

This is the simplest local run and matches the production architecture. Build
the frontend, then start FastAPI:

**Windows (PowerShell)**

```powershell
Set-Location frontend
npm run build
Set-Location ..
uv run uvicorn backend.app.main:app --reload
```

**macOS and Linux**

```bash
cd frontend
npm run build
cd ..
uv run uvicorn backend.app.main:app --reload
```

Open <http://127.0.0.1:8000>, choose an application, and select **Verify label**.

### Frontend hot-reload mode

Use two terminals when changing React code.

Terminal 1 - FastAPI:

```bash
uv run uvicorn backend.app.main:app --reload
```

Terminal 2 - Vite:

```bash
cd frontend
npm run dev
```

Open the URL printed by Vite, normally <http://127.0.0.1:5173>. Vite proxies
`/applications`, `/verify`, and `/health` to FastAPI on port 8000.

The browser sends only application identifiers and review actions. The API key
stays on the FastAPI server. Never put it in a `VITE_` variable because Vite
variables are bundled into browser JavaScript.

### Demo extraction mode

Set `DEMO_VISION_MODE=true` to exercise the interface without making OpenAI API
requests. Demo mode returns a fixed extraction and is intended only for local
development; it does not inspect the selected label.

## Configuration

All settings are optional except `OPENAI_API_KEY` when live extraction is used.
Shell and deployment environment variables take precedence over `.env`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `OPENAI_API_KEY` | None | Server-side credential for live AI extraction. |
| `OPENAI_VISION_MODEL` | `gpt-5.6-luna` | Vision model used by the extraction service. |
| `DEMO_VISION_MODE` | `false` | Uses a fixed local extraction instead of the provider. |
| `BATCH_VERIFICATION_CONCURRENCY` | `3` | Concurrent batch items, clamped to 1-8. |
| `APPLICATION_CSV_PATH` | `backend/data/applications.csv` | Override for the mock application data. |
| `LABEL_IMAGE_BUCKET` | `backend/data/label-images` | Override for the local image bucket. |
| `REVIEW_STATUS_CSV_PATH` | `backend/data/review_status.csv` | Override for demo review decisions. |
| `VITE_API_BASE_URL` | Same origin | Optional API origin for frontend development. Never store secrets here. |

## Verification behavior

For a single verification, the backend reloads the authoritative application row,
loads the referenced image from the configured bucket, runs extraction, and sends
the extracted values to the shared deterministic comparison engine. Batch
verification uses the same path for every selected application.

Government warning text uses exact, case-sensitive equality. Other fields use
their explicitly defined normalization or comparison strategy. A missing or
uncertain extracted value becomes `REVIEW`; AI output never determines the final
field result on its own.

Images are limited to 10 MiB, validated against their expected format, oriented,
resized to a maximum 1600-pixel dimension, and JPEG-encoded in memory before live
extraction. Processed images are not written to disk.

## API overview

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Service health check. |
| `GET` | `/applications` | List available applications and review status. |
| `GET` | `/applications/{id}` | Load expected application details. |
| `GET` | `/applications/{id}/image` | Load the associated source image. |
| `POST` | `/verify` | Verify one selected application. |
| `POST` | `/verify/batch` | Verify 1-25 selected applications. |
| `POST` | `/applications/{id}/decision` | Save a demo review decision. |
| `GET` | `/applications/{id}/review` | Load a saved demo review. |
| `POST` | `/applications/reset-statuses` | Reset all demo review statuses. |

Example single verification:

```bash
curl -X POST http://127.0.0.1:8000/verify \
  -H "Content-Type: application/json" \
  --data '{"application_id":"TTB-0001"}'
```

FastAPI's interactive API documentation is available at
<http://127.0.0.1:8000/docs> while the service is running.

## Vision smoke test

After configuring `.env`, run extraction against one local image:

```bash
uv run python -m backend.scripts.run_vision_sample backend/data/label-images/harbor-pine-demo.png
```

The script prints the seven extracted fields as JSON. Automated tests use a fake
vision service and do not make external API requests.

## Tests and production build

```bash
uv run pytest
cd frontend
npm run build
```

To build and run the production container:

```bash
docker build -t ttb-label-verification .
docker run --rm -p 8000:8000 --env-file .env ttb-label-verification
```

## Deploy to Render

1. Push the repository to GitHub.
2. In Render, select **New > Blueprint**, connect the repository, and apply
   `render.yaml`.
3. Add `OPENAI_API_KEY` as a secret in the service's **Environment** settings.
   `render.yaml` intentionally declares the name without a value.
4. Deploy and confirm that `<service-url>/health` returns `{"status":"ok"}`.

Never commit `.env`, place a real key in `render.yaml`, or bake credentials into
the Docker image. If a key is exposed, revoke it in the provider dashboard,
create a replacement, and redeploy.

## Security and proof-of-concept limits

- Provider credentials are read from the server environment and are not returned
  by API responses.
- Live extraction requests use `store=false` and send the processed label image
  to the configured OpenAI API.
- Application, image, and verification responses use `Cache-Control: no-store`.
- User-facing errors omit provider details and stack traces.
- Local image references are confined to the configured bucket directory.
- The application intentionally has no authentication or authorization. Do not
  expose real application data, label images, review decisions, or the reset
  endpoint on a public deployment until access controls are added.
- Demo review decisions are written to a local CSV. This is not production-grade
  persistence and may be ephemeral on container hosting.

## Project layout

```text
backend/app/                 FastAPI routes, models, extraction, and comparison
backend/data/                Mock CSV data and local label-image bucket
backend/tests/               Backend test suite
frontend/src/                React application
Dockerfile                   Multi-stage production image
render.yaml                  Render Blueprint
```
