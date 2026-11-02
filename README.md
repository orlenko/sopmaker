# Sopmaker inspection notebook

An inspection assistant that turns photos, short narrated videos, and voice
notes into searchable draft observations with links to their source evidence.
Inspectors review the notes and use selected observations in their existing
reporting process.

The first milestone is a portable Windows proof of concept with light local
media preparation and external AI APIs. No hosted service is needed.
The [design](docs/inspection-notebook-design.md) defines the workflow,
architecture, evidence rules, implementation sequence, and pilot criteria.
A [synthetic observation](examples/observation.json) illustrates the proposed
record format; its filenames do not refer to actual media.

This repository currently contains the design and example data. Processing,
search, review, and Windows delivery are proposed features, not implemented
software.
