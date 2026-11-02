import json
import threading
import wave
import zipfile
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import av
import pytest

from sopmaker.credentials import Credentials
from sopmaker.exporter import export_notes
from sopmaker.media import prepare
from sopmaker.metadata import capture_time
from sopmaker.models import DraftBatch, validate_evidence
from sopmaker.server import ReviewServer
from sopmaker.store import Conflict, Store
from sopmaker.worker import Engine


def batch(source_id):
    return DraftBatch.model_validate({"observations": [{
        "title": "Synthetic label", "area": None, "equipment_identifiers": ["C-17"],
        "claims": [{"text": "A green test image is visible.", "basis": "visible_detail",
                    "evidence": [{"source_id": source_id, "quote": None}]}],
        "follow_ups": [], "open_questions": [], "tags": ["fixture"]}]})


class FakeProvider:
    def __init__(self, _key):
        self.drafts = 0

    def draft(self, sources, directory, model, context):
        self.drafts += 1
        return batch(next(iter(sources))), {"model": "offline-fixture", "usage": {}}

    def transcribe(self, *_args):
        raise AssertionError("A photo must not invoke transcription")

    def close(self):
        pass


def processed(store, photo):
    store.import_file(photo)
    provider = FakeProvider("fake")
    Engine(store, Credentials(False)).process_one(store.assets()[0], store.settings(), provider)
    return provider


def test_photo_pipeline_search_and_deduplication(store, photo, tmp_path):
    provider = processed(store, photo)
    renamed = tmp_path / "renamed.png"
    renamed.write_bytes(photo.read_bytes())
    assert not store.import_file(renamed)
    assert len(store.assets()) == 1
    assert store.observations("C-17")[0]["state"] == "draft"
    assert store.observations('" OR *') == []
    assert store.asset(store.assets()[0]["id"])["state"] == "ready"
    Engine(store, Credentials(False)).process_one(store.assets()[0], store.settings(), provider)
    assert provider.drafts == 1


def test_revisions_conflict_and_reprocessing_preserve_review(store, photo):
    provider = processed(store, photo)
    note = store.observations()[0]
    updated = store.edit(note["id"], 1, "Human correction", "reviewed", "Test reviewer")
    assert updated["revision"] == 2
    with pytest.raises(Conflict):
        store.edit(note["id"], 1, "Stale edit", "reviewed", "Reviewer")
    settings = store.settings() | {"context": "Changed vocabulary"}
    Engine(store, Credentials(False)).process_one(store.assets()[0], settings, provider)
    assert provider.drafts == 2
    notes = store.observations()
    assert any(o["note"] == "Human correction" and o["state"] == "reviewed" for o in notes)
    assert store.history(note["id"])[0]["note"] == note["note"]


def test_invalid_evidence_and_false_quotes_rejected():
    data = batch("missing")
    with pytest.raises(ValueError, match="unknown source"):
        validate_evidence(data, {})
    claim = data.observations[0].claims[0]
    claim.basis = "inspector_narration"
    claim.evidence[0].quote = "Words never spoken"
    with pytest.raises(ValueError, match="absent"):
        validate_evidence(data, {"missing": {"kind": "transcript", "text": "Something else"}})
    claim.evidence[0].quote = None
    with pytest.raises(ValueError, match="supporting transcript quote"):
        validate_evidence(data, {"missing": {"kind": "transcript", "text": "Something else"}})


@pytest.mark.parametrize("raw,zone,basis", [
    ("2026:11:02 09:00:00", "America/Toronto", "assumed_timezone"),
    ("2026-11-01T01:30:00", "America/Toronto", "ambiguous_local_time"),
    ("2026-03-08T02:30:00", "America/Toronto", "ambiguous_local_time"),
    ("2026-11-02T09:00:00-05:00", "UTC", "embedded_offset"),
    (None, "UTC", "unknown"), ("not a date", "UTC", "invalid_metadata"),
])
def test_capture_time_ambiguity(raw, zone, basis):
    result = capture_time(raw, zone)
    assert result["basis"] == basis
    if basis in ("ambiguous_local_time", "unknown", "invalid_metadata"):
        assert result["utc"] is None


def test_interrupted_job_requires_explicit_retry(store, photo):
    store.import_file(photo)
    asset = store.assets()[0]
    store.update_asset(asset["id"], state="processing")
    reopened = Store(store.root)
    assert reopened.asset(asset["id"])["state"] == "interrupted"


