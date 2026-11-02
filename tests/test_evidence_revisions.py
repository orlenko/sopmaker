import json
import sqlite3
import wave
import zipfile

import pytest

from sopmaker.credentials import Credentials
from sopmaker.exporter import export_notes
from sopmaker.models import DraftBatch
from sopmaker.store import Store
from sopmaker.worker import Engine


class Narrator:
    def __init__(self, prefix, fail_chunk=None, fail_draft=False):
        self.prefix = prefix
        self.fail_chunk = fail_chunk
        self.fail_draft = fail_draft
        self.calls = []

    def transcribe(self, path, *_):
        self.calls.append(path.name)
        index = int(path.stem.split("-")[-1])
        if index == self.fail_chunk:
            raise ValueError("Invented transcription failure")
        return {"text": f"{self.prefix} account, part {index}."}

    def draft(self, sources, *_):
        if self.fail_draft:
            raise ValueError("Invented draft failure")
        source_id, source = next(iter(sources.items()))
        value = {"title": f"{self.prefix} observation", "area": None, "equipment_identifiers": [],
                 "claims": [{"text": f"Inspector reported: {source['text']}", "basis": "inspector_narration",
                             "evidence": [{"source_id": source_id, "quote": source["text"]}]}],
                 "follow_ups": [], "open_questions": [], "tags": []}
        return DraftBatch.model_validate({"observations": [value]}), {"model": "invented-offline"}


def test_historical_transcripts_survive_changed_models_partial_failures_and_exports(store, tmp_path, monkeypatch):
    import sopmaker.media as media

    monkeypatch.setattr(media, "CHUNK_SAMPLES", 8000)
    audio = tmp_path / "invented.wav"
    with wave.open(str(audio), "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        output.writeframes(b"\x00\x10" * 16000)
    store.import_file(audio)
    aid = store.assets()[0]["id"]
    engine = Engine(store, Credentials(False))
    settings = store.settings()
    engine.process_one(store.asset(aid), settings, Narrator("First"))
    original = store.observations()[0]
    store.edit(original["id"], 1, original["note"], "reviewed", "Fixture reviewer")
    first_evidence = original["evidence"]
    assert len(first_evidence["transcript"]["chunks"]) == 2

    changed = settings | {"context": "new vocabulary", "transcription_model": "invented-model-2"}
    engine.process_one(store.asset(aid), changed, Narrator("Second"))
    latest = store.asset(aid)["transcript"]
    assert "Second" in latest["chunks"][0]["text"]
    retained = next(o for o in store.observations() if o["id"] == original["id"])
    assert retained["evidence"] == first_evidence
    assert latest["chunks"][0]["id"] != first_evidence["transcript"]["chunks"][0]["id"]
    assert store.observations("First")[0]["id"] == original["id"]
    with zipfile.ZipFile(export_notes(store, [original["id"]])) as archive:
        index = archive.read("index.html").decode()
        data = archive.read("observations.json").decode()
        assert "First account" in index and "Second account" not in index
        assert "First account" in data and "Second account" not in data

    third = changed | {"context": "third attempt"}
    with pytest.raises(ValueError, match="transcription failure"):
        engine.process_one(store.asset(aid), third, Narrator("Third", fail_chunk=1))
    assert store.asset(aid)["transcript"] == latest  # partial result was not published
    assert store.evidence(original["evidence_id"], aid) == first_evidence
    resumed = Narrator("Third")
    engine.process_one(store.asset(aid), third, resumed)
    assert resumed.calls == ["audio-001.wav"]  # completed first chunk is reused

    with pytest.raises(ValueError, match="draft failure"):
        engine.process_one(store.asset(aid), third | {"context": "fourth attempt"}, Narrator("Fourth", fail_draft=True))
    assert store.evidence(original["evidence_id"], aid) == first_evidence
    assert next(o for o in store.observations() if o["id"] == original["id"])["state"] == "reviewed"


def test_preparation_revisions_keep_old_files(store, photo):
    from test_notebook import FakeProvider

    store.import_file(photo)
    engine = Engine(store, Credentials(False))
    aid = store.assets()[0]["id"]
    engine.process_one(store.asset(aid), store.settings(), FakeProvider("fake"))
    first = store.observations()[0]
    store.edit(first["id"], 1, first["note"], "reviewed", "Fixture reviewer")
    before = first["evidence"]["prepared"]
    old_file = store.root / before["directory"] / before["images"][0]["file"]
    contents = old_file.read_bytes()
    engine.process_one(store.asset(aid), store.settings() | {"timezone": "America/Toronto"}, FakeProvider("fake"))
    after = store.asset(aid)["prepared"]
    assert before["directory"] != after["directory"]
    assert before["images"][0]["id"] != after["images"][0]["id"]
    assert old_file.read_bytes() == contents
    assert (store.root / after["directory"] / after["images"][0]["file"]).exists()
    (store.root / after["directory"] / after["images"][0]["file"]).unlink()
    engine.process_one(store.asset(aid), store.settings() | {"timezone": "America/Toronto"}, FakeProvider("fake"))
    regenerated = store.asset(aid)["prepared"]
    assert regenerated["directory"] != after["directory"]
    assert regenerated["images"][0]["id"] != after["images"][0]["id"]
    assert old_file.read_bytes() == contents


def test_version_one_migration_flags_uncertain_historical_inputs(tmp_path):
    root = tmp_path / "legacy"
    root.mkdir()
    with sqlite3.connect(root / "inspection.sqlite3") as db:
        db.executescript("""
            CREATE TABLE assets (id TEXT PRIMARY KEY, sha256 TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
                original TEXT NOT NULL, imported_at TEXT NOT NULL, state TEXT NOT NULL,
                error TEXT, prepared TEXT, transcript TEXT, fingerprint TEXT);
            CREATE TABLE observations (id TEXT PRIMARY KEY, asset_id TEXT NOT NULL,
                generated TEXT NOT NULL, note TEXT NOT NULL, state TEXT NOT NULL,
                revision INTEGER NOT NULL, updated_at TEXT NOT NULL, reviewer TEXT,
                archived INTEGER NOT NULL DEFAULT 0);
            PRAGMA user_version=1;
        """)
        db.execute("INSERT INTO assets VALUES(?,?,?,?,?,?,?,?,?,?)",
                   ("a_old", "invented-digest", "invented.wav", "originals/invented.wav", "then", "ready", None,
                    None, json.dumps({"chunks": [{"id": "old-reused-id", "text": "Later transcript"}]}), "old"))
        db.execute("INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?)",
                   ("o_old", "a_old", json.dumps({"title": "Old note", "claims": []}), "Earlier account", "reviewed", 2, "then", "Reviewer", 0))
    migrated = Store(root)
    note = migrated.observations()[0]
    assert note["note"] == "Earlier account" and note["state"] == "reviewed"
    assert note["evidence"]["provenance"] == "legacy_unverified"
    assert note["evidence"]["transcript"]["chunks"][0]["text"] == "Later transcript"
    with migrated.connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
    assert Store(root).observations()[0]["evidence_id"] == note["evidence_id"]


def test_shared_import_limit_prevents_cli_or_other_import_bypasses(store, photo, monkeypatch):
    import sopmaker.store as module

    monkeypatch.setattr(module, "MAX_FILE_BYTES", 1)
    with pytest.raises(ValueError, match="1 GB"):
        store.import_file(photo)
    assert not store.assets()
