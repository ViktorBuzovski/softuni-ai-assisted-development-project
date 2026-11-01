import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, update

from invoice_digitizer.api import create_app
from invoice_digitizer.extraction import ExtractionError
from invoice_digitizer.models import ExtractedInvoice, Invoice, InvoiceStatus

PDF = b"%PDF-1.7\nsample invoice"
EXTRACTED = ExtractedInvoice(
    document_type="invoice",
    invoice_number="INV-1",
    invoice_date="2026-03-15",
    supplier_name="Example Supplier",
    currency="EUR",
    total="1234.50",
    line_items=[{"description": "Printing", "quantity": "2", "amount": "0.10"}],
)


@pytest.fixture
def extract(extract):
    extract.outcome = EXTRACTED
    return extract


@pytest.fixture
def invoice_id(client):
    return client.post("/api/invoices", files={"file": ("a.pdf", PDF)}).json()["id"]


def test_extraction_stores_the_fields(client, invoice_id):
    response = client.post(f"/api/invoices/{invoice_id}/extract")

    assert response.status_code == 200
    invoice = response.json()
    assert invoice["status"] == "extracted"
    assert invoice["document_type"] == "invoice"
    assert invoice["invoice_number"] == "INV-1"
    assert invoice["invoice_date"] == "2026-03-15"
    assert invoice["due_date"] is None
    assert invoice["total"] == "1234.50"
    assert invoice["line_items"] == [
        {"description": "Printing", "quantity": "2", "unit_price": None, "amount": "0.10"}
    ]
    assert invoice["extraction_error"] is None
    assert invoice["extracted_at"] is not None
    assert client.get(f"/api/invoices/{invoice_id}").json() == invoice


def test_extraction_reads_the_stored_file(client, extract, settings, invoice_id):
    client.post(f"/api/invoices/{invoice_id}/extract")

    [path] = extract.paths
    assert path.parent == settings.uploads_dir
    assert path.read_bytes() == PDF


def test_new_invoice_has_no_extracted_fields(client, invoice_id):
    invoice = client.get(f"/api/invoices/{invoice_id}").json()

    assert invoice["status"] == "uploaded"
    assert invoice["total"] is None
    assert invoice["line_items"] == []


def test_status_is_extracting_during_the_run(client, extract, invoice_id):
    seen = []
    extract.during_call = lambda: seen.append(client.get(f"/api/invoices/{invoice_id}").json())

    client.post(f"/api/invoices/{invoice_id}/extract")

    assert seen[0]["status"] == "extracting"


def test_second_request_during_the_run_gets_409(client, extract, invoice_id):
    seen = []
    extract.during_call = lambda: seen.append(client.post(f"/api/invoices/{invoice_id}/extract"))

    response = client.post(f"/api/invoices/{invoice_id}/extract")

    assert seen[0].status_code == 409
    assert response.json()["status"] == "extracted"
    assert len(extract.paths) == 1


def test_extracted_invoice_can_not_be_extracted_again(client, extract, invoice_id):
    client.post(f"/api/invoices/{invoice_id}/extract")

    response = client.post(f"/api/invoices/{invoice_id}/extract")

    assert response.status_code == 409
    assert len(extract.paths) == 1
    assert client.get(f"/api/invoices/{invoice_id}").json()["total"] == "1234.50"


def test_extraction_error_marks_the_invoice_failed(client, extract, invoice_id):
    extract.outcome = ExtractionError("Extraction timed out")

    response = client.post(f"/api/invoices/{invoice_id}/extract")

    assert response.status_code == 200
    invoice = response.json()
    assert invoice["status"] == "failed"
    assert invoice["extraction_error"] == "Extraction timed out"
    assert invoice["total"] is None


def test_unexpected_error_marks_the_invoice_failed_without_details(client, extract, invoice_id):
    extract.outcome = RuntimeError("secret internal detail")

    invoice = client.post(f"/api/invoices/{invoice_id}/extract").json()

    assert invoice["status"] == "failed"
    assert invoice["extraction_error"] == "Extraction failed unexpectedly"


def test_failed_save_marks_the_invoice_failed(client, invoice_id, monkeypatch):
    real_commit = Session.commit
    commits = []

    def fail_second_commit(self):
        commits.append(self)
        if len(commits) == 2:
            raise RuntimeError("database is locked")
        real_commit(self)

    monkeypatch.setattr(Session, "commit", fail_second_commit)

    invoice = client.post(f"/api/invoices/{invoice_id}/extract").json()

    assert invoice["status"] == "failed"
    assert invoice["extraction_error"] == "Extraction failed unexpectedly"
    assert invoice["total"] is None


def test_document_that_is_not_an_invoice_is_marked_failed(client, extract, invoice_id):
    extract.outcome = ExtractedInvoice(document_type="other")

    invoice = client.post(f"/api/invoices/{invoice_id}/extract").json()

    assert invoice["status"] == "failed"
    assert "doesn't look like an invoice" in invoice["extraction_error"]


def test_failed_invoice_can_be_retried(client, extract, invoice_id):
    extract.outcome = ExtractionError("Extraction timed out")
    client.post(f"/api/invoices/{invoice_id}/extract")
    extract.outcome = EXTRACTED

    invoice = client.post(f"/api/invoices/{invoice_id}/extract").json()

    assert invoice["status"] == "extracted"
    assert invoice["extraction_error"] is None


def test_unknown_invoice_returns_404(client):
    assert client.post("/api/invoices/999/extract").status_code == 404


def test_fields_survive_a_restart(client, settings, extract, invoice_id):
    extracted = client.post(f"/api/invoices/{invoice_id}/extract").json()

    restarted = TestClient(create_app(settings, extract))

    assert restarted.get(f"/api/invoices/{invoice_id}").json() == extracted


def test_restart_marks_an_interrupted_extraction_as_failed(client, settings, extract, invoice_id):
    engine = create_engine(f"sqlite:///{settings.database_path}")
    with Session(engine) as session:
        session.exec(update(Invoice).values(status=InvoiceStatus.extracting))
        session.commit()

    restarted = TestClient(create_app(settings, extract))

    invoice = restarted.get(f"/api/invoices/{invoice_id}").json()
    assert invoice["status"] == "failed"
    assert "restarted" in invoice["extraction_error"]
    assert restarted.post(f"/api/invoices/{invoice_id}/extract").json()["status"] == "extracted"
