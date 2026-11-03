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
        assert library.parent.store.observations()[0]["note"] == "Correction in original notebook"

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
        assert not errors, errors
        await browser.close()


def main():
    with tempfile.TemporaryDirectory(prefix="sopmaker-library-browser-") as folder:
        store = Store(Path(folder) / "workspace")
        seed_demo(store)
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
