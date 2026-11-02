"""One bounded worker; explicit retries and independently cached preparation/transcription."""

import hashlib
import json
import threading
import uuid

from .exporter import export_notes
from .media import PREPARATION_VERSION, prepare
from .provider import PROMPT_VERSION, OpenAIProvider, safe_error


class Engine:
    def __init__(self, store, credentials, provider_factory=OpenAIProvider):
        self.store = store
        self.credentials = credentials
        self.provider_factory = provider_factory
        self.lock = threading.Lock()
        self.cancelled = threading.Event()
        self.thread = None
        self.progress = {"running": False, "current": None}

    def start(self, asset_ids: list[str] | None = None, generate_report: bool = False) -> None:
        if not self.credentials.key:
            raise ValueError("Set up your OpenAI API key first.")
        if not self.lock.acquire(blocking=False):
            raise ValueError("Processing is already running.")
        try:
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
            self.cancelled.clear()
            self.progress = {"running": True, "current": None}
            self.thread = threading.Thread(target=self._run, args=(assets, settings, self.credentials.key, generate_report), daemon=True)
            self.thread.start()
        except Exception:
            self.lock.release()
            raise

    def _run(self, assets, settings, key, generate_report=False):
        provider = None
        outcome = {}
        try:
            provider = self.provider_factory(key)
            for asset in assets:
                if self.cancelled.is_set():
                    break
                self.progress = {"running": True, "current": asset["name"]}
                try:
                    self.process_one(asset, settings, provider)
                except Exception as exc:
                    self.store.update_asset(asset["id"], state="error", error=safe_error(exc))
            if generate_report:
                ids = {asset["id"] for asset in assets}
                incomplete = [a for a in self.store.assets() if a["id"] in ids and a["state"] != "ready"]
                if self.cancelled.is_set() or incomplete:
                    outcome = {"report_error": "Report not generated: processing stopped or a file failed. Review Source files and retry explicitly."}
                else:
                    notes = [o["id"] for o in self.store.observations() if o["asset_id"] in ids]
                    if notes:
                        output = export_notes(self.store, notes, include_drafts=True)
                        outcome = {"report_url": "/exports/" + output.name, "report_notes": len(notes)}
                    else:
                        outcome = {"report_error": "Processing finished with no observations. Review the source files; no report was generated."}
        except Exception as exc:
            outcome = {"report_error": safe_error(exc)}
        finally:
            try:
                if provider is not None:
                    provider.close()
            finally:
                self.progress = {"running": False, "current": None, **outcome}
                self.lock.release()

    def stop(self):
        self.cancelled.set()

    def _checkpoint(self, asset_id):
        if self.cancelled.is_set():
            self.store.update_asset(asset_id, state="interrupted", error="Cancelled between stages. Retry explicitly to continue.")
            return False
        return True

    def process_one(self, asset, settings, provider):
        aid = asset["id"]
        path = self.store.root / asset["original"]
        with path.open("rb") as stream:
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
            result = provider.transcribe(directory / audio["file"], settings["transcription_model"], settings["context"])
            chunk = {"id": f"{aid}:transcript:{transcription_fingerprint}:{index}",
                     "text": result.get("text", ""), "start": audio["start"], "end": audio["end"], "response": result}
            self.store.cache_chunk(transcription_fingerprint, aid, index, chunk)
            transcript["chunks"].append(chunk)
        # Publish only a complete transcription. Failed attempts retain their completed
        # chunks separately and cannot replace the last complete transcript.
        self.store.update_asset(aid, transcript=transcript)
        if not self._checkpoint(aid):
            return
        fingerprint = hashlib.sha256(json.dumps([
            transcription_fingerprint, settings["vision_model"], PROMPT_VERSION, prepared
        ], sort_keys=True).encode()).hexdigest()
        if asset["fingerprint"] == fingerprint:
            self.store.update_asset(aid, state="ready", error=None)
            return
        sources = {image["id"]: {"kind": "image", **image} for image in prepared["images"]}
        sources.update({chunk["id"]: {"kind": "transcript", **chunk}
                        for chunk in transcript["chunks"] if chunk["text"].strip()})
        if sources:
            batch, response = provider.draft(sources, directory, settings["vision_model"], settings["context"])
            drafts = [draft.model_dump(mode="json") for draft in batch.observations]
        else:
            drafts, response = [], {"skipped": "No images or transcribed speech to summarize."}
        self.store.add_drafts(aid, drafts, response, fingerprint)
