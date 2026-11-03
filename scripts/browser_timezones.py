"""Offline timezone selection/default acceptance, including aliases and unavailable detection."""

import tempfile
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from sopmaker.credentials import Credentials
from sopmaker.server import ReviewServer
from sopmaker.store import Store
from sopmaker.worker import Engine


def main():
    with tempfile.TemporaryDirectory(prefix="sopmaker-timezone-browser-") as temporary, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for scenario in ("new", "existing UTC", "saved alias", "unavailable", "fixed computer"):
            store = Store(Path(temporary) / scenario)
            if scenario == "existing UTC":
                with store.connect() as db:
                    db.execute("DELETE FROM settings WHERE key='_timezone_origin'")
                store = Store(store.root)
            if scenario == "fixed computer":
                store.save_settings({"timezone": "UTC"})
            if scenario == "saved alias":
                store.save_settings({"timezone": "US/Eastern"})
            credentials = Credentials(False)
            def no_provider(_):
                raise AssertionError("Timezone setup must not construct an AI provider")
            with ReviewServer(store, Engine(store, credentials, no_provider), credentials) as server:
                thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
                thread.start()
                try:
                    context = browser.new_context(timezone_id="Etc/GMT+5" if scenario == "fixed computer" else "Asia/Kathmandu", viewport={"width": 1366, "height": 900})
                    if scenario == "unavailable":
                        context.add_init_script("""(() => {
                          const original = Intl.DateTimeFormat;
                          Intl.DateTimeFormat = function(...args) {
                            if (!args.length) throw new Error('Invented unavailable timezone');
                            return new original(...args);
                          };
                        })();""")
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(server.url)
                    select = page.get_by_label("Inspection timezone", exact=True)
                    expect(select).to_be_visible()
                    assert select.evaluate("element => element.tagName") == "SELECT"
                    expected = {"existing UTC": "UTC", "saved alias": "US/Eastern", "unavailable": "UTC", "fixed computer": "UTC"}.get(scenario)
                    if scenario == "new":
                        detected = page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone")
                        expect(select).to_have_value(detected)
                        assert store.settings()["timezone"] == detected
                        assert "UTC+05:45" in select.locator("option:checked").inner_text()
                        # Native keyboard type-ahead; no IANA identifier entry.
                        select.focus()
                        select.press_sequentially("Toronto", delay=40)
                        expect(select).to_have_value("America/Toronto")
                        assert "Eastern Time" in select.locator("option:checked").inner_text()
                        page.evaluate("refresh()")
                        expect(select).to_have_value("America/Toronto")
                        expect(select).to_be_focused()
                        page.get_by_role("button", name="Save details", exact=True).click()
                        expect(page.locator("#message")).to_contain_text("Notebook details saved")
                        assert store.settings()["timezone"] == "America/Toronto"
                        page.reload()
                        expect(select).to_have_value("America/Toronto")
                    else:
                        expect(select).to_have_value(expected)
                        assert store.settings()["timezone"] == expected
                        if scenario == "fixed computer":
                            option = select.locator('optgroup[label="This computer"] option')
                            expect(option).to_have_attribute("value", "Etc/GMT+5")
                            expect(option).to_have_text("Fixed offset — UTC-05:00")
                            select.select_option("Etc/GMT+5")
                            page.get_by_role("button", name="Save details", exact=True).click()
                            expect(page.locator("#message")).to_contain_text("Notebook details saved")
                            assert store.settings()["timezone"] == "Etc/GMT+5"
                        elif scenario == "unavailable":
                            expect(page.locator("#message")).to_contain_text("could not be detected")
                            select.select_option("America/Toronto")
                            page.get_by_role("button", name="Save details", exact=True).click()
                            expect(page.locator("#message")).to_contain_text("Notebook details saved")
                            expect(page.locator("#timezone-help")).to_contain_text("saved timezone")
                        else:
                            page.reload()
                            expect(select).to_have_value(expected)
                    page.set_viewport_size({"width": 600, "height": 900})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    assert not errors, errors
                    context.close()
                finally:
                    server.shutdown()
                    thread.join(5)
        browser.close()
    print("Timezone readable dropdown, computer default, keyboard choice, dirty selection, saved UTC/alias, fallback and narrow-layout checks passed (offline).")


if __name__ == "__main__":
    main()
