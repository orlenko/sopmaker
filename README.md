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

## Windows installation for everyday use

Use the **Sopmaker-Windows-Setup** download from a passing
[build run](https://github.com/orlenko/sopmaker/actions/workflows/check.yml).
Actions downloads require a GitHub login; someone preparing the pilot can hand
the contained `Sopmaker-Windows-Setup.zip` directly to the inspector. This is
a self-contained setup package, with no Python, Git, or FFmpeg setup required:

1. Right-click the setup ZIP and choose **Extract All**. If GitHub wraps it in
   another ZIP, extract the contained setup ZIP as well.
2. Open the extracted folder and double-click **Install Sopmaker.cmd**.
   It installs for the current user and creates desktop and Start menu shortcuts;
   administrator rights are not needed. The browser opens when setup finishes.
3. In **Setup**, enter your own API key and inspection timezone.
4. Open **Add files**, drop photos, short videos, voice notes or a folder.
   Extract media ZIPs before importing.
5. Open **Generate report** and click **Generate draft report** once. The button
   locks immediately; the status strip follows processing and ZIP packaging.
6. When processing finishes, click **Download draft report**, extract the ZIP,
   and open `notes.docx` or `index.html`. Check and correct the draft before use.

Generating explicitly uses the configured API key and includes unreviewed drafts.
Each automatic report contains only its processing batch, leaving earlier notes
in the notebook. A failed/cancelled batch or zero observations produces a visible
message instead of a report. Retry failed files explicitly in **Add files**, then use selected-note
export to combine completed notes. Retrying one file saves notes; it does not
automatically rebuild the earlier batch report.

Setup copies the adjacent application to `%LOCALAPPDATA%\Programs\Sopmaker`;
it does not download code or change saved PowerShell policies. It checks bundled
file hashes for incomplete extraction/copying; these are not code signatures.
The package is unsigned and may need workplace IT approval. Close the app's
console window before reinstalling/updating; the installer refuses a running
copy and preserves notebooks and saved keys. Keep this window open while working.
The package contains a plain-language `START HERE.txt` with recovery steps.
Setup serializes competing installations and refuses updates if the installed
folder contains extra data or filesystem links. Keep notebooks outside that
folder. Previous app copies are retained beside the installed app as
`Sopmaker-backup-*`; failed new copies may be retained as `Sopmaker-failed-*`.
Setup never recursively deletes a committed app folder. Review retained copies
before manually removing them, especially if you previously put data there.

Choose files or a folder in **Add files**. Import copies files locally;
**Generate draft report** explicitly sends image previews, sampled video frames,
and extracted audio to OpenAI, using your account's API billing. Failed or
interrupted files require an explicit retry. Completed stages are cached.

The default workspace is `~/Documents/Sopmaker/Notebook`. To keep a separate
inspection, start with `--workspace PATH`. Keep workspaces outside the source
checkout and back them up as inspection data.

## Processing feedback and diagnostics

A status strip stays visible across Add files, Generate report, Review notes and
Setup. It distinguishes actual media preparation, transcription/draft API waits,
saving, ZIP packaging, cleanup and terminal outcomes. File completion and new
observation counts are separate. There are no invented percentages or time estimates.
Saved observations can appear before the report download is ready.

If a start response is lost, Sopmaker checks the same run identity without
resubmitting. **Check status now** is read-only. A missing receipt remains
uncertain; it is not permission to start the same work again. Keep the app open.
Browser refresh retains a pending identity when session storage is available.
Run status/receipts last for this app session only; reopening the app retains
notes/exports but does not restore a previous running job or last-download link.
At most 256 run identities are admitted per app session, without eviction/replay;
finish your work and restart Sopmaker before starting more.

In **Setup → Troubleshooting**, **Download diagnostics** saves the last 300
allowlisted events from this app session. It includes stages, elapsed durations,
internal run/file IDs, known model IDs (custom models are labelled `custom`) and
safe error/request categories. It excludes media, narration, notes, API keys,
filenames, notebook paths and the token-bearing launcher URL. Review before sharing;
use this download rather than copying the whole launch console.

Diagnostics go to stderr so CLI result stdout stays parseable. Console events
are also written to `diagnostics.jsonl`, with two rotated backups
and a 512,000-byte limit per file. Windows uses
`%LOCALAPPDATA%\Sopmaker\Logs`; macOS uses `~/Library/Logs/Sopmaker`; Linux uses
`$XDG_STATE_HOME/sopmaker/logs` or `~/.local/state/sopmaker/logs`. An explicitly set
`XDG_STATE_HOME` also overrides the macOS location. Log writes are coordinated
across notebook processes; unavailable logging does not fail processing.
A periodic waiting event confirms local activity only, not provider progress.

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

The pinned [shared quality setup](docs/quality.md) provides an inspection-only
plan and outside-target invocation. It preserves the full native gates below;
shared command execution and enforced-review evidence remain unavailable.

```sh
uv run ruff check .
uv run pytest
```

See [testing on Mac and Windows](docs/testing.md) for browser checks, opt-in
paid API tests, executable builds, and a manual Windows acceptance protocol.
GitHub Actions tests all three operating systems and builds a portable Windows
ZIP; download the artifact from a passing workflow run, extract it, and open
`Sopmaker.exe`. Keep its `_internal` folder beside the executable. Builds
also include the setup ZIP described above, with automated installation/update
checks. They contain source/dependency identity and [third-party notices](docs/third-party.md).

## Current limits

This is an initial prototype for human review. Model output can be wrong even
when its source IDs and quotes validate. Use staged, non-identifying material
for evaluation and agree on permitted services before processing actual lab
records. New notebooks default to `gpt-6.1-sol` for notes and `gpt-transcribe` for
audio. Setup offers Sol, Terra, the previous Mini option, and an advanced custom
model. Existing saved models are preserved; a change affects future explicit
processing only. Sol/Terra requests use low reasoning and a 12,000-token output
cap; legacy/custom requests retain their existing 6,000-token shape. These new
choices have offline request-contract checks, but representative extraction
accuracy, latency, cost and account access remain unqualified. There is no
automatic model fallback or paid retry. See the official
[Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol) and
[Terra](https://developers.openai.com/api/docs/models/gpt-6.1-terra) specifications.

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
