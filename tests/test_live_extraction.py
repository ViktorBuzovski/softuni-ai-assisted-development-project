"""Runs the real claude command on the samples. Start it with: uv run pytest -m live"""

import json
from pathlib import Path

import pytest

from invoice_digitizer.extraction import extract_invoice
from invoice_digitizer.models import ExtractedInvoice
from invoice_digitizer.settings import Settings

SAMPLES = Path(__file__).parent.parent / "samples"
EXPECTED = json.loads((SAMPLES / "expected.json").read_text())

pytestmark = pytest.mark.live


@pytest.mark.parametrize("name", EXPECTED)
def test_extracts_the_correct_values(name, monkeypatch):
    # The placeholder from .env.example must not stop the run from using the subscription.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy-key")

    extracted = extract_invoice(SAMPLES / name, Settings(_env_file=None))

    assert extracted == ExtractedInvoice.model_validate(EXPECTED[name])
