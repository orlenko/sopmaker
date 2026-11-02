"""Opt-in browser test against invented local data; never invokes a model or credential store."""

import argparse
import tempfile
import threading
import zipfile
from pathlib import Path

from PIL import Image
from playwright.sync_api import expect, sync_playwright

from sopmaker.credentials import Credentials
from sopmaker.demo import seed_demo
from sopmaker.server import ReviewServer
from sopmaker.store import Store
from sopmaker.worker import Engine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="sopmaker-browser-") as directory:
        root = Path(directory)
        store = Store(root / "workspace")
        seed_demo(store)
        credentials = Credentials(False)
        with ReviewServer(store, Engine(store, credentials), credentials) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch()
                    page = browser.new_page(viewport={"width": 1440, "height": 1100})
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(server.url)
                    page.get_by_role("button", name="Synthetic example: cabinet label").click()
                    editor = page.get_by_label("Edit the note before using it in your report")
                    editor.fill("Human-reviewed synthetic observation C-17.")
                    page.get_by_role("textbox", name="Reviewer name").fill("Fixture reviewer")
                    page.get_by_label("Reviewed", exact=True).check()
                    page.get_by_role("button", name="Save note", exact=True).click()
                    expect(page.get_by_role("status")).to_contain_text("Note saved")
                    page.get_by_role("searchbox").fill("Human-reviewed")
                    expect(page.locator(".note-card")).to_have_count(1)
                    page.get_by_label("Select Synthetic example: cabinet label for export").check()
                    with page.expect_download() as download:
                        page.get_by_role("button", name="Export selected", exact=True).click()
                    file = root / "download.zip"
                    download.value.save_as(file)
                    with zipfile.ZipFile(file) as archive:
                        assert "notes.docx" in archive.namelist()
                        assert b"Human-reviewed" in archive.read("index.html")
                    if args.screenshot:
                        page.evaluate("scrollTo(0, 0)")
                        page.screenshot(path=str(args.screenshot), full_page=True)
                    page.get_by_role("button", name="Setup", exact=True).click()
                    page.get_by_label("API key", exact=True).fill("invented-browser-test-key")
                    page.get_by_role("button", name="Use this key", exact=True).click()
                    expect(page.locator("#key-status")).to_contain_text("session")
                    assert page.get_by_label("API key", exact=True).input_value() == ""
                    assert "invented-browser-test-key" not in page.content()
                    page.get_by_role("button", name="Source files", exact=False).click()
                    upload = root / "another-synthetic.png"
                    Image.new("RGB", (100, 100), "blue").save(upload)
                    page.locator("#file-picker").set_input_files(str(upload))
                    expect(page.get_by_role("status")).to_contain_text("Imported 1 file(s) locally")
                    expect(page.locator(".file-row")).to_have_count(2)
                    page.set_viewport_size({"width": 600, "height": 900})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    assert not errors, errors
                    browser.close()
                    print("Browser review, search, key setup, local import, export and narrow-layout checks passed.")
            finally:
                server.shutdown()
                thread.join(timeout=5)


if __name__ == "__main__":
    main()
