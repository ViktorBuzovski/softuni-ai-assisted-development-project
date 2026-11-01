"""Real Chrome + uvicorn; all extraction is fake, with no Claude calls.

Run with: uv run pytest tests/test_e2e.py -rs
Chrome launch failures are skipped with the browser's reason in the report.
"""

import base64
import re
import socket
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request, urlopen

import pytest
import uvicorn
from playwright.sync_api import Error, expect, sync_playwright

from invoice_digitizer.api import create_app
from invoice_digitizer.extraction import ExtractionError
from invoice_digitizer.models import ExtractedInvoice
from invoice_digitizer.settings import Settings

PDF = b"%PDF-1.7\nsample invoice"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aJ1sAAAAASUVORK5CYII="
)
HTML_NAME = '<img src=x onerror=alert(1)>.pdf'


class BrowserExtract:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = Counter()

    def __call__(self, path):
        self.calls[path.name] += 1
        self.started.set()
        if not self.release.wait(15):
            raise ExtractionError("Test did not release the extractor")
        content = path.read_bytes()
        if b"retry" in content and self.calls[path.name] == 1:
            raise ExtractionError("Could not read this invoice. Try again.")
        credit = b"credit" in content
        return ExtractedInvoice(
            document_type="credit_note" if credit else "invoice",
            supplier_name="<b>Acme</b>" if credit else "Müller Studio",
            buyer_name="Example Buyer",
            invoice_number="CN-42" if credit else "INV-42",
            invoice_date="2026-03-15",
            currency="EUR",
            subtotal="1250",
            total="-99999999999999.99" if credit else "1430.70",
            line_items=[{
                "description": "<script>alert(1)</script>",
                "quantity": "2",
                "unit_price": "0.1234500",
                "amount": None,
            }],
        )


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
        except Error as error:
            pytest.skip(f"Chrome could not start in this environment: {error}")
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    page.set_default_timeout(8000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    yield page
    context.close()
    assert errors == []


@pytest.fixture
def live_server(tmp_path):
    extract = BrowserExtract()
    app = create_app(Settings(_env_file=None, data_dir=tmp_path / "data"), extract)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server.started, "The test uvicorn server did not start"
            yield f"http://127.0.0.1:{port}", extract
        finally:
            extract.release.set()
            server.should_exit = True
            thread.join(timeout=8)
            assert not thread.is_alive(), "The test uvicorn server did not stop"


def upload_file(name, content=PDF, mime="application/pdf"):
    return {"name": name, "mimeType": mime, "buffer": content}


def invoice_row(page, filename):
    return page.locator("#invoice-rows tr").filter(has=page.get_by_role("button", name=filename, exact=True))


def field(page, name):
    term = page.get_by_role("term").filter(has_text=re.compile(f"^{re.escape(name)}$"))
    return page.locator("#fields > div").filter(has=term).locator("dd")


def test_upload_search_detail_retry_delete_and_untrusted_text(page, live_server):
    url, extract = live_server
    page.goto(url)
    page.locator("#file-input").set_input_files([
        upload_file("rejected.txt", b"not an invoice", "text/plain"),
        upload_file(HTML_NAME, PDF + b" credit"),
        upload_file("retry.pdf", PDF + b" retry"),
    ])
    expect(page.locator("#upload-log")).to_contain_text("Only PDF, PNG and JPEG files are accepted")
    expect(invoice_row(page, HTML_NAME)).to_contain_text("Reading…")
    assert sum(extract.calls.values()) == 1
    expect(invoice_row(page, "retry.pdf")).to_have_count(0)
    extract.release.set()
    expect(invoice_row(page, HTML_NAME)).to_contain_text("Extracted")
    expect(invoice_row(page, "retry.pdf")).to_contain_text("Failed")
    expect(page.locator("#queue-status")).to_contain_text("Queue complete")
    expect(page.locator("#invoice-rows tr").first).to_contain_text("retry.pdf")
    expect(page.locator("#invoice-rows img, #upload-log img")).to_have_count(0)

    # Use the keyboard to select a row and inspect exact, missing and negative amounts.
    page.get_by_role("button", name=HTML_NAME, exact=True).focus()
    page.keyboard.press("Enter")
    expect(page.locator("#detail-title")).to_have_text(HTML_NAME)
    expect(page.locator("#detail-title")).to_be_focused()
    expect(page.locator("#preview iframe")).to_have_attribute("src", "/api/invoices/1/file")
    expect(field(page, "Document type")).to_have_text("Credit note")
    expect(field(page, "Subtotal")).to_have_text("1,250.00")
    expect(field(page, "Total")).to_have_text("-99,999,999,999,999.99 EUR")
    expect(field(page, "Due date")).to_have_text("—")
    expect(page.locator("#line-items")).to_contain_text("0.1234500")
    expect(page.locator("#line-items td.numeric").first).to_have_text("2")
    expect(page.locator("#line-items td").last).to_have_text("—")
    expect(page.locator("#fields b, #detail-title img, #line-items script")).to_have_count(0)
    expect(page.locator("#line-items")).to_contain_text("<script>alert(1)</script>")
    page.get_by_role("button", name="Close details").click()
    expect(page.get_by_role("button", name=HTML_NAME, exact=True)).to_be_focused()

    page.get_by_label("Search invoices").fill("acme")
    page.get_by_label("Status", exact=True).select_option("extracted")
    expect(page.locator("#invoice-rows tr")).to_have_count(1)
    expect(page.locator("#export-link")).to_have_attribute("href", "/api/invoices/export.csv?q=acme&status=extracted")
    exported = page.request.get(url + page.locator("#export-link").get_attribute("href"))
    assert "CN-42" in exported.text() and "retry.pdf" not in exported.text()
    page.get_by_label("Search invoices").fill("no match")
    expect(page.locator("#invoice-rows tr")).to_have_count(0)
    expect(page.locator("#empty-state")).to_have_text("No invoices match these filters.")
    page.get_by_label("Search invoices").fill("")
    page.get_by_label("Status", exact=True).select_option("failed")
    page.get_by_role("button", name="retry.pdf", exact=True).click()
    expect(page.locator("#extraction-error")).to_have_text("Could not read this invoice. Try again.")
    extract.release.clear()
    page.get_by_role("button", name="Retry", exact=True).click()
    expect(page.locator("#detail-status")).to_have_text("Reading…")
    expect(page.get_by_role("button", name="Delete invoice")).to_be_hidden()
    extract.release.set()
    expect(page.locator("#detail-status")).to_have_text("Extracted")
    expect(page.locator("#extraction-error")).to_be_hidden()
    expect(page.locator("#invoice-rows tr")).to_have_count(0)
    page.get_by_label("Status", exact=True).select_option("")
    expect(invoice_row(page, "retry.pdf")).to_contain_text("Extracted")
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.get_by_role("button", name="Delete invoice").click()
    expect(page.locator("#detail")).to_be_visible()
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="Delete invoice").click()
    expect(page.locator("#detail")).to_be_hidden()
    expect(invoice_row(page, "retry.pdf")).to_have_count(0)
    expect(page.locator("#invoices")).to_be_focused()
    assert page.request.get(f"{url}/api/invoices/2").status == 404


