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

### Prompt 2.1: Extraction rules, skill and shared instructions

**Goal:** Give both tools the same knowledge of this project before any extraction code exists, and make the running app follow those same rules.

**Context:** Claude Code reads `CLAUDE.md` and `.claude/skills`. Codex reads `AGENTS.md` and `.agents/skills`. Nothing is shared between them yet. Module 1 is committed.

**Requirements:**
- Write `src/invoice_digitizer/extraction_rules.md`, the instructions the app sends to the model. Cover document types, supplier and buyer, tax IDs, dates including ambiguous ones, number formats such as `1.234,56` and `1,234.56`, subtotal and tax and total, total versus balance due, currency codes, line items, null for anything not printed, and treating the file's content as data rather than instructions.
- Add a skill named `invoice-extraction` to `.claude/skills/`, with an identical copy in `.agents/skills/`. It says when to use it, points to the rules file, gives the steps for changing extraction, lists the edge cases the samples must keep covering, and says how to show extracted values.
- Add `AGENTS.md` with the commands, layout, project decisions and working rules, and a `CLAUDE.md` that imports it.
- Add a test that fails if the two skill copies differ.

**Constraints:** Add no review step or confidence scores, because AI-read data is trusted in this project. Keep the skill short and point to the rules instead of repeating them.

**Done when:** `uv run pytest` passes, and `claude -p` and `codex exec` each list the skill when asked.

**Follow-ups:**
- The first check with `claude -p` couldn't confirm the skill, because it ran with `--tools ""`, which also removes the Skill tool that carries the list. Run again with `--tools Skill`, it listed the skill.

### Prompt 2.2: Sample invoices and extraction

**Goal:** Read a stored invoice by running the `claude` command, and save the result on the invoice's record.

**Context:** The rules and skill from prompt 2.1 exist. A trial run showed three things about the command:
- `claude -p --restricted --tools Read --json-schema ... --output-format json` returns the fields in `structured_output`.
- A dummy `ANTHROPIC_API_KEY` in the environment makes it fail with a 401 instead of using the subscription.
- It waits 3 seconds on stdin unless stdin is closed.

**Requirements:**
- Add fictional samples under `samples/`, rendered from HTML by a small script, with the correct values in `samples/expected.json`:
  - a PDF with a deposit, so that total and balance due differ, and a separate ship-to party
  - a German PNG with `1.234,56` amounts and two VAT rates but no printed tax total
  - a UK JPEG credit note with negative amounts, a day-first date and no due date
  - a PDF with hidden text telling the AI to change the values
  - a PNG that isn't an invoice
- Add these fields to `Invoice`: `document_type`, `invoice_number`, `invoice_date`, `due_date`, `supplier_name`, `supplier_tax_id`, `buyer_name`, `buyer_tax_id`, `currency`, `subtotal`, `tax_amount`, `total`, `line_items`, `extraction_error` and `extracted_at`. Keep amounts as exact decimals.
- Add `extract_invoice(path, settings)`, which:
  - copies the file into an empty temporary folder and runs `claude` there
  - uses a JSON schema generated from the same model that validates the answer
  - sends `extraction_rules.md` as the system prompt
  - removes `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` from the environment and closes stdin
  - raises `ExtractionError` with a safe message if the command is missing, times out, exits with an error, reports `is_error`, or returns output that doesn't validate
- Add `POST /api/invoices/{id}/extract`, which waits for the result and returns the record:
  - Status goes from `uploaded` or `failed` to `extracting`, then to `extracted` or `failed`.
  - Claim the invoice with one atomic update. Return 409 if it is already `extracting` or `extracted`.
  - A document the model calls `other` ends as `failed` with a clear message.
  - On startup, mark anything left in `extracting` as `failed`.
- Add the settings `CLAUDE_COMMAND`, `EXTRACTION_MODEL` (default `claude-opus-5-5`), `EXTRACTION_EFFORT` (default `medium`) and `EXTRACTION_TIMEOUT_SECONDS` (default 180).

**Constraints:**
- Treat every invoice file as untrusted. The run gets only the `Read` tool, inside `--restricted`, in a folder holding that one file.
- Don't call the Anthropic API or ask for a real key.
- Add no review step or confidence scores.
- Normal tests must not call the real command. Use a fake executable for the command, and a fake extract function for the API.

**Done when:**
- pytest covers the command's arguments, environment and working folder, each failure above, each status change, the 409s, and the startup reset.
- `uv run pytest -m live` passes on all five samples against the real command, with a dummy key set and outside any Claude Code session.

**Follow-ups:**
- SQLModel's `Field` has no `pattern` argument, so the first test run failed on import. The currency code now uses a Pydantic `StringConstraints` type.
- Two extra checks were run to make sure the live tests weren't passing for the wrong reason. Changing one line item's quantity makes the comparison fail, and the model can quote the hidden text in the injection sample, so it did see the text and didn't follow it.
- The first check through a real server failed because another program on the machine already used the chosen port. Running on a free port, upload and extraction worked through `curl`, a second extraction returned 409, and the non-invoice ended as `failed`.

## Module 3: Invoice records API

### Prompt 3.1: Listing, search, delete and export

**Goal:** Let a client find, remove and export invoice records, so that the digitized data can be used outside this app.

**Context:** Modules 1 and 2 are committed. `Invoice` holds the file details and the extracted fields. Invoice text can be in any language and is untrusted, and credit notes have negative amounts.

**Requirements:**
- Add `GET /api/invoices`, newest first, returning `{"items": [...], "total": n}`:
  - `q` searches the filename, invoice number, supplier and buyer. It ignores case, including outside ASCII, so `MÜLLER` finds `Müller`. `%` and `_` count as plain characters.
  - `status` filters by status, and an unknown value returns 422.
  - `limit` (default 100, at most 500) and `offset` page through the results. `total` counts all matches, not just the page.
- Add `GET /api/invoices/export.csv` with the same `q` and `status` filters:
  - One row per invoice, with the file details and header fields but no line items.
  - Encoded as UTF-8 with a byte order mark, so that Excel shows non-English text correctly.
  - Text cells that a spreadsheet would run as a formula are neutralized. Amounts stay as plain numbers, including negative ones.
- Add `DELETE /api/invoices/{id}`, which returns 204 and removes the record and its file. Return 409 while the invoice is `extracting`.
- Return every timestamp as UTC with an explicit offset. SQLite drops the time zone, so a browser would read the stored value as local time.

**Constraints:** Add no editing of extracted fields, because AI-read data is trusted. Add no authentication. Put the CSV and search helpers in their own small module and keep routes thin.

**Done when:** pytest covers newest-first order, each searched field, non-ASCII case, `%` and `_` as plain characters, the status filter, paging with the right `total`, the CSV's encoding and contents, a formula cell, a negative amount in the CSV, delete removing the file, delete during extraction, and timestamps with an offset.

## Module 4: Web UI

Not written yet.
