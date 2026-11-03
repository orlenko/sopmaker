"""Invented notebook library fixtures; no model or personal filesystem inputs."""

import json
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

import pytest
from filelock import FileLock, Timeout
from test_notebook import FakeProvider, processed

from sopmaker.credentials import Credentials
from sopmaker.notebooks import MARKER, NotebookLibrary
from sopmaker.server import ReviewServer
from sopmaker.store import Conflict, Store
from sopmaker.worker import Engine


@contextmanager
def library_for(store):
    credentials = Credentials(False)
    credentials.set("invented-library-key")
    with ReviewServer(store, Engine(store, credentials, FakeProvider), credentials) as server:
        library = NotebookLibrary(server)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        try:
            yield library
        finally:
            server.shutdown()
            thread.join(5)
            library.close()


def request(url, path, body=None, token=None):
    parsed = urlparse(url)
    bearer = parse_qs(parsed.fragment)["token"][0] if token is None else token
    with urlopen(Request(f"http://{parsed.netloc}" + path,
                         data=json.dumps(body).encode() if body is not None else None,
                         headers={"Authorization": "Bearer " + bearer, "Content-Type": "application/json"}), timeout=5) as response:
        return json.loads(response.read())


def test_create_list_reopen_preserves_original_evidence_revisions_and_export(store, photo):
    from sopmaker.exporter import export_notes

    processed(store, photo)
    note = store.observations()[0]
    store.edit(note["id"], note["revision"], "First inspection correction", "reviewed", "Fixture inspector")
    exported = export_notes(store, [note["id"]])
    original_bytes = exported.read_bytes()
    original_history = store.history(note["id"])
    notebook_id = uuid.uuid4().hex
    with library_for(store) as library:
        first = library.parent.url
        second = request(first, "/api/notebooks", {"id": notebook_id, "name": "Second inspection"})["url"]
        blank = request(second, "/api/state")
        assert blank["assets"] == blank["observations"] == []
        assert blank["settings"]["context"] == "" and blank["settings"]["name"] == "Second inspection"
        assert blank["credentials"]["configured"]
        assert len(request(first, "/api/state")["observations"]) == 1
        # An old tab still writes to its original Store after a second is open.
        request(first, "/api/settings", {"name": "Original renamed"})
        assert request(second, "/api/state")["settings"]["name"] == "Second inspection"
        assert request(second, "/api/notebooks/open", {"id": "default"})["url"] == first
        assert library.create(notebook_id, "A retry must not rename") == second
        with pytest.raises(HTTPError) as denied:
            request(second, "/api/state", token=library.parent.token)
        assert denied.value.code == 403
        with pytest.raises(HTTPError):
            request(first, "/api/notebooks", token="")
        with pytest.raises(HTTPError):
            request(first, "/api/browser-link", token="")
        assert request(first, "/api/browser-link")["url"] == first
    # Closing/opening the library retains its index and complete original data.
    with library_for(store) as library:
        rows = library.notebooks("default")
        assert {row["name"] for row in rows} == {"Original renamed", "Second inspection"}
        assert request(library.open(notebook_id), "/api/state")["assets"] == []
    assert store.observations()[0]["revision"] == 2
    assert store.history(note["id"]) == original_history
    assert exported.read_bytes() == original_bytes
    assert (store.root / store.assets()[0]["original"]).read_bytes() == photo.read_bytes()


def test_parallel_create_and_cross_thread_lease_cleanup(store):
    notebook_id = uuid.uuid4().hex
    with library_for(store) as library:
        with ThreadPoolExecutor(max_workers=4) as pool:
            urls = list(pool.map(lambda _: library.create(notebook_id, "Concurrent notebook"), range(4)))
        assert len(set(urls)) == 1 and len(library.notebooks("default")) == 2
        lease_path = library.directory / notebook_id / ".sopmaker.lock"
        with pytest.raises(Timeout):
            FileLock(lease_path).acquire(timeout=0)
    with FileLock(lease_path).acquire(timeout=0):
        pass


def test_unavailable_notebook_is_not_repaired_or_replaced(store):
    notebook_id = uuid.uuid4().hex
    with library_for(store) as library:
        library.create(notebook_id, "Damaged fixture")
        path = library.directory / notebook_id
    with closing(sqlite3.connect(path / "inspection.sqlite3")) as db:
        with db:
            db.execute("DROP TABLE revisions")
    before = (path / "inspection.sqlite3").read_bytes()
    with library_for(store) as library:
        assert not library.notebooks("default")[1]["available"]
        with pytest.raises(sqlite3.Error):
            library.open(notebook_id)
    assert (path / "inspection.sqlite3").read_bytes() == before
    (path / "inspection.sqlite3").unlink()
    with library_for(store) as library:
        assert not library.notebooks("default")[1]["available"]
        with pytest.raises(ValueError):
            library.open(notebook_id)
    assert not (path / "inspection.sqlite3").exists()