def test_already_running_invoice_updates_after_opening_page(page, live_server):
    url, extract = live_server
    response = page.request.post(f"{url}/api/invoices", multipart={"file": upload_file("already-running.png", PNG, "image/png")})
    invoice_id = response.json()["id"]

    def start_extraction():
        with urlopen(Request(f"{url}/api/invoices/{invoice_id}/extract", method="POST"), timeout=20) as response:
            return response.status

    with ThreadPoolExecutor() as pool:
        running = pool.submit(start_extraction)
        try:
            assert extract.started.wait(5)
            page.goto(url)
            expect(invoice_row(page, "already-running.png")).to_contain_text("Reading…")
            page.get_by_role("button", name="already-running.png", exact=True).click()
            expect(page.locator("#preview img")).to_have_attribute("src", f"/api/invoices/{invoice_id}/file")
            expect(page.locator("#detail-status")).to_have_text("Reading…")
            expect(page.get_by_role("button", name="Delete invoice")).to_be_hidden()
            page.get_by_label("Status", exact=True).select_option("extracted")
            expect(page.locator("#invoice-rows tr")).to_have_count(0)
            extract.release.set()
            assert running.result(timeout=5) == 200
            expect(invoice_row(page, "already-running.png")).to_contain_text("Extracted")
            expect(page.locator("#detail-status")).to_have_text("Extracted")
            expect(field(page, "Total")).to_have_text("1,430.70 EUR")
        finally:
            extract.release.set()


