# Softuni AI assisted development project

An AI-powered invoice digitizer, built for the SoftUni AI-Assisted Development course exam.

## Run

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run uvicorn --factory invoice_digitizer.api:create_app
```

API documentation is at <http://127.0.0.1:8000/docs>.

## Test

```bash
uv run pytest
```

## Documentation

- [AI tooling and setup](docs/AI_SETUP.md)
- [Key prompts](docs/PROMPTS.md)
