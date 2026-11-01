"""Adversarial tests by the tester agent. A failing test here is a reported bug, left to re-run."""

import csv
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from io import StringIO

import pytest
import uvicorn
from fastapi.testclient import TestClient
from playwright.sync_api import Error, expect, sync_playwright
from sqlalchemy.sql.dml import Delete, Update
from sqlmodel import Session

from invoice_digitizer.api import create_app
from invoice_digitizer.extraction import ExtractionError, _parse_output, extract_invoice
from invoice_digitizer.models import ExtractedInvoice
from invoice_digitizer.settings import Settings

PDF = b"%PDF-1.7\nsample invoice"
PNG = b"\x89PNG\r\n\x1a\nsample invoice"
TOO_BIG_FOR_SQLITE = 2**70


def upload(client, name="a.pdf", content=PDF):
    response = client.post("/api/invoices", files={"file": (name, content)})
    assert response.status_code == 201, response.text
    return response.json()


def add_extracted(client, extract, name="a.pdf", **fields):
    created = upload(client, name)
    extract.outcome = ExtractedInvoice(document_type="invoice", **fields)
    return client.post(f"/api/invoices/{created['id']}/extract").json()


def names(response):
    return [invoice["filename"] for invoice in response.json()["items"]]


def csv_rows(response):
    return list(csv.DictReader(StringIO(response.content.decode("utf-8-sig"), newline="")))


def raw_upload(client, disposition: bytes, content=PDF):
    boundary = b"testboundary"
    body = (
        b"--" + boundary + b"\r\nContent-Disposition: " + disposition + b"\r\n"
        b"Content-Type: application/octet-stream\r\n\r\n" + content + b"\r\n--" + boundary + b"--\r\n"
    )
    return client.post(
        "/api/invoices",
        content=body,
        headers={"content-type": "multipart/form-data; boundary=testboundary"},
    )


@pytest.fixture
def lenient_client(settings, extract):
    """Answers a server error with a 500, as a real client would see it."""
    return TestClient(create_app(settings, extract), raise_server_exceptions=False)


def run_before_first(monkeypatch, statement_type, action):
    """Run another request just before this one's first write, to force a race."""
    real_exec = Session.exec
    fired = []

    def exec_with_race(self, statement, *args, **kwargs):
        if isinstance(statement, statement_type) and not fired:
            fired.append(True)
            action()
        return real_exec(self, statement, *args, **kwargs)

    monkeypatch.setattr(Session, "exec", exec_with_race)


# --- Requests that overlap or fail part way ---


def test_extract_of_an_invoice_deleted_after_loading_is_not_a_server_error(
    lenient_client, monkeypatch
):
    invoice_id = upload(lenient_client)["id"]
    run_before_first(
        monkeypatch, Update, lambda: lenient_client.delete(f"/api/invoices/{invoice_id}")
    )

    response = lenient_client.post(f"/api/invoices/{invoice_id}/extract")

    assert response.status_code in (404, 409)


def test_second_delete_of_the_same_invoice_is_not_reported_as_being_extracted(
    client, monkeypatch
):
    invoice_id = upload(client)["id"]
    run_before_first(monkeypatch, Delete, lambda: client.delete(f"/api/invoices/{invoice_id}"))

    response = client.delete(f"/api/invoices/{invoice_id}")

    assert response.status_code in (204, 404)


@pytest.mark.parametrize(
    "outcome",
    [
        ExtractedInvoice(document_type="invoice"),
        ExtractionError("Extraction timed out"),
        RuntimeError("boom"),
    ],
    ids=["success", "extraction-error", "unexpected-error"],
)
def test_locked_database_at_the_end_of_a_run_does_not_leave_the_invoice_stuck(
    lenient_client, settings, extract, outcome
):
    """A real write lock from another connection, held past SQLite's five second wait."""
    invoice_id = upload(lenient_client)["id"]
    blocker = sqlite3.connect(settings.database_path, check_same_thread=False)
    extract.outcome = outcome
    extract.during_call = lambda: blocker.execute("BEGIN IMMEDIATE")

    lenient_client.post(f"/api/invoices/{invoice_id}/extract")
    blocker.rollback()
    blocker.close()

    # The invoice must be retryable or deletable now that the database is free again.
    assert lenient_client.get(f"/api/invoices/{invoice_id}").json()["status"] != "extracting"


