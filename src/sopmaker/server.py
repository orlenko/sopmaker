"""Authenticated loopback review server. No public bind, request logging or filesystem browsing."""

import hmac
import json
import mimetypes
import re
import secrets
import tempfile
from contextlib import nullcontext
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .diagnostics import error_category
from .exporter import export_notes
from .media import SUPPORTED
from .model_settings import MODEL_OPTIONS
from .store import MAX_FILE_BYTES, Conflict
from .timezones import timezone_options

WEB = Path(__file__).parent / "web"
MAX_UPLOAD = MAX_FILE_BYTES


class ReviewServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, store, engine, credentials, port: int = 0):
        super().__init__(("127.0.0.1", port), Handler)
        self.store, self.engine, self.credentials = store, engine, credentials
        self.library = None
        self.token = secrets.token_urlsafe(32)
        self.cookie_name = "sopmaker_" + self.token[:12]
        self.origin = f"http://127.0.0.1:{self.server_port}"

    @property
    def url(self):
        return self.origin + "/#token=" + self.token


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(60)

    def _send(self, status, data, mime="application/json", headers=None):
        if not isinstance(data, bytes):
            data = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self._headers(mime)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _headers(self, mime):
        self.send_header("Content-Type", mime)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self'; media-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")

    def _authorized(self):
        if self.headers.get("Host") != self.server.origin.removeprefix("http://"):
            return False
        if self.headers.get("Origin", self.server.origin) != self.server.origin:
            return False
        token = self.headers.get("Authorization", "").removeprefix("Bearer ")
        if not token:
            cookies = SimpleCookie()
            try:
                cookies.load(self.headers.get("Cookie", ""))
                token = cookies[self.server.cookie_name].value if self.server.cookie_name in cookies else ""
            except Exception:
                return False
        return hmac.compare_digest(token, self.server.token)

    def _json(self):
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Expected a JSON request.")
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 1_000_000:
            raise ValueError("Invalid request size.")
        body = json.loads(self.rfile.read(length))
        if not isinstance(body, dict):
            raise ValueError("Expected an object.")
        return body

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def do_PUT(self):
        self._handle()

    def _handle(self):
        path = urlparse(self.path)
        try:
            if self.command == "GET" and path.path in ("/", "/app.js", "/style.css"):
                name = "index.html" if path.path == "/" else path.path[1:]
                mime = {"index.html": "text/html", "app.js": "application/javascript", "style.css": "text/css"}[name]
                return self._send(200, (WEB / name).read_bytes(), mime)
            if not self._authorized():
                return self._send(403, {"error": "Open this window from the Sopmaker launcher to start a session. For another browser, use Copy browser link in an open Sopmaker window, or the complete Open link in the launcher console. The address-bar URL alone does not sign you in."})
            if self.server.library:
                with self.server.library.admission():
                    pass
            if self.command == "GET":
                return self._get(path)
            if self.command == "PUT" and path.path == "/api/files":
                return self._upload(path)
            if self.command == "POST":
                return self._post(path.path, self._json())
            self._send(404, {"error": "Not found."})
        except Conflict as exc:
            self._send(409, {"error": str(exc)})
        except ValueError as exc:
            self._send(400, {"error": str(exc)})
        except (KeyError, TypeError):
            self._send(400, {"error": "The request could not be applied. Check inputs, reviewer name, and setup."})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            self.server.engine.diagnostics.emit("local_error", category=error_category(exc))
            self._send(500, {"error": "The local operation failed. Your originals and saved notes are preserved."})

    def _get(self, path):
        store = self.server.store
        query = parse_qs(path.query)
        if path.path == "/api/browser-link":
            return self._send(200, {"url": self.server.url})
        if path.path == "/api/notebooks":
            if not self.server.library:
                raise ValueError("Use the Sopmaker launcher to open the notebook library.")
            return self._send(200, {"notebooks": self.server.library.notebooks(self.server.notebook_id)})
        if path.path == "/api/state":
            settings = store.settings()
            # A terminal snapshot must not accompany notes/assets read before
            # completion, since the browser stops polling on terminal state.
            for _ in range(3):
                before = self.server.engine.progress
                assets = store.assets()
                observations = store.observations(query.get("q", [""])[0], query.get("archived") == ["1"])
                progress = self.server.engine.progress
                if before["sequence"] == progress["sequence"]:
                    return self._send(200, {"settings": settings, "assets": assets,
                                            "observations": observations, "progress": progress,
                                            "model_options": MODEL_OPTIONS,
                                            "timezone_options": timezone_options(), "timezone_origin": store.timezone_origin(),
                                            "diagnostics": self.server.engine.diagnostics.info(),
                                            "credentials": {"configured": bool(self.server.credentials.key), "origin": self.server.credentials.origin}})
            return self._send(503, {"error": "Status changed while it was being read. Check status again; nothing was resubmitted."})
        if path.path.startswith("/api/runs/"):
            run_id = path.path.rsplit("/", 1)[1]
            if not re.fullmatch(r"[a-f0-9]{32}", run_id):
                raise ValueError("Invalid run identity.")
            return self._send(200, {"instance_id": self.server.engine.instance_id,
                                    "run": self.server.engine.receipt(run_id)})
        if path.path == "/api/diagnostics":
            return self._send(200, self.server.engine.diagnostics.download(), headers={
                "Content-Disposition": 'attachment; filename="sopmaker-diagnostics.json"'})
        if path.path.startswith("/api/history/"):
            return self._send(200, store.history(path.path.rsplit("/", 1)[1]))
        if path.path.startswith("/media/"):
            parts = path.path.split("/")
            if len(parts) != 4:
                raise ValueError("Invalid media path")
            asset = store.asset(parts[2])
            name = unquote(parts[3])
            evidence_id = query.get("evidence", [None])[0]
            evidence = store.evidence(evidence_id, asset["id"]) if evidence_id else asset
            prepared = evidence["prepared"] or {"images": [], "audio": []}
            allowed = {x["file"] for x in prepared["images"] + prepared["audio"]}
            if name == "original":
                return self._file(store.root / asset["original"])
            if name not in allowed:
                raise ValueError("Unknown derivative")
            directory = prepared.get("directory", f"derived/{asset['id']}")
            return self._file(store.root / directory / name)
        if path.path.startswith("/exports/"):
            name = path.path.rsplit("/", 1)[1]
            if not re.fullmatch(r"inspection-[a-f0-9]{12}\.zip", name):
                raise ValueError("Invalid export")
            return self._file(store.root / "exports" / name, attachment=True)
        self._send(404, {"error": "Not found."})

    def _file(self, file, attachment=False):
        total = file.stat().st_size
        start, end, status = 0, total - 1, 200
        raw_range = self.headers.get("Range")
        if raw_range:
            match = re.fullmatch(r"bytes=(\d+)-(\d*)", raw_range)
            if not match or int(match[1]) >= total:
                return self._send(416, {"error": "Invalid byte range."})
            start, end = int(match[1]), min(total - 1, int(match[2]) if match[2] else total - 1)
            if end < start:
                return self._send(416, {"error": "Invalid byte range."})
            status = 206
        self.send_response(status)
        self._headers(mimetypes.guess_type(file.name)[0] or "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
        if attachment:
            self.send_header("Content-Disposition", f'attachment; filename="{file.name}"')
        self.end_headers()
        with file.open("rb") as stream:
            stream.seek(start)
            remaining = end - start + 1
            while remaining:
                chunk = stream.read(min(256 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _upload(self, path):
        filename = parse_qs(path.query).get("name", [""])[0]
        extension = Path(filename).suffix.lower()
        if extension not in SUPPORTED or len(filename) > 250:
            raise ValueError("Unsupported file")
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_UPLOAD:
            raise ValueError("Files must be between 1 byte and 1 GB.")
        with tempfile.TemporaryDirectory(prefix="sopmaker-import-") as folder:
            incoming = Path(folder) / ("input" + extension)
            remaining = length
            with incoming.open("wb") as stream:
                while remaining:
                    data = self.rfile.read(min(1024 * 1024, remaining))
                    if not data:
                        raise ValueError("Incomplete file")
                    stream.write(data)
                    remaining -= len(data)
            imported = self.server.store.import_file(incoming, filename)
        self._send(200, {"imported": imported})

    def _post(self, path, body):
        store, engine, credentials = self.server.store, self.server.engine, self.server.credentials
        if path == "/api/session":
            computer_timezone = store.initialize_timezone(body.get("timezone"))
            return self._send(200, {"ok": True, "computer_timezone": computer_timezone}, headers={"Set-Cookie": f"{self.server.cookie_name}={self.server.token}; HttpOnly; SameSite=Strict; Path=/"})
        if path in ("/api/notebooks", "/api/notebooks/open"):
            if not self.server.library:
                raise ValueError("Use the Sopmaker launcher to open the notebook library.")
            library = self.server.library
            url = library.create(body.get("id"), body.get("name")) if path == "/api/notebooks" else library.open(body.get("id"))
            return self._send(200, {"url": url})
        if path == "/api/settings":
            store.save_settings(body)
        elif path == "/api/key":
            credentials.set(body["key"], body.get("remember") is True)
        elif path == "/api/key/load":
            credentials.load_saved()
        elif path == "/api/key/clear":
            credentials.clear_session()
        elif path == "/api/key/forget":
            credentials.forget()
        elif path == "/api/process":
            intent = body.get("intent", "new" if body.get("generate_report") is True else "retry")
            if intent not in ("new", "retry"):
                raise ValueError("Choose new-file generation or an explicit retry.")
            with self.server.library.admission() if self.server.library else nullcontext():
                result = engine.start(body.get("asset_ids"), generate_report=body.get("generate_report") is True,
                                      run_id=body.get("run_id"), new_only=intent == "new")
            return self._send(200, {"run": result})
        elif path == "/api/cancel":
            if not isinstance(body.get("run_id"), str):
                raise ValueError("Check the current run before cancelling.")
            engine.stop(body["run_id"])
        elif path == "/api/edit":
            return self._send(200, store.edit(body["id"], body["revision"], body["note"], body["state"], body.get("reviewer", "")))
        elif path == "/api/export":
            output = export_notes(store, body["ids"], body.get("include_drafts") is True)
            return self._send(200, {"url": "/exports/" + output.name})
        else:
            return self._send(404, {"error": "Not found."})
        self._send(200, {"ok": True})
