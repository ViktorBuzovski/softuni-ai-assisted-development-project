import pytest
from fastapi.testclient import TestClient

from invoice_digitizer.api import create_app
from invoice_digitizer.settings import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, data_dir=tmp_path / "data", max_upload_bytes=1024)


@pytest.fixture
def client(settings):
    return TestClient(create_app(settings))