def test_export_is_portable_and_drafts_are_explicit(store, photo, tmp_path):
    processed(store, photo)
    note = store.observations()[0]
    with pytest.raises(ValueError, match="Review selected"):
        export_notes(store, [note["id"]])
    output = export_notes(store, [note["id"]], include_drafts=True)
    with zipfile.ZipFile(output) as archive:
        assert "notes.docx" in archive.namelist()
        assert any(name.startswith("originals/") for name in archive.namelist())
        for name in ("index.html", "observations.json"):
            assert str(store.root) not in archive.read(name).decode()
        assert "draft" in archive.read("index.html").decode()
        moved = tmp_path / "another computer"
        archive.extractall(moved)
    for asset in store.assets():
        assert (moved / asset["original"]).exists()


def test_silent_audio_and_video_are_preserved_without_fabricated_narration(tmp_path):
    audio = tmp_path / "silence.wav"
    with wave.open(str(audio), "wb") as file:
        file.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        file.writeframes(b"\0" * 32000)
    assert prepare(audio, tmp_path / "audio", "a_silent", "UTC")["audio"] == []
    video = tmp_path / "silent.mp4"
    from PIL import Image
    with av.open(str(video), "w") as output:
        stream = output.add_stream("mpeg4", rate=10)
        stream.width, stream.height = 160, 120
        stream.pix_fmt = "yuv420p"
        for i in range(20):
            frame = av.VideoFrame.from_image(Image.new("RGB", (160, 120), (i * 10, 50, 100)))
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
    prepared = prepare(video, tmp_path / "video", "a_video", "UTC")
    assert prepared["kind"] == "video"
    assert prepared["audio"] == []
    assert 1 <= len(prepared["images"]) <= 12
    assert all(0 <= frame["seconds"] <= 2 for frame in prepared["images"])


def test_credentials_default_to_memory_and_never_appear_in_database(store, photo):
    credentials = Credentials(False)
    credentials.set("invented-test-credential")
    assert credentials.origin == "session"
    processed(store, photo)
    for file in store.root.rglob("*"):
        if file.is_file():
            assert b"invented-test-credential" not in file.read_bytes()
    credentials.clear_session()
    assert not credentials.key


def test_loopback_auth_origin_upload_range_and_key_redaction(store, photo):
    credentials = Credentials(False)
    engine = Engine(store, credentials, FakeProvider)
    with ReviewServer(store, engine, credentials) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(path, data=None, method=None, **headers):
            return urlopen(Request(server.origin + path, data=data, method=method,
                                   headers={"Authorization": "Bearer " + server.token, **headers}), timeout=5)
        try:
            with pytest.raises(HTTPError) as denied:
                urlopen(server.origin + "/api/state", timeout=5)
            assert denied.value.code == 403
            with pytest.raises(HTTPError) as cross_origin:
                request("/api/key", b'{"key":"invented-test-credential"}', Origin="https://example.invalid", **{"Content-Type": "application/json"})
            assert cross_origin.value.code == 403
            request("/api/key", b'{"key":"invented-test-credential"}', **{"Content-Type": "application/json"}).close()
            result = request("/api/state").read()
            assert b"invented-test-credential" not in result
            assert json.loads(result)["credentials"]["configured"]
            with request("/app.js") as script:
                assert script.headers["Content-Type"] == "application/javascript"
            request("/api/files?name=synthetic.png", photo.read_bytes(), method="PUT").close()
            aid = store.assets()[0]["id"]
            partial = request(f"/media/{aid}/original", Range="bytes=0-9")
            assert partial.status == 206 and partial.read() == photo.read_bytes()[:10]
            with pytest.raises(HTTPError):
                request(f"/media/{aid}/../../inspection.sqlite3")
        finally:
            server.shutdown()
            thread.join(timeout=5)


def test_failed_file_does_not_prevent_other_files(store, photo, tmp_path):
    bad = tmp_path / "broken.png"
    bad.write_bytes(b"not a picture")
    store.import_file(bad)
    store.import_file(photo)
    credentials = Credentials(False)
    credentials.set("invented-test-credential")
    engine = Engine(store, credentials, FakeProvider)
    engine.start()
    engine.thread.join(timeout=10)
    assert not engine.thread.is_alive()
    assert {a["state"] for a in store.assets()} == {"error", "ready"}
    assert len(store.observations()) == 1
