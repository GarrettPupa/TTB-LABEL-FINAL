# TTB Label Verification Project Rules

## Project description

TTB Label Verification is a standalone proof-of-concept application for screening
alcohol label images against expected application data.

The application simulates a workflow in which alcohol label application records already
exist in an external system and their corresponding label images already exist in object
storage. For this proof of concept, those systems are represented by a mock CSV dataset
and a local bucket-style image directory.

The application uses AI/OCR to extract visible text from label images and then applies
deterministic, field-by-field comparison rules between the extracted label information
and the expected application data.

Verification results are review aids only. They are not final legal determinations,
regulatory decisions, TTB approvals, or substitutes for human review.

## Required stack

- Python 3.12
- FastAPI backend
- Pydantic models
- React, TypeScript, and Vite frontend
- uv for Python dependency management
- pytest for backend tests
- No production database
- Stateless request processing
- Docker-based deployment
- Frontend production build served by FastAPI as static files
- Mock application data stored in CSV format
- Label images stored in a local bucket-style folder for the proof of concept

## Application data and image model

This proof of concept must simulate retrieving an existing TTB application and its
associated label image. It is not a user-submitted form or file-upload application.

### Mock application data

- Application records must come from a CSV file that acts as the mock application
  database.
- Each CSV row represents one application.
- Each application must contain a stable application identifier.
- Each row must contain the expected application fields needed for verification.
- Each row must also contain a reference to its associated label image.
- The image reference must point to an image stored in the project's bucket-style
  image folder.
- Do not require the user to manually enter application data.
- Do not require the user to upload a label image.
- Treat the CSV and referenced images as read-only source data during verification.

The CSV is a mock representation of an upstream application system. Do not introduce
a real database unless the project requirements are explicitly changed.

### Image storage

- Label images must already exist in a designated bucket-style folder.
- The CSV record determines which image belongs to an application.
- Application records and images must be associated using a stable identifier or
  explicit image reference.
- The application must not permanently copy, modify, or persist images as part of
  verification.
- Structure image access so that replacing the local proof-of-concept bucket with
  real object storage in the future would not require redesigning the verification
  workflow.

## User interaction model

### Single application verification

The normal user workflow is:

1. Load available applications from the mock CSV dataset.
2. Allow the user to select an application from the interface.
3. When an application is selected, populate the interface with that application's
   expected application data.
4. Load and display the label image referenced by that application.
5. Do not run OCR simply because the application was selected.
6. When the user selects **Verify**, send the selected application for verification.
7. Run OCR/AI extraction against the application's referenced label image.
8. Compare extracted label values against the expected values from the CSV application.
9. Apply the required comparison rule for each field.
10. Display clear match, mismatch, and review-needed results to the user.

The user must not need to manually copy application information or locate and upload
the associated image.

### Batch verification

Batch verification must operate on the existing mock application dataset rather than
requiring users to upload multiple files.

The batch workflow is:

1. Read all applicable application records from the mock CSV dataset.
2. Resolve the label image associated with each application.
3. Load each referenced image from the bucket-style image folder.
4. Run the same OCR/extraction and deterministic comparison process used for a
   single application.
5. Produce an individual verification result for each application.
6. Present an understandable batch summary while allowing individual application
   results to be reviewed.

Single and batch verification must use the same underlying verification logic.
Do not create separate comparison behavior for batch processing.

A missing, unreadable, or invalid image reference must produce a clear per-application
error and must not cause the entire batch operation to fail unnecessarily.

## Verification rules

- Government warning text must be compared exactly and case-sensitively.
- Government warning verification must not use fuzzy matching, case normalization,
  spelling correction, or semantic similarity.
- Other fields may use explicitly defined normalization or fuzzy comparison where
  appropriate.
- Keep OCR/extraction separate from deterministic verification logic.
- AI/OCR output must not itself determine whether an application passes verification.
- Deterministic application code must make the final field comparison decisions.
- Preserve enough information in verification results to show the expected value,
  extracted value, and comparison result.
- OCR uncertainty or missing extracted values should be surfaced as review conditions
  rather than silently treated as matches.

## Hard requirements

- A normal single-application verification request must complete in under 5 seconds.
- The interface must be usable by a nontechnical user without instructions.
- Single-application verification is required.
- Batch verification of the mock CSV application dataset is required.
- Users must not be required to manually fill out an application form.
- Users must not be required to upload label images.
- Government warning text must be compared exactly and case-sensitively.
- Other fields may use normalization or fuzzy comparison.
- API keys and secrets must only come from environment variables.
- Never hardcode or commit secrets.
- Do not persist uploaded, generated, or processed images or application data.
- Do not modify the source CSV or source label images during verification.
- Errors shown to users must be readable and must never expose stack traces.
- A failure processing one application during a batch must not unnecessarily prevent
  other applications from being processed.

## Architecture expectations

Keep the implementation small and modular.

Prefer clear separation between:

- CSV application-data loading
- Image resolution/loading
- OCR or AI extraction
- Pydantic request/response models
- Deterministic verification/comparison logic
- Single-application orchestration
- Batch-verification orchestration
- FastAPI routes
- React presentation and user interaction

The verification engine should operate on application data and extracted label data,
not on UI-specific structures.

The single-application and batch workflows should call the same verification engine.

Do not introduce:

- A production database
- User authentication
- User-managed file storage
- A general-purpose file-upload system
- Background job infrastructure unless required to satisfy an approved phase
- Message queues
- Persistent application state
- Unnecessary cloud infrastructure

unless the project requirements are explicitly changed.

## Working process

When I say PLAN:

- Read the existing repository.
- Propose an implementation plan.
- List files to create or modify.
- Identify risks and acceptance checks.
- Do not write code or run modifying commands.

When I say REVIEW:

- Critique the proposed plan against the project requirements.
- Identify missing tests, security problems, deployment risks, and scope creep.
- Produce a finalized plan.
- Do not write code.

When I say EXECUTE:

- Implement only the approved phase.
- Keep changes limited to the current phase.
- Write or update tests.
- Run all relevant tests and quality checks.
- Report what changed and how I can verify it.

Prefer a small, correct, well-tested implementation over unnecessary features.