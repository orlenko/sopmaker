# Working on Sopmaker

Read the [local quality rules](docs/quality.md), [native testing guide](docs/testing.md)
and [evidence/editor invariants](docs/evidence-and-editor-decisions.md).
Preserve concurrent work and use an owned branch. One implementer coordinates
process/setup changes for this canonical repository across checkouts.

This repository prospectively adopts the centralized manual review supplement at
`orlenko/software-quality` source `36b0b858b293e14af726e0adf58af476334ccd5d`:

- [Manual workflow](https://github.com/orlenko/software-quality/blob/36b0b858b293e14af726e0adf58af476334ccd5d/docs/review-workflow.md)
- [Review roles](https://github.com/orlenko/software-quality/blob/36b0b858b293e14af726e0adf58af476334ccd5d/docs/review-roles.md)

Keep role instructions centralized. Plan small working increments; roughly 500
and fewer than 1,000 authored changed lines are guidance, not a size gate.
Substantial changes need independent skeptical design critique and risk-selected
specialists at design and code stages, each in a separate context/report. Use the
existing approved native review route; no new provider or paid API is authorized.
A fresh general bug hunt forms findings before reading peer conclusions. A
separate adjudicator preserves original finding IDs, evidence and completeness;
the implementer and report authors cannot adjudicate their own reports.

Record actual identities, exposure, exact source, applicable/unavailable lenses,
dispositions and affected fix re-review. Do not backfill review chronology.
Required missing review remains incomplete. Disputes go to the project owner,
`orlenko`; no summary or owner decision can waive a valid blocker.

Integration still requires exact-tree independent review and the full existing
three-OS/browser/Windows gates. Use invented data and fake/null providers; keep
credentials, private media and generated inspection outputs out of Git and
hosted review contexts. Source integration, releases, deployment and app restarts
remain distinct effects. See the [adoption record](docs/quality.md#manual-review-supplement-adoption).
