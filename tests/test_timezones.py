"""Invented notebooks; timezone defaults must never reinterpret an existing choice."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from urllib.error import HTTPError
from zoneinfo import ZoneInfo

import pytest
from test_notebooks import library_for, request

from sopmaker.metadata import capture_time
from sopmaker.store import Store
from sopmaker.timezones import timezone_options


def test_new_default_persists_and_explicit_utc_wins(store):
    assert store.timezone_origin() == "pending" and store.settings()["timezone"] == "UTC"
    store.initialize_timezone("America/Toronto")
    assert Store(store.root).settings()["timezone"] == "America/Toronto"
    store.initialize_timezone("Asia/Tokyo")
    assert store.settings()["timezone"] == "America/Toronto"
    store.save_settings({"timezone": "UTC"})
    Store(store.root).initialize_timezone("Asia/Tokyo")
    assert store.settings()["timezone"] == "UTC" and store.timezone_origin() == "chosen"
    assert "_timezone_origin" not in store.settings()
    with pytest.raises(ValueError, match="Unknown setting"):
        store.save_settings({"_timezone_origin": "pending"})
    # Display offsets must never replace date-specific conversion rules.
    assert capture_time("2026:07:01 12:00:00", "America/Toronto")["utc"].endswith("16:00:00+00:00")


@pytest.mark.parametrize("saved", ["UTC", "US/Eastern", "Etc/GMT+4"])
def test_pre_upgrade_notebooks_keep_saved_zone_even_when_empty(store, saved):
    store.save_settings({"timezone": saved})
    with store.connect() as db:
        db.execute("DELETE FROM settings WHERE key='_timezone_origin'")  # Version 0.2.1 had no marker.
    reopened = Store(store.root)
    reopened.initialize_timezone("Asia/Kathmandu")
    assert reopened.settings()["timezone"] == saved and reopened.timezone_origin() == "existing"


@pytest.mark.parametrize("detected", [None, "Unknown/Fixture", "../../escape", 42])
def test_failed_detection_is_visible_and_does_not_block_explicit_choice(store, detected):
    store.initialize_timezone(detected)
    assert store.settings()["timezone"] == "UTC" and store.timezone_origin() == "unavailable"
    store.save_settings({"timezone": "Asia/Kathmandu"})
    store.initialize_timezone("America/Toronto")
    assert store.settings()["timezone"] == "Asia/Kathmandu"


def test_imported_data_prevents_automatic_timezone_change(store, photo):
    store.import_file(photo)
    store.initialize_timezone("America/Toronto")
    assert store.settings()["timezone"] == "UTC" and store.timezone_origin() == "existing"


def test_detected_fixed_zone_is_validated_without_overwriting_saved_utc(store):
    store.save_settings({"timezone": "UTC"})
    assert store.initialize_timezone("Etc/GMT+5") == "Etc/GMT+5"
    assert store.settings()["timezone"] == "UTC"
    assert store.initialize_timezone("../invalid") is None


@pytest.mark.parametrize("first", ["initialize", "choose", "simultaneous"])
def test_startup_and_explicit_choice_are_atomic_in_both_orders(store, first):
    def initialize():
        store.initialize_timezone("America/Toronto")

    def choose():
        store.save_settings({"timezone": "Asia/Tokyo"})
    if first == "initialize":
        initialize()
        choose()
    elif first == "choose":
        choose()
        initialize()
    else:
        barrier = Barrier(2)
        def synchronized(operation):
            barrier.wait(timeout=5)
            operation()
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(synchronized, operation) for operation in (initialize, choose)]
            for job in jobs:
                job.result(timeout=5)
    assert store.settings()["timezone"] == "Asia/Tokyo" and store.timezone_origin() == "chosen"


def test_staged_child_initializes_before_fake_processing_and_requires_auth(store, photo):
    with library_for(store) as library:
        notebook_id = uuid.uuid4().hex
        url = library.create(notebook_id, "Timezone fixture")
        child = library.servers[notebook_id][0]
        assert child.store.timezone_origin() == "pending"
        with pytest.raises(HTTPError) as denied:
            request(url, "/api/session", {"timezone": "America/Toronto"}, token="")
        assert denied.value.code == 403 and child.store.settings()["timezone"] == "UTC"
        request(url, "/api/session", {"timezone": "America/Toronto"})
        state = request(url, "/api/state")
        assert state["settings"]["timezone"] == "America/Toronto" and state["timezone_origin"] == "computer"
        child.store.import_file(photo)
        request(url, "/api/process", {"intent": "new"})
        child.engine.thread.join(5)
        assert child.engine.progress["state"] == "completed"
        assert child.store.assets()[0]["prepared"]["zone"] == "America/Toronto"
        request(url, "/api/session", {"timezone": "Asia/Tokyo"})
        assert child.store.settings()["timezone"] == "America/Toronto"


def test_installed_options_include_real_locations_and_cache_immutable_values():
    options = timezone_options()
    assert {"UTC", "America/Toronto", "Asia/Kathmandu"} <= set(options)
    assert isinstance(options, tuple) and len(options) == len(set(options))
    for zone in options:
        ZoneInfo(zone)
