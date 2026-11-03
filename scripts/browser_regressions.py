"""Offline browser regressions for delayed saves, source-only review and multiple notebooks."""

import asyncio
import copy
import tempfile
import threading
import wave
import zipfile
from contextlib import ExitStack
from pathlib import Path

from PIL import Image
from playwright.async_api import async_playwright, expect

from sopmaker.credentials import Credentials
from sopmaker.demo import seed_demo
from sopmaker.models import DraftBatch
from sopmaker.server import ReviewServer
from sopmaker.store import Store
from sopmaker.worker import Engine


class NoObservations:
    def transcribe(self, *_):
        return {"text": "Invented source narration: unit Z-22 needs a closer look."}

    def draft(self, *_):
        return DraftBatch(observations=[]), {"model": "offline-fixture"}


class HistoricalNarration:
    def __init__(self, text, title):
        self.text, self.title = text, title

    def transcribe(self, *_):
        return {"text": self.text}

    def draft(self, sources, *_):
        source_id = next(iter(sources))
        value = {"title": self.title, "area": None, "equipment_identifiers": [],
                 "claims": [{"text": self.text, "basis": "inspector_narration",
                             "evidence": [{"source_id": source_id, "quote": self.text}]}],
                 "follow_ups": [], "open_questions": [], "tags": []}
        return DraftBatch.model_validate({"observations": [value]}), {"model": "offline-fixture"}


async def check(first, second, store, note_id, root):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(first.url)
        await page.get_by_role("button", name="Synthetic example: cabinet label").click()
        editor = page.get_by_label("Edit the note before using it in your report")
        save = page.get_by_role("button", name="Save note", exact=True)

        # Typing during an in-flight POST must survive its response and next save.
        held, release = asyncio.Event(), asyncio.Event()

        async def hold_post(route):
            held.set()
            await release.wait()
            await route.continue_()

        await page.route("**/api/edit", hold_post)
        await editor.fill("Submitted first edit")
        await save.click()
        await asyncio.wait_for(held.wait(), timeout=5)
        await editor.fill("Newer edit typed while the request was pending")
        release.set()
        await expect(page.locator("#message")).to_contain_text("revision 3")
        await expect(editor).to_have_value("Newer edit typed while the request was pending")
        await expect(page.locator(".save-status")).to_contain_text("newer changes are still unsaved")
        await page.unroute("**/api/edit", hold_post)
        await save.click()
        await expect(page.locator("#message")).to_contain_text("revision 4")
        assert next(o for o in store.observations() if o["id"] == note_id)["note"] == "Newer edit typed while the request was pending"

        # Typing during the later refresh is a separate race from the POST itself.
        held, release = asyncio.Event(), asyncio.Event()

        async def hold_refresh(route):
            held.set()
            await release.wait()
            await route.continue_()

        await page.route("**/api/state?*", hold_refresh)
        await editor.fill("Submitted before delayed refresh")
        await save.click()
        await asyncio.wait_for(held.wait(), timeout=5)
        await editor.fill("Typed during delayed refresh")
        release.set()
        await expect(page.locator("#message")).to_contain_text("revision 5")
        await expect(editor).to_have_value("Typed during delayed refresh")
        await page.unroute("**/api/state?*", hold_refresh)
        await save.click()
        await expect(page.locator("#message")).to_contain_text("revision 6")

        # Switching notes while a save is pending cannot replace the new editor.
        held, release = asyncio.Event(), asyncio.Event()
        await page.route("**/api/edit", hold_post)
        await editor.fill("Save from the first note")
        await save.click()
        await asyncio.wait_for(held.wait(), timeout=5)
        page.once("dialog", lambda dialog: dialog.accept())
        await page.get_by_role("button", name="Second synthetic observation").click()
        await editor.fill("Unsaved text in the second note")
        release.set()
        await expect(page.locator("#message")).to_contain_text("revision 7")
        await expect(page.locator("#detail h2")).to_have_text("Second synthetic observation")
        await expect(editor).to_have_value("Unsaved text in the second note")
        await expect(page.locator(".save-status")).to_contain_text("Unsaved changes")
        await page.unroute("**/api/edit", hold_post)
        await save.click()
        await expect(page.locator("#message")).to_contain_text("Second synthetic observation (revision 2)")

        # Old reviewed notes display/export their own transcript, while source review
        # independently exposes the latest complete transcript of the same recording.
        await page.get_by_role("button", name="Historical narrated note", exact=False).click()
        await expect(page.locator("#detail .transcript")).to_contain_text("Earlier invented account.")
        await expect(page.locator("#detail .transcript")).not_to_contain_text("Later invented account.")
        await page.get_by_label("Select Historical narrated note for export").check()
        async with page.expect_download() as pending_download:
            await page.get_by_role("button", name="Export selected", exact=True).click()
        download = await pending_download.value
        file = root / "historical-export.zip"
        await download.save_as(file)
        with zipfile.ZipFile(file) as archive:
            index = archive.read("index.html").decode()
            assert "Earlier invented account." in index and "Later invented account." not in index

        # Review a narrated source that produced zero observations.
        await page.get_by_role("button", name="Add files", exact=False).click()
        await page.get_by_role("searchbox", name="Search source files").fill("Z-22")
        await expect(page.locator("#files-list .file-row")).to_have_count(1)
        await page.get_by_role("button", name="Review source invented-narration.wav", exact=True).click()
        await expect(page.locator("#source-detail")).to_contain_text("Invented source narration: unit Z-22")
        await expect(page.locator("#source-detail audio")).to_have_count(1)
        assert not any(o["asset_id"] == next(a["id"] for a in store.assets() if a["name"] == "invented-narration.wav") for o in store.observations())

        # Unprocessed and failed source photos remain directly accessible too.
        await page.get_by_role("searchbox", name="Search source files").fill("unprocessed.png")
        await page.get_by_role("button", name="Review source unprocessed.png", exact=True).click()
        await expect(page.locator("#source-detail")).to_contain_text("Processing state: imported")
        await expect(page.locator("#source-detail img")).to_be_visible()
        await page.get_by_role("searchbox", name="Search source files").fill("failed.png")
        await page.get_by_role("button", name="Review source failed.png", exact=True).click()
        await expect(page.locator("#source-detail")).to_contain_text("Invented processing failure")
        await expect(page.locator("#source-detail a")).to_have_count(1)
        await page.get_by_role("searchbox", name="Search source files").fill("historical-narration.wav")
        await page.get_by_role("button", name="Review source historical-narration.wav", exact=True).click()
        await expect(page.locator("#source-detail .transcript")).to_contain_text("Later invented account.")

        # Cookies are shared across ports. Each notebook must retain its own session.
        other = await context.new_page()
        await other.goto(second.url)
        await expect(other.locator(".note-card")).to_have_count(1)
        aid = store.assets()[0]["id"]
        response = await context.request.get(first.origin + f"/media/{aid}/original")
        assert response.status == 200
        assert len(await response.body()) > 0
        output = await context.request.post(first.origin + "/api/export", data={"ids": [note_id]})
        assert output.status == 200
        exported = await output.json()
        download = await context.request.get(first.origin + exported["url"])
        assert download.status == 200
        await browser.close()
        print("Delayed POST/refresh saves, navigation, zero-note/unprocessed/failed source review, and concurrent-notebook cookie checks passed.")


