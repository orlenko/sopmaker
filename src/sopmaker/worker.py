"""One bounded worker; explicit retries and independently cached preparation/transcription."""

import hashlib
import json
import re
import threading
import time
import uuid
from contextlib import contextmanager
from copy import deepcopy

from .diagnostics import Diagnostics, error_category, utc_now
from .exporter import export_notes
from .media import PREPARATION_VERSION, prepare
from .model_settings import draft_options
from .provider import PROMPT_VERSION, OpenAIProvider, safe_error


class Engine:
    def __init__(self, store, credentials, provider_factory=OpenAIProvider, diagnostics=None):
        self.store = store
        self.credentials = credentials
        self.provider_factory = provider_factory
        self.lock = threading.Lock()
        self.cancelled = threading.Event()
        self.publication_lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.diagnostics = diagnostics or Diagnostics()
        self.instance_id = uuid.uuid4().hex
        self.sequence = 0
        self.run_clock = self.stage_clock = time.monotonic()
        self.receipts = {}
        self.thread = None
        self._progress = {"running": False, "current": None, "state": "idle",
                          "instance_id": self.instance_id, "sequence": 0, "run_id": None}

    @property
    def progress(self):
        with self.state_lock:
            return deepcopy(self._progress)

    def receipt(self, run_id):
        with self.state_lock:
            return deepcopy(self.receipts.get(run_id))

    def _update(self, **values):
        with self.state_lock:
            self.sequence += 1
            self._progress = {**self._progress, **values, "sequence": self.sequence,
                              "updated_at": utc_now()}
            for key in ("report_url", "report_error"):
                if self._progress.get(key) is None:
                    self._progress.pop(key, None)
            if self._progress["run_id"]:
                self.receipts[self._progress["run_id"]] = deepcopy(self._progress)
            return deepcopy(self._progress)

    def _event(self, event, **fields):
        snapshot = self.progress
        self.diagnostics.emit(event, **{"run_id": snapshot.get("run_id"), "instance_id": self.instance_id,
                                       "stage": snapshot.get("stage"), **fields})

    def start(self, asset_ids: list[str] | None = None, generate_report: bool = False, run_id=None, new_only=False):
        run_id = uuid.uuid4().hex if run_id is None else run_id
        if not isinstance(run_id, str) or not re.fullmatch(r"[a-f0-9]{32}", run_id):
            raise ValueError("Invalid run identity.")
        existing = self.receipt(run_id)
        if existing:
            return existing
        if not self.credentials.key:
            raise ValueError("Set up your OpenAI API key first.")
        if not self.lock.acquire(blocking=False):
            # A double click may reach here before its first admission completed.
            existing = self.receipt(run_id)
            if existing:
                return existing
            raise ValueError("Processing is already running.")
        admitted = False
        try:
            # A same-ID request may have observed absence before a complete run
            # acquired and released the lock. Admission must recheck atomically.
            existing = self.receipt(run_id)
            if existing:
                self.lock.release()
                return existing
            # Never evict an admitted identity and accidentally process it twice.
            if len(self.receipts) >= 256:
                raise ValueError("This app session has processed 256 runs. Finish your work and restart Sopmaker before starting more.")
            settings = self.store.settings()
            assets = self.store.assets()
            if asset_ids is not None:
                known = {a["id"] for a in assets}
                if not asset_ids or set(asset_ids) - known:
                    raise ValueError("Select existing source files.")
                assets = [a for a in assets if a["id"] in asset_ids]
            else:
                assets = [a for a in assets if a["state"] == "imported"]
            if not assets:
                raise ValueError("Import new files first, or explicitly select files to retry.")
            if new_only and any(a["state"] != "imported" for a in assets):
                raise ValueError("These files changed since this window loaded. No work was started. Check status, then use Add files to retry explicitly if needed.")
            with self.publication_lock:
                self.cancelled.clear()
                self.run_clock = self.stage_clock = time.monotonic()
                self._update(run_id=run_id, running=True, state="running", current=None, current_id=None,
                             stage="starting", started_at=utc_now(), stage_started_at=utc_now(),
                             file_started_at=None, finished_at=None, completed=0, failed=0, notes=0,
                             total=len(assets), asset_ids=[a["id"] for a in assets], files={},
                             model=settings["vision_model"], cancel_requested=False,
                             generate_report=generate_report, report_url=None, report_error=None,
                             report_notes=0, cleanup_error=False)
                admitted = True
            self.thread = threading.Thread(target=self._run, args=(assets, settings, self.credentials.key, generate_report), daemon=True)
            self.thread.start()
            return self.receipt(run_id)
        except Exception:
            try:
                if admitted:
                    self._update(running=False, state="failed", stage="finished", finished_at=utc_now(),
                                 report_error="The worker could not start. No processing was started; check diagnostics.")
            finally:
                self.lock.release()
            raise

    @contextmanager
    def stage(self, stage, model=None):
        active = self.progress["running"]
        started = time.monotonic()
        outcome = "completed"
        if active:
            self.stage_clock = started
            self._update(stage=stage, stage_started_at=utc_now())
            self._event("stage_started", model=model)
        try:
            yield
        except Exception:
            outcome = "failed"
            raise
        finally:
            if active:
                self._event("stage_finished", stage=stage, model=model, state=outcome, duration_s=time.monotonic() - started)

    def _heartbeat(self, finished):
        while not finished.wait(15):
            snapshot = self.progress
            if snapshot["running"]:
                self._event("waiting", completed=snapshot["completed"], total=snapshot["total"],
                            duration_s=time.monotonic() - self.stage_clock,
                            elapsed_s=time.monotonic() - self.run_clock)

    def _run(self, assets, settings, key, generate_report=False):
        provider = None
        outcome = {}
        output = None
        state = "completed"
        finished = threading.Event()
        heartbeat = None
        try:
            self._event("run_started", model=settings["vision_model"], total=len(assets))
            heartbeat = threading.Thread(target=self._heartbeat, args=(finished,), daemon=True)
            heartbeat.start()
            provider = self.provider_factory(key)
            for asset in assets:
                if self.cancelled.is_set():
                    break
                self._update(current=asset["name"], current_id=asset["id"], file_started_at=utc_now())
                before = self.store.observation_ids(asset["id"])
                try:
                    self.process_one(asset, settings, provider)
                except Exception as exc:
                    self.store.update_asset(asset["id"], state="error", error=safe_error(exc))
                    self._event("file_failed", asset_id=asset["id"], category=error_category(exc),
                                http_status=getattr(exc, "status_code", None), request_id=getattr(exc, "request_id", None))
                after = self.store.observation_ids(asset["id"])
                snapshot = self.progress
                file_state = self.store.asset(asset["id"])["state"]
                files = {**snapshot["files"], asset["id"]: {"state": file_state, "new_notes": len(after - before)}}
                self._update(files=files, completed=sum(f["state"] == "ready" for f in files.values()),
                             failed=sum(f["state"] == "error" for f in files.values()),
                             notes=sum(f["new_notes"] for f in files.values()))
                self._event("file_finished", asset_id=asset["id"], notes=len(after - before))
            incomplete = self.progress["completed"] != len(assets)
            if self.cancelled.is_set():
                state = "cancelled"
            elif incomplete:
                state = "failed"
            if generate_report:
                ids = {asset["id"] for asset in assets}
                if self.cancelled.is_set() or incomplete:
                    outcome = {"report_error": "Report not generated: processing stopped or a file failed. Review Add files and retry explicitly."}
                else:
                    notes = [o["id"] for o in self.store.observations() if o["asset_id"] in ids]
                    if notes:
                        with self.stage("packaging"):
                            output = export_notes(self.store, notes, include_drafts=True)
                        outcome = {"report_url": "/exports/" + output.name, "report_notes": len(notes)}
                    else:
                        state = "empty"
                        outcome = {"report_error": "Processing finished with no observations. Review the source files; no report was generated."}
        except Exception as exc:
            state = "failed"
            outcome = {"report_error": safe_error(exc)}
            self._event("local_error", category=error_category(exc))
        finally:
            try:
                if provider is not None:
                    with self.stage("closing"):
                        provider.close()
            except Exception as exc:
                state = "failed"
                outcome = {"report_error": "Processing cleanup failed. Saved notes are preserved; check diagnostics and export selected notes explicitly."}
                self._update(cleanup_error=True)
                self._event("local_error", category=error_category(exc))
            finally:
                try:
                    # Commit visibility and cancellation atomically. Do not publish
                    # success until export and cleanup really finished.
                    with self.publication_lock:
                        if self.cancelled.is_set():
                            state = "cancelled"
                            outcome = {"report_error": "Report not generated: processing stopped or a file failed. Review Add files and retry explicitly."}
                        if output is not None and state in ("failed", "cancelled"):
                            try:
                                output.unlink(missing_ok=True)
                            except OSError:
                                outcome = {"report_error": "Report not published. Its unused ZIP could not be removed; saved notes are preserved."}
                        self._update(running=False, state=state, stage="finished", current=None,
                                     finished_at=utc_now(), **outcome)
                    snapshot = self.progress
                    self._event("run_finished", state=state, completed=snapshot["completed"],
                                failed=snapshot["failed"], notes=snapshot["notes"], total=len(assets))
                finally:
                    finished.set()
                    self.lock.release()

    def stop(self, run_id=None):
        requested = False
        with self.publication_lock:
            if run_id is not None and run_id != self.progress.get("run_id"):
                raise ValueError("That run is no longer current. Check status before cancelling.")
            if self.progress["running"]:
                self.cancelled.set()
                self._update(cancel_requested=True)
                requested = True
        if requested:
            self._event("cancel_requested")

    def _checkpoint(self, asset_id):
        if self.cancelled.is_set():
            self.store.update_asset(asset_id, state="interrupted", error="Cancelled between stages. Retry explicitly to continue.")
            return False
        return True

    def process_one(self, asset, settings, provider):
        aid = asset["id"]
        path = self.store.root / asset["original"]
        with self.stage("preparing"), path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != asset["sha256"]:
                raise ValueError("Original content changed.")
        self.store.update_asset(aid, state="processing", error=None)
        preparation_key = hashlib.sha256(json.dumps(
            [asset["sha256"], PREPARATION_VERSION, settings["timezone"]]
        ).encode()).hexdigest()
        prepared = asset["prepared"]
        if (not prepared or prepared.get("version") != PREPARATION_VERSION
                or prepared.get("zone") != settings["timezone"]
                or prepared.get("cache_key") != preparation_key
                or not all((self.store.root / prepared["directory"] / x["file"]).is_file() for x in prepared["images"] + prepared["audio"])):
            # Every actual preparation gets a new directory, including regeneration
            # after missing files. Never rewrite a derivative used by an old note.
            preparation_id = uuid.uuid4().hex
            relative_directory = f"derived/{aid}/{preparation_id}"
            directory = self.store.root / relative_directory
            with self.stage("preparing"):
                prepared = prepare(path, directory, f"{aid}:prep:{preparation_id}", settings["timezone"])
            prepared["zone"] = settings["timezone"]
            prepared["cache_key"] = preparation_key
            prepared["directory"] = relative_directory
            prepared["revision_id"] = preparation_id
            self.store.update_asset(aid, prepared=prepared)
        directory = self.store.root / prepared["directory"]
        preparation_id = prepared["revision_id"]
        if not self._checkpoint(aid):
            return
        transcription_fingerprint = hashlib.sha256(json.dumps(
            [asset["sha256"], preparation_id, settings["transcription_model"], settings["context"]]
        ).encode()).hexdigest()
        transcript = asset["transcript"]
        if not transcript or transcript.get("fingerprint") != transcription_fingerprint:
            transcript = {"fingerprint": transcription_fingerprint,
                          "model": settings["transcription_model"], "context": settings["context"],
                          "preparation_id": preparation_id,
                          "chunks": self.store.cached_chunks(transcription_fingerprint)}
        completed = len(transcript["chunks"])
        for index, audio in enumerate(prepared["audio"]):
            if index < completed:
                continue
            if not self._checkpoint(aid):
                return
            with self.stage("transcribing", settings["transcription_model"]):
                result = provider.transcribe(directory / audio["file"], settings["transcription_model"], settings["context"])
            self._event("provider_response", request_id=getattr(provider, "last_request_id", None), asset_id=aid)
            chunk = {"id": f"{aid}:transcript:{transcription_fingerprint}:{index}",
                     "text": result.get("text", ""), "start": audio["start"], "end": audio["end"], "response": result}
            self.store.cache_chunk(transcription_fingerprint, aid, index, chunk)
            transcript["chunks"].append(chunk)
        # Publish only a complete transcription. Failed attempts retain their completed
        # chunks separately and cannot replace the last complete transcript.
        self.store.update_asset(aid, transcript=transcript)
        if not self._checkpoint(aid):
            return
        fingerprint_parts = [
            transcription_fingerprint, settings["vision_model"], PROMPT_VERSION, prepared
        ]
        if "reasoning" in draft_options(settings["vision_model"]):
            fingerprint_parts.append(draft_options(settings["vision_model"]))
        fingerprint = hashlib.sha256(json.dumps(fingerprint_parts, sort_keys=True).encode()).hexdigest()
        if asset["fingerprint"] == fingerprint:
            self.store.update_asset(aid, state="ready", error=None)
            return
        sources = {image["id"]: {"kind": "image", **image} for image in prepared["images"]}
        sources.update({chunk["id"]: {"kind": "transcript", **chunk}
                        for chunk in transcript["chunks"] if chunk["text"].strip()})
        if sources:
            with self.stage("drafting", settings["vision_model"]):
                batch, response = provider.draft(sources, directory, settings["vision_model"], settings["context"])
            self._event("provider_response", request_id=getattr(provider, "last_request_id", None), asset_id=aid)
            drafts = [draft.model_dump(mode="json") for draft in batch.observations]
        else:
            drafts, response = [], {"skipped": "No images or transcribed speech to summarize."}
        with self.stage("saving"):
            self.store.add_drafts(aid, drafts, response, fingerprint)
