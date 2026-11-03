"""Offline first-run/drop-to-report acceptance check; no model or OS-keychain calls."""

import base64
import json
import tempfile
import threading
import zipfile
from pathlib import Path

from PIL import Image
from playwright.sync_api import expect, sync_playwright

from sopmaker.credentials import Credentials
from sopmaker.demo import seed_demo
from sopmaker.models import DraftBatch
from sopmaker.server import ReviewServer
from sopmaker.store import Store
from sopmaker.worker import Engine


class OfflineProvider:
    def __init__(self, _):
        pass

    def draft(self, sources, *_):
        return DraftBatch.model_validate({"observations": [{
            "title": "Drop fixture observation", "area": None, "equipment_identifiers": [],
            "claims": [{"text": "An invented blue photo.", "basis": "visible_detail",
                        "evidence": [{"source_id": next(iter(sources)), "quote": None}]}],
            "follow_ups": [], "open_questions": [], "tags": []}]}), {}

    def close(self):
        pass


def main():
    with tempfile.TemporaryDirectory(prefix="sopmaker-quickstart-") as directory:
        root = Path(directory)
        store = Store(root / "notebook")
        credentials = Credentials(False)
        with ReviewServer(store, Engine(store, credentials, OfflineProvider), credentials) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch()
                    page = browser.new_page(accept_downloads=True)
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(server.url)
                    expect(page.get_by_role("heading", name="Notebook setup")).to_be_visible()
                    page.get_by_label("API key", exact=True).fill("invented-quickstart-key")
                    page.get_by_role("button", name="Use this key", exact=True).click()
                    expect(page.locator("#key-status")).to_contain_text("session")
                    # Existing notes must not slip into this batch's automatic report.
                    seed_demo(store)
                    page.get_by_role("button", name="Add files", exact=False).click()
                    photo = root / "dropped.png"
                    Image.new("RGB", (80, 80), "blue").save(photo)
                    data = base64.b64encode(photo.read_bytes()).decode()
                    page.evaluate("""encoded => {
                      const data = new DataTransfer();
                      const bytes = Uint8Array.from(atob(encoded), c => c.charCodeAt(0));
                      data.items.add(new File([bytes], 'dropped.png', {type: 'image/png'}));
                      document.getElementById('drop-zone').dispatchEvent(new DragEvent('drop', {dataTransfer: data, bubbles: true}));
                    }""", data)
                    expect(page.locator("#message")).to_contain_text("Imported 1 file(s) locally")
                    assert len(store.observations()) == 1  # Import made no model call.
                    page.get_by_role("button", name="Generate report", exact=False).first.click()
                    page.get_by_role("button", name="Generate draft report", exact=True).click()
                    download_link = page.get_by_role("link", name="Download draft report")
                    expect(download_link).to_be_visible(timeout=15000)
                    # Reloading keeps a completed report available; no new API request.
                    page.reload()
                    page.get_by_role("button", name="Generate report", exact=False).first.click()
                    expect(download_link).to_be_visible()
                    with page.expect_download() as pending:
                        download_link.click()
                    file = root / "report.zip"
                    pending.value.save_as(file)
                    with zipfile.ZipFile(file) as archive:
                        notes = json.loads(archive.read("observations.json"))["observations"]
                        assert [n["generated"]["title"] for n in notes] == ["Drop fixture observation"]
                        assert notes[0]["state"] == "draft"
                        assert "notes.docx" in archive.namelist()
                    # Failed files must not leave a stale success link visible.
                    bad = root / "bad.png"
                    bad.write_bytes(b"Invented corrupt media")
                    page.locator("#file-picker").set_input_files(str(bad))
                    expect(page.locator("#message")).to_contain_text("Imported 1 file(s) locally")
                    page.get_by_role("button", name="Generate report", exact=False).first.click()
                    page.get_by_role("button", name="Generate draft report", exact=True).click()
                    expect(page.locator("#report-result")).to_contain_text("file failed", timeout=15000)
                    expect(download_link).to_have_count(0)
                    expect(page.locator(".file-error")).not_to_have_count(0)
                    page.set_viewport_size({"width": 600, "height": 900})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    assert not errors, errors
                    browser.close()
                    print("First-run key setup, drop/import, batch-only draft report, reload/download and failed-batch checks passed (offline).")
            finally:
                server.shutdown()
                thread.join(timeout=5)


if __name__ == "__main__":
    from browser_lifecycle import main as lifecycle_main
    from browser_notebooks import main as notebooks_main

    main()
    lifecycle_main()
    notebooks_main()
