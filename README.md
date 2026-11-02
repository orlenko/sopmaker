# Sopmaker inspection notebook

An inspection assistant that turns photos, short narrated videos, and voice
notes into searchable draft observations with links to their source evidence.
Inspectors review the notes and use selected observations in their existing
reporting process.

The proof of concept runs locally on macOS, Windows, and Linux, with light
media preparation and external AI APIs. No hosted service or separate FFmpeg
installation is needed. The local browser interface supports file/folder
import, per-file processing, search, source review, note edits and revision
history, and selected-note exports to Word, CSV, JSON, and a portable HTML
evidence index with originals. Every source file can be opened independently,
including files with no generated observation or incomplete processing.

## Run from source

Install [uv](https://docs.astral.sh/uv/), then:

```sh
uv sync --locked
uv run sopmaker serve
```

The browser opens a local session. In **Setup**, enter your own OpenAI API key
and the inspection timezone. Use the key for this session only, or explicitly
remember it in Windows Credential Manager / macOS Keychain. Saved keys are
loaded only when you select **Use saved key**. No key is included in the app,
inspection workspace, or exports. Developers can alternatively set
`OPENAI_API_KEY` in their environment.

Choose files or a folder in **Source files**. Import copies files locally;
**Draft new notes** explicitly sends image previews, sampled video frames,
and extracted audio to OpenAI, using your account's API billing. Failed or
interrupted files require an explicit retry. Completed stages are cached.

The default workspace is `~/Documents/Sopmaker/Notebook`. To keep a separate
inspection, start with `--workspace PATH`. Keep workspaces outside the source
checkout and back them up as inspection data.

## Try the interface without a key

```sh
uv run sopmaker demo --workspace ./workspaces/demo
```

This seeds a clearly marked synthetic observation without calling OpenAI.
To import or process from the command line:

```sh
uv run sopmaker import PATH_TO_MEDIA --workspace PATH_TO_INSPECTION
uv run sopmaker process --workspace PATH_TO_INSPECTION
uv run sopmaker export --workspace PATH_TO_INSPECTION
```

Exports include reviewed observations by default. Explicitly pass
`--include-drafts` to include unreviewed notes. In the browser, select the
observations to export. The ZIP contains editable `notes.docx`, an evidence
`index.html`, original media, previews, CSV, and JSON. Extract it before opening
the index. Editing a note preserves the original AI claims and previous saved
revisions; reprocessing keeps reviewed notes and archives prior unreviewed drafts.
Each note retains its own preparation and transcript evidence revision, including
after a different model is used or a later attempt fails. Version 1 workspaces
are upgraded automatically; older notes are visibly marked when their original
input association cannot be verified. Such lost history cannot be recovered
by copying the latest transcript. See the [evidence and editor decisions](docs/evidence-and-editor-decisions.md).

Separate workspaces may be open concurrently in the same browser. Each local
server has its own session cookie, so source and download links remain usable.

## Testing and portable Windows builds

```sh
uv run ruff check .
uv run pytest
```

See [testing on Mac and Windows](docs/testing.md) for browser checks, opt-in
paid API tests, executable builds, and a manual Windows acceptance protocol.
GitHub Actions tests all three operating systems and builds a portable Windows
ZIP; download the artifact from a passing workflow run, extract it, and open
`Sopmaker.exe`. Keep its `_internal` folder beside the executable. Builds
contain source/dependency identity and [third-party notices](docs/third-party.md).

## Current limits

This is an initial prototype for human review. Model output can be wrong even
when its source IDs and quotes validate. Use staged, non-identifying material
for evaluation and agree on permitted services before processing actual lab
records. Initial defaults are `gpt-5.4-mini` for notes and `gpt-transcribe` for
audio; model access depends on the user's account and is configurable in Setup.

JPEG/PNG/WebP, common video containers, and voice recordings are supported.
HEIC, automated SOP ingestion, grouping/merging observations, and manual
capture-time corrections are deferred. Recording fragments are limited to
30 minutes; files to 1 GB. Capture times remain unknown or ambiguous when
metadata is insufficient. Video playback depends on browser codec support;
source frames and originals remain available. Word-template integration,
clinical validation, code signing, and the normal-user Windows 11 workflow
require separate acceptance work.

The [design](docs/inspection-notebook-design.md) records the longer-term
workflow and pilot criteria. A [synthetic observation](examples/observation.json)
illustrates the design's record format; the implemented database stores
source-linked claims and human revisions separately.
