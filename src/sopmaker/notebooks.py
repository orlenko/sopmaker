"""A small local library. Each opened notebook keeps its own server and writer lease."""

import json
import re
import sqlite3
import stat
import threading
from contextlib import closing, contextmanager
from datetime import datetime

from filelock import FileLock, Timeout

from .server import ReviewServer
from .store import Conflict, Store, now
from .worker import Engine

MAX_NOTEBOOKS = 100
MAX_OPEN = 16
MARKER = ".sopmaker-notebook.json"
TABLES = {
    "settings": "key,value",
    "assets": "id,sha256,name,original,imported_at,state,error,prepared,transcript,fingerprint",
    "observations": "id,asset_id,generated,note,state,revision,updated_at,reviewer,archived,evidence_id",
    "revisions": "observation_id,revision,snapshot,created_at",
    "runs": "id,asset_id,created_at,response",
    "observation_search": "observation_id,text",
    "evidence_revisions": "id,asset_id,created_at,prepared,transcript,provenance",
    "transcript_chunks": "revision_id,asset_id,chunk_index,payload",
}


def ordinary(path, directory=False, required=True):
    try:
        info = path.lstat()
    except FileNotFoundError:
        if not required:
            return False
        raise ValueError("A notebook file is missing. Keep the folder intact; nothing was reset.") from None
    if (stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))):
        raise ValueError("A notebook contains an unexpected file or filesystem link. Nothing was changed.")
    return True


def metadata(root):
    """Read without Store initialization, schema repair, or interrupted-job recovery."""
    with closing(sqlite3.connect(root.joinpath("inspection.sqlite3").as_uri() + "?mode=ro",
                                 uri=True, timeout=0.1)) as db:
        if db.execute("PRAGMA user_version").fetchone()[0] != 2:
            raise ValueError("This notebook needs a compatible Sopmaker version.")
        for table, columns in TABLES.items():
            db.execute(f"SELECT {columns} FROM {table} LIMIT 0")
        settings = {key: json.loads(value) for key, value in db.execute("SELECT key,value FROM settings")}
        if any(not isinstance(settings.get(key), str) for key in Store.defaults()):
            raise ValueError("Notebook details are incomplete. Nothing was reset.")
        return settings["name"]