def test_only_one_of_many_simultaneous_extracts_runs(client, extract):
    invoice_id = upload(client)["id"]
    extract.during_call = lambda: time.sleep(0.2)

    with ThreadPoolExecutor(10) as pool:
        responses = list(
            pool.map(lambda _: client.post(f"/api/invoices/{invoice_id}/extract"), range(10))
        )

    assert len(extract.paths) == 1
    assert sorted(response.status_code for response in responses) == [200] + [409] * 9


def test_simultaneous_uploads_all_succeed(client, settings):
    with ThreadPoolExecutor(16) as pool:
        created = list(pool.map(lambda n: upload(client, f"{n}.pdf"), range(48)))

    assert len({invoice["id"] for invoice in created}) == 48
    assert len(list(settings.uploads_dir.iterdir())) == 48
    assert client.get("/api/invoices").json()["total"] == 48


def test_delete_racing_extract_always_leaves_a_consistent_record(lenient_client, settings):
    for _ in range(25):
        invoice_id = upload(lenient_client)["id"]
        with ThreadPoolExecutor(2) as pool:
            pool.submit(lenient_client.post, f"/api/invoices/{invoice_id}/extract")
            pool.submit(lenient_client.delete, f"/api/invoices/{invoice_id}")

    items = lenient_client.get("/api/invoices", params={"limit": 500}).json()["items"]
    assert all(invoice["status"] != "extracting" for invoice in items)
    assert len(list(settings.uploads_dir.iterdir())) == len(items)


def test_many_running_extractions_do_not_use_up_the_database_connections(client, extract):
    """A running extraction must not hold a connection, or the pool would run out."""
    count = 20
    started = threading.Semaphore(0)
    release = threading.Event()

    def wait():
        started.release()
        release.wait(20)

    extract.during_call = wait
    ids = [upload(client, f"{n}.pdf")["id"] for n in range(count)]

    with ThreadPoolExecutor(count) as pool:
        runs = [pool.submit(client.post, f"/api/invoices/{i}/extract") for i in ids]
        for _ in range(count):
            assert started.acquire(timeout=10)
        began = time.monotonic()
        listed = client.get("/api/invoices", params={"status": "extracting"})
        elapsed = time.monotonic() - began
        release.set()
        results = [run.result(timeout=30) for run in runs]

    assert listed.json()["total"] == count
    assert elapsed < 5
    assert all(result.json()["status"] == "extracted" for result in results)


# --- Odd ids, paging and missing files ---


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", f"/api/invoices/{TOO_BIG_FOR_SQLITE}"),
        ("GET", f"/api/invoices/{TOO_BIG_FOR_SQLITE}/file"),
        ("DELETE", f"/api/invoices/{TOO_BIG_FOR_SQLITE}"),
        ("POST", f"/api/invoices/{TOO_BIG_FOR_SQLITE}/extract"),
    ],
)
def test_id_too_large_for_the_database_is_not_a_server_error(lenient_client, method, path):
    assert lenient_client.request(method, path).status_code in (404, 422)


def test_offset_too_large_for_the_database_is_not_a_server_error(lenient_client):
    response = lenient_client.get("/api/invoices", params={"offset": TOO_BIG_FOR_SQLITE})

    assert response.status_code in (200, 422)


def test_stored_file_that_has_gone_missing_gives_404(lenient_client, settings):
    invoice_id = upload(lenient_client)["id"]
    [stored] = settings.uploads_dir.iterdir()
    stored.unlink()

    assert lenient_client.get(f"/api/invoices/{invoice_id}/file").status_code == 404


