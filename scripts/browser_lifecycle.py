"""Offline lifecycle races; called by browser_quickstart so all native CI jobs exercise them."""

import asyncio
import json
import tempfile
import threading
from pathlib import Path

from browser_quickstart import OfflineProvider
from PIL import Image
from playwright.async_api import async_playwright, expect

import sopmaker.worker as worker
from sopmaker.credentials import Credentials
from sopmaker.demo import seed_demo
from sopmaker.server import ReviewServer
from sopmaker.store import Store
from sopmaker.worker import Engine


async def check(server, engine, root, draft_entered, finish_draft, zip_entered, finish_zip, calls):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        context = await browser.new_context(viewport={"width": 1366, "height": 768}, accept_downloads=True)
        page = await context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.goto(server.url)
        await page.get_by_role("button", name="Generate report", exact=False).first.click()
        stale_window = await context.new_page()
        await stale_window.goto(server.url)
        await stale_window.get_by_role("button", name="Generate report", exact=False).first.click()
        held, send = asyncio.Event(), asyncio.Event()
        posts = []

        async def delay_start(route):
            posts.append(route.request.post_data_json)
            held.set()
            await send.wait()
            await route.continue_()

        await page.route("**/api/process", delay_start)
        await page.get_by_role("button", name="Generate draft report", exact=True).click()
        await asyncio.wait_for(held.wait(), 5)
        await expect(page.locator("#generate-report")).to_be_disabled()
        await expect(page.locator("#worker-status")).to_have_text("Starting…")
        await page.locator("#generate-report").evaluate("button => button.click()")
        assert len(posts) == 1 and not calls
        await page.clock.install()
        await page.clock.fast_forward(31000)
        await expect(page.locator("#worker-status")).to_have_text("Still waiting for confirmation")
        await page.clock.resume()
        send.set()
        assert await asyncio.to_thread(draft_entered.wait, 5)
        await expect(page.locator("#worker-status")).to_contain_text("Waiting for draft observations")
        run_id = engine.progress["run_id"]
        assert posts[0]["run_id"] == run_id
        await page.unroute("**/api/process", delay_start)

        # A stale same-run state response must not rewind packaging to drafting.
        old_read, release_read, delivered_read = asyncio.Event(), asyncio.Event(), asyncio.Event()
        holding_read = False

        async def stale_state(route):
            nonlocal holding_read
            if holding_read:
                await route.continue_()
                return
            holding_read = True
            response = await route.fetch()
            body = await response.body()
            old_read.set()
            await release_read.wait()
            await route.fulfill(response=response, body=body)
            delivered_read.set()

        await page.route("**/api/state?*", stale_state)
        await page.evaluate("void refresh()")
        await asyncio.wait_for(old_read.wait(), 5)
        finish_draft.set()
        assert await asyncio.to_thread(zip_entered.wait, 5)
        await page.evaluate("refresh()")
        await expect(page.locator("#worker-status")).to_have_text("Preparing the report ZIP")
        assert len(engine.store.observations()) == 2
        await expect(page.get_by_role("link", name="Download draft report", exact=True)).to_have_count(0)
        release_read.set()
        await asyncio.wait_for(delivered_read.wait(), 5)
        await page.unroute("**/api/state?*", stale_state)
        await expect(page.locator("#worker-status")).to_have_text("Preparing the report ZIP")

        await page.get_by_role("button", name="Review notes", exact=False).first.click()
        await page.get_by_role("button", name="Synthetic example: cabinet label", exact=False).click()
        editor = page.get_by_label("Edit the note before using it in your report")
        await editor.fill("Unsaved fixture correction while report is packaging")
        await page.evaluate("refresh()")
        await expect(editor).to_have_value("Unsaved fixture correction while report is packaging")
        await page.get_by_role("button", name="Export selected", exact=True).click()
        await expect(page.locator("#message")).to_contain_text("Save or discard")
        await page.locator("#status-cancel").click()
        await expect(page.locator("#worker-status")).to_have_text("Cancel requested")
        finish_zip.set()
        await expect(page.locator("#worker-status")).to_have_text("Processing cancelled", timeout=10000)
        assert not engine.progress.get("report_url")
        assert not list((engine.store.root / "exports").glob("*.zip"))
        await stale_window.get_by_role("button", name="Generate draft report", exact=True).click()
        await expect(stale_window.locator("#message")).to_contain_text("No work was started")
        assert len(calls) == 1  # A stale New badge must not authorize paid reprocessing.
        await stale_window.close()
        page.once("dialog", lambda dialog: dialog.accept())
        await page.get_by_role("button", name="Synthetic example: cabinet label", exact=False).click()
        for width, height in ((1366, 768), (1093, 614), (1024, 640), (600, 900)):
            await page.set_viewport_size({"width": width, "height": height})
            await page.locator("#detail").scroll_into_view_if_needed()
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert await page.locator("#detail textarea").evaluate("el => el.getBoundingClientRect().width >= 200")
        await page.set_viewport_size({"width": 1366, "height": 768})

        # A start is accepted by the server but its acknowledgement is lost.
        draft_entered.clear()
        finish_draft.clear()
        extra = root / "second.png"
        Image.new("RGB", (40, 40), "orange").save(extra)
        await page.get_by_role("button", name="Add files", exact=False).first.click()
        await page.locator("#file-picker").set_input_files(str(extra))
        await expect(page.locator("#message")).to_contain_text("Imported 1")
        await page.get_by_role("button", name="Generate report", exact=False).first.click()
        lost, allow_state = asyncio.Event(), asyncio.Event()

        async def lose_ack(route):
            await route.fetch()
            await route.abort()
            lost.set()

        async def delay_reconcile(route):
            await allow_state.wait()
            await route.continue_()

        await page.route("**/api/process", lose_ack)
        await page.route("**/api/state?*", delay_reconcile)
        await page.get_by_role("button", name="Generate draft report", exact=True).click()
        await asyncio.wait_for(lost.wait(), 5)
        assert await asyncio.to_thread(draft_entered.wait, 5)
        second_id = engine.progress["run_id"]
        assert second_id != run_id
        assert await page.evaluate("!!sessionStorage.getItem('sopmaker-pending-run')")
        await page.reload(wait_until="domcontentloaded")
        assert await page.evaluate("!!sessionStorage.getItem('sopmaker-pending-run')")
        allow_state.set()
        await expect(page.locator("#worker-status")).to_contain_text("Waiting for draft observations")
        assert not await page.evaluate("!!sessionStorage.getItem('sopmaker-pending-run')")
        assert len(calls) == 2  # Reload and reconciliation did not submit again.
        await page.unroute("**/api/process", lose_ack)
        await page.unroute("**/api/state?*", delay_reconcile)
        stale_cancel = await context.request.post(server.origin + "/api/cancel", data={"run_id": run_id})
        assert stale_cancel.status == 400 and not engine.cancelled.is_set()
        competing = await context.request.post(server.origin + "/api/process", data={
            "run_id": "a" * 32, "asset_ids": engine.progress["asset_ids"], "generate_report": True})
        assert competing.status == 400 and len(calls) == 2
        await page.emulate_media(reduced_motion="reduce")
        assert await page.locator("#status-progress span").evaluate("el => getComputedStyle(el).animationName === 'none'")
        finish_draft.set()
        await expect(page.locator("#worker-status")).to_have_text("Your draft report is ready", timeout=10000)
        await expect(page.locator("#message")).not_to_contain_text("Preparing your draft")
        await page.get_by_role("button", name="Setup", exact=True).click()
        picker = page.get_by_label("AI model for notes", exact=True)
        await picker.focus()
        await picker.press("l")
        await picker.press("Enter")
        assert await picker.input_value() == "gpt-6.1-terra"
        await page.get_by_role("button", name="Save details", exact=True).click()
        await expect(page.locator("#message")).to_contain_text("future processing")
        assert engine.progress["model"] == "gpt-6.1-sol"
        assert engine.store.settings()["vision_model"] == "gpt-6.1-terra"
        async with page.expect_download() as pending:
            await page.get_by_role("link", name="Download diagnostics", exact=True).click()
        output = root / "diagnostics.json"
        await (await pending.value).save_as(output)
        text = output.read_text()
        assert "invented-lifecycle-key" not in text and server.token not in text and "second.png" not in text
        assert json.loads(text)["events"]
        assert not errors, errors
        await browser.close()


