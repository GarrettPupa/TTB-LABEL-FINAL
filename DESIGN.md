# TTB Label Verification Design

## 1. Purpose

TTB Label Verification is a standalone proof of concept for screening an
existing alcohol-label application against its associated label image. It
demonstrates how AI-assisted text extraction can be combined with deterministic
comparison rules to help a human reviewer identify matches, mismatches, and
uncertain fields.

The application is a review aid. It does not issue a legal determination,
regulatory decision, or TTB approval.

## 2. Scope

The proof of concept supports:

- Loading existing application records from a mock CSV data source.
- Resolving an existing image from a local bucket-style directory.
- Viewing expected application values before verification.
- On-demand verification of one selected application.
- Batch verification of up to 25 selected applications.
- AI extraction of seven configured label fields.
- Deterministic, auditable comparison results for every field.
- Saving temporary demo review decisions separately from source data.
- A React user interface and FastAPI JSON API.
- Local execution, Docker execution, and deployment through `render.yaml`.

It intentionally excludes authentication, a production database, file uploads,
user-managed storage, background queues, and final regulatory decision-making.

## 3. Design goals

1. Keep expected application data authoritative and server-side.
2. Run extraction only when the user explicitly requests verification.
3. Keep probabilistic AI extraction separate from deterministic decisions.
4. Reuse the same verification path for single and batch processing.
5. Isolate a failed batch item so other items can complete.
6. Keep source CSV records and source images unchanged.
7. Return readable errors without provider details or stack traces.
8. Keep a normal single verification within the five-second target when the
   external vision service responds within that budget.

## 4. System context

```text
                         +---------------------------+
Mock application CSV -->| Application repository    |
                         +-------------+-------------+
                                       |
Local image bucket ---->| Image store  |             +------------------+
                         +------+------+             | React interface  |
                                |                    +--------+---------+
                                v                             |
                         +------+-----------------------------v--+
                         |            FastAPI API                |
                         +------+----------------------+---------+
                                |                      |
                                v                      v
                         +------+-------+       +------+----------+
                         | AI extraction|       | Review status   |
                         | service      |       | demo CSV        |
                         +------+-------+       +-----------------+
                                |
                                v
                         +------+----------------+
                         | Deterministic         |
                         | comparison engine     |
                         +------+----------------+
                                |
                                v
                         Auditable field results
```

## 5. Components and responsibilities

| Component | Location | Responsibility |
| --- | --- | --- |
| Configuration bootstrap | `backend/app/config.py` | Loads the local `.env` without overriding deployment variables. |
| Pydantic models | `backend/app/models.py` | Defines validated application, request, extraction, and result contracts. |
| Application repository | `backend/app/data_access.py` | Reads authoritative records from the mock CSV. |
| Image store | `backend/app/data_access.py` | Resolves and validates images within the configured bucket. |
| Vision service | `backend/app/vision.py` | Preprocesses images and requests typed extraction from the OpenAI Responses API. |
| Comparison engine | `backend/app/comparison.py` | Applies deterministic per-field rules and calculates the overall verdict. |
| Verification API | `backend/app/verification_api.py` | Orchestrates single and batch verification using the same internal function. |
| Application API | `backend/app/applications_api.py` | Lists applications, serves images, and manages demo review decisions. |
| API shell | `backend/app/main.py` | Configures FastAPI, error handling, headers, metrics, and static frontend serving. |
| React interface | `frontend/src/App.tsx` | Presents selection, verification, batch results, and review actions. |

## 6. Tools and technologies

| Tool | Use |
| --- | --- |
| Python 3.12 | Backend implementation language. |
| FastAPI | HTTP routes, dependency injection, error handling, and OpenAPI documentation. |
| Pydantic | Strict request, source-data, extraction, and response validation. |
| OpenAI Responses API | AI-assisted transcription into a typed seven-field schema. |
| Pillow | Image type validation, orientation, resizing, and in-memory JPEG encoding. |
| React and TypeScript | Nontechnical reviewer interface with typed frontend code. |
| Vite | Frontend development server, proxy, and production bundling. |
| `uv` | Reproducible Python environment and dependency management. |
| pytest | Unit and route-level backend tests using a fake vision service. |
| Docker | Reproducible multi-stage production build. |
| Render Blueprint | Proof-of-concept hosting configuration. |

No database, message queue, authentication provider, or persistent object store is
required for this phase.

## 7. Data model

Each row in `backend/data/applications.csv` contains:

- A stable `application_id`.
- An explicit `image_reference`.
- Expected values for brand name, class/type, producer, country of origin,
  alcohol content, net contents, and government warning.

The image reference is resolved relative to
`backend/data/label-images` by default. Absolute paths and references that
escape the bucket are rejected.

Source application data and images are read-only. Demo review decisions are
written separately to `backend/data/review_status.csv`; this file is not a
production database and may be ephemeral in a container deployment.

## 8. Single-verification flow

1. The frontend requests the available applications.
2. The user selects an application.
3. FastAPI loads the authoritative CSV record and returns its expected values.
4. The frontend displays the image through the application image endpoint.
5. No extraction occurs until the user selects **Verify label**.
6. The frontend posts only the stable application identifier.
7. FastAPI reloads the authoritative CSV row.
8. The image store confines, loads, and validates the referenced image.
9. The vision service preprocesses the image in memory and extracts typed fields.
10. The comparison engine applies all seven deterministic field rules.
11. FastAPI returns expected values, extracted values, outcomes, reasons, and
    measured latency.