def test_missing_stored_file_fails_cleanly_with_real_extraction_and_can_be_deleted(
    settings, tmp_path
):
    settings.claude_command = str(tmp_path / "must-not-run")
    client = TestClient(create_app(settings))
    invoice_id = upload(client)["id"]
    [stored] = settings.uploads_dir.iterdir()
    stored.unlink()

    invoice = client.post(f"/api/invoices/{invoice_id}/extract").json()

    assert invoice["status"] == "failed"
    assert str(settings.data_dir) not in invoice["extraction_error"]
    assert client.delete(f"/api/invoices/{invoice_id}").status_code == 204


def test_restart_leaves_uploaded_extracted_and_failed_invoices_alone(client, settings, extract):
    uploaded = upload(client, "uploaded.pdf")
    extracted = add_extracted(client, extract, "extracted.pdf", invoice_number="1")
    failed = upload(client, "failed.pdf")
    extract.outcome = ExtractionError("Extraction timed out")
    failed = client.post(f"/api/invoices/{failed['id']}/extract").json()

    restarted = TestClient(create_app(settings, extract))

    for before in (uploaded, extracted, failed):
        assert restarted.get(f"/api/invoices/{before['id']}").json() == before


# --- Upload: odd bodies and filenames ---


@pytest.mark.parametrize(
    ("headers", "body"),
    [
        ({"content-type": "multipart/form-data"}, b"no boundary"),
        ({"content-type": "multipart/form-data; boundary=x"}, b"--x\r\nbroken"),
        ({"content-type": "application/json"}, b"{}"),
        ({"content-type": "text/plain"}, PDF),
    ],
)
def test_malformed_upload_is_a_client_error(lenient_client, settings, headers, body):
    response = lenient_client.post("/api/invoices", content=body, headers=headers)

    assert 400 <= response.status_code < 500
    assert list(settings.uploads_dir.iterdir()) == []


@pytest.mark.parametrize(
    "name",
    [
        "semi;colon=1.pdf",
        "пример-фактура.pdf",
        "emoji-😀.pdf",
        "percent%20%0d%0a.pdf",
        "<script>alert(1)<script>.pdf",
        "x" * 250 + ".pdf",
    ],
)
def test_odd_filename_survives_list_csv_and_download(client, name):
    invoice_id = upload(client, name)["id"]

    assert names(client.get("/api/invoices")) == [name]
    assert csv_rows(client.get("/api/invoices/export.csv"))[0]["filename"] == name
    download = client.get(f"/api/invoices/{invoice_id}/file")
    assert download.status_code == 200
    assert "\r" not in download.headers["content-disposition"]
    assert "\n" not in download.headers["content-disposition"]


@pytest.mark.parametrize(
    "filename",
    [b"bad\xffbyte.pdf", b"nul\x00byte.pdf", b"a\\b\\c.pdf", b"", b"./", b"foo/.."],
    ids=["invalid-utf8", "nul", "backslashes", "empty", "dot-slash", "parent"],
)
def test_raw_filename_bytes_do_not_break_later_requests(client, settings, filename):
    response = raw_upload(client, b'form-data; name="file"; filename="' + filename + b'"')

    assert response.status_code in (201, 422)
    if response.status_code == 201:
        invoice_id = response.json()["id"]
        assert client.get(f"/api/invoices/{invoice_id}/file").content == PDF
        assert client.get("/api/invoices/export.csv").status_code == 200
        assert [path.parent for path in settings.uploads_dir.iterdir()] == [settings.uploads_dir]


def test_filename_too_long_for_the_multipart_parser_is_a_clean_client_error(client, settings):
    response = client.post("/api/invoices", files={"file": ("x" * 5000 + ".pdf", PDF)})

    assert response.status_code == 400
    assert list(settings.uploads_dir.iterdir()) == []


def test_api_and_csv_do_not_reveal_the_stored_name(client, settings):
    upload(client)
    [stored] = settings.uploads_dir.iterdir()

    assert stored.stem not in client.get("/api/invoices").text
    assert stored.stem not in client.get("/api/invoices/export.csv").text


