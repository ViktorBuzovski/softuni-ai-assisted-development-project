import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from invoice_digitizer.api import create_app

PDF = b"%PDF-1.7\nsample invoice"
PNG = b"\x89PNG\r\n\x1a\nsample invoice"
JPEG = b"\xff\xd8\xff\xe0sample invoice"


def upload(client, name, content, content_type="application/octet-stream"):
    return client.post("/api/invoices", files={"file": (name, content, content_type)})


def stored_files(settings):
    return list(settings.uploads_dir.iterdir())


@pytest.mark.parametrize(
    ("name", "content", "content_type", "extension"),
    [
        ("invoice.pdf", PDF, "application/pdf", ".pdf"),
        ("invoice.png", PNG, "image/png", ".png"),
        ("invoice.jpg", JPEG, "image/jpeg", ".jpg"),
    ],
)
def test_upload_accepts_each_supported_type(
    client, settings, name, content, content_type, extension
):
    response = upload(client, name, content)

    assert response.status_code == 201
    invoice = response.json()
    assert invoice["filename"] == name
    assert invoice["content_type"] == content_type
    assert invoice["size_bytes"] == len(content)
    assert invoice["status"] == "uploaded"
    assert "stored_name" not in invoice

    [stored] = stored_files(settings)
    assert stored.suffix == extension
    assert stored.read_bytes() == content


def test_type_comes_from_the_content_not_the_name(client):
    response = upload(client, "invoice.pdf", PNG, "application/pdf")

    assert response.status_code == 201
    assert response.json()["content_type"] == "image/png"


@pytest.mark.parametrize(
    ("name", "content_type"),
    [("invoice.pdf", "application/pdf"), ("notes.txt", "text/plain")],
)
def test_upload_rejects_unsupported_content(client, settings, name, content_type):
    response = upload(client, name, b"just some text", content_type)

    assert response.status_code == 415
    assert stored_files(settings) == []
    assert client.get("/api/invoices/1").status_code == 404


def test_upload_rejects_empty_file(client, settings):
    response = upload(client, "invoice.pdf", b"")

    assert response.status_code == 400
    assert stored_files(settings) == []


def test_upload_rejects_file_over_the_limit(client, settings):
    response = upload(client, "invoice.pdf", PDF.ljust(settings.max_upload_bytes + 1, b"x"))

    assert response.status_code == 413
    assert stored_files(settings) == []


def test_upload_accepts_file_at_the_limit(client, settings):
    response = upload(client, "invoice.pdf", PDF.ljust(settings.max_upload_bytes, b"x"))

    assert response.status_code == 201
    assert response.json()["size_bytes"] == settings.max_upload_bytes


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("../../evil.pdf", "evil.pdf"),
        ("..\\..\\evil.pdf", "evil.pdf"),
        ("/etc/evil.pdf", "evil.pdf"),
        ("..", "invoice"),
    ],
)
def test_path_traversal_filename_stays_in_the_uploads_folder(
    client, settings, tmp_path, name, expected
):
    response = upload(client, name, PDF)

    assert response.status_code == 201
    assert response.json()["filename"] == expected
    [stored] = stored_files(settings)
    assert stored.parent == settings.uploads_dir
    assert expected not in stored.name
    assert not list(tmp_path.rglob("evil.pdf"))


def test_read_invoice_returns_the_record(client):
    created = upload(client, "invoice.pdf", PDF).json()

    response = client.get(f"/api/invoices/{created['id']}")

    assert response.status_code == 200
    assert response.json() == created


def test_read_file_returns_the_original_file(client):
    created = upload(client, "invoice.png", PNG).json()

    response = client.get(f"/api/invoices/{created['id']}/file")

    assert response.status_code == 200
    assert response.content == PNG
    assert response.headers["content-type"] == "image/png"
    assert 'filename="invoice.png"' in response.headers["content-disposition"]


@pytest.mark.parametrize("path", ["/api/invoices/999", "/api/invoices/999/file"])
def test_unknown_invoice_returns_404(client, path):
    assert client.get(path).status_code == 404


def test_failed_database_write_leaves_no_file(settings, monkeypatch):
    client = TestClient(create_app(settings), raise_server_exceptions=False)

    def fail(self):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(Session, "commit", fail)

    response = upload(client, "invoice.pdf", PDF)

    assert response.status_code == 500
    assert stored_files(settings) == []


def test_record_and_file_survive_a_restart(client, settings):
    created = upload(client, "invoice.pdf", PDF).json()

    restarted = TestClient(create_app(settings))

    assert restarted.get(f"/api/invoices/{created['id']}").json() == created
    assert restarted.get(f"/api/invoices/{created['id']}/file").content == PDF