def test_drop_upload_mobile_layout_and_number_precision(page, live_server):
    url, extract = live_server
    extract.release.set()
    page.set_viewport_size({"width": 375, "height": 812})
    page.goto(url)
    page.locator("#drop-area").evaluate("""element => {
        const transfer = new DataTransfer();
        transfer.items.add(new File(['%PDF-1.7\\ncredit'], 'dropped.pdf', {type: 'application/pdf'}));
        element.dispatchEvent(new DragEvent('drop', {bubbles: true, dataTransfer: transfer}));
    }""")
    expect(invoice_row(page, "dropped.pdf")).to_contain_text("Extracted")
    page.get_by_role("button", name="dropped.pdf", exact=True).click()
    expect(page.locator("#detail")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    original = page.locator(".original").bounding_box()
    details = page.locator(".extracted-panel").bounding_box()
    assert details["y"] >= original["y"] + original["height"]
    assert page.evaluate("""() => [null, '0', '1250', '-1430.70', '1.23450',
        '999999999999999999.99', '1E-7', '-1.20E+3'].map(formatAmount)""") == [
        "—", "0.00", "1,250.00", "-1,430.70", "1.23450",
        "999,999,999,999,999,999.99", "0.0000001", "-1,200.00",
    ]


def test_status_badges_are_compact_and_the_phone_layout_shows_total_and_status(page, live_server):
    url, extract = live_server
    extract.release.set()
    page.goto(url)
    page.locator("#file-input").set_input_files([upload_file("a-long-invoice-file-name.pdf")])
    row = invoice_row(page, "a-long-invoice-file-name.pdf")
    expect(row).to_contain_text("Extracted")
    assert row.locator(".status").bounding_box()["height"] < 32

    page.set_viewport_size({"width": 375, "height": 812})
    expect(row.locator("td").nth(1)).to_be_hidden()
    expect(row.locator("td").nth(4)).to_be_visible()
    expect(row.locator(".status")).to_be_visible()
    assert page.locator(".table-scroll").first.evaluate("e => e.scrollWidth <= e.clientWidth")
    assert row.locator(".status").bounding_box()["x"] + row.locator(".status").bounding_box()["width"] <= 375


def test_uploaded_invoice_can_be_read_from_the_detail_view(page, live_server):
    url, extract = live_server
    extract.release.set()
    response = page.request.post(f"{url}/api/invoices", multipart={"file": upload_file("left-behind.pdf")})
    assert response.status == 201
    page.goto(url)
    page.get_by_role("button", name="left-behind.pdf", exact=True).click()
    expect(page.locator("#detail-status")).to_have_text("Uploaded")

    page.get_by_role("button", name="Read invoice", exact=True).click()

    expect(page.locator("#detail-status")).to_have_text("Extracted")
    expect(field(page, "Total")).to_have_text("1,430.70 EUR")
    expect(page.get_by_role("button", name="Read invoice", exact=True)).to_be_hidden()


def test_paging_and_deleting_the_last_result_on_a_page(page, live_server):
    url, _ = live_server
    for number in range(26):
        response = page.request.post(
            f"{url}/api/invoices", multipart={"file": upload_file(f"invoice-{number:02}.pdf")}
        )
        assert response.status == 201
    page.goto(url)
    expect(page.locator("#invoice-rows tr")).to_have_count(25)
    expect(page.locator("#invoice-rows tr").first).to_contain_text("invoice-25.pdf")
    expect(page.get_by_role("button", name="Previous", exact=True)).to_be_disabled()
    page.get_by_role("button", name="Next", exact=True).click()
    expect(page.locator("#invoice-rows tr")).to_have_count(1)
    expect(page.locator("#result-count")).to_have_text("26–26 of 26 invoices")
    expect(page.get_by_role("button", name="Next", exact=True)).to_be_disabled()
    page.get_by_role("button", name="invoice-00.pdf", exact=True).click()
    expect(page.locator("#detail-status")).to_have_text("Uploaded")
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="Delete invoice").click()
    expect(page.locator("#result-count")).to_have_text("1–25 of 25 invoices")
    page.get_by_label("Search invoices").fill("invoice-12")
    expect(page.locator("#invoice-rows tr")).to_have_count(1)
    expect(page.locator("#result-count")).to_have_text("1–1 of 1 invoice")