@pytest.mark.parametrize(
    "path",
    [
        "/..%2f..%2fapi.py",
        "/%2e%2e%2fapi.py",
        "/..%5capi.py",
        "/static/../api.py",
        "/data/invoices.db",
        "/invoices.db",
        "/uploads/",
    ],
)
def test_nothing_outside_the_static_folder_is_served(client, path):
    response = client.get(path)

    assert response.status_code in (404, 405)


# --- Extraction: what the model or the command can send back ---


@pytest.mark.parametrize(
    "amount",
    ["NaN", "sNaN", "Infinity", "-Infinity", "1,234.50", "", "abc", "12 EUR"],
)
def test_amount_that_is_not_a_number_is_rejected(amount):
    output = json.dumps(
        {"is_error": False, "structured_output": {"document_type": "invoice", "total": amount}}
    )

    with pytest.raises(ExtractionError):
        _parse_output(output)


def test_amount_sent_as_a_json_number_is_not_silently_rounded():
    """models.py asks for strings because floats round. An answer that ignores this must not pass."""
    output = (
        '{"is_error": false, "structured_output": '
        '{"document_type": "invoice", "total": 99999999999999.99}}'
    )

    try:
        total = _parse_output(output).total
    except ExtractionError:
        return
    assert str(total) == "99999999999999.99"


@pytest.mark.parametrize("value", [0, 86400, "86400"])
def test_number_sent_as_a_date_is_not_stored_as_a_1970_date(value):
    output = json.dumps(
        {
            "is_error": False,
            "structured_output": {"document_type": "invoice", "invoice_date": value},
        }
    )

    with pytest.raises(ExtractionError):
        _parse_output(output)


@pytest.mark.parametrize(
    "fields",
    [
        {"document_type": "invoice", "line_items": None},
        {"document_type": "invoice", "line_items": [{"amount": "1", "extra": "x"}]},
        {"document_type": "invoice", "currency": "EUR\n"},
        {"document_type": "invoice", "invoice_date": "2026-02-30"},
        {"document_type": "receipt"},
        "a string",
        ["a list"],
        7,
    ],
)
def test_malformed_structured_output_is_a_safe_error(fields):
    with pytest.raises(ExtractionError):
        _parse_output(json.dumps({"is_error": False, "structured_output": fields}))


@pytest.mark.parametrize(
    "fields",
    [
        {"supplier_name": "half an emoji \ud83d"},
        {"line_items": [{"description": "half an emoji \ud83d"}]},
    ],
    ids=["header-field", "line-item"],
)
def test_broken_unicode_in_an_answer_does_not_break_later_requests(
    lenient_client, extract, fields
):
    """JSON can carry half of a surrogate pair, and json.loads and the model both accept it."""
    good = upload(lenient_client, "good.pdf")["id"]
    bad = upload(lenient_client, "bad.pdf")["id"]
    extract.outcome = _parse_output(
        json.dumps({"is_error": False, "structured_output": {"document_type": "invoice", **fields}})
    )

    extract_answer = lenient_client.post(f"/api/invoices/{bad}/extract")

    answers = {
        "extract": extract_answer.status_code,
        "list": lenient_client.get("/api/invoices").status_code,
        "good invoice": lenient_client.get(f"/api/invoices/{good}").status_code,
        "bad invoice": lenient_client.get(f"/api/invoices/{bad}").status_code,
        "csv": lenient_client.get("/api/invoices/export.csv").status_code,
    }
    assert answers == dict.fromkeys(answers, 200)


def test_decimals_survive_storage_exactly(client, extract):
    invoice = add_extracted(
        client,
        extract,
        total="123456789012345678901234567890.123456789",
        tax_amount="0.10",
        line_items=[{"quantity": "0.000001", "amount": "-0.00"}],
    )

    read = client.get(f"/api/invoices/{invoice['id']}").json()
    assert read["total"] == "123456789012345678901234567890.123456789"
    assert read["tax_amount"] == "0.10"
    assert read["line_items"][0]["quantity"] == "0.000001"
    assert read["line_items"][0]["amount"] == "-0.00"
    [row] = csv_rows(client.get("/api/invoices/export.csv"))
    assert row["total"] == "123456789012345678901234567890.123456789"


