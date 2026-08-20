# TTB Label Verification

This proof of concept uses FastAPI plus React/Vite. The production frontend is
served by FastAPI from one Docker container.

## Local development with live label extraction

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and Node.js 22+ first.

Create an API key in the [OpenAI API dashboard](https://platform.openai.com/api-keys),
then create a local environment file from the safe template. The real key belongs only
in the repository-root `.env` file; that file is ignored by both Git and Docker.

```bash
cp .env.example .env
```

Open `.env` locally and set these values:

```dotenv
OPENAI_API_KEY=your_key_from_the_OpenAI_API_dashboard
OPENAI_VISION_MODEL=gpt-5.6-luna
DEMO_VISION_MODE=false
```

Do not use a `VITE_` variable for the key: Vite variables are bundled into browser
JavaScript. Do not paste the key into source, CSV files, screenshots, commits, or
chat. Existing shell/deployment environment variables take precedence over `.env`.

Then install, build, and run:

```bash
uv sync
cd frontend
npm install
npm run build
cd ..
uv run uvicorn backend.app.main:app --reload
```

Open http://127.0.0.1:8000, choose an application, and press **Verify label**.
The browser sends only the application identifier; the API key remains on the
FastAPI server.

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

Configure `APPLICATION_CSV_PATH` and `LABEL_IMAGE_BUCKET` only when overriding the
built-in proof-of-concept data. Successful responses contain
the application identifier, overall verdict, seven field results, and `latency_ms`.

## Vision extraction smoke test

The vision layer uses `gpt-5.6-luna` by default and the Responses API's typed
Structured Outputs. Images are oriented, bounded to a 1600-pixel longest edge,
and JPEG-encoded in memory before the request. Override the model with
`OPENAI_VISION_MODEL` when needed.

After configuring `.env`, run one local label image:

```bash
uv run python -m backend.scripts.run_vision_sample path\to\sample-label.jpg
```

The command prints the seven extracted fields as JSON and exits successfully when
at least one field is populated. Unit tests use `FakeVisionService` and make no API
requests.

## Deploy to Render free tier

1. Push this repository to GitHub.
2. In Render, choose **New > Blueprint**, connect the repository, and apply `render.yaml`.
3. Render builds the `Dockerfile` and assigns a URL like `https://ttb-label-verification.onrender.com`.
4. In the Render service's **Environment** settings, enter `OPENAI_API_KEY` as a
   secret value. `render.yaml` intentionally declares the variable without a value.
5. Keep `DEMO_VISION_MODE=false`, deploy, and confirm `<live-url>/health` returns
   `{"status":"ok"}`.

Never add `.env` to the Docker image or Render configuration. If a key is ever
exposed, revoke it in the OpenAI API dashboard, create a replacement, and redeploy.

The service may sleep when idle on Render's free tier; the first request after idle can take a short time to wake it.

## Security notes

- Label images are resized and re-encoded in memory, sent to the configured OpenAI
  API for extraction, and not written as processed files. API requests use
  `store=false`.
- The frontend receives verification results but never receives the API key.
- Verification and application responses are marked `Cache-Control: no-store`, and
  user-facing errors never include provider exception details or stack traces.
- This proof of concept intentionally has no authentication. Its application images,
  review decisions, and reset action must not be exposed on a public production URL
  with sensitive data until an approved authentication/authorization phase exists.
