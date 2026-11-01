# Invoice digitizer

Turns uploaded invoices (PDF, PNG, JPEG) into structured records, using Claude to read them. It is a proof of concept for the SoftUni AI-Assisted Development exam. Claude Code and Codex both work in this repo and read this file.

## Commands

```bash
uv sync                                                  # install
uv run pytest                                            # all tests, with no call to the real model
uv run pytest -m live                                    # real extraction on samples/, uses the Claude subscription
uv run uvicorn --factory invoice_digitizer.api:create_app  # serve on http://127.0.0.1:8000
```

## Layout

- `src/invoice_digitizer/`: the application
  - `api.py`: the FastAPI app, built by `create_app(settings)` so tests can use a temporary folder
  - `models.py`: database tables and API schemas
  - `extraction.py`: runs the `claude` command
  - `extraction_rules.md`: the rules sent to the model
  - `records.py`: search filters and the CSV export
  - `files.py`: file type detection and file names
  - `static/`: the web page, in plain HTML, CSS and JavaScript with no build step
  - `settings.py`: settings read from the environment and `.env`
- `tests/`: pytest tests, including browser tests in `test_e2e.py` that drive Google Chrome with a fake extract function
- `samples/`: fictional invoices with their correct values
- `docs/PROMPTS.md`: the prompts used to build each module
- `docs/AI_SETUP.md`: the tools, models, agents and skills used

## Project decisions

- **AI-read invoice data is trusted.** Don't add a review step, confidence scores or a validation gate.
- **It is a proof of concept.** Don't add authentication, migrations or Docker.
- **It runs as one server process.** Startup marks any invoice left in `extracting` as failed, so a second process would disturb running extractions. Don't use `--workers` or `--reload` while extractions are running.
- **Extraction runs on a Claude subscription through the `claude` command.** `ANTHROPIC_API_KEY` is a dummy placeholder. Don't ask for a real key or call the Anthropic API.
- **Invoice files are untrusted input.** Keep the extraction run limited to reading one file, and never render invoice text as HTML.

Use the `invoice-extraction` skill for any work on extraction or on showing extracted fields.

The `tester` agent in `.claude/agents/` tries to break the code by writing and running new tests. It reports bugs and doesn't fix them.

## Working rules

- Write a module's prompt in `docs/PROMPTS.md` before starting it, and record corrections under it as follow-ups.
- Update `docs/AI_SETUP.md` when a stage changes which tools, models, agents or skills were used. It records only what was actually run.
- Add tests with every change. Normal tests must not call the real model.
- Follow the code around you: small functions, plain names, and few comments.
- Don't commit `.env`, `data/` or `Project-Assignment.docx`.
