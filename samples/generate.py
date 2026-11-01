"""Render the fictional invoices in samples/sources into the files the live tests read."""

from pathlib import Path

from playwright.sync_api import sync_playwright

SAMPLES = Path(__file__).parent
FILES = {
    "standard": "standard.pdf",
    "decimal-comma": "decimal-comma.png",
    "credit-note": "credit-note.jpg",
    "prompt-injection": "prompt-injection.pdf",
    "not-an-invoice": "not-an-invoice.png",
}


def render(page, source: str, output: Path) -> None:
    page.goto((SAMPLES / "sources" / f"{source}.html").as_uri())
    if output.suffix == ".pdf":
        page.pdf(path=output, format="A4", print_background=True)
    elif output.suffix == ".jpg":
        page.screenshot(path=output, full_page=True, type="jpeg", quality=70)
    else:
        page.screenshot(path=output, full_page=True)


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome")
        page = browser.new_page(viewport={"width": 900, "height": 800})
        for source, name in FILES.items():
            render(page, source, SAMPLES / name)
        browser.close()


if __name__ == "__main__":
    main()
