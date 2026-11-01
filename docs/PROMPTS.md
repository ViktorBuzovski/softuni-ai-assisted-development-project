# Key prompts

The main prompts used to build each module. Each prompt is written before the module's work starts and then carried out as written, so this file shows what the tools were actually asked to do. Claude Code generates its prompts and carries them out in the same session. Codex receives its prompts through `codex exec`. Follow-up corrections are noted under the prompt they belong to.

## Structure

Every prompt uses these sections:

- **Goal:** the outcome in one or two sentences.
- **Context:** the relevant files and what already exists.
- **Requirements:** what to build or change.
- **Constraints:** what to avoid, and which libraries or patterns to use.
- **Done when:** the tests or checks that prove it works.

## Module 1: Upload and storage

### Prompt 1.1: Project setup

**Goal:** Set up a Python project for an invoice digitizer, so that modules can be added and tested from the start.

**Context:** The repo holds only documentation and a `.gitignore`. The stack is Python 3.12 managed with uv, FastAPI, SQLModel on SQLite, and pytest.

**Requirements:**
- Create an installable package named `invoice_digitizer` under `src/`.
- Add a settings class that reads the environment and a `.env` file:
  - `DATA_DIR`, default `data`
  - `MAX_UPLOAD_BYTES`, default 10 MB
  - `ANTHROPIC_API_KEY`, default a dummy placeholder
- Add a `.env.example` that documents each setting. Say that the API key is a placeholder and isn't used, because this proof of concept runs on a Claude Code subscription.
- Ignore `.venv/`, Python caches and `data/` in git.

**Constraints:** This is a proof of concept, so add no authentication, migrations or Docker. Keep real keys out of the repo.

**Done when:** `uv run pytest` passes with tests for the setting defaults and for overriding them through the environment.

### Prompt 1.2: Upload and storage API

**Goal:** Let a client upload an invoice file and get back a stored record that is still available after a restart.

**Context:** The project from prompt 1.1 exists. This adds the first database table, and later modules will add extracted fields to the same record.

**Requirements:**
- Add an `Invoice` table with `id`, `filename`, `stored_name`, `content_type`, `size_bytes`, `status` (starts as `uploaded`) and `uploaded_at`.
- Add these endpoints:
  - `POST /api/invoices` takes a multipart file and returns 201 with the record.
  - `GET /api/invoices/{id}` returns the record.
  - `GET /api/invoices/{id}/file` returns the original file.
  - Both `GET` endpoints return 404 for an unknown id.
- Accept only PDF, PNG and JPEG. Detect the type from the file's first bytes, not from its name or the client's content type, and return 415 for anything else.
- Return 400 for an empty file and 413 for a file larger than `MAX_UPLOAD_BYTES`.
- Save files under `DATA_DIR/uploads` with generated names. Keep the original filename, without any directory part, for display only.
- If the database write fails, remove the saved file.

**Constraints:**
- Use an app factory, `create_app(settings)`, so that tests can run against a temporary folder.
- Don't add listing or search (module 3), AI extraction (module 2) or authentication.

**Done when:**
- pytest covers each accepted type, content that doesn't match its extension, an empty file, an oversized file, a path traversal filename, an unknown id, a failed database write leaving no file behind, and a record and file surviving an app restart.
- A real server started with uvicorn accepts an upload through `curl` and returns the same file.

**Follow-ups:**
- The first test run failed during collection, because `tests/test_upload.py` imported a constant from `tests.conftest` and `tests` isn't an importable package. The tests now read the limit from the `settings` fixture.
- pytest warned that using `httpx` with Starlette's test client is deprecated, so the test dependency changed to `httpx2`.

## Module 2: AI extraction

Not written yet.

## Module 3: Invoice records API

Not written yet.

## Module 4: Web UI

Not written yet.
