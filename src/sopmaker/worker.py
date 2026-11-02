"""One bounded worker; explicit retries and independently cached preparation/transcription."""

import hashlib
import json
import threading

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

    def start(self, asset_ids: list[str] | None = None) -> None:
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
            self.cancelled.clear()
            self.progress = {"running": True, "current": None}
            self.thread = threading.Thread(target=self._run, args=(assets, settings, self.credentials.key), daemon=True)
            self.thread.start()
        except Exception:
            self.lock.release()
            raise

    def _run(self, assets, settings, key):
        provider = None
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
        finally:
            try:
                if provider is not None:
                    provider.close()
            finally:
                self.progress = {"running": False, "current": None}
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
        directory = self.store.root / "derived" / aid
        prepared = asset["prepared"]
        if (not prepared or prepared.get("version") != PREPARATION_VERSION
                or prepared.get("zone") != settings["timezone"]
                or not all((directory / x["file"]).is_file() for x in prepared["images"] + prepared["audio"])):
            prepared = prepare(path, directory, aid, settings["timezone"])
            prepared["zone"] = settings["timezone"]
            self.store.update_asset(aid, prepared=prepared)
        if not self._checkpoint(aid):
            return
        transcription_fingerprint = hashlib.sha256(json.dumps(
            [asset["sha256"], PREPARATION_VERSION, settings["transcription_model"], settings["context"]]
        ).encode()).hexdigest()
        transcript = asset["transcript"]
        if not transcript or transcript.get("fingerprint") != transcription_fingerprint:
            transcript = {"fingerprint": transcription_fingerprint, "chunks": []}
            # Save each completed chunk. A later chunk failing need not repeat earlier charges.
            self.store.update_asset(aid, transcript=transcript)
        completed = len(transcript["chunks"])
        for index, audio in enumerate(prepared["audio"]):
            if index < completed:
                continue
            if not self._checkpoint(aid):
                return
            result = provider.transcribe(directory / audio["file"], settings["transcription_model"], settings["context"])
            transcript["chunks"].append({"id": f"{aid}:transcript:{index}", "text": result.get("text", ""),
                                         "start": audio["start"], "end": audio["end"], "response": result})
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
