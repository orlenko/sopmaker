"""Small, allowlisted diagnostics; never capture general logging or exception text."""

import json
import os
import queue
import re
import sys
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from filelock import FileLock
from openai import APIConnectionError, APIStatusError, APITimeoutError

from . import __version__

MODELS = {"gpt-6.1-sol", "gpt-6.1-terra", "gpt-5.4-mini", "gpt-transcribe"}
STAGES = {"starting", "preparing", "transcribing", "drafting", "saving", "packaging", "closing", "finished"}
EVENTS = {"app_started", "run_started", "stage_started", "stage_finished", "waiting",
          "provider_response", "file_finished", "file_failed", "cancel_requested", "run_finished", "local_error"}
STATES = {"idle", "running", "completed", "failed", "cancelled", "empty"}
CATEGORIES = {"timeout", "connection", "authentication", "rate_limit", "api", "validation", "local", "unexpected"}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def log_directory():
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Sopmaker" / "Logs"
    if os.environ.get("XDG_STATE_HOME"):
        return Path(os.environ["XDG_STATE_HOME"]) / "sopmaker" / "logs"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / "Sopmaker"
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "sopmaker" / "logs"


def error_category(exc):
    if isinstance(exc, APITimeoutError):
        return "timeout"
    if isinstance(exc, APIConnectionError):
        return "connection"
    if isinstance(exc, APIStatusError):
        return {401: "authentication", 403: "authentication", 429: "rate_limit"}.get(exc.status_code, "api")
    if isinstance(exc, ValueError):
        return "validation"
    return "local" if isinstance(exc, OSError) else "unexpected"


def safe_fields(values):
    result = {}
    for key, value in values.items():
        if key == "model" and isinstance(value, str):
            result[key] = value if value in MODELS else "custom"
        elif key in ("run_id", "instance_id") and isinstance(value, str) and re.fullmatch(r"[a-f0-9]{32}", value):
            result[key] = value
        elif key == "asset_id" and isinstance(value, str) and re.fullmatch(r"a_[a-f0-9]{24}", value):
            result[key] = value
        elif key == "request_id" and isinstance(value, str) and re.fullmatch(r"req_[a-zA-Z0-9_-]{1,100}", value):
            result[key] = value
        elif isinstance(value, str) and ((key == "stage" and value in STAGES)
                                        or (key == "state" and value in STATES)
                                        or (key == "category" and value in CATEGORIES)):
            result[key] = value
        elif key in ("completed", "failed", "total", "notes", "sequence") and type(value) is int and 0 <= value <= 1_000_000:
            result[key] = value
        elif key in ("duration_s", "elapsed_s") and type(value) in (float, int) and 0 <= value <= 1_000_000_000:
            result[key] = round(value, 2)
        elif key == "http_status" and type(value) is int and 100 <= value <= 599:
            result[key] = value
    return result


class Diagnostics:
    def __init__(self, directory=None, console=False, max_bytes=512_000):
        self.directory = Path(directory) if directory is not None else None
        self.console, self.max_bytes = console, max_bytes
        self.events = deque(maxlen=300)
        self.lock = threading.Lock()
        self.write_failed = False
        self.dropped_events = 0
        self.closed = False
        self.pending = queue.Queue(maxsize=128)
        self.stopping = threading.Event()
        self.writer = None
        self.console_fd = None
        if self.console:
            try:
                # A daemon must never hold CPython's process-global buffered
                # stderr lock into interpreter finalization. Own a raw duplicate.
                self.console_fd = os.dup(sys.stderr.fileno())
            except (OSError, ValueError, AttributeError):
                self.write_failed = True
        if self.console or self.directory is not None:
            try:
                self.writer = threading.Thread(target=self._drain, name="sopmaker-diagnostics", daemon=True)
                self.writer.start()
            except Exception:
                self.writer = None
                self.write_failed = True
                if self.console_fd is not None:
                    os.close(self.console_fd)
                    self.console_fd = None

    def emit(self, event, **fields):
        # Diagnostics must never alter processing or publication outcomes.
        try:
            if event not in EVENTS:
                return
            record = {"time": utc_now(), "event": event, **safe_fields(fields)}
            line = json.dumps(record, ensure_ascii=True) + "\n"
            with self.lock:
                self.events.append(record)
                if self.console or self.directory is not None:
                    if self.closed or self.writer is None:
                        self.write_failed = True
                        self.dropped_events += 1
                    else:
                        try:
                            self.pending.put_nowait(line)
                        except queue.Full:
                            self.write_failed = True
                            self.dropped_events += 1
        except Exception:
            self._failed()

    def _failed(self):
        with self.lock:
            self.write_failed = True
            self.dropped_events += 1

    def _drain(self):
        try:
            while not (self.stopping.is_set() and self.pending.empty()):
                try:
                    line = self.pending.get(timeout=0.05)
                except queue.Empty:
                    continue
                try:
                    # No memory/counter/worker locks are held across sink I/O.
                    self._write(line)
                except Exception:
                    self._failed()
                finally:
                    self.pending.task_done()
        finally:
            if self.console_fd is not None:
                try:
                    os.close(self.console_fd)
                except OSError:
                    pass

    def _write(self, line):
        if self.console:
            try:
                if self.console_fd is None:
                    raise OSError("Console unavailable")
                remaining = memoryview(line.encode("utf-8"))
                while remaining:
                    written = os.write(self.console_fd, remaining)
                    if written <= 0:
                        raise OSError("Console unavailable")
                    remaining = remaining[written:]
            except (OSError, ValueError):
                self._failed()
        if self.directory is not None:
            self.directory.mkdir(parents=True, exist_ok=True)
            file = self.directory / "diagnostics.jsonl"
            # All notebook processes serialize short-lived rotation/append handles.
            with FileLock(self.directory / "diagnostics.lock", timeout=0.1):
                if file.exists() and file.stat().st_size + len(line.encode()) > self.max_bytes:
                    (self.directory / "diagnostics.2.jsonl").unlink(missing_ok=True)
                    previous = self.directory / "diagnostics.1.jsonl"
                    if previous.exists():
                        previous.replace(self.directory / "diagnostics.2.jsonl")
                    file.replace(previous)
                with file.open("a", encoding="utf-8") as stream:
                    stream.write(line)

    def close(self, timeout=0.2):
        with self.lock:
            self.closed = True
        self.stopping.set()
        if self.writer is not None:
            self.writer.join(timeout=min(0.2, max(0, timeout)))
            if self.writer.is_alive():
                with self.lock:
                    self.write_failed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def info(self):
        with self.lock:
            return {"path": str(self.directory / "diagnostics.jsonl") if self.directory else None,
                    "write_failed": self.write_failed, "dropped_events": self.dropped_events}

    def download(self):
        # No filesystem reads, arbitrary paths, launcher output or provider bodies.
        with self.lock:
            return {"app_version": __version__, "platform": sys.platform,
                    "scope": "Last 300 diagnostic events from this app session only; no inspection content.",
                    "log_write_failed": self.write_failed, "dropped_events": self.dropped_events,
                    "events": [dict(event) for event in self.events]}
