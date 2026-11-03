import json
import threading
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from PIL import Image
from test_reports import ReportProvider

from sopmaker.credentials import Credentials
from sopmaker.diagnostics import Diagnostics
from sopmaker.server import ReviewServer
from sopmaker.store import Store
from sopmaker.worker import Engine


def engine_for(store, provider=ReportProvider, diagnostics=None):
    key = Credentials(False)
    key.set("invented-lifecycle-key")
    return Engine(store, key, provider, diagnostics)


def test_correlated_run_frozen_batch_and_real_packaging(store, photo, tmp_path, monkeypatch):
    from sopmaker.exporter import export_notes

    drafting, finish_draft, packaging, finish_zip = (threading.Event() for _ in range(4))
    calls = []

    class Held(ReportProvider):
        def draft(self, sources, directory, model, context):
            calls.append(model)
            drafting.set()
            assert finish_draft.wait(5)
            return super().draft(sources)

    def held_export(*args, **kwargs):
        packaging.set()
        assert finish_zip.wait(5)
        return export_notes(*args, **kwargs)

    monkeypatch.setattr("sopmaker.worker.export_notes", held_export)
    store.import_file(photo)
    engine = engine_for(store, Held)
    run_id = uuid.uuid4().hex
    engine.start(generate_report=True, run_id=run_id)
    try:
        assert drafting.wait(5)
        snapshot = engine.progress
        assert snapshot["stage"] == "drafting" and snapshot["running"]
        assert snapshot["completed"] == 0 and snapshot["notes"] == 0
        assert snapshot["run_id"] == run_id and snapshot["file_started_at"]
        snapshot["asset_ids"].clear()
        assert len(engine.progress["asset_ids"]) == 1
        assert engine.start(generate_report=True, run_id=run_id)["run_id"] == run_id
        with pytest.raises(ValueError, match="already running"):
            engine.start(run_id=uuid.uuid4().hex)
        with pytest.raises(ValueError, match="no longer current"):
            engine.stop(uuid.uuid4().hex)
        extra = tmp_path / "extra.png"
        Image.new("RGB", (10, 10), "orange").save(extra)
        store.import_file(extra)
        store.save_settings({"vision_model": "custom-after-start"})
        finish_draft.set()
        assert packaging.wait(5)
        packed = engine.progress
        assert packed["stage"] == "packaging" and packed["running"]
        assert packed["completed"] == packed["total"] == packed["notes"] == 1
        assert len(store.observations()) == 1 and not packed.get("report_url")
        assert packed["model"] != "custom-after-start"
        assert packed["sequence"] > snapshot["sequence"]
    finally:
        finish_draft.set()
        finish_zip.set()
        engine.thread.join(10)
    result = engine.progress
    assert result["state"] == "completed" and result["report_url"]
    assert result["total"] == 1 and calls == [result["model"]]
    assert engine.receipt(run_id) == result
    # A delayed identical start after completion must not consume a new file.
    engine.start(generate_report=True, run_id=run_id)
    assert calls == [result["model"]]
    assert store.asset(store.assets()[-1]["id"])["state"] == "imported"


def test_thread_start_failure_is_terminal_and_releases_admission(store, photo, monkeypatch):
    store.import_file(photo)
    engine = engine_for(store)
    run_id = uuid.uuid4().hex
    with monkeypatch.context() as patch:
        patch.setattr(threading.Thread, "start", lambda _: (_ for _ in ()).throw(RuntimeError("invented secret")))
        with pytest.raises(RuntimeError):
            engine.start(run_id=run_id)
    assert engine.progress["state"] == "failed" and not engine.lock.locked()
    assert "invented secret" not in json.dumps(engine.progress)
    engine.start(run_id=run_id)
    assert engine.progress["state"] == "failed"  # No replay of the rejected admission.
    engine.start()
    engine.thread.join(10)
    assert engine.progress["state"] == "completed"


