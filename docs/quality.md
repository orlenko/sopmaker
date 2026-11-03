# Shared quality setup

This is a **partial, inspection-only setup** for `orlenko/sopmaker`. The source
config and lock declare the shared plan; they do not install a runtime in each
clone or establish executed checks, enforced review, or verified adoption.
Command execution is disabled with `trusted_local: false` and
`isolation: unavailable`. No application behavior or dependency changes here.

The project owner integrates changes; one designated implementer coordinates
quality-file edits across checkouts. Use an owned branch/worktree and preserve
concurrent product work. The [design](inspection-notebook-design.md),
[evidence/editor decisions](evidence-and-editor-decisions.md),
[native testing guide](testing.md) and canonical
[CI workflow](../.github/workflows/check.yml) retain their roles.

## Approved identities

Use the shared [policy 0.1](https://github.com/orlenko/software-quality/blob/db120201d3f38d1c6e444bc4d1ca9586c3d8eee0/docs/development-standard.md)
and [runner instructions](https://github.com/orlenko/software-quality/blob/db120201d3f38d1c6e444bc4d1ca9586c3d8eee0/README.md),
rather than a copied policy engine. The lock pins the existing R2 archive;
the later shared-source integration is not its build source.

| Component | Approved identity |
| --- | --- |
| Publisher | `orlenko/software-quality` |
| R2 engine / policy / schema / adapter protocol | `0.1.2` / `0.1` / `1` / `1` |
| Archive build source | `db120201d3f38d1c6e444bc4d1ca9586c3d8eee0` |
| `software-quality-0.1.2-root-alias.pyz` SHA-256 | `7b87b9c23fbaf43941729f8bb2b0e1969fbf6872b02d8dbd23e074714bc526b0` |
| `quality_launch.py` SHA-256 | `14d77ae4dd7a9fa69197f970fc7c98a716b65c1e7486ceb668b7b8f7a621aaf6` |
| `quality_trust.py` SHA-256 | `4c3ebc346b1428e1d226180ecbb05a249c90de48ae25cdc8438b89bb901f6c2e` |

Do not rebuild, rewrap, relabel, select `latest`, or replace these pins from an
adjacent checksum. Obtain these exact existing bytes through the approved owner
route and verify them before loading. Preserve historical/held archives.
The optional adapter bundle and live source-review controller are not installed
or enabled by this setup. No global plugin or slash command is registered.

## Whole native gate and effects

The config's nine argv entries identify native commands and effects. They are
**not a runnable cross-platform sequence**. Every required native gate remains
required; a local subset or shared `doctor` report cannot replace the workflow.

| Gate | Required native context and effects |
| --- | --- |
| Provisioning | Each CI job uses locked `uv sync --locked --python 3.13`. Browser jobs also install Chromium and platform dependencies with `uv run playwright install --with-deps chromium`. These downloads/provisioning are separate from test execution. |
| Ruff, pytest, three browser suites | Required on Linux, macOS **and** Windows in `test`. Tests use invented data, temporary notebooks/media/SQLite/exports, null or fake credentials/providers, owned loopback servers and Chromium. Caches and installed dependencies are additional state. |
| Windows credential smoke | Windows-only opt-in command under a unique synthetic service, with cleanup. It really accesses the OS credential backend; it does not use the app's normal saved key. Mapping its argv grants no new permission to run it on a personal host. |
| Windows build | Depends on all test jobs. Requires real Git metadata, writes executable/build/source/license/hash inventories, and may fetch pinned public native sources. |
| Packaged smoke | Runs the built executable using invented state and a null keyring; no model call. Requires the preceding build. |
| Portable and setup archives | CI creates the portable ZIP, copies setup files and executable into a setup directory, compresses it, then extracts `dist/Extracted-Setup`. These preparation steps are prerequisites for the setup smoke. |
| Setup/update smoke | Windows PowerShell 5.1, temporary installation/desktop/Start menu destinations, synthetic data and junctions, owned processes, and a per-user LocalAppData setup lease file. Exercises refusal, update, serialization and rollback; no real app deployment, saved-key use or app restart. |
| Artifact uploads | Normal native CI retains portable/setup artifacts with build identity for seven days. This does not approve a release, deployment or installation. |

R2 executes each check in a fresh Git-free snapshot and supports POSIX execution
only. That cannot supply the Windows matrix, build's Git metadata, or shared
build/ZIP outputs. Its 600-second per-command bound remains intact; the native
workflow's existing job bounds are unchanged. Keep execution disabled until a
separately reviewed, authorized supported route exists. Do not manufacture a
Git index, turn missing prerequisites into skips/passes, or toggle the two
execution fields as a shortcut to full adoption.

Actual staged-content/privacy review and the manual acceptance checks below
remain outside the shared command plan. No private-input skip can become a
passing full gate. Paid `live_smoke.py` is opt-in and is not part of this rollout.

## Reviewer requirement and integration

`unavailable-enforced-source-reviewer` is a reserved, **unavailable** future
principal. Schema 1 requires `boundary.enforced: true`; this is an **unmet
requirement**, not an observed boundary or enforcement attestation. One route
with `require_distinct_identities: true` does not itself establish independence.
The network flag describes required permission, while `public-source` describes
disclosure, including public text entering a hosted review context. Neither
authorizes a new provider, account, API call or upload of private material.

Current independent human/separate-agent bootstrap reviews are procedural.
Do not convert them into a recorded-v1 response claiming mechanical enforcement.
Before any future response is accepted, independently approve and bind the
actual reviewer/controller identity, tools, permissions, disclosure, exact
context and response digest through an accepted config change. Until then,
leave the required review stage incomplete; do not invent a witness.

Nontrivial work requires a short problem/acceptance/effects/failure/test design
and independent critique **before** implementation. Obtain independent finished
adversarial review of the exact change before integration, resolve reachable
blockers, and re-review affected fixes. Review against the accepted rules, not
rules introduced by the same change. The project owner and coordinating reviewer
adjudicate disputes with evidence; they cannot waive a valid blocker.

For this initial setup, the smallest choice is four text/config files and an
outside-target documented invocation. A docs-only inventory would not establish
real pins/configuration; a target-side shim or adapter adds no needed capability
while execution is disabled. Independent pre-edit critiques accepted this design
with the unmet reviewer boundary, full native mapping, accepted-base context and
exact-tree checks retained. The original product-base-to-setup diff and its
finished bootstrap review remain separate from the empty-diff metadata probe.

Before publishing, inspect the real staged contents, filenames and commit
metadata for secrets/private material; use `git diff --cached --check` as a
format check, not a privacy scanner. No automated secret/privacy scanner is
claimed here. Use invented inputs; keep local test media, outputs, credentials
and raw evidence out of Git. Source review does not authorize private pixels or
text entering hosted contexts.

Use normal project CI and exact-tree independent review before a main integration.
Confirm the source tree is unchanged between evidence collection and integration;
reconcile a moving base. UI/product changes still need workflow/accessibility
evidence. Windows 11 desktop/SmartScreen/Word/codecs, report correctness and any
clinical/domain acceptance are separate manual work, not proven by this setup.
There is no release/deploy/app-restart authority here. Preserve BYOK boundaries,
notebooks, revisions, originals and existing recovery behavior.

## Outside-target invocation

Use an existing approved absolute POSIX Python with `-I -S`; the observed native
Darwin runtime for this rollout is CPython 3.14.7. Initial interpreter, stdlib,
bootstrap and import provenance require owner approval before loading. No global
runtime/environment is provisioned. Missing `waitid`/`WNOWAIT` or process-cleanup
capability is an incomplete prerequisite; do not weaken the guards. Windows
continues to use its native CI, not this runner.

Choose a **new owned private** root outside the repository and toolkit source.
Keep bootstrap, cache, state and evidence as separate directories there. Use one
assigned state root for this canonical repository across its worktrees. Only copy
the verified archive and approved launcher/trust files into bootstrap; never use
head-side code to locate or select the runner. Keep other artifacts unchanged.
The following paths are operator-supplied placeholders, not an installer:

```sh
QUALITY_PYTHON=/absolute/path/to/approved/python3.14
QUALITY_BOOTSTRAP=/outside/owned/sopmaker-quality/bootstrap
QUALITY_CACHE=/outside/owned/sopmaker-quality/cache
QUALITY_STATE=/outside/owned/sopmaker-quality/state
QUALITY_REPO=/absolute/path/to/sopmaker

env -i PATH=/usr/bin:/bin LC_ALL=C "$QUALITY_PYTHON" -I -S -B \
  "$QUALITY_BOOTSTRAP/quality_launch.py" install \
  --artifact "$QUALITY_BOOTSTRAP/software-quality-0.1.2-root-alias.pyz" \
  --source db120201d3f38d1c6e444bc4d1ca9586c3d8eee0 \
  --sha256 7b87b9c23fbaf43941729f8bb2b0e1969fbf6872b02d8dbd23e074714bc526b0 \
  --cache "$QUALITY_CACHE"
```

Set `QUALITY_BASE` and `QUALITY_HEAD` to full, independently accepted immutable
commit identities. Config/lock/references are read from **base**, not head. The
initial `base=head` inspection is permitted only after independent acceptance of
the exact setup bytes; it inspects an empty diff and is not review/testing of the
rollout change. Any premerge diagnostic is provisional. Repeat on the actual
integrated commit and verify the persisted context, trees, config/lock/artifact
hashes, inspection/artifact/source stages and all remaining incomplete stages.

```sh
env -i PATH=/usr/bin:/bin LC_ALL=C "$QUALITY_PYTHON" -I -S -B \
  "$QUALITY_BOOTSTRAP/quality_launch.py" doctor \
  --repo "$QUALITY_REPO" --base "$QUALITY_BASE" --head "$QUALITY_HEAD" \
  --identity orlenko/sopmaker --cache "$QUALITY_CACHE" --state "$QUALITY_STATE"
```

No `--execute-trusted` or review response is supplied. Expected exit **2** means
incomplete; validate the report rather than treating any exit 2 as success.
An input/pin/capability error is not a successful doctor inspection. These
commands use only approved shared code, isolated startup, Git/source inspection,
benign supervision probes and new outside-target reports/locks/cache. A clean
environment/worktree is not containment for unknown target code.

Report configured source, installed tooling, metadata inspection, native checks
and verified adoption separately. This setup cannot claim a full shared gate
pass while required execution or enforced-review evidence is missing.

## Manual review supplement adoption

On acceptance of this documentation increment, Sopmaker prospectively adopts
the manual source-process supplement at `orlenko/software-quality`
`36b0b858b293e14af726e0adf58af476334ccd5d` (3 November 2026):
[workflow](https://github.com/orlenko/software-quality/blob/36b0b858b293e14af726e0adf58af476334ccd5d/docs/review-workflow.md)
and [roles](https://github.com/orlenko/software-quality/blob/36b0b858b293e14af726e0adf58af476334ccd5d/docs/review-roles.md).
[AGENTS](../AGENTS.md) is the discoverable entry point. This source pin is
separate from every runtime/policy/schema/config/lock/artifact identity above;
none is advanced or enabled by manual adoption.

Plan independently working increments and record their immutable comparison
bases, authored line counts and interaction gates. About 500 and fewer than
1,000 authored changed lines are recommendations, not new merge-size limits.
A larger coherent increment records its rationale and bounded review plan.

For substantial designs, obtain skeptical general critique before implementation.
Select and justify specialist lenses separately at design and code stages:
privacy/security for authentication, secrets and disclosures; data integrity for
evidence/revisions, concurrency and partial writes; reliability for background
work, cancellation, timeouts and resource limits; product/accessibility for
changed workflows, report claims, keyboard and scaled desktop behavior. Selection
depends on the increment, not a permanent blanket checklist. A relevant unavailable
lens is incomplete, not inapplicable. Each selected specialist has a separate
reviewer context and report. The fresh general code bug hunt forms its own
findings before reading peer conclusions.

The established route is the existing Orchestra collaboration with independently
commissioned native read-only public-source reviewer contexts. Record actual
identity/context, exposure, source, rubric, allowed tools/disclosure and inspected
scope with each report. A named route or fresh conversation alone does not prove
independence, enforcement or permission. If a required context cannot be supplied
within current authority, preserve the gap; do not switch services or buy access.

The conductor can coordinate a separate native adjudicator, but cannot adjudicate
a report it authored. Neither the implementer nor an author of the reports being
reconciled can be that adjudicator. Escalated disputes belong to project owner
`orlenko`. Preserve original report/finding IDs and distinct scenarios, group only
true duplicates, record evidence-based dispositions and fix priority, and keep
review completeness separate. Valid blockers require correction/removal and
affected independent re-review; no vote, round limit or summary waives them.

The ongoing owner-approved UI/lifecycle implementation began before this adoption
dispatch. Its existing general design critiques remain those actual reviews;
they are not retroactively specialist design passes, fresh code bug hunts or
separate adjudication. Record remaining reviews against the exact affected source;
future substantial design changes receive prospective review. Do not invent a
completed feature walkthrough or rewrite earlier chronology.

Before main integration, retain the full native CI and exact-tree independent
review required above, including this documentation increment. Check staged
content/metadata, unchanged runtime pins, finding dispositions and affected fixes.
Windows 11/125% scaling, assistive technology, Word/report/domain acceptance and
paid model evaluation remain separate evidence. Manual reviews do not satisfy
the unavailable enforced `recorded-v1` route or establish a full shared-tool pass.
Per-increment review/check/source evidence belongs in its PR or durable linked
record; missing required evidence remains explicit there.