class NotebookLibrary:
    def __init__(self, server):
        self.parent = server
        self.directory = server.store.root / ".notebooks"
        self.lock = threading.RLock()
        self.closing = False
        self.servers = {"default": (server, None, None)}
        self._attach(server, "default")

    def _attach(self, server, notebook_id):
        server.library, server.notebook_id = self, notebook_id
        # server_close must drain handlers before any workspace lease is released.
        server.daemon_threads = False

    @contextmanager
    def admission(self):
        with self.lock:
            if self.closing:
                raise Conflict("Sopmaker is closing. Finish closing it, then use the launcher.")
            yield

    def _paths(self):
        if not ordinary(self.directory, directory=True, required=False):
            return []
        paths = sorted(p for p in self.directory.iterdir() if re.fullmatch(r"[a-f0-9]{32}", p.name))
        if len(paths) >= MAX_NOTEBOOKS:
            raise ValueError("The notebook library exceeds this POC's 100-notebook limit.")
        return paths

    def _inspect(self, path):
        ordinary(self.directory, directory=True)
        ordinary(path, directory=True)
        for name in ("originals", "derived", "exports"):
            ordinary(path / name, directory=True)
        for name in (MARKER, "inspection.sqlite3"):
            ordinary(path / name)
        for name in (".sopmaker.lock", "inspection.sqlite3-wal", "inspection.sqlite3-shm", "inspection.sqlite3-journal"):
            ordinary(path / name, required=False)
        marker_path = path / MARKER
        if marker_path.stat().st_size > 512:
            raise ValueError("Unrecognized notebook marker. Nothing was changed.")
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        if not isinstance(marker, dict) or marker.get("format") != 1 or marker.get("id") != path.name:
            raise ValueError("Unrecognized notebook marker. Nothing was changed.")
        created = datetime.fromisoformat(marker["created_at"]).isoformat()
        return {"name": metadata(path), "created_at": created}

    def notebooks(self, current):
        with self.admission():
            paths = self._paths()
        # SQLite busy waits and filesystem reads must not hold the admission mutex.
        rows = [{"id": "default", "name": metadata(self.parent.store.root),
                 "detail": "Original notebook", "available": True, "current": current == "default"}]
        for path in paths:
            row = {"id": path.name, "name": "Unavailable notebook", "detail": path.name[:8],
                   "available": False, "current": current == path.name}
            try:
                info = self._inspect(path)
                row.update(name=info["name"], detail=info["created_at"][:16].replace("T", " ") + " UTC · " + path.name[:8], available=True)
            except (ValueError, OSError, sqlite3.Error, KeyError, TypeError):
                row["error"] = "Cannot open this notebook. Keep its folder intact; nothing was reset."
            rows.append(row)
        return rows

    @staticmethod
    def _identity(notebook_id):
        if not isinstance(notebook_id, str) or not re.fullmatch(r"[a-f0-9]{32}", notebook_id):
            raise ValueError("Choose a notebook from the list.")

    def create(self, notebook_id, name):
        self._identity(notebook_id)
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise ValueError("Enter a notebook name of 1 to 120 characters.")
        with self.admission():
            paths = self._paths()
            path = self.directory / notebook_id
            # The UUID identifies the request, even after its name has been edited.
            if path in paths:
                return self.open(notebook_id)
            if len(paths) + 1 >= MAX_NOTEBOOKS:
                raise ValueError("This library already has 100 notebooks. Use an existing notebook.")
            self.directory.mkdir(exist_ok=True)
            ordinary(self.directory, directory=True)
            staging = self.directory / (".creating-" + notebook_id)
            staging.mkdir()  # Never overwrite or remove a previous failed creation.
            store = Store(staging)
            store.save_settings({"name": name.strip()})
            (staging / MARKER).write_text(json.dumps({"format": 1, "id": notebook_id, "created_at": now()}), encoding="utf-8")
            staging.rename(path)
            try:
                return self.open(notebook_id)
            except (ValueError, OSError, sqlite3.Error):
                raise Conflict("The notebook was saved, but could not be opened. Refresh the notebook list to open it; finish work and restart Sopmaker if its open-notebook limit was reached.") from None

    def open(self, notebook_id):
        with self.admission():
            if notebook_id in self.servers:
                return self.servers[notebook_id][0].url
            self._identity(notebook_id)
            path = self.directory / notebook_id
            self._inspect(path)
            if len(self.servers) >= MAX_OPEN:
                raise Conflict("16 notebooks are already open. Finish your work, close Sopmaker, then launch it again to open more.")
            lease = FileLock(path / ".sopmaker.lock", thread_local=False)
            try:
                lease.acquire(timeout=0)
            except Timeout:
                raise Conflict("This notebook is open in another Sopmaker process. Close that process before opening it here.") from None
            server = None
            try:
                self._inspect(path)  # The list was advisory; revalidate under the lease.
                store = Store(path)
                engine = Engine(store, self.parent.credentials, self.parent.engine.provider_factory,
                                self.parent.engine.diagnostics)
                server = ReviewServer(store, engine, self.parent.credentials)
                self._attach(server, notebook_id)
                thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
                thread.start()
                self.servers[notebook_id] = (server, thread, lease)
                return server.url
            except BaseException:
                if server:
                    server.server_close()
                lease.release()
                raise

    def close(self):
        """Owner has ended the parent serving loop. Never release a live writer's lease."""
        with self.lock:
            self.closing = True
            opened = list(self.servers.values())
        for server, _, _ in opened:
            server.engine.stop()
        for server, thread, _ in opened:
            if thread:
                server.shutdown()
                thread.join()
        for server, _, _ in opened:
            server.server_close()
        for server, _, _ in opened:
            server.engine.stop()
            if server.engine.thread and server.engine.thread.ident is not None:
                server.engine.thread.join()
        for _, _, lease in opened:
            if lease:
                lease.release()
