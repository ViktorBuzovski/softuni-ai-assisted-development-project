import pytest


def test_page_is_served(client):
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert '<html lang="en">' in response.text
    assert 'src="/app.js"' in response.text
    assert 'href="/style.css"' in response.text
    assert 'id="file-input"' in response.text
    assert 'id="invoice-rows"' in response.text


@pytest.mark.parametrize(
    ("path", "content_type", "content"),
    [("/app.js", "javascript", "textContent"), ("/style.css", "text/css", ":focus-visible")],
)
def test_static_assets_are_served(client, path, content_type, content):
    response = client.get(path)

    assert response.status_code == 200
    assert content_type in response.headers["content-type"]
    assert content in response.text


def test_static_mount_does_not_shadow_api(client):
    assert client.get("/api/invoices").json() == {"items": [], "total": 0}
    assert client.get("/api/invoices/export.csv").headers["content-type"].startswith("text/csv")
    assert client.get("/api/invoices/999").json() == {"detail": "Invoice not found"}
    assert client.post("/api/invoices").status_code == 422


@pytest.mark.parametrize("path", ["/missing.js", "/api.py", "/extraction_rules.md", "/%2e%2e/api.py"])
def test_only_static_directory_is_exposed(client, path):
    assert client.get(path).status_code == 404