def main():
    with tempfile.TemporaryDirectory(prefix="sopmaker-lifecycle-") as folder:
        root = Path(folder)
        store = Store(root / "workspace")
        seed_demo(store)
        photo = root / "first.png"
        Image.new("RGB", (40, 40), "blue").save(photo)
        store.import_file(photo)
        credentials = Credentials(False)
        credentials.set("invented-lifecycle-key")
        draft_entered, finish_draft, zip_entered, finish_zip = (threading.Event() for _ in range(4))
        calls = []

        class HeldProvider(OfflineProvider):
            def draft(self, sources, *args):
                calls.append(1)
                draft_entered.set()
                assert finish_draft.wait(30)
                return super().draft(sources)

        original_export = worker.export_notes

        def held_export(*args, **kwargs):
            zip_entered.set()
            assert finish_zip.wait(30)
            return original_export(*args, **kwargs)

        worker.export_notes = held_export
        engine = Engine(store, credentials, HeldProvider)
        try:
            with ReviewServer(store, engine, credentials) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    asyncio.run(check(server, engine, root, draft_entered, finish_draft, zip_entered, finish_zip, calls))
                finally:
                    finish_draft.set()
                    finish_zip.set()
                    if engine.thread:
                        engine.thread.join(10)
                    server.shutdown()
                    thread.join(5)
        finally:
            worker.export_notes = original_export
    print("Immediate lock, unconfirmed/lost start and reload, stale polls/cancel, packaging, saved-edit export guard, responsive layout, model picker and diagnostics passed (offline).")


if __name__ == "__main__":
    main()
