# Targeted regrading

## Scope and prerequisites

Regrading reruns one selected question for **all present students** in a
separate child run. The parent must have current, human-approved scores,
regions, rubric and attendance. It is not modified. The new child always
needs fresh review and approval of every identity and every score.

The original run is selected by omitting `--parent` or `--run`. A child can
also be a parent by supplying its ID. Run and proposal IDs are 1–64 ASCII
letters, digits, hyphens or underscores, starting with a letter or digit.
Run directories cannot be overwritten. Lineage is limited to 32 levels.
The same roster, scans, region boxes and attendance must remain current.
Individual-student-only reruns, changed scan order, mixed layouts and
automatic approval are not supported.

## Optional replacement rubric

Without a replacement, regrading uses the parent's approved rubric. To change
it, prepare an XLSX outside the output directory using the same `Marking
Scheme` columns as the normal rubric. It must retain the original questions,
order and labels, and must not change any other question's text, criteria or
marks. A general guide affecting other questions is therefore not allowed.

```sh
.venv/bin/handwriting-grader prepare-regrade-rubric --exam /private/exam.yaml \
  --proposal rubric-v2 --question Q1 --rubric /private/replacement.xlsx
```

For a child parent, add `--parent PARENT_RUN`. Compare the printed private
proposal workbook against the parent and source; review the selected
question's entire text, criteria and maximum marks. After explicit approval:

```sh
.venv/bin/handwriting-grader approve-regrade-rubric --exam /private/exam.yaml \
  --proposal rubric-v2 --confirm-reviewed
```

The proposal is hash-bound to the source, parent approval and exam
configuration. Editing either workbook or any bound input makes it stale.
Make corrections in a new source and prepare a new proposal; do not overwrite
or silently reuse the previous approval.

## Optional adjusted crops

The score editor's temporary crop preview accepts pixel coordinates and
does not save or change any grading input. To use different crops for a
regrade, write a private JSON file **outside** the output directory. Set
`shared` to replace the crop for every present script and/or `students` to
override individual zero-based script indices. Without `shared`, unspecified
scripts retain the parent's crop. Every crop must fit the question's existing
approved page; moving the question to another page is unsupported.

```json
{
  "shared": {"x": 100, "y": 120, "width": 300, "height": 200},
  "students": {"1": {"x": 110, "y": 125, "width": 290, "height": 190}}
}
```

```sh
.venv/bin/handwriting-grader prepare-regrade-crops --exam /private/exam.yaml \
  --proposal crop-v2 --question Q1 --crops /private/crops.json
```

Add `--parent PARENT_RUN` for a child parent. Inspect the proposal's
`manifest.json` and compare every resolved crop against its script using the
parent score editor's visual crop preview. Script indices start at zero;
the browser's script labels start at one. Only after explicit human review:

```sh
.venv/bin/handwriting-grader approve-regrade-crops --exam /private/exam.yaml \
  --proposal crop-v2 --confirm-reviewed
```

Add `--crop-proposal crop-v2` to the regrade command below. Both shared and
individual crop proposals still rerun the question for **all** present
students; they do not enable a single-student-only rerun. Coordinates,
source, parent and approval are bound to the child, and inherited by later
children. Source changes invalidate the proposal and descendants. Create a
new proposal rather than overwriting old files.

## Preview, rerun and review

Select a new child ID and preview with no paid calls:

```sh
.venv/bin/handwriting-grader regrade --exam /private/exam.yaml \
  --run revision-1 --question Q1 --model-config /private/model-config.yaml \
  --project PROJECT_ID --location LOCATION
```

Add `--parent PARENT_RUN` for a child parent. Add `--rubric-proposal rubric-v2`
or `--crop-proposal crop-v2` only if the separately approved proposal matches
this parent and question.
The preview displays the selected rubric, model, target, cache and upper
bound of `2 × present students + 1` requests: answer OCR and grading for each
student, plus whole-question moderation. Cache hits or skipped moderation may
reduce calls; retries and in-flight requests may incur additional charges.
After the user explicitly approves the target, data transfer and potential
cost, repeat the same command with `--confirm-cost`.

By default, run-scoped caching uses a new child cache. To reuse an approved
parent's existing private cache, explicitly enable at least one of the same
OCR, grading or moderation cache agents in the child model configuration and
add `--reuse-parent-cache` to both preview and execution. Preview shows the
exact directory. This may return previous model responses instead of an
independent fresh assessment; changed inputs, models, project, location or
cache implementation cause misses. Model aliases can change without changing
the cache key. Misses can add entries to the parent cache, and calls may still
incur charges. Human review and `--confirm-cost` remain mandatory. Shared
cache scope may already reuse responses without this flag.

The child is under `output_dir/runs/revision-1/`; images and inherited raw
results are bound to the approved parent. Other questions are not re-OCRed or
regraded. If an answer fails, it is not replaced with the old score; if all
selected answers cannot be moderated together, the selected answers are
flagged for manual review.

```sh
.venv/bin/handwriting-grader review-grading --exam /private/exam.yaml --run revision-1
```

Open the printed private local URL. Previous approved answers, reasoning and
marks appear alongside the child review; `grading/review.xlsx` also includes
a `Previous` comparison sheet. Values for unchanged questions and identities
are drafts only. Review **all** identities, answers, scores and reasoning
again. Failed, flagged or corrected answers require manual marks. Only after
the user explicitly approves all reviews, use the browser approval button or:

```sh
.venv/bin/handwriting-grader approve-grading --exam /private/exam.yaml \
  --run revision-1 --confirm-reviewed
.venv/bin/handwriting-grader report --exam /private/exam.yaml --run revision-1
```

Child reports, analytics, AI analysis, infographics and email are isolated
under the child directory. Pass `--run revision-1` to each downstream
command; without it, the command selects the original run. Each stage still
requires its own review, consent and output checks. A pass policy configured
for the exam is also checked against the child rubric's total marks.

## Recovery and validation

An interrupted child with a valid manifest and checkpoint, but without a
review workbook or approval, may be resumed:

```sh
.venv/bin/handwriting-grader resume-grading --exam /private/exam.yaml --run revision-1
```

Review the printed original target, then obtain fresh cost approval before
repeating with `--confirm-cost`. Completed checkpointed answers are retained;
an in-flight request can repeat. The original cache selection is preserved and
shown on resume. The exclusive lock prevents concurrent
resume; investigate a stale lock and never remove a live one. Partial runs
and prior outputs are not automatically deleted.
If creation was interrupted before the manifest and checkpoint were written,
resume cannot recover it; preserve the partial directory and use a new run ID.

Changes to parent approvals, review, results, manifest, images, rubric,
attendance or exam inputs invalidate the child. Existing child reports do not
disappear when this happens; do not distribute them as current. Hash-bound
approvals are local records, not tamper-proof identity verification. Offline
tests use synthetic inputs and fake services, not live Vertex or SMTP.