## 9. Batch-verification flow

The frontend sends 1-25 unique application identifiers. FastAPI runs the same
single-item orchestration function for every identifier with a bounded
concurrency of 1-8. Each item becomes either a verification result or a readable
item error. An invalid or missing image does not terminate unrelated items.

Batch processing is request-scoped and uses no background worker or persistent
job state.

## 10. Extraction design

The image is treated as untrusted input. Before a live request, the service:

- Rejects empty, unsupported, mismatched, oversized, or decompression-bomb images.
- Applies EXIF orientation.
- Converts transparency onto a white background.
- Limits the longest dimension to 1600 pixels.
- Re-encodes the image as JPEG in memory.
- Instructs the model to treat visible image text as data, not instructions.
- Requests a typed `ExtractedLabel` result.
- Uses `store=false`.
- Returns `null` for missing or uncertain fields.

The AI service transcribes values only. It does not decide whether a field passes.

## 11. Deterministic comparison design

| Field | Strategy |
| --- | --- |
| Brand name | Case/punctuation-normalized fuzzy ratio of at least 90. |
| Class/type | Case/punctuation-normalized fuzzy ratio of at least 90. |
| Producer | Removes a recognized role prefix, then uses the 90 fuzzy threshold. |
| Country of origin | Removes recognized origin prefixes, maps configured aliases, then compares exactly. |
| Alcohol content | Parses ABV or proof and permits a 0.1 percentage-point difference. |
| Net contents | Converts supported units to milliliters and permits a 0.5 mL difference. |
| Government warning | Case-sensitive text equality after collapsing whitespace. No spelling, case, fuzzy, or semantic correction. |

A missing extraction, or a numeric value that cannot be parsed reliably, returns
`REVIEW`. A field that clearly differs returns `FAIL`. The overall result is
`PASS` only when all seven fields pass; otherwise it is `NEEDS_REVIEW`.

The current whitespace collapse for the warning treats line wrapping and repeated
spaces as layout rather than wording. If policy requires byte-for-byte whitespace
equality, this strategy and its tests must be tightened.

## 12. API design

The API accepts stable identifiers instead of expected application fields or
uploaded images. This prevents the browser from replacing the authoritative data
used for comparison.

Primary routes:

- `GET /applications`
- `GET /applications/{application_id}`
- `GET /applications/{application_id}/image`
- `POST /verify`
- `POST /verify/batch`
- `POST /applications/{application_id}/decision`
- `GET /applications/{application_id}/review`
- `POST /applications/reset-statuses`
- `GET /health`

Pydantic rejects extra request fields, invalid identifiers, duplicate batch
identifiers, batches outside the 1-25 limit, and review notes over 2,000
characters.

## 13. Error and resilience design

- Known source-data and provider failures map to stable public error codes.
- Unexpected exceptions return a generic message without a stack trace.
- Batch failures are represented per application.
- External provider retries are disabled so hidden retry delays do not consume
  the response budget.
- Provider calls have a configured timeout.
- Request latency and the five-second budget result are logged without logging
  credentials or extracted label contents.

## 14. Security and privacy boundaries

Implemented controls include:

- API keys are read from environment variables and remain server-side.
- `.env` is excluded from Git and the Docker context.
- The Docker runtime uses a non-root user.
- Image references are confined to the configured bucket.
- Images are bounded and validated before processing.
- Processed images are not persisted.
- Provider requests use `store=false`.
- Application and verification responses use `Cache-Control: no-store`.
- Errors omit internal and provider exception details.
- Basic browser hardening headers are applied.

The proof of concept has no authentication, authorization, rate limiting, or
CSRF protection. It must not expose sensitive records or an unrestricted paid
AI endpoint publicly without those controls.

## 15. Assumptions

- Application identifiers are stable and unique.
- The mock CSV represents an upstream source of truth.
- Each image reference belongs to the application row that contains it.
- Source CSV records and label images are trusted administrative inputs, not
  browser-submitted content.
- Live mode has outbound network access and a valid server-side OpenAI API key.
- The configured vision model supports the requested typed output.
- Reviewers make final decisions; `PASS` is only a screening result.
- Batch size and concurrency limits are sufficient for the proof-of-concept data.
- Local review-status persistence is acceptable for a demo and is not relied on
  as a durable audit log.
- Deployment termination provides TLS; the application itself serves HTTP.

## 16. Tradeoffs and future evolution

The repository and image-store protocols provide seams for replacing the mock CSV
and local bucket with real upstream services. The vision-service protocol permits
another OCR provider without changing deterministic comparison logic.

Before production use, add:

1. Authentication and role-based authorization.
2. Rate limits and quotas around live extraction.
3. Durable, access-controlled review/audit storage.
4. CSRF protection for state-changing browser actions.
5. A real object-storage implementation behind `ImageStore`.
6. Centralized structured logging, metrics, and alerting.
7. Secret-manager integration and a documented key-rotation process.
8. Content Security Policy and deployment-level HSTS.
9. Load and latency testing against representative production images.

## 17. Verification and acceptance checks

```bash
uv run pytest
cd frontend
npm run build
```

Acceptance checks should confirm that:

- Selecting an application does not invoke extraction.
- Single verification returns seven field results.
- Batch verification uses the same comparison behavior.
- A failed batch image does not fail other items.
- Missing extraction values produce `REVIEW`.
- Government-warning comparison remains case-sensitive.
- API responses do not contain environment secrets or stack traces.
- The production frontend is served by FastAPI.
