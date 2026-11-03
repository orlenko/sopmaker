"""Offline library workflow: blank second inspection, retained tabs, lost responses and browser sign-in."""

import asyncio
import re
import tempfile
import threading
from pathlib import Path

from playwright.async_api import async_playwright, expect

from sopmaker.credentials import Credentials
from sopmaker.demo import seed_demo
from sopmaker.notebooks import NotebookLibrary
from sopmaker.server import ReviewServer
from sopmaker.store import Store
from sopmaker.worker import Engine


async def check(library):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        context = await browser.new_context(permissions=["clipboard-read", "clipboard-write"])
        page = await context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.goto(library.parent.url)
        await expect(page.locator("#current-notebook")).to_have_text("Original inspection")
        await page.get_by_role("button", name="Copy browser link", exact=True).click()
        await expect(page.locator("#message")).to_contain_text("Browser link copied")
        copied = await page.evaluate("navigator.clipboard.readText()")
        assert copied == library.parent.url
        # A separate browser context has neither session storage nor cookies.
        other_context = await browser.new_context()
        other = await other_context.new_page()
        await other.goto(library.parent.origin)
        await expect(other.locator("#message")).to_contain_text("Copy browser link")
        await other.goto(copied)
        await expect(other.locator("#current-notebook")).to_have_text("Original inspection")
        await other_context.close()

        await page.get_by_role("button", name="Notebooks", exact=True).click()
        await expect(page.locator("#notebooks-heading")).to_be_focused()
        await page.get_by_label("New notebook name", exact=True).fill("Second inspection")
        await page.get_by_label("New notebook name", exact=True).press("Enter")
        await expect(page.locator("#current-notebook")).to_have_text("Second inspection")
        await expect(page.get_by_role("heading", name="Add files", exact=True)).to_be_visible()
        await expect(page.locator("#file-count")).to_have_text("0")
        await expect(page.locator("#note-count")).to_have_text("0")
        await expect(page.locator("#key-status")).to_contain_text("Key ready")

        # The retained original tab remains visibly and functionally independent.
        old = await context.new_page()
        old.on("pageerror", lambda error: errors.append(str(error)))
        await old.goto(library.parent.url)
        await old.get_by_role("button", name="Synthetic example: cabinet label").click()
        await old.get_by_label("Edit the note before using it in your report").fill("Correction in original notebook")
        await old.get_by_role("button", name="Notebooks", exact=True).click()
        await old.get_by_role("button", name=re.compile(r"^Open Second inspection")).click()
        await expect(old.locator("#notebook-status")).to_contain_text("Save your notes")
        await old.get_by_role("button", name="Review notes", exact=False).first.click()
        await expect(old.get_by_label("Edit the note before using it in your report")).to_have_value("Correction in original notebook")
        await old.get_by_role("button", name="Save note", exact=True).click()
        await expect(old.locator("#message")).to_contain_text("revision 2")
        assert any(note["note"] == "Correction in original notebook" for note in library.parent.store.observations())

        # Pending note saves remain guarded even after switching editors.
        save_entered, save_release = asyncio.Event(), asyncio.Event()

        async def held_note(route):
            save_entered.set()
            await save_release.wait()
            await route.continue_()

        async def accept_dialog(dialog):
            await dialog.accept()

        await old.route("**/api/edit", held_note)
        await old.get_by_label("Edit the note before using it in your report").fill("Pending original correction")
        await old.get_by_role("button", name="Save note", exact=True).click()
        await asyncio.wait_for(save_entered.wait(), timeout=5)
        old.once("dialog", accept_dialog)
        await old.get_by_role("button", name="Second invented observation").click()
        await old.get_by_role("button", name="Notebooks", exact=True).click()
        await old.get_by_role("button", name=re.compile(r"^Open Second inspection")).click()
        await expect(old.locator("#notebook-status")).to_contain_text("Wait for any save")
        save_release.set()
        await expect(old.locator("#message")).to_contain_text("revision 3")
        await old.unroute("**/api/edit", held_note)

        await old.get_by_role("button", name="Setup", exact=True).click()
        await old.locator('[name="context"]').fill("Unsaved inspection context")
        await old.get_by_role("button", name="Notebooks", exact=True).click()
        await old.get_by_role("button", name=re.compile(r"^Open Second inspection")).click()
        await expect(old.locator("#notebook-status")).to_contain_text("Setup details")
        await old.get_by_role("button", name="Setup", exact=True).click()
        await expect(old.locator('[name="context"]')).to_have_value("Unsaved inspection context")
        held, release = asyncio.Event(), asyncio.Event()

        async def hold_settings(route):
            held.set()
            await release.wait()
            await route.continue_()

        await old.route("**/api/settings", hold_settings)
        await old.get_by_role("button", name="Save details", exact=True).click()
        await asyncio.wait_for(held.wait(), timeout=5)
        await old.get_by_role("button", name="Notebooks", exact=True).click()
        await old.get_by_role("button", name=re.compile(r"^Open Second inspection")).click()
        await expect(old.locator("#notebook-status")).to_contain_text("Wait for any save")
        release.set()
        await expect(old.locator("#message")).to_contain_text("Notebook details saved")
        await old.unroute("**/api/settings", hold_settings)
        await old.get_by_role("button", name=re.compile(r"^Open Second inspection")).click()
        await expect(old.locator("#current-notebook")).to_have_text("Second inspection")
        await expect(old.locator("#note-count")).to_have_text("0")

        # Commit a new notebook, then lose only its HTTP response; recover from list.
        await page.get_by_role("button", name="Notebooks", exact=True).click()
        await page.get_by_label("New notebook name", exact=True).fill("Uncertain inspection")
        started, finish = asyncio.Event(), asyncio.Event()

        async def lose_response(route):
            started.set()
            await finish.wait()
            await route.fetch()
            await route.abort("failed")

        await page.route("**/api/notebooks", lose_response)
        await page.get_by_role("button", name="New notebook", exact=True).click()
        await asyncio.wait_for(started.wait(), timeout=5)
        await expect(page.locator("#new-notebook")).to_be_disabled()
        finish.set()
        await expect(page.locator("#notebook-status")).to_contain_text("may already be saved")
        await expect(page.get_by_label("New notebook name", exact=True)).to_have_value("Uncertain inspection")
        await page.unroute("**/api/notebooks", lose_response)
        await page.get_by_role("button", name="Refresh list", exact=True).click()
        await expect(page.locator("#notebooks-heading")).to_be_focused()
        await expect(page.locator("#notebook-status")).to_contain_text("3 notebook(s)")
        await page.get_by_role("button", name=re.compile(r"^Open Uncertain inspection")).click()
        await expect(page.locator("#current-notebook")).to_have_text("Uncertain inspection")
        await page.set_viewport_size({"width": 600, "height": 900})
        await expect(page.locator("#current-notebook")).to_be_visible()
        assert await page.title() == "Uncertain inspection · Sopmaker"
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        # A confirmed creation with withheld navigation ends that request ID.
        await page.get_by_role("button", name="Notebooks", exact=True).click()
        await page.get_by_label("New notebook name", exact=True).fill("Created while editing")
        create_entered, create_release = asyncio.Event(), asyncio.Event()

        async def held_creation(route):
            create_entered.set()
            await create_release.wait()
            await route.continue_()

        await page.route("**/api/notebooks", held_creation)
        await page.get_by_role("button", name="New notebook", exact=True).click()
        await asyncio.wait_for(create_entered.wait(), timeout=5)
        await page.get_by_role("button", name="Setup", exact=True).click()
        await page.locator('[name="context"]').fill("Keep these details")
        create_release.set()
        await expect(page.locator("#notebook-status")).to_contain_text("Save your work")
        await page.unroute("**/api/notebooks", held_creation)
        await page.get_by_role("button", name="Save details", exact=True).click()
        await expect(page.locator("#message")).to_contain_text("Notebook details saved")
        await page.get_by_role("button", name="Notebooks", exact=True).click()
        await page.get_by_label("New notebook name", exact=True).fill("A distinct new inspection")
        await page.get_by_role("button", name="New notebook", exact=True).click()
        await expect(page.locator("#current-notebook")).to_have_text("A distinct new inspection")
        assert len(library.notebooks("default")) == 5
        await expect(page.locator("#note-count")).to_have_text("0")

        # A lost credential response stays uncertain across reload; no retry.
        async def lost_key(route):
            await route.fetch()
            await route.abort("failed")

        await page.get_by_role("button", name="Setup", exact=True).click()
        await page.get_by_label("API key", exact=True).fill("invalid invented key with spaces")
        await page.get_by_role("button", name="Use this key", exact=True).click()
        await expect(page.locator("#message")).to_contain_text("Enter an API key without spaces")
        await expect(page.get_by_label("API key", exact=True)).to_have_value("invalid invented key with spaces")
        await page.route("**/api/key", lost_key)
        await page.get_by_label("API key", exact=True).fill("invented-uncertain-key")
        await page.get_by_role("button", name="Use this key", exact=True).click()
        await expect(page.locator("#message")).to_contain_text("Setup change was not confirmed")
        await page.unroute("**/api/key", lost_key)
        await page.get_by_role("button", name="Notebooks", exact=True).click()
        await page.get_by_role("button", name=re.compile(r"^Open Second inspection")).click()
        await expect(page.locator("#notebook-status")).to_contain_text("restart Sopmaker")
        page.once("dialog", accept_dialog)
        await page.reload()
        await page.get_by_role("button", name="Notebooks", exact=True).click()
        await page.get_by_role("button", name=re.compile(r"^Open Second inspection")).click()
        await expect(page.locator("#notebook-status")).to_contain_text("restart Sopmaker")
        assert await page.evaluate("sessionStorage.getItem('sopmaker-setup-uncertain')")
        # Restart only this owned synthetic fixture on its previous port.
        parsed_port = int(page.url.split(":")[2].split("/")[0])
        selected_store = next(server.store for server, _, _ in library.servers.values() if server.server_port == parsed_port)
        await asyncio.to_thread(library.parent.shutdown)
        await asyncio.to_thread(library.close)
        fresh_keys = Credentials(False)
        fresh_store = Store(selected_store.root)
        with ReviewServer(fresh_store, Engine(fresh_store, fresh_keys), fresh_keys, parsed_port) as fresh:
            fresh_library = NotebookLibrary(fresh)
            fresh_thread = threading.Thread(target=fresh.serve_forever, daemon=True)
            fresh_thread.start()
            try:
                page.once("dialog", accept_dialog)
                await page.goto(fresh.url)
                await expect(page.locator("#current-notebook")).to_have_text("A distinct new inspection")
                assert await page.evaluate("sessionStorage.getItem('sopmaker-setup-uncertain')") is None
                await page.get_by_role("button", name="Notebooks", exact=True).click()
                await page.get_by_label("New notebook name", exact=True).fill("After fixture restart")
                await page.get_by_role("button", name="New notebook", exact=True).click()
                await expect(page.locator("#current-notebook")).to_have_text("After fixture restart")
            finally:
                await asyncio.to_thread(fresh.shutdown)
                fresh_thread.join(5)
                await asyncio.to_thread(fresh_library.close)
        assert not errors, errors
        await browser.close()


def main():
    with tempfile.TemporaryDirectory(prefix="sopmaker-library-browser-") as folder:
        store = Store(Path(folder) / "workspace")
        seed_demo(store)
        original = store.observations()[0]
        store.add_drafts(original["asset_id"], [original["generated"], original["generated"] | {"title": "Second invented observation"}],
                         {"mode": "offline_synthetic_pair"}, "fixture-pair")
        store.save_settings({"name": "Original inspection"})
        credentials = Credentials(False)
        credentials.set("invented-browser-key")
        # The workflow never processes anything: no provider is constructed.
        def no_provider(_):
            raise AssertionError("Library navigation must not call an AI provider")

        with ReviewServer(store, Engine(store, credentials, no_provider), credentials) as server:
            library = NotebookLibrary(server)
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
            thread.start()
            try:
                asyncio.run(check(library))
            finally:
                server.shutdown()
                thread.join(5)
                library.close()
    print("Notebook create/list/open, old-tab isolation, unsaved work, lost-response recovery and separate-browser sign-in passed (offline).")


if __name__ == "__main__":
    main()