def make_fake_claude(tmp_path, body):
    command = tmp_path / "fake-claude"
    command.write_text(f"#!{sys.executable}\nimport os, sys, time, pathlib, subprocess\n{body}\n")
    command.chmod(0o755)
    return command


def stored_pdf(tmp_path):
    path = tmp_path / "stored.pdf"
    path.write_bytes(PDF)
    return path


def fake_settings(tmp_path, command, timeout=30):
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        claude_command=str(command),
        extraction_timeout_seconds=timeout,
    )


def test_output_that_is_not_utf8_marks_the_invoice_failed(tmp_path):
    command = make_fake_claude(tmp_path, r"sys.stdout.buffer.write(b'\xff\xfe not text')")
    client = TestClient(create_app(fake_settings(tmp_path, command)))
    invoice_id = upload(client)["id"]

    invoice = client.post(f"/api/invoices/{invoice_id}/extract").json()

    assert invoice["status"] == "failed"
    assert invoice["extraction_error"]


def test_command_that_is_not_executable_marks_the_invoice_failed(tmp_path):
    command = tmp_path / "not-executable"
    command.write_text("plain file")
    client = TestClient(create_app(fake_settings(tmp_path, command)))
    invoice_id = upload(client)["id"]

    invoice = client.post(f"/api/invoices/{invoice_id}/extract").json()

    assert invoice["status"] == "failed"
    assert str(tmp_path) not in invoice["extraction_error"]


def test_failing_command_output_is_not_shown_to_the_user(tmp_path):
    command = make_fake_claude(tmp_path, "print('secret-token-123'); sys.exit(3)")
    client = TestClient(create_app(fake_settings(tmp_path, command)))
    invoice_id = upload(client)["id"]

    invoice = client.post(f"/api/invoices/{invoice_id}/extract").json()

    assert invoice["status"] == "failed"
    assert "secret-token-123" not in invoice["extraction_error"]


def test_very_large_output_does_not_hang_the_run(tmp_path):
    command = make_fake_claude(tmp_path, "sys.stdout.write('x' * 20_000_000)")

    with pytest.raises(ExtractionError):
        extract_invoice(stored_pdf(tmp_path), fake_settings(tmp_path, command))


def test_timeout_holds_even_when_a_child_process_keeps_the_output_open(tmp_path):
    command = make_fake_claude(tmp_path, "subprocess.Popen(['sleep', '5']); time.sleep(5)")
    began = time.monotonic()

    with pytest.raises(ExtractionError, match="timed out"):
        extract_invoice(stored_pdf(tmp_path), fake_settings(tmp_path, command, timeout=0.5))

    assert time.monotonic() - began < 3


@pytest.mark.parametrize(
    ("body", "timeout"),
    [
        ("pathlib.Path(os.environ['SEEN_CWD']).write_text(os.getcwd()); sys.exit(1)", 30),
        ("pathlib.Path(os.environ['SEEN_CWD']).write_text(os.getcwd()); time.sleep(5)", 0.5),
        (
            "pathlib.Path(os.environ['SEEN_CWD']).write_text(os.getcwd());"
            "pathlib.Path('left-behind.txt').write_text('x'); print('{}')",
            30,
        ),
    ],
    ids=["exit-1", "timeout", "extra-file"],
)
def test_temporary_copy_is_removed_however_the_run_ends(tmp_path, monkeypatch, body, timeout):
    seen = tmp_path / "seen-cwd"
    monkeypatch.setenv("SEEN_CWD", str(seen))
    command = make_fake_claude(tmp_path, body)

    with pytest.raises(ExtractionError):
        extract_invoice(stored_pdf(tmp_path), fake_settings(tmp_path, command, timeout))

    assert not os.path.exists(seen.read_text())