def main():
    with tempfile.TemporaryDirectory(prefix="sopmaker-browser-regressions-") as directory:
        root = Path(directory)
        store = Store(root / "first")
        seed_demo(store)
        note = store.observations()[0]
        store.edit(note["id"], 1, note["note"], "reviewed", "Fixture reviewer")
        another = copy.deepcopy(note["generated"])
        another["title"] = "Second synthetic observation"
        store.add_drafts(note["asset_id"], [another], {"mode": "offline-fixture"}, "second-fixture")
        photo = root / "unprocessed.png"
        Image.new("RGB", (80, 80), "blue").save(photo)
        store.import_file(photo)
        failed = root / "failed.png"
        failed.write_bytes(b"invented corrupt image")
        store.import_file(failed)
        failed_asset = next(a for a in store.assets() if a["name"] == failed.name)
        store.update_asset(failed_asset["id"], state="error", error="Invented processing failure")
        audio = root / "invented-narration.wav"
        with wave.open(str(audio), "wb") as output:
            output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            output.writeframes(b"\x00\x10" * 16000)
        store.import_file(audio)
        asset = next(a for a in store.assets() if a["name"] == audio.name)
        Engine(store, Credentials(False)).process_one(asset, store.settings(), NoObservations())
        historical = root / "historical-narration.wav"
        with wave.open(str(historical), "wb") as output:
            output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            output.writeframes(b"\x00\x20" * 16000)
        store.import_file(historical)
        asset = next(a for a in store.assets() if a["name"] == historical.name)
        engine = Engine(store, Credentials(False))
        engine.process_one(asset, store.settings(), HistoricalNarration("Earlier invented account.", "Historical narrated note"))
        old = next(o for o in store.observations() if o["asset_id"] == asset["id"])
        store.edit(old["id"], 1, old["note"], "reviewed", "Fixture reviewer")
        engine.process_one(store.asset(asset["id"]), store.settings() | {"context": "changed"},
                           HistoricalNarration("Later invented account.", "Newer narrated note"))
        second = Store(root / "second")
        seed_demo(second)
        with ExitStack() as stack:
            servers = [stack.enter_context(ReviewServer(s, Engine(s, Credentials(False)), Credentials(False))) for s in (store, second)]
            threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in servers]
            for thread in threads:
                thread.start()
            try:
                asyncio.run(check(*servers, store, note["id"], root))
            finally:
                for server in servers:
                    server.shutdown()
                for thread in threads:
                    thread.join(timeout=5)


if __name__ == "__main__":
    main()
