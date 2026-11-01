import json
import os
import re
from datetime import date
from decimal import Decimal

import pytest

from invoice_digitizer.extraction import RULES, SCHEMA, ExtractionError, extract_invoice
from invoice_digitizer.models import DocumentType, ExtractedInvoice
from invoice_digitizer.settings import Settings

FAKE_CLAUDE = """#!{python}
import json, os, pathlib, sys, time

pathlib.Path(os.environ["FAKE_CLAUDE_RECORD"]).write_text(json.dumps({{
    "args": sys.argv[1:],
    "cwd": os.getcwd(),
    "files": os.listdir("."),
    "api_key": os.environ.get("ANTHROPIC_API_KEY"),
    "auth_token": os.environ.get("ANTHROPIC_AUTH_TOKEN"),
    "path": os.environ.get("PATH"),
    "stdin_closed": os.path.samestat(os.fstat(0), os.stat(os.devnull)),
}}))
time.sleep(float(os.environ.get("FAKE_CLAUDE_SLEEP", "0")))
print(os.environ["FAKE_CLAUDE_OUTPUT"])
sys.exit(int(os.environ.get("FAKE_CLAUDE_EXIT", "0")))
"""

FIELDS = {
    "document_type": "invoice",
    "invoice_number": "INV-1",
    "invoice_date": "2026-03-15",
    "due_date": None,
    "supplier_name": "Müller & Töchter KG",
    "supplier_tax_id": "DE123456789",
    "buyer_name": "Example Buyer",
    "buyer_tax_id": None,
    "currency": "EUR",
    "subtotal": "1430.70",
    "tax_amount": "261.07",
    "total": "1691.77",
    "line_items": [
        {"description": "Printing", "quantity": "2", "unit_price": "45.50", "amount": "91.00"},
    ],
}


def result_with(fields) -> str:
    return json.dumps({"type": "result", "is_error": False, "structured_output": fields})


@pytest.fixture
def invoice_file(tmp_path):
    path = tmp_path / "abc123.pdf"
    path.write_bytes(b"%PDF-1.7\nsample invoice")
    (tmp_path / "other-invoice.pdf").write_bytes(b"%PDF-1.7\nsomeone else's invoice")
    return path


@pytest.fixture
def record_file(tmp_path, monkeypatch):
    path = tmp_path / "record.json"
    monkeypatch.setenv("FAKE_CLAUDE_RECORD", str(path))
    monkeypatch.setenv("FAKE_CLAUDE_OUTPUT", result_with(FIELDS))
    return path


@pytest.fixture
def settings(tmp_path, record_file):
    command = tmp_path / "fake-claude"
    command.write_text(FAKE_CLAUDE.format(python=os.sys.executable))
    command.chmod(0o755)
    return Settings(_env_file=None, claude_command=str(command), extraction_timeout_seconds=30)


def recorded_run(record_file) -> dict:
    return json.loads(record_file.read_text())


def value_after(args: list[str], flag: str) -> str:
    return args[args.index(flag) + 1]


def test_returns_the_fields_as_exact_values(invoice_file, settings):
    extracted = extract_invoice(invoice_file, settings)

    assert extracted.document_type == DocumentType.invoice
    assert extracted.invoice_date == date(2026, 3, 15)
    assert extracted.due_date is None
    assert extracted.supplier_name == "Müller & Töchter KG"
    assert extracted.total == Decimal("1691.77")
    assert str(extracted.line_items[0].unit_price) == "45.50"