def test_names_stay_correct_when_the_server_runs_under_a_non_utf8_locale(tmp_path):
    """The claude command writes UTF-8 whatever the locale, as this fake command does."""
    answer = json.dumps(
        {"is_error": False, "structured_output": {"document_type": "invoice", "supplier_name": "Müller"}},
        ensure_ascii=False,
    )
    command = make_fake_claude(tmp_path, f"sys.stdout.buffer.write({answer.encode('utf-8')!r})")
    script = tmp_path / "run_extraction.py"
    script.write_text(
        "import locale, pathlib, sys\n"
        "from invoice_digitizer.extraction import extract_invoice\n"
        "from invoice_digitizer.settings import Settings\n"
        "settings = Settings(_env_file=None, claude_command=sys.argv[1])\n"
        "name = extract_invoice(pathlib.Path(sys.argv[2]), settings).supplier_name\n"
        "print(locale.getencoding(), ascii(name))\n"
    )
    environment = {
        **{k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "LANG", "LC_CTYPE")},
        "LC_ALL": "en_US.ISO8859-1",
    }

    result = subprocess.run(
        [sys.executable, str(script), str(command), str(stored_pdf(tmp_path))],
        env=environment,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    encoding, name = result.stdout.split()
    if encoding.lower().replace("-", "") == "utf8":
        pytest.skip("This machine has no ISO8859-1 locale")
    assert name == ascii("Müller")


# --- Records: search ---


@pytest.mark.parametrize(
    ("stored", "search"),
    [
        ("2026/001", "2026/0"),
        ("O\\Brien", "o\\b"),
        ("A/B%C_D", "/b%c_"),
        ("Straße", "STRASSE"),
        ("it's; DROP TABLE invoice;--", "drop table"),
    ],
)
def test_search_finds_awkward_text_literally(client, extract, stored, search):
    add_extracted(client, extract, "match.pdf", invoice_number=stored)
    add_extracted(client, extract, "other.pdf", invoice_number="zzz")

    assert names(client.get("/api/invoices", params={"q": search})) == ["match.pdf"]
    assert client.get("/api/invoices", params={"status": "extracted"}).json()["total"] == 2


def test_search_matches_a_name_stored_in_decomposed_unicode(client):
    """macOS can hand over file names in decomposed form, and people type the composed form."""
    upload(client, unicodedata.normalize("NFD", "Müller.pdf"))

    found = client.get("/api/invoices", params={"q": unicodedata.normalize("NFC", "Müller")})

    assert found.json()["total"] == 1


def test_search_for_a_nul_character_does_not_crash(client):
    upload(client, "a.pdf")

    assert client.get("/api/invoices", params={"q": "\x00"}).status_code == 200


def test_filtered_list_past_the_end_reports_the_real_total(client):
    upload(client)

    body = client.get("/api/invoices", params={"offset": 50}).json()

    assert body == {"items": [], "total": 1}


# --- CSV ---


def test_csv_round_trips_separators_quotes_newlines_and_nul(client, extract):
    text = 'A, "B"\r\nC;\tD\x00E 😀'
    add_extracted(client, extract, supplier_name=text, buyer_name="plain")

    [row] = csv_rows(client.get("/api/invoices/export.csv"))

    assert row["supplier_name"] == text
    assert row["buyer_name"] == "plain"


def test_csv_lists_records_of_every_status(client, extract):
    upload(client, "uploaded.pdf")
    add_extracted(client, extract, "extracted.pdf", total="1.50")
    failed = upload(client, "failed.pdf")
    extract.outcome = ExtractionError("Extraction timed out")
    client.post(f"/api/invoices/{failed['id']}/extract")

    rows = {row["filename"]: row for row in csv_rows(client.get("/api/invoices/export.csv"))}

    assert rows["uploaded.pdf"]["status"] == "uploaded"
    assert rows["uploaded.pdf"]["total"] == ""
    assert rows["extracted.pdf"]["total"] == "1.50"
    assert rows["failed.pdf"]["status"] == "failed"


def test_csv_of_an_empty_filter_is_a_header_only(client):
    upload(client)

    response = client.get("/api/invoices/export.csv", params={"q": "no such thing"})

    assert response.status_code == 200
    assert csv_rows(response) == []
    assert response.content.decode("utf-8-sig").startswith("id,filename,status")


def test_csv_includes_more_records_than_one_page_of_the_list(client):
    for number in range(30):
        upload(client, f"{number}.pdf")

    assert len(csv_rows(client.get("/api/invoices/export.csv"))) == 30


# --- Web page ---


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
    yield page
    context.close()


@pytest.fixture
def live_url(tmp_path, extract):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path / "browser-data"), extract)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="critical", lifespan="off"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server.started
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            thread.join(timeout=8)


