"""Portable, selected-note exports. No API calls and no developer filesystem paths."""

import csv
import html
import io
import json
import uuid
import zipfile
from pathlib import Path

from docx import Document
from docx.shared import Inches


def export_notes(store, selected: list[str], include_drafts: bool = False) -> Path:
    if not selected or len(selected) != len(set(selected)):
        raise ValueError("Select one or more distinct observations to export.")
    available = {o["id"]: o for o in store.observations(include_archived=True)}
    if set(selected) - available.keys():
        raise ValueError("The export contains an unknown observation.")
    notes = [available[oid] for oid in selected]
    if not include_drafts and any(o["state"] != "reviewed" for o in notes):
        raise ValueError("Review selected notes or explicitly include drafts in this export.")
    assets = {o["asset_id"]: store.asset(o["asset_id"]) for o in notes}
    document = Document()
    document.add_heading(store.settings()["name"], 0)
    document.add_paragraph("Selected inspection observations. Consult the evidence index and originals.")
    entries, rows, files = [], [], set()
    for note in notes:
        draft = note["generated"]
        asset = assets[note["asset_id"]]
        prepared = asset["prepared"] or {"images": [], "capture_time": {"utc": None, "basis": "unknown"}}
        capture = prepared["capture_time"]
        label = f"{draft['title']} ({note['state']})"
        document.add_heading(label, level=1)
        document.add_paragraph(note["note"])
        document.add_paragraph(f"Source: {asset['name']} | Capture: {capture['utc'] or 'unknown'} ({capture['basis']})")
        if note["reviewer"]:
            document.add_paragraph(f"Reviewer: {note['reviewer']} | Revision: {note['revision']}")
        details = []
        for heading, values in (("Follow-up", draft["follow_ups"]), ("Open questions", draft["open_questions"])):
            if values:
                document.add_paragraph(heading + ": " + "; ".join(values))
                details.append(f"<p><strong>{heading}:</strong> {html.escape('; '.join(values))}</p>")
        for claim in draft["claims"]:
            refs = ", ".join(ref["source_id"] for ref in claim["evidence"])
            document.add_paragraph(f"Evidence ({claim['basis']}): {refs}", style="Caption")
        images = []
        for image in prepared["images"]:
            relative = f"derived/{asset['id']}/{image['file']}"
            files.add(relative)
            caption = image["id"] + (f" — {image['seconds']}s into clip" if image["seconds"] is not None else "")
            images.append(f'<figure><img src="{html.escape(relative)}" alt="Source evidence">'
                          f'<figcaption>{html.escape(caption)}</figcaption></figure>')
        if prepared["images"]:
            document.add_picture(str(store.root / f"derived/{asset['id']}/{prepared['images'][0]['file']}"), width=Inches(3))
        files.add(asset["original"])
        transcript_html = "".join(f"<p>{html.escape(chunk['text'])}</p>" for chunk in (asset["transcript"] or {}).get("chunks", []))
        entries.append(f"<article><h2>{html.escape(label)}</h2><p class='note'>{html.escape(note['note'])}</p>"
                       f"<p>Capture: {html.escape(capture['utc'] or 'unknown')} ({html.escape(capture['basis'])})</p>"
                       f"<a href='{html.escape(asset['original'], quote=True)}'>Original: {html.escape(asset['name'])}</a>"
                       + "".join(details) + "".join(images) + "<h3>Transcript</h3>" + (transcript_html or "<p>No narration.</p>")
                       + "<h3>Claim evidence</h3><pre>" + html.escape(json.dumps(draft["claims"], indent=2, ensure_ascii=False))
                       + "</pre></article>")
        rows.append([note["id"], draft["title"], note["state"], note["note"], asset["name"],
                     capture["utc"] or "", capture["basis"], note["reviewer"] or ""])
    output = store.root / "exports" / f"inspection-{uuid.uuid4().hex[:12]}.zip"
    temporary = output.with_suffix(".tmp")
    word = io.BytesIO()
    document.save(word)
    csv_file = io.StringIO(newline="")
    writer = csv.writer(csv_file)
    writer.writerow(["id", "title", "state", "note", "source", "capture_time", "time_basis", "reviewer"])
    # Prevent formulas when user/media-derived strings are opened in a spreadsheet.
    for row in rows:
        writer.writerow(["'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v for v in row])
    page = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Inspection evidence</title><style>body{font:17px system-ui;max-width:960px;margin:40px auto;padding:0 24px;color:#182a35}
article{border-top:1px solid #ccd6dc;padding:20px 0}.note{white-space:pre-wrap}img{max-width:100%;max-height:400px}
figure{display:inline-block;margin:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><h1>Inspection evidence</h1>"""
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("notes.docx", word.getvalue())
            archive.writestr("observations.csv", "\ufeff" + csv_file.getvalue())
            archive.writestr("observations.json", json.dumps({"observations": notes, "assets": assets}, indent=2, ensure_ascii=False))
            archive.writestr("index.html", page + "".join(entries) + "</html>")
            for relative in sorted(files):
                archive.write(store.root / relative, relative)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return output
