# Testing on Mac and Windows

Develop and test the Python core on macOS. Use automated Windows jobs for
platform behavior and executable packaging, then perform a short manual session
on a Windows desktop before handing the app to an inspector.

## Local checks

Install [uv](https://docs.astral.sh/uv/) and run:

```sh
uv sync --locked
uv run ruff check .
uv run pytest
uv run playwright install chromium
uv run python scripts/browser_smoke.py
```

The default tests create disposable workspaces and invented images/audio/video.
They do not call OpenAI, touch a real credential store, or use personal media.
They check deduplication, source/quote validation, timestamp ambiguity,
interrupted jobs, edit conflicts, reprocessing, search, per-file failure, server
access controls, credential redaction, media playback ranges, and portable export.
The browser check exercises import, search, review, session-key setup and export.

To inspect the app without a key or API charges:

```sh
uv run sopmaker demo --workspace /tmp/sopmaker-demo
```

The demo seeds a clearly marked synthetic observation. It is an interface
demonstration, not evidence of AI accuracy. The same command accepts a Windows
workspace path in PowerShell.

An actual API smoke test is separate and explicitly paid:

```sh
uv run python scripts/live_smoke.py --allow-paid
```

It uses `OPENAI_API_KEY` from the environment and sends a generated label image.
Add `--audio PATH` only for staged narration you explicitly want uploaded. Verify
numbers, identifiers, quoted speech, and frame references against the supplied
material; schema validity alone does not establish factual accuracy.

## Automated Windows checks

`.github/workflows/check.yml` runs offline tests and the browser workflow on
Linux, macOS, and Windows. An explicitly isolated Windows-only check saves,
loads, and deletes an invented credential under a unique temporary service name.
It does not use the app's normal saved credential.

The Windows packaging job builds `Sopmaker.exe`, exercises the packaged app's
startup/server/demo/export with no API key, and uploads a portable ZIP as an
Actions artifact. Its `build-info.json` identifies the source and dependency lock.
Extract the ZIP before running the executable and keep its `_internal` directory
beside it. No Python or separate FFmpeg installation is needed on that machine.

Standard hosted runners are free for public repositories; artifact storage has
separate plan limits. The workflow uses standard runners and retains its artifact
for seven days. [GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)

## Short manual Windows session

No cloud VM is needed for initial development. For the final manual pass, a
Windows VM accessed over Remote Desktop is convenient when no local Windows
machine is available. For example, Lightsail lists Windows Server instances
with 4 GB RAM at USD 44/month, billed hourly up to the monthly cap. A few hours
should cost well under a few dollars for the instance itself; check the selected
region, current rate and any additional resources before creating it.
[Lightsail bundles](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-bundles.html)

Delete a Lightsail instance when finished: stopped instances continue to incur
charges. Check whether snapshots or other retained resources also need removal.
[Lightsail billing](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-frequently-asked-questions-faq-billing-and-account-management.html)

Use the packaged ZIP from the passing CI run. Work as a normal desktop user:

1. Extract it under a path containing spaces. Open `Sopmaker.exe`, confirm the
   local browser opens, and navigate with keyboard and mouse.
2. Enter an API key in Setup. Test session-only mode, then opt into remembering
   it, restart the app, select **Use saved key**, and finally delete the saved key.
3. Import a small staged set: JPEG/PNG, phone MOV/MP4 with narration, a silent
   clip, and a voice memo. Check orientation, playback, labels and capture times.
4. Process the set. Interrupt one run, retry explicitly, and confirm completed
   chunks are reused. Use an invalid key once to check recoverable error display.
5. Find an observation by identifier, correct its note, mark it reviewed, restart,
   and confirm the correction survives. Confirm exports reject drafts unless
   their inclusion is explicitly selected.
6. Extract an exported evidence ZIP into another folder. Open `index.html`, its
   originals and the Word document. Check copied paths, thumbnails, draft/review
   labels and identifiers. Test Word in the actual reporting environment when
   available; generating a DOCX does not prove that template integration works.

Record Windows version, source/build revision, inputs, observed results and any
manual corrections. Windows Server CI/VMs validate much of the application,
but they do not establish the normal-user, Defender/SmartScreen, codec and
desktop behavior of the intended Windows 11 laptop. Obtain that final acceptance
check before claiming the target laptop workflow is supported.