def test_unowned_invalid_paths_and_staging_failure_preserve_state(store, monkeypatch):
    with library_for(store) as library:
        for value in ("../escape", "default/../escape", "A" * 32, None):
            with pytest.raises(ValueError):
                library.create(value, "Bad ID")
        notebook_id = uuid.uuid4().hex
        path = library.directory / notebook_id
        path.mkdir(parents=True)
        marker = path / "keep.txt"
        marker.write_text("invented unowned contents")
        with pytest.raises(ValueError):
            library.create(notebook_id, "Do not overwrite")
        assert marker.read_text() == "invented unowned contents"
        failed_id = uuid.uuid4().hex
        with monkeypatch.context() as patch:
            patch.setattr(Store, "save_settings", lambda *_: (_ for _ in ()).throw(OSError("invented failure")))
            with pytest.raises(OSError):
                library.create(failed_id, "Unfinished")
        assert (library.directory / (".creating-" + failed_id)).is_dir()
        assert not (library.directory / failed_id).exists()
        assert len(library.notebooks("default")) == 2  # Unowned entry remains visible as unavailable.


def test_limits_replay_and_failed_child_start_are_recoverable(store, monkeypatch):
    with library_for(store) as library:
        notebook_id = uuid.uuid4().hex
        first = library.create(notebook_id, "Retained")
        monkeypatch.setattr("sopmaker.notebooks.MAX_OPEN", 2)
        assert library.create(notebook_id, "Do not rename") == first
        saved_id = uuid.uuid4().hex
        with pytest.raises(Conflict, match="saved"):
            library.create(saved_id, "Saved but not opened")
        assert len(library.notebooks("default")) == 3
        monkeypatch.setattr("sopmaker.notebooks.MAX_OPEN", 16)
        with monkeypatch.context() as patch:
            patch.setattr(threading.Thread, "start", lambda _: (_ for _ in ()).throw(RuntimeError("invented start failure")))
            with pytest.raises(RuntimeError):
                library.open(saved_id)
        with FileLock(library.directory / saved_id / ".sopmaker.lock").acquire(timeout=0):
            with pytest.raises(Conflict, match="another Sopmaker"):
                library.open(saved_id)
        assert request(library.open(saved_id), "/api/state")["settings"]["name"] == "Saved but not opened"


def test_list_does_not_interrupt_processing_and_closing_rejects_late_admission(store, photo):
    with library_for(store) as library:
        notebook_id = uuid.uuid4().hex
        library.create(notebook_id, "Active")
        child = library.servers[notebook_id][0]
        child.store.import_file(photo)
        asset = child.store.assets()[0]
        child.store.update_asset(asset["id"], state="processing")
        library.notebooks("default")
        assert child.store.asset(asset["id"])["state"] == "processing"
        with library.lock:
            library.closing = True
        with pytest.raises(Conflict):
            with library.admission():
                child.engine.start()
        with pytest.raises(HTTPError) as denied:
            request(child.url, "/api/settings", {"name": "Too late"})
        assert denied.value.code == 409


@pytest.mark.parametrize("entry", [".notebooks", "originals", "inspection.sqlite3", MARKER, ".sopmaker.lock"])
def test_static_symlinks_are_not_followed(store, tmp_path, entry):
    target = tmp_path / "unrelated"
    target.mkdir()
    sentinel = target / "sentinel.txt"
    sentinel.write_text("invented unrelated state")
    with library_for(store) as library:
        notebook_id = uuid.uuid4().hex
        library.create(notebook_id, "Linked fixture")
    path = store.root / ".notebooks" / notebook_id
    location = store.root / entry if entry == ".notebooks" else path / entry
    if entry == ".sopmaker.lock":
        location.touch(exist_ok=True)  # Windows FileLock may remove this optional file on release.
    moved = location.with_name(location.name + ".retained")
    location.rename(moved)
    try:
        location.symlink_to(target if entry in (".notebooks", "originals") else sentinel, target_is_directory=entry in (".notebooks", "originals"))
    except OSError as exc:
        pytest.skip(f"Host does not permit symlinks: {type(exc).__name__}")
    with library_for(store) as library:
        with pytest.raises(ValueError):
            library.open(notebook_id)
    assert sentinel.read_text() == "invented unrelated state"


