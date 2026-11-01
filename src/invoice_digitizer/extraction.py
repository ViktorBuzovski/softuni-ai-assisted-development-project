import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import ValidationError

from invoice_digitizer.models import ExtractedInvoice
from invoice_digitizer.settings import Settings

logger = logging.getLogger(__name__)

RULES = Path(__file__).with_name("extraction_rules.md").read_text(encoding="utf-8")
SCHEMA = json.dumps(ExtractedInvoice.model_json_schema())

# Claude Code prefers these to the subscription login, and the dummy key would fail with a 401.
API_CREDENTIALS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


class ExtractionError(Exception):
    """An extraction failed. The message is safe to show to the user."""


def extract_invoice(path: Path, settings: Settings) -> ExtractedInvoice:
    with TemporaryDirectory(prefix="invoice-") as folder:
        # The run can read only this folder, so it holds this one file and nothing else.
        name = f"invoice{path.suffix}"
        shutil.copyfile(path, Path(folder, name))
        output = _run_claude(name, folder, settings)
    return _parse_output(output)


def _run_claude(name: str, folder: str, settings: Settings) -> str:
    command = [
        settings.claude_command,
        "--print",
        f"Read {name} in the current folder, all of its pages, and return its data.",
        "--system-prompt",
        RULES,
        "--json-schema",
        SCHEMA,
        "--output-format",
        "json",
        "--model",
        settings.extraction_model,
        "--effort",
        settings.extraction_effort,
        # Invoice files are untrusted: no commands, web, user settings or MCP servers.
        "--restricted",
        "--strict-mcp-config",
        "--tools",
        "Read",
        "--permission-mode",
        "dontAsk",
        "--no-session-persistence",
    ]
    environment = {k: v for k, v in os.environ.items() if k not in API_CREDENTIALS}
    try:
        result = subprocess.run(
            command,
            cwd=folder,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=settings.extraction_timeout_seconds,
        )
    except FileNotFoundError as error:
        raise ExtractionError("The claude command was not found") from error
    except subprocess.TimeoutExpired as error:
        raise ExtractionError("Extraction timed out") from error

    if result.returncode != 0:
        logger.error("claude exited with %s: %s", result.returncode, result.stderr or result.stdout)
        raise ExtractionError("The claude command failed. Check that it is logged in.")
    return result.stdout


def _parse_output(output: str) -> ExtractedInvoice:
    try:
        result = json.loads(output)
    except json.JSONDecodeError as error:
        logger.error("claude returned output that isn't JSON: %s", output)
        raise ExtractionError("Claude returned output that couldn't be read") from error

    if not isinstance(result, dict) or result.get("is_error"):
        logger.error("claude reported an error: %s", output)
        raise ExtractionError("Claude reported an error while reading the file")

    try:
        return ExtractedInvoice.model_validate(result.get("structured_output"))
    except ValidationError as error:
        logger.error("claude returned invalid fields: %s", error)
        raise ExtractionError("Claude's answer didn't match the expected fields") from error
