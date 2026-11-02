# Evidence and editor decisions

The inspection notebook retains a small local review interface alongside the
shared CLI and processing core. A CLI with a static evidence index remains a
useful extraction experiment. The interface serves the inspector's additional
needs: correcting notes, marking review state, searching narration, and seeing
revision history without editing data files.

## Immutable evidence

Each actual preparation writes into a fresh revision directory. Regeneration
never overwrites derivatives referenced by an earlier note. Frame IDs include
that preparation revision. Transcription IDs include the preparation, model,
and vocabulary fingerprint, so different inputs cannot reuse a citation ID.

Completed transcription chunks are cached as append-only records. A partial
failure leaves the last complete transcript intact and can resume from its
completed chunks. A complete transcript is published atomically. Successful
draft generation binds each observation to an immutable evidence snapshot
containing the preparation, transcript, source IDs and time interpretation
used for that generation. The input snapshot and generated observations are
committed together.

Observation review, search, and export use that bound revision, even if the
source asset now has a newer transcript. Source-file review separately shows
the latest completed preparation and transcript, with current error state.
Selecting an old observation for export does not include a newer, unrelated
transcript. Exports identify evidence revisions and source offsets.

Schema version 2 migrates existing notes without discarding their text or
review state. Version 1 retained only an asset's latest transcript, so an
earlier association may already be irretrievable. Migrated notes are marked
`legacy_unverified` in the interface and export. Migration preserves the
available evidence and cannot establish which input originally generated
the note. New generations are bound to their stored input
revision; this verifies association, not the factual truth of model output.

## Editor state during saves

A save captures the submitted values and editor generation. The user may
continue typing or open another note while the request completes. Its response
updates the revision token for that save's editor, and only rebuilds that same
editor if no newer input exists. Later text remains dirty and can be saved
against the returned revision. A response from another editor cannot clear
the current editor's dirty state or change the displayed note.

Local JSON requests have bounded waits. A timeout or conflict leaves typed
input in the window. Because a timed-out save may have committed, the user
should check saved revisions before retrying. The server still rejects stale
revision tokens rather than overwriting another saved change.

## Source review without observations

Source files have their own review view, with original links, available frames,
players, transcript, and processing state. A zero-observation result is valid
and does not require manufacturing a note to expose the evidence. Pending
and failed files remain accessible through the same view.

Concurrent notebook workspaces are supported. Cookies have instance-specific
names because browsers share cookies across ports on the same host. The
shared importer enforces the file-size ceiling for both browser and CLI paths.

## Regression evidence

Offline tests use invented audio with two different transcripts, interrupted
transcription and failed drafting, and export of retained reviewed notes.
Browser tests hold save and refresh requests with explicit synchronization,
then type or switch notes before releasing the responses. They also open
zero-observation, pending and failed sources and exercise cookie-only media
and downloads with two notebook servers. No model calls or real inspection
material are needed for these checks.