def test_shutdown_cancels_before_handler_drain_and_holds_writer_lease(store, photo, monkeypatch):
    from sopmaker.server import Handler

    drafting, finish_draft, handler_entered, finish_handler = (threading.Event() for _ in range(4))
    calls = []

    class Held(FakeProvider):
        def draft(self, *args):
            calls.append("draft")
            drafting.set()
            assert finish_draft.wait(8)
            return super().draft(*args)

    original_post = Handler._post

    def held_post(self, path, body):
        if path == "/api/settings":
            handler_entered.set()
            assert finish_handler.wait(8)
        return original_post(self, path, body)

    monkeypatch.setattr(Handler, "_post", held_post)
    with library_for(store) as library:
        notebook_id = uuid.uuid4().hex
        library.create(notebook_id, "Closing fixture")
        child = library.servers[notebook_id][0]
        # A recoverable start failure leaves an unstarted Thread on this engine.
        store.import_file(photo)
        with monkeypatch.context() as patch:
            patch.setattr(threading.Thread, "start", lambda _: (_ for _ in ()).throw(RuntimeError("invented start failure")))
            with pytest.raises(RuntimeError):
                library.parent.engine.start()
        assert library.parent.engine.thread.ident is None
        child.engine.provider_factory = Held
        child.store.import_file(photo)
        child.engine.start()
        assert drafting.wait(5)
        failures = []

        def delayed_settings():
            try:
                request(child.url, "/api/settings", {"name": "Saved before close finished"})
            except Exception as exc:
                failures.append(type(exc).__name__)

        handler = threading.Thread(target=delayed_settings)
        handler.start()
        assert handler_entered.wait(5)
        library.parent.shutdown()
        closer = threading.Thread(target=library.close)
        closer.start()
        try:
            assert child.engine.cancelled.wait(5)  # Not postponed by the held handler.
            assert closer.is_alive()
            with pytest.raises(Timeout):
                FileLock(child.store.root / ".sopmaker.lock").acquire(timeout=0)
            finish_handler.set()
            handler.join(5)
            assert not failures and not handler.is_alive()
            assert closer.is_alive()  # Actual writer is still in the fake provider.
            with pytest.raises(Timeout):
                FileLock(child.store.root / ".sopmaker.lock").acquire(timeout=0)
        finally:
            finish_handler.set()
            finish_draft.set()
            handler.join(5)
            closer.join(5)
        assert not closer.is_alive() and not child.engine.thread.is_alive()
        assert calls == ["draft"] and child.engine.progress["state"] == "cancelled"
        with FileLock(child.store.root / ".sopmaker.lock").acquire(timeout=0):
            pass


def test_handler_delayed_before_process_admission_cannot_start_after_close(store, photo, monkeypatch):
    from sopmaker.server import Handler

    entered, release = threading.Event(), threading.Event()
    original_post = Handler._post

    def held_post(self, path, body):
        if path == "/api/process":
            entered.set()
            assert release.wait(8)
        return original_post(self, path, body)

    monkeypatch.setattr(Handler, "_post", held_post)
    with library_for(store) as library:
        store.import_file(photo)
        codes = []

        def submit():
            try:
                request(library.parent.url, "/api/process", {})
            except HTTPError as exc:
                codes.append(exc.code)

        sender = threading.Thread(target=submit)
        sender.start()
        assert entered.wait(5)
        library.parent.shutdown()
        closer = threading.Thread(target=library.close)
        closer.start()
        try:
            # Closing is published before server_close drains this held handler.
            with library.lock:
                pass
            for _ in range(100):
                if library.closing:
                    break
                threading.Event().wait(0.01)
            assert library.closing
            release.set()
            sender.join(5)
            closer.join(5)
            assert codes == [409] and library.parent.engine.thread is None
        finally:
            release.set()
            sender.join(5)
            closer.join(5)


def test_windows_junction_container_is_refused(store, tmp_path):
    import subprocess
    import sys

    if sys.platform != "win32":
        pytest.skip("Windows-only junction admission; exercised by the Windows matrix.")
    target = tmp_path / "invented-junction-target"
    target.mkdir()
    with library_for(store) as library:
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(library.directory), str(target)],
                                capture_output=True, check=True)
        assert result.returncode == 0
        try:
            with pytest.raises(ValueError, match="filesystem link"):
                library.create(uuid.uuid4().hex, "Must not follow junction")
            assert list(target.iterdir()) == []
        finally:
            library.directory.rmdir()  # Removes only the junction, never its target.


def test_launcher_cleanup_does_not_depend_on_console_output(tmp_path, photo, monkeypatch):
    import signal
    import sys

    import sopmaker.app as application

    started = threading.Event()
    closing = threading.Event()
    observed = []
    root = tmp_path / "launcher-workspace"

    class Held(FakeProvider):
        def draft(self, *args):
            started.set()
            engine = observed[0]
            assert engine.cancelled.wait(5)
            with pytest.raises(Timeout):
                FileLock(root / ".sopmaker.lock").acquire(timeout=0)
            return super().draft(*args)

    class ClosedConsole:
        def write(self, text):
            if closing.is_set():
                raise BrokenPipeError("invented closed output")
            return len(text)

        def flush(self):
            if closing.is_set():
                raise BrokenPipeError("invented closed output")

    def serve(self, **_):
        self.credentials.set("invented-main-key")
        self.engine.provider_factory = Held
        self.store.import_file(photo)
        observed.append(self.engine)
        self.engine.start()
        assert started.wait(5)
        closing.set()
        raise KeyboardInterrupt

    previous_signal = signal.getsignal(signal.SIGINT)
    monkeypatch.setattr(sys, "stdout", ClosedConsole())
    monkeypatch.setattr(ReviewServer, "serve_forever", serve)
    monkeypatch.setattr(application, "log_directory", lambda: tmp_path / "logs")
    application.main(["serve", "--no-open", "--workspace", str(root)])
    assert not observed[0].thread.is_alive()
    assert observed[0].progress["state"] == "cancelled"
    assert signal.getsignal(signal.SIGINT) == previous_signal
    with FileLock(root / ".sopmaker.lock").acquire(timeout=0):
        pass
