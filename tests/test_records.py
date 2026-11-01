import csv
from datetime import datetime, timedelta
from decimal import Decimal
from io import StringIO

import pytest

from invoice_digitizer.extraction import ExtractionError
from invoice_digitizer.models import ExtractedInvoice

PDF = b"%PDF-1.7\nsample invoice"


@pytest.fixture
def add_invoice(client, extract):
    def add(filename="invoice.pdf", **fields):
        created = client.post("/api/invoices", files={"file": (filename, PDF)}).json()
        extract.outcome = ExtractedInvoice(document_type="invoice", **fields)
        return client.post(f"/api/invoices/{created['id']}/extract").json()

    return add


def names(response) -> list[str]:
    return [invoice["filename"] for invoice in response.json()["items"]]


def csv_rows(response) -> list[dict]:
    return list(csv.DictReader(StringIO(response.content.decode("utf-8-sig"))))


def test_list_is_empty_at_first(client):
    response = client.get("/api/invoices")

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0}


def test_list_returns_full_records_newest_first(client, add_invoice):
    add_invoice("first.pdf")
    second = add_invoice("second.pdf", total=Decimal("12.50"))

    body = client.get("/api/invoices").json()

    assert [invoice["filename"] for invoice in body["items"]] == ["second.pdf", "first.pdf"]
    assert body["items"][0] == second
    assert body["total"] == 2


@pytest.mark.parametrize(
    ("fields", "search"),
    [
        ({"invoice_number": "INV-777"}, "inv-777"),
        ({"supplier_name": "Brightwater Studio"}, "BRIGHTWATER"),
        ({"buyer_name": "Fenwick & Sons"}, "fenwick &"),
    ],
)
def test_search_covers_each_field(client, add_invoice, fields, search):
    add_invoice("match.pdf", **fields)
    add_invoice("other.pdf")

    assert names(client.get("/api/invoices", params={"q": search})) == ["match.pdf"]


def test_search_covers_the_filename(client, add_invoice):
    add_invoice("march-rent.pdf")
    add_invoice("other.pdf")

    assert names(client.get("/api/invoices", params={"q": "RENT"})) == ["march-rent.pdf"]


@pytest.mark.parametrize(
    ("stored", "search"),
    [("Müller KG", "MÜLLER"), ("Müller KG", "müller"), ("СОФИЯ ООД", "софия")],
)
def test_search_ignores_case_outside_ascii(client, add_invoice, stored, search):
    add_invoice("match.pdf", supplier_name=stored)

    assert names(client.get("/api/invoices", params={"q": search})) == ["match.pdf"]


@pytest.mark.parametrize("search", ["%", "_", "0%_"])
def test_search_treats_wildcards_as_plain_characters(client, add_invoice, search):
    add_invoice("match.pdf", invoice_number="100%_A")
    add_invoice("other.pdf", invoice_number="100xyA")

    assert names(client.get("/api/invoices", params={"q": search})) == ["match.pdf"]


def test_blank_search_returns_everything(client, add_invoice):
    add_invoice("one.pdf")

    assert names(client.get("/api/invoices", params={"q": "   "})) == ["one.pdf"]


def test_status_filter(client, add_invoice, extract):
    add_invoice("good.pdf")
    created = client.post("/api/invoices", files={"file": ("bad.pdf", PDF)}).json()
    extract.outcome = ExtractionError("Extraction timed out")
    client.post(f"/api/invoices/{created['id']}/extract")
    client.post("/api/invoices", files={"file": ("new.pdf", PDF)})

    assert names(client.get("/api/invoices", params={"status": "failed"})) == ["bad.pdf"]
    assert names(client.get("/api/invoices", params={"status": "uploaded"})) == ["new.pdf"]


def test_search_and_status_combine(client, add_invoice):
    add_invoice("match.pdf", supplier_name="Acme")
    client.post("/api/invoices", files={"file": ("acme-new.pdf", PDF)})

    response = client.get("/api/invoices", params={"q": "acme", "status": "extracted"})

    assert names(response) == ["match.pdf"]


def test_unknown_status_returns_422(client):
    assert client.get("/api/invoices", params={"status": "nonsense"}).status_code == 422


def test_paging_reports_the_total_of_all_matches(client, add_invoice):
    for number in range(5):
        add_invoice(f"{number}.pdf")

    body = client.get("/api/invoices", params={"limit": 2, "offset": 1})

    assert names(body) == ["3.pdf", "2.pdf"]
    assert body.json()["total"] == 5


@pytest.mark.parametrize(
    "params", [{"limit": 0}, {"limit": 501}, {"offset": -1}, {"limit": "many"}]
)
def test_invalid_paging_returns_422(client, params):
    assert client.get("/api/invoices", params=params).status_code == 422


