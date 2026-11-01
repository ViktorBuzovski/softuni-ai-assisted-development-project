import pytest
from fastapi.testclient import TestClient

from invoice_digitizer.api import create_app
from invoice_digitizer.models import ExtractedInvoice
from invoice_digitizer.settings import Settings


class FakeExtract:
    """Stands in for the real extraction and returns or raises what each test sets."""

    def __init__(self):
        self.outcome = ExtractedInvoice(document_type="invoice")
        self.paths = []
        self.during_call = None

    def __call__(self, path):
        self.paths.append(path)
        if self.during_call:
            self.during_call()
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, data_dir=tmp_path / "data", max_upload_bytes=1024)


@pytest.fixture
def extract():
    return FakeExtract()


@pytest.fixture
def client(settings, extract):
    return TestClient(create_app(settings, extract))
