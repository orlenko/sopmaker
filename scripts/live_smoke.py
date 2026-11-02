"""Opt-in paid API smoke test, using only a generated non-identifying photo."""

import argparse
import json
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

from sopmaker.credentials import Credentials
from sopmaker.provider import safe_error
from sopmaker.store import Store
from sopmaker.worker import Engine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-paid", action="store_true", required=True)
    parser.add_argument("--audio", type=Path, help="Optional explicitly selected staged narration file to upload")
    args = parser.parse_args()
    credentials = Credentials()
    if not credentials.key:
        parser.error("Set OPENAI_API_KEY for this opt-in test. No key is included with Sopmaker.")
    with tempfile.TemporaryDirectory(prefix="sopmaker-live-smoke-") as directory:
        root = Path(directory)
        photo = root / "synthetic-label.png"
        image = Image.new("RGB", (900, 500), "white")
        draw = ImageDraw.Draw(image)
        draw.text((70, 80), "SYNTHETIC TEST: CABINET C-17", fill="black", font_size=34)
        draw.text((70, 160), "Service date: unreadable", fill="black", font_size=32)
        image.save(photo)
        store = Store(root / "workspace")
        store.import_file(photo)
        if args.audio:
            store.import_file(args.audio)
        engine = Engine(store, credentials)
        engine.start()
        engine.thread.join(timeout=400)
        if engine.thread.is_alive():
            raise SystemExit("API smoke test exceeded its time budget; it may still have incurred charges.")
        failures = [{"source": asset["name"], "error": asset["error"]}
                    for asset in store.assets() if asset["state"] != "ready"]
        if failures:
            print(json.dumps({"result": "failed", "errors": failures}))
            raise SystemExit(1)
        print(json.dumps({"result": "passed", "sources": len(store.assets()),
                          "observations": len(store.observations()),
                          "transcribed_chunks": sum(len((a["transcript"] or {}).get("chunks", [])) for a in store.assets())}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        raise SystemExit(safe_error(exc)) from None