def test_large_amounts_stay_exact(invoice_file, settings, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_OUTPUT", result_with({**FIELDS, "total": "99999999999999.99"}))

    assert str(extract_invoice(invoice_file, settings).total) == "99999999999999.99"


def test_run_gets_only_the_read_tool_and_a_locked_down_session(invoice_file, settings, record_file):
    extract_invoice(invoice_file, settings)

    args = recorded_run(record_file)["args"]
    assert value_after(args, "--tools") == "Read"
    assert value_after(args, "--permission-mode") == "dontAsk"
    assert "--restricted" in args
    assert "--strict-mcp-config" in args
    assert "--no-session-persistence" in args


def test_run_sees_only_a_copy_of_the_one_file(invoice_file, settings, record_file):
    extract_invoice(invoice_file, settings)

    run = recorded_run(record_file)
    assert run["files"] == ["invoice.pdf"]
    assert run["cwd"] not in str(invoice_file)
    assert "invoice.pdf" in value_after(run["args"], "--print")
    assert not os.path.exists(run["cwd"])
    assert invoice_file.exists()


def test_run_uses_the_rules_schema_and_settings(invoice_file, settings, record_file):
    settings.extraction_model = "some-model"
    settings.extraction_effort = "low"

    extract_invoice(invoice_file, settings)

    args = recorded_run(record_file)["args"]
    assert value_after(args, "--system-prompt") == RULES
    assert value_after(args, "--json-schema") == SCHEMA
    assert value_after(args, "--output-format") == "json"
    assert value_after(args, "--model") == "some-model"
    assert value_after(args, "--effort") == "low"


def test_api_credentials_are_kept_from_the_run(invoice_file, settings, record_file, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy-key")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "dummy-token")

    extract_invoice(invoice_file, settings)

    run = recorded_run(record_file)
    assert run["api_key"] is None
    assert run["auth_token"] is None
    assert run["path"] == os.environ["PATH"]


def test_stdin_is_closed(invoice_file, settings, record_file):
    extract_invoice(invoice_file, settings)

    assert recorded_run(record_file)["stdin_closed"] is True


@pytest.mark.parametrize(
    "output",
    [
        "not json",
        "[]",
        json.dumps({"type": "result", "is_error": True, "result": "Invalid API key"}),
        json.dumps({"type": "result", "is_error": False}),
        result_with({"document_type": "invoice", "invoice_date": "15/03/2026"}),
        result_with({"document_type": "invoice", "currency": "euro"}),
        result_with({"document_type": "invoice", "unexpected": "field"}),
        result_with({"invoice_number": "no document type"}),
    ],
)
def test_unusable_output_raises_a_safe_error(invoice_file, settings, monkeypatch, output):
    monkeypatch.setenv("FAKE_CLAUDE_OUTPUT", output)

    with pytest.raises(ExtractionError) as error:
        extract_invoice(invoice_file, settings)

    assert "Invalid API key" not in str(error.value)
    assert "unexpected" not in str(error.value)


def test_failing_command_raises_a_safe_error(invoice_file, settings, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_EXIT", "1")

    with pytest.raises(ExtractionError, match="logged in"):
        extract_invoice(invoice_file, settings)


def test_missing_command_raises_a_clear_error(invoice_file, settings, tmp_path):
    settings.claude_command = str(tmp_path / "no-such-command")

    with pytest.raises(ExtractionError, match="not found"):
        extract_invoice(invoice_file, settings)


def test_slow_command_times_out(invoice_file, settings, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP", "10")
    settings.extraction_timeout_seconds = 0.5

    with pytest.raises(ExtractionError, match="timed out"):
        extract_invoice(invoice_file, settings)


def test_schema_makes_the_model_answer_every_field():
    schema = json.loads(SCHEMA)

    assert set(schema["required"]) == set(ExtractedInvoice.model_fields)
    assert schema["additionalProperties"] is False
    line_item = schema["$defs"]["ExtractedLineItem"]
    assert set(line_item["required"]) == {"description", "quantity", "unit_price", "amount"}
    assert line_item["additionalProperties"] is False


def test_schema_asks_for_amounts_as_strings():
    schema = json.loads(SCHEMA)

    [amount, _] = schema["properties"]["total"]["anyOf"]
    assert amount["type"] == "string"
    assert re.fullmatch(amount["pattern"], "-1234.50")
    assert not re.fullmatch(amount["pattern"], "1,234.50")