def test_cleanup_failure_withholds_report_and_cached_retry_is_explicit(store, photo):
    class BadClose(ReportProvider):
        def close(self):
            raise OSError("invented private path / credential")

    store.import_file(photo)
    engine = engine_for(store, BadClose)
    engine.start(generate_report=True)
    engine.thread.join(10)
    assert engine.progress["state"] == "failed" and engine.progress["cleanup_error"]
    assert not engine.progress.get("report_url") and not engine.lock.locked()
    assert not list((store.root / "exports").glob("*.zip"))
    assert len(store.observations()) == 1
    events = engine.diagnostics.download()
    assert "invented private" not in json.dumps(events)
    engine.provider_factory = ReportProvider
    engine.start(asset_ids=[store.assets()[0]["id"]], generate_report=True)
    engine.thread.join(10)
    assert engine.progress["state"] == "completed" and engine.progress["notes"] == 0
    assert engine.progress["completed"] == engine.progress["report_notes"] == 1


def test_saved_models_survive_and_request_policy_changes_require_explicit_processing(store, photo, monkeypatch):
    for model in ("gpt-5.4-mini", "custom-saved-model", "gpt-6.1-terra"):
        store.save_settings({"vision_model": model})
        assert Store(store.root).settings()["vision_model"] == model
    store.save_settings({"vision_model": "gpt-6.1-sol"})
    store.import_file(photo)
    engine = engine_for(store)
    calls = []

    class Counting(ReportProvider):
        def draft(self, sources, *args):
            calls.append(1)
            return super().draft(sources)

    provider = Counting(None)
    aid = store.assets()[0]["id"]
    engine.process_one(store.asset(aid), store.settings(), provider)
    engine.process_one(store.asset(aid), store.settings(), provider)
    assert len(calls) == 1
    monkeypatch.setattr("sopmaker.worker.draft_options", lambda _: {"reasoning": {"effort": "low"}, "max_output_tokens": 12001})
    assert len(calls) == 1  # Configuration changes do not launch paid processing.
    engine.process_one(store.asset(aid), store.settings(), provider)
    assert len(calls) == 2
    assert len(store.observations(include_archived=True)) == 2


def test_diagnostics_redaction_rotation_and_write_failure(tmp_path):
    logs = tmp_path / "logs"
    diagnostics = Diagnostics(logs, max_bytes=400)
    for _ in range(30):
        diagnostics.emit("stage_started", stage="drafting", model="invented-secret-as-model",
                         request_id="req_bad\nprivate narration", note="private note", path="private path")
    files = list(logs.glob("*.jsonl"))
    assert len(files) == 3 and all(f.stat().st_size <= 400 for f in files)
    output = json.dumps(diagnostics.download()) + "".join(f.read_text() for f in files)
    assert "invented-secret" not in output and "private" not in output
    assert '"model": "custom"' in output
    diagnostics.emit("file_failed", category="local", request_id="req_fixture_123", http_status=429)
    assert diagnostics.download()["events"][-1]["request_id"] == "req_fixture_123"
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("invented")
    failed = Diagnostics(blocked)
    failed.emit("run_started", total=1)
    assert failed.info()["write_failed"] and len(failed.download()["events"]) == 1


def test_diagnostic_and_receipt_routes_require_current_session(store):
    engine = engine_for(store)
    engine.diagnostics.emit("app_started", secret="invented-secret")
    with ReviewServer(store, engine, engine.credentials) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for path in ("/api/diagnostics", "/api/runs/" + uuid.uuid4().hex):
                with pytest.raises(HTTPError) as denied:
                    urlopen(server.origin + path, timeout=5)
                assert denied.value.code == 403
            request = Request(server.origin + "/api/diagnostics?path=inspection.sqlite3",
                              headers={"Authorization": "Bearer " + server.token})
            with urlopen(request, timeout=5) as response:
                data = response.read().decode()
                assert "attachment" in response.headers["Content-Disposition"]
                assert "invented-secret" not in data and server.token not in data and str(store.root) not in data
                assert json.loads(data)["events"][0]["event"] == "app_started"
            with pytest.raises(HTTPError) as denied:
                urlopen(Request(server.origin + "/api/diagnostics", headers={
                    "Authorization": "Bearer " + server.token, "Origin": "https://example.invalid"}), timeout=5)
            assert denied.value.code == 403
        finally:
            server.shutdown()
            thread.join(5)