def test_csv_holds_the_header_fields_as_utf8(client, add_invoice):
    add_invoice(
        "a.pdf",
        invoice_number="RE-1",
        invoice_date="2026-02-03",
        supplier_name="Müller & Töchter KG",
        currency="EUR",
        subtotal=Decimal("1430.70"),
        total=Decimal("1691.77"),
    )

    response = client.get("/api/invoices/export.csv")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert 'filename="invoices.csv"' in response.headers["content-disposition"]
    assert response.content.startswith(b"\xef\xbb\xbf")
    [row] = csv_rows(response)
    assert row["filename"] == "a.pdf"
    assert row["status"] == "extracted"
    assert row["document_type"] == "invoice"
    assert row["invoice_number"] == "RE-1"
    assert row["invoice_date"] == "2026-02-03"
    assert row["supplier_name"] == "Müller & Töchter KG"
    assert row["subtotal"] == "1430.70"
    assert row["total"] == "1691.77"
    assert row["due_date"] == ""
    assert "line_items" not in row


@pytest.mark.parametrize(
    "text", ["=1+1", "+1", "-1", "@SUM(A1)", "\tx", "\n=1+1", "＝1+1", "＋1", "－1", "＠SUM(A1)"]
)
def test_csv_neutralizes_formula_text(client, add_invoice, text):
    add_invoice("a.pdf", supplier_name=text)

    [row] = csv_rows(client.get("/api/invoices/export.csv"))

    assert row["supplier_name"] == f"'{text}"


def test_csv_neutralizes_a_formula_filename(client, add_invoice):
    add_invoice("=cmd.pdf")

    [row] = csv_rows(client.get("/api/invoices/export.csv"))

    assert row["filename"] == "'=cmd.pdf"


def test_csv_keeps_negative_amounts_as_numbers(client, add_invoice):
    add_invoice("credit.pdf", subtotal=Decimal("-120.00"), total=Decimal("-144.00"))

    [row] = csv_rows(client.get("/api/invoices/export.csv"))

    assert row["subtotal"] == "-120.00"
    assert row["total"] == "-144.00"


def test_csv_uses_the_same_filters_as_the_list(client, add_invoice):
    add_invoice("match.pdf", supplier_name="Acme")
    add_invoice("other.pdf", supplier_name="Zenith")

    response = client.get("/api/invoices/export.csv", params={"q": "acme"})

    assert [row["filename"] for row in csv_rows(response)] == ["match.pdf"]


def test_csv_reads_as_a_route_not_an_id(client):
    assert client.get("/api/invoices/export.csv").status_code == 200


def test_delete_removes_the_record_and_the_file(client, settings, add_invoice):
    invoice = add_invoice("a.pdf")
    assert list(settings.uploads_dir.iterdir())

    response = client.delete(f"/api/invoices/{invoice['id']}")

    assert response.status_code == 204
    assert client.get(f"/api/invoices/{invoice['id']}").status_code == 404
    assert client.get(f"/api/invoices/{invoice['id']}/file").status_code == 404
    assert client.get("/api/invoices").json()["total"] == 0
    assert list(settings.uploads_dir.iterdir()) == []


def test_deleted_id_is_never_reused(client, add_invoice):
    deleted = add_invoice("old.pdf")["id"]
    client.delete(f"/api/invoices/{deleted}")

    new = add_invoice("new.pdf")["id"]

    assert new != deleted
    assert client.get(f"/api/invoices/{deleted}").status_code == 404


def test_delete_unknown_invoice_returns_404(client):
    assert client.delete("/api/invoices/999").status_code == 404


def test_delete_during_extraction_returns_409(client, settings, extract, add_invoice):
    created = client.post("/api/invoices", files={"file": ("a.pdf", PDF)}).json()
    seen = []
    extract.outcome = ExtractedInvoice(document_type="invoice")
    extract.during_call = lambda: seen.append(client.delete(f"/api/invoices/{created['id']}"))

    response = client.post(f"/api/invoices/{created['id']}/extract")

    assert seen[0].status_code == 409
    assert response.json()["status"] == "extracted"
    assert list(settings.uploads_dir.iterdir())


def test_timestamps_have_an_explicit_utc_offset(client, add_invoice):
    invoice = add_invoice("a.pdf")

    for read in (invoice, client.get(f"/api/invoices/{invoice['id']}").json()):
        for name in ("uploaded_at", "extracted_at"):
            assert datetime.fromisoformat(read[name]).utcoffset() == timedelta(0)


def test_csv_timestamps_have_an_explicit_utc_offset(client, add_invoice):
    add_invoice("a.pdf")

    [row] = csv_rows(client.get("/api/invoices/export.csv"))

    assert datetime.fromisoformat(row["uploaded_at"]).utcoffset() == timedelta(0)
