"""Inspection persistence. SQLite transactions own jobs, notes and revision history."""

import hashlib
import json
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .media import SUPPORTED
from .models import note_text


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        for name in ("originals", "derived", "exports"):
            (self.root / name).mkdir(exist_ok=True)
        self.path = self.root / "inspection.sqlite3"
        with self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError("This workspace needs a newer Sopmaker version.")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS assets (
                    id TEXT PRIMARY KEY, sha256 TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
                    original TEXT NOT NULL, imported_at TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'imported', error TEXT,
                    prepared TEXT, transcript TEXT, fingerprint TEXT);
                CREATE TABLE IF NOT EXISTS observations (
                    id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id),
                    generated TEXT NOT NULL, note TEXT NOT NULL, state TEXT NOT NULL,
                    revision INTEGER NOT NULL, updated_at TEXT NOT NULL, reviewer TEXT,
                    archived INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS revisions (
                    observation_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    snapshot TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(observation_id, revision));
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id),
                    created_at TEXT NOT NULL, response TEXT NOT NULL);
                CREATE VIRTUAL TABLE IF NOT EXISTS observation_search USING fts5(
                    observation_id UNINDEXED, text, tokenize='unicode61');
                PRAGMA user_version=1;
            """)
            for key, value in self.defaults().items():
                db.execute("INSERT OR IGNORE INTO settings VALUES (?, ?)", (key, json.dumps(value)))
            # A previous process may have stopped during an API call. Never retry it automatically.
            db.execute("UPDATE assets SET state='interrupted', error=? WHERE state='processing'",
                       ("Processing was interrupted. Review and explicitly retry this file.",))

    @staticmethod
    def defaults() -> dict:
        return {"name": "Inspection notebook", "timezone": "UTC", "context": "",
                "vision_model": "gpt-5.4-mini", "transcription_model": "gpt-transcribe"}

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def settings(self) -> dict:
        with self.connect() as db:
            return {r["key"]: json.loads(r["value"]) for r in db.execute("SELECT * FROM settings")}

    def save_settings(self, values: dict) -> None:
        if set(values) - set(self.defaults()):
            raise ValueError("Unknown setting.")
        for key, value in values.items():
            if not isinstance(value, str) or len(value) > 4000:
                raise ValueError("Invalid setting value.")
            if key == "timezone":
                ZoneInfo(value)
            if key in ("vision_model", "transcription_model", "name") and not value.strip():
                raise ValueError("A name or model cannot be empty.")
        with self.connect() as db:
            db.executemany("UPDATE settings SET value=? WHERE key=?",
                           [(json.dumps(v), k) for k, v in values.items()])

    def import_folder(self, folder: Path) -> dict:
        folder = folder.expanduser().resolve()
        if not folder.is_dir() or folder == self.root or self.root in folder.parents:
            raise ValueError("Choose an input folder outside this inspection workspace.")
        imported, duplicates, skipped, errors = 0, 0, 0, []
        for source in sorted(folder.iterdir()):
            if not source.is_file() or source.is_symlink() or source.suffix.lower() not in SUPPORTED:
                skipped += 1
                continue
            try:
                if self.import_file(source):
                    imported += 1
                else:
                    duplicates += 1
            except (OSError, ValueError):
                errors.append({"name": source.name, "error": "Could not safely copy this file."})
        return dict(imported=imported, duplicates=duplicates, skipped=skipped, errors=errors)

    def import_file(self, source: Path, display_name: str | None = None) -> bool:
        if source.suffix.lower() not in SUPPORTED:
            raise ValueError("Unsupported file type.")
        with source.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        asset_id = "a_" + digest[:24]
        relative = f"originals/{asset_id}{source.suffix.lower()}"
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM assets WHERE sha256=?", (digest,)).fetchone():
                return False
            temporary = (self.root / relative).with_suffix(".importing")
            try:
                shutil.copyfile(source, temporary)
                with temporary.open("rb") as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                        raise ValueError("File changed during import; try it again.")
                temporary.replace(self.root / relative)
            finally:
                temporary.unlink(missing_ok=True)
            db.execute("INSERT INTO assets(id,sha256,name,original,imported_at) VALUES(?,?,?,?,?)",
                       (asset_id, digest, display_name or source.name, relative, now()))
        return True

    def assets(self) -> list[dict]:
        with self.connect() as db:
            return [self._asset(r) for r in db.execute("SELECT * FROM assets ORDER BY imported_at,id")]

    def asset(self, asset_id: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        if not row:
            raise ValueError("Unknown source file.")
        return self._asset(row)

    @staticmethod
    def _asset(row) -> dict:
        result = dict(row)
        for key in ("prepared", "transcript"):
            result[key] = json.loads(result[key]) if result[key] else None
        return result

    def update_asset(self, asset_id: str, **values) -> None:
        if not values or set(values) - {"state", "error", "prepared", "transcript", "fingerprint"}:
            raise ValueError("Invalid source update.")
        for key in ("prepared", "transcript"):
            if key in values:
                values[key] = json.dumps(values[key])
        with self.connect() as db:
            db.execute("UPDATE assets SET " + ",".join(f"{k}=?" for k in values) + " WHERE id=?",
                       [*values.values(), asset_id])

    def add_drafts(self, asset_id: str, drafts: list[dict], response: dict, fingerprint: str) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO runs VALUES(?,?,?,?)",
                       (uuid.uuid4().hex, asset_id, now(), json.dumps(response)))
            # Preserve all prior notes; archive earlier unreviewed drafts instead of overwriting.
            db.execute("UPDATE observations SET archived=1 WHERE asset_id=? AND state='draft'",
                       (asset_id,))
            for draft in drafts:
                oid = "o_" + uuid.uuid4().hex
                text = note_text(draft)
                db.execute("INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,0)",
                           (oid, asset_id, json.dumps(draft), text, "draft", 1, now(), None))
                self._index(db, oid, asset_id, draft, text)
            db.execute("UPDATE assets SET state='ready', error=NULL, fingerprint=? WHERE id=?",
                       (fingerprint, asset_id))

    @staticmethod
    def _index(db, oid, asset_id, draft, text):
        asset = db.execute("SELECT name,transcript FROM assets WHERE id=?", (asset_id,)).fetchone()
        searchable = " ".join([asset["name"], asset["transcript"] or "", json.dumps(draft), text])
        db.execute("DELETE FROM observation_search WHERE observation_id=?", (oid,))
        db.execute("INSERT INTO observation_search VALUES(?,?)", (oid, searchable))

    def observations(self, query: str = "", include_archived: bool = False) -> list[dict]:
        params = []
        sql = "SELECT o.* FROM observations o"
        if query.strip():
            # Literal terms, not arbitrary FTS syntax; quotes and punctuation are safe.
            terms = query.split()[:30]
            expression = " AND ".join('"' + term.replace('"', '""') + '"' for term in terms)
            sql += " JOIN observation_search s ON s.observation_id=o.id WHERE s.text MATCH ?"
            params.append(expression)
        else:
            sql += " WHERE 1=1"
        if not include_archived:
            sql += " AND o.archived=0"
        with self.connect() as db:
            result = [self._observation(row) for row in db.execute(sql + " ORDER BY o.updated_at,o.id", params)]
        return result

    @staticmethod
    def _observation(row) -> dict:
        result = dict(row)
        result["generated"] = json.loads(result["generated"])
        return result

    def edit(self, oid: str, revision: int, note: str, state: str, reviewer: str) -> dict:
        if state not in ("draft", "reviewed") or not isinstance(note, str) or len(note) > 30000:
            raise ValueError("Invalid observation edit.")
        if state == "reviewed" and not reviewer.strip():
            raise ValueError("Enter a reviewer name before marking this note reviewed.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM observations WHERE id=?", (oid,)).fetchone()
            if not row:
                raise ValueError("Unknown observation.")
            if row["revision"] != revision:
                raise Conflict("This note changed in another window. Reload before saving.")
            db.execute("INSERT INTO revisions VALUES(?,?,?,?)",
                       (oid, revision, json.dumps(dict(row)), now()))
            db.execute("UPDATE observations SET note=?,state=?,reviewer=?,revision=revision+1,updated_at=? WHERE id=?",
                       (note, state, reviewer.strip() or None, now(), oid))
            self._index(db, oid, row["asset_id"], json.loads(row["generated"]), note)
            return self._observation(db.execute("SELECT * FROM observations WHERE id=?", (oid,)).fetchone())

    def history(self, oid: str) -> list[dict]:
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT snapshot FROM revisions WHERE observation_id=? ORDER BY revision DESC", (oid,))]