def api_upload(page, url, name="a.pdf", content=PDF):
    response = page.request.post(
        f"{url}/api/invoices",
        multipart={"file": {"name": name, "mimeType": "application/pdf", "buffer": content}},
    )
    assert response.status == 201
    return response.json()["id"]


def test_amount_with_a_huge_exponent_does_not_blank_the_whole_list(page, live_url, extract):
    """One bad record must not take every other invoice off the page."""
    api_upload(page, live_url, "good.pdf")
    bad = api_upload(page, live_url, "huge.pdf")
    extract.outcome = ExtractedInvoice(document_type="invoice", total="1E+999999999")
    assert page.request.post(f"{live_url}/api/invoices/{bad}/extract").json()["status"] == "extracted"

    page.goto(live_url)

    expect(page.get_by_role("button", name="good.pdf", exact=True)).to_be_visible()


def test_list_that_can_not_be_drawn_keeps_showing_an_error(page, live_url, extract):
    """Whatever the cause, a blank list with no message must not look like a healthy page."""
    bad = api_upload(page, live_url, "huge.pdf")
    extract.outcome = ExtractedInvoice(document_type="invoice", total="1E+999999999")
    assert page.request.post(f"{live_url}/api/invoices/{bad}/extract").ok

    page.goto(live_url)
    page.wait_for_timeout(5000)

    assert page.locator("#list-error").is_visible() or page.locator("#invoice-rows tr").count() > 0


def test_empty_state_says_no_match_when_a_filter_finds_nothing_on_an_empty_database(page, live_url):
    page.goto(live_url)
    expect(page.locator("#empty-state")).to_contain_text("No invoices yet")

    page.get_by_label("Search invoices").fill("zzz")

    expect(page.locator("#empty-state")).to_have_text("No invoices match these filters.")


def test_empty_state_returns_when_the_filter_is_cleared_after_deleting_the_last_invoice(
    page, live_url
):
    api_upload(page, live_url, "only.pdf")
    page.goto(live_url)
    page.get_by_label("Search invoices").fill("only")
    page.get_by_role("button", name="only.pdf", exact=True).click()
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="Delete invoice").click()
    expect(page.locator("#empty-state")).to_have_text("No invoices match these filters.")

    page.get_by_label("Search invoices").fill("")

    expect(page.locator("#empty-state")).to_contain_text("No invoices yet")


def test_detail_error_clears_once_the_server_answers_again(page, live_url):
    invoice_id = api_upload(page, live_url)
    page.goto(live_url)
    page.get_by_role("button", name="a.pdf", exact=True).click()
    expect(page.locator("#detail-status")).to_have_text("Uploaded")
    pattern = f"**/api/invoices/{invoice_id}"
    page.route(pattern, lambda route: route.fulfill(status=503, body="{}"))
    expect(page.locator("#detail-error")).to_be_visible()

    page.unroute(pattern)

    expect(page.locator("#detail-error")).to_be_hidden()


def test_list_error_clears_once_the_server_answers_again(page, live_url):
    page.goto(live_url)
    expect(page.locator("#empty-state")).to_contain_text("No invoices yet")
    pattern = "**/api/invoices?*"
    page.route(pattern, lambda route: route.fulfill(status=503, body="{}"))
    expect(page.locator("#list-error")).to_be_visible()

    page.unroute(pattern)

    expect(page.locator("#list-error")).to_be_hidden()


