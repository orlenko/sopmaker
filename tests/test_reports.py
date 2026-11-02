import json
import zipfile

import pytest

from sopmaker.credentials import Credentials
from sopmaker.demo import seed_demo
from sopmaker.models import DraftBatch
from sopmaker.worker import Engine


class ReportProvider:
    def __init__(self, _):
        pass

    def draft(self, sources, *_):
        return DraftBatch.model_validate({"observations": [{
            "title": "Invented batch observation", "area": None, "equipment_identifiers": [],
            "claims": [{"text": "A test image.", "basis": "visible_detail",
                        "evidence": [{"source_id": next(iter(sources)), "quote": None}]}],
            "follow_ups": [], "open_questions": [], "tags": []}]}), {}

    def close(self):
        pass


def run_report(store, provider=ReportProvider):
    credentials = Credentials(False)
    credentials.set("invented-offline-key")
    engine = Engine(store, credentials, provider)
    engine.start(generate_report=True)
    engine.thread.join(timeout=10)
    assert not engine.thread.is_alive()
    assert not engine.progress["running"]
    return engine.progress


def test_report_contains_only_new_batch_and_keeps_drafts(store, photo):
    seed_demo(store)
    prior = {o["id"] for o in store.observations()}
    store.import_file(photo)
    result = run_report(store)
    assert result["report_notes"] == 1
    output = store.root / result["report_url"].lstrip("/")
    with zipfile.ZipFile(output) as archive:
        data = json.loads(archive.read("observations.json"))
        assert len(data["observations"]) == 1
        note = data["observations"][0]
        assert note["id"] not in prior
        assert note["state"] == "draft"
        assert note["evidence"]["provenance"] == "bound"
        assert "notes.docx" in archive.namelist()
    # Clicking again without new files must not incur a second model call.
    credentials = Credentials(False)
    credentials.set("invented-offline-key")
    with pytest.raises(ValueError, match="Import new files"):
        Engine(store, credentials, ReportProvider).start(generate_report=True)


def test_incomplete_batch_does_not_export_a_partial_success(store, photo, tmp_path):
    store.import_file(photo)
    bad = tmp_path / "broken.png"
    bad.write_bytes(b"Invented invalid image")
    store.import_file(bad)
    result = run_report(store)
    assert "report_url" not in result
    assert "file failed" in result["report_error"]
    assert len(store.observations()) == 1
    assert {a["state"] for a in store.assets()} == {"ready", "error"}
    assert not list((store.root / "exports").glob("*.zip"))


def test_zero_observations_and_export_failure_are_visible(store, photo, monkeypatch):
    class NoNotes(ReportProvider):
        def draft(self, *_):
            return DraftBatch(observations=[]), {}

    store.import_file(photo)
    result = run_report(store, NoNotes)
    assert "no observations" in result["report_error"]
    assert "report_url" not in result

    # An export failure after successful processing must release the worker lock
    # and report a safe failure, rather than appearing to run forever.
    asset = store.assets()[0]
    store.update_asset(asset["id"], state="imported", fingerprint=None)
    def fail_export(*_, **__):
        raise OSError("invented sensitive path")
    monkeypatch.setattr("sopmaker.worker.export_notes", fail_export)
    result = run_report(store)
    assert "report_error" in result and "report_url" not in result
    assert "invented sensitive path" not in result["report_error"]
    assert store.assets()[0]["state"] == "ready"


def test_cancelled_report_is_not_exported(store, photo):
    import threading

    entered, release = threading.Event(), threading.Event()
    class HeldProvider(ReportProvider):
        def draft(self, *args):
            entered.set()
            assert release.wait(timeout=5)
            return super().draft(*args)

    store.import_file(photo)
    credentials = Credentials(False)
    credentials.set("invented-offline-key")
    engine = Engine(store, credentials, HeldProvider)
    engine.start(generate_report=True)
    try:
        assert entered.wait(timeout=5)
        engine.stop()
    finally:
        release.set()
        engine.thread.join(timeout=10)
    assert not engine.progress["running"]
    assert "report_url" not in engine.progress
    assert "processing stopped" in engine.progress["report_error"]


def test_cancel_during_zip_export_never_publishes_success(store, photo, monkeypatch):
    import threading

    from sopmaker.exporter import export_notes

    entered, release = threading.Event(), threading.Event()
    def held_export(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=5)
        return export_notes(*args, **kwargs)

    monkeypatch.setattr("sopmaker.worker.export_notes", held_export)
    store.import_file(photo)
    credentials = Credentials(False)
    credentials.set("invented-offline-key")
    engine = Engine(store, credentials, ReportProvider)
    engine.start(generate_report=True)
    try:
        assert entered.wait(timeout=5)
        engine.stop()
    finally:
        release.set()
        engine.thread.join(timeout=10)
    assert not engine.thread.is_alive()
    assert not engine.progress["running"]
    assert "report_url" not in engine.progress
    assert "processing stopped" in engine.progress["report_error"]
    assert not list((store.root / "exports").glob("*.zip"))
    assert store.assets()[0]["state"] == "ready"
    assert len(store.observations()) == 1
