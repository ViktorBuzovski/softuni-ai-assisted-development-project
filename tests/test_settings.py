from pathlib import Path

import pytest

from invoice_digitizer.settings import Settings


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in ("DATA_DIR", "MAX_UPLOAD_BYTES", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_defaults():
    settings = Settings(_env_file=None)

    assert settings.data_dir == Path("data")
    assert settings.max_upload_bytes == 10 * 1024 * 1024
    assert settings.anthropic_api_key == "dummy-key"


def test_paths_are_inside_the_data_folder():
    settings = Settings(_env_file=None, data_dir=Path("somewhere"))

    assert settings.uploads_dir == Path("somewhere/uploads")
    assert settings.database_path == Path("somewhere/invoices.db")


def test_environment_overrides_defaults(monkeypatch):
    monkeypatch.setenv("DATA_DIR", "/tmp/invoices")
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "1024")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "other-key")

    settings = Settings(_env_file=None)

    assert settings.data_dir == Path("/tmp/invoices")
    assert settings.max_upload_bytes == 1024
    assert settings.anthropic_api_key == "other-key"


def test_env_file_overrides_defaults(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("MAX_UPLOAD_BYTES=2048\n")

    settings = Settings(_env_file=env_file)

    assert settings.max_upload_bytes == 2048