def test_slow_answer_for_an_old_search_does_not_replace_the_new_one(page, live_url):
    api_upload(page, live_url, "slow.pdf")
    api_upload(page, live_url, "fast.pdf")
    page.goto(live_url)
    expect(page.locator("#invoice-rows tr")).to_have_count(2)

    def delay_slow_search(route):
        time.sleep(1.5)
        route.continue_()

    page.route("**/api/invoices?q=slow*", delay_slow_search)
    page.get_by_label("Search invoices").fill("slow")
    page.wait_for_timeout(400)
    page.get_by_label("Search invoices").fill("fast")

    expect(page.get_by_role("button", name="fast.pdf", exact=True)).to_be_visible()
    page.wait_for_timeout(2000)
    expect(page.locator("#invoice-rows tr")).to_have_count(1)
    expect(page.get_by_role("button", name="fast.pdf", exact=True)).to_be_visible()


def test_invoice_deleted_in_another_tab_closes_the_open_detail(page, live_url):
    invoice_id = api_upload(page, live_url)
    page.goto(live_url)
    page.get_by_role("button", name="a.pdf", exact=True).click()
    expect(page.locator("#detail")).to_be_visible()

    assert page.request.delete(f"{live_url}/api/invoices/{invoice_id}").status == 204

    expect(page.locator("#detail")).to_be_hidden()
    expect(page.locator("#invoice-rows tr")).to_have_count(0)


def test_queue_carries_on_after_a_server_error_on_one_upload(page, live_url):
    page.goto(live_url)
    answers = []

    def fail_first_upload(route):
        if route.request.method == "POST" and not answers:
            answers.append(True)
            route.fulfill(status=500, body="not json")
        else:
            route.continue_()

    page.route("**/api/invoices", fail_first_upload)
    page.locator("#file-input").set_input_files(
        [
            {"name": "first.pdf", "mimeType": "application/pdf", "buffer": PDF},
            {"name": "second.pdf", "mimeType": "application/pdf", "buffer": PDF},
        ]
    )

    expect(page.locator("#queue-status")).to_contain_text("Queue complete")
    expect(page.locator("#upload-log")).to_contain_text("first.pdf — Request failed (500)")
    expect(page.get_by_role("button", name="second.pdf", exact=True)).to_be_visible()


def test_untrusted_text_in_every_shown_field_stays_text(page, live_url, extract):
    payload = "<img src=x onerror=\"window.hacked=1\">"
    invoice_id = api_upload(page, live_url, f"{payload}.pdf")
    extract.outcome = ExtractedInvoice(
        document_type="invoice",
        invoice_number=payload,
        supplier_name=payload,
        supplier_tax_id=payload,
        buyer_name=payload,
        buyer_tax_id=payload,
        line_items=[{"description": payload}],
    )
    assert page.request.post(f"{live_url}/api/invoices/{invoice_id}/extract").ok

    page.goto(live_url)
    page.get_by_role("button", name=f"{payload}.pdf", exact=True).click()
    expect(page.locator("#fields")).to_contain_text(payload)
    page.get_by_label("Search invoices").fill(payload)
    expect(page.locator("#invoice-rows tr")).to_have_count(1)

    assert page.evaluate("window.hacked") is None
    assert page.locator("main img:not(#preview img)").count() == 0


def test_html_disguised_as_a_png_is_not_run_when_opened_directly(page, live_url):
    invoice_id = api_upload(
        page, live_url, "polyglot.png", PNG + b"<script>window.hacked=1</script>"
    )

    response = page.goto(f"{live_url}/api/invoices/{invoice_id}/file")

    assert response.headers["content-type"] == "image/png"
    assert page.evaluate("window.hacked") is None


def test_search_text_is_encoded_in_the_export_link(page, live_url):
    page.goto(live_url)

    page.get_by_label("Search invoices").fill("a&status=failed#x")

    href = page.locator("#export-link").get_attribute("href")
    assert href == "/api/invoices/export.csv?q=a%26status%3Dfailed%23x"
