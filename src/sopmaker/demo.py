"""Explicit offline demonstration built entirely from invented content."""

import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

from .media import prepare


def seed_demo(store):
    with tempfile.TemporaryDirectory(prefix="sopmaker-demo-") as folder:
        photo = Path(folder) / "synthetic-cabinet-label.jpg"
        image = Image.new("RGB", (960, 600), "#dae5e4")
        draw = ImageDraw.Draw(image)
        draw.rectangle((170, 130, 790, 440), fill="white", outline="#345651", width=4)
        draw.text((210, 180), "SYNTHETIC DEMO - CABINET C-17", fill="#183e37", font_size=28)
        draw.text((210, 250), "Service date: unreadable", fill="#183e37", font_size=28)
        draw.text((210, 320), "Example evidence only", fill="#183e37", font_size=24)
        image.save(photo)
        imported = store.import_file(photo)
        if not imported:
            return
    asset = next(a for a in store.assets() if a["name"] == photo.name)
    prepared = prepare(store.root / asset["original"], store.root / "derived" / asset["id"], asset["id"], "UTC")
    prepared["zone"] = "UTC"
    store.update_asset(asset["id"], prepared=prepared)
    draft = {"title": "Synthetic example: cabinet label", "area": None,
             "equipment_identifiers": ["C-17"], "tags": ["synthetic", "label"],
             "claims": [{"text": "The synthetic image shows a cabinet label marked C-17 and 'Service date: unreadable'.",
                         "basis": "visible_detail", "evidence": [{"source_id": prepared["images"][0]["id"], "quote": None}]}],
             "follow_ups": [], "open_questions": ["What date is recorded in the maintenance log?"]}
    store.add_drafts(asset["id"], [draft], {"mode": "offline_synthetic_example"}, "demo")
