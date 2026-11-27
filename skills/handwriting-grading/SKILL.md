---
name: handwriting-grading
description: >-
  Guides preparation and human review of scanned handwriting grading with
  Gemini Handwriting Grader. Use for exam configuration, input validation,
  rubric and bounding-box review, grading review, reports, or safe planning
  of student-score distribution. Does not provide a general OCR service or
  authorize automated grading or email.
version: 1
---

# Human-reviewed handwriting grading

## Prerequisites and scope

Use the repository's Python CLI, `handwriting-grader`, in its existing environment,
not notebooks. The engine uses `google-genai` with Google Gemini models and Vertex
project/location settings. Google ADK is an optional external integration only;
neither ADK nor Microsoft Agent Framework is required. PDF rendering uses
`pypdfium2` on Windows, macOS and Linux, without Poppler. LibreOffice is needed
only for optional answer-sheet conversion.

If installation is needed, install the checkout into the selected environment:
`python -m pip install -e /path/to/gemini-handwriting-grader-skills`.
Quote filesystem paths. Relative paths in `exam.yaml` resolve from its parent,
not the working directory.

Answer sheets, offline input validation, rubric import/extraction, reviewed
attendance, regions, grading, human score review, reports, statistics, optional
grounded verification, reviewed AI analysis, supported grading recovery, caches,
child regrading and gated email are available. Check installed CLI help before
using an enhanced command. If unavailable, explain the missing implementation;
never simulate success, use notebooks as a workaround, or claim approval.

## Safety and cost governance

- Keep scans, rosters, answers, feedback, caches, configurations and outputs
  private and outside version control. Do not expose credentials or student data.
- Never infer approval from a saved file, completed manifest, passing test,
  model response or index. Validate current hash-bound approvals and inputs.
- Do not overwrite outputs, delete history, call a paid model or send email
  without the relevant explicit authorization. Skill text is not enforcement;
  the executable stage must enforce its own gates.
- Preserve VERIFIED configuration and selected stages. Capability requirements
  differ by stage: image input for OCR/annotation/grading, structured JSON schemas
  for evaluation, compatible Gemini thinking settings for reasoning, and a
  separate embedding or image model where needed. Keep Google SDK/Vertex semantics;
  do not substitute another provider or promise capability from a model name.
  See [configuration](references/configuration.md).
- `discover-models --project PROJECT [--location LOCATION] [--write-config PATH]`
  lists model metadata by default; it does not perform paid capability pings.
  Only `--probe --confirm-cost` authorizes those paid pings, after a separate
  target/data/request/cost preview and consent. Listing or probing does not
  verify quota, current pricing, quality or permission for subsequent calls.
- Before **each new paid stage**, present the selected project/location/models,
  transferred data, request bounds and estimated cost using independently
  reviewed current Vertex rates. Request bounds exclude SDK retry attempts.
  Missing prices mean an unpriced estimate, not free usage. Reusing settings,
  caches or a previous consent does not authorize a new paid stage.
- After success or failure, use offline `cost-report --exam EXAM` and recorded
  `telemetry/gemini_usage.jsonl` / `cost_summary.json` to report recorded calls,
  usage, priced estimates and missing usage/pricing. These are not invoices or
  authoritative totals including every failed call and SDK retry. Google billing
  is authoritative. Do not promise fixed savings, accuracy or cohort consistency.
  Unknown embedding usage persists with `cost_usd: null` and **UNPRICED** audit
  rows/subtotals; never present it as zero cost.

## Private teacher output index

Once `exam.output_dir` exists, maintain a self-contained, offline `index.html`
there. This is an **assistant-maintained private guide**, not CLI-generated output
or an approval record. Refresh on resume and after **every output change,
failure, archive or cleanup**, before presenting the outcome. Link the page and
explain that it opens in a normal browser without a server or internet.

Use relative URL-encoded links and escaped HTML; group outputs by purpose and
run/version, with current deliverables first. Link collections, not student
identities. Clearly mark partial, failed, stale, unverified and superseded outputs;
never link absent files or imply file presence means approval. Preserve user
content outside the single managed section. Do not replace an unmanaged index.
If maintenance fails, report it and provide actual output links directly.
Follow [output-index instructions](references/output-index.md). Updating the guide
must never trigger a model call, retry, approval, email or deletion.

## Start or resume without repeating setup

Inspect existing exam/model settings, stage choices, manifests, approval bindings
and available session context first. Reuse VERIFIED values; ask only for unresolved
settings needed by the requested stage, grouping related questions. Offline
preparation/review does not need credentials or a complete model profile.
Defer embeddings, image generation and email settings until requested. Do not
rewrite an approved configuration to add an optional stage.

This table is orientation, **not evidence of approval**. Resolve attendance's
current version, inspect `grading/results.json.completed`, and validate bound
inputs before choosing a branch. Missing referenced region/grading images can
invalidate approvals: stop and explain, then obtain permission to restore the
exact files from a verified backup. Do not silently regenerate or reapprove.

| Artifact / status | Possible stage | Next action |
|---|---|---|
| No exam configuration | Not configured | Resolve stage-needed settings; use the existing [exam template](../../exam.yaml.template). |
| Exam configuration exists | Configured, not necessarily verified | Reuse verified choices; offline `preflight`; prepare attendance or rubric as needed. |
| `attendance/current.json` selects a version without current approval | Attendance prepared | Review `attendance/versions/<review_id>/attendance_review.xlsx` -> `approve-attendance`. |
| `rubric/rubric.xlsx` without current approval | Rubric prepared | Review source and workbook -> `approve-rubric`. |
| Current rubric approval, no region setup | Ready for regions | `prepare-regions` -> recommended paid `suggest-regions`, or explicit offline manual choice -> `review-regions`. |
| `regions/images/` and draft/proposals without current approval | Regions prepared | Review every page -> explicit `approve-regions` or editor approval. |
| Current region approval, no grading run | Ready to grade | Preview `grade`; obtain new cost consent. |
| Incomplete grading + `grading/id-check.json` with `passed: false` | ID check blocked answers | Report every flagged PDF page/script and ask the teacher to inspect the source. No answer OCR/grading. A separately consented resume re-reads invalid saved metadata crops; changed sources require fresh preparation/approvals. |
| `grading/manifest.json` + `grading/results.json` with `completed: false` | Incomplete grading | Supported `resume-grading`; optionally hash-bound `retry-failed-batches` or separate offline `export-failed-review`. No ordinary completed-run review/approval yet. |
| Same files with `completed: true`, no current score approval | Completed grading, possibly failed marks | Manual review or appropriate failure recovery; optional grouping -> `review-grading` -> explicit approval. Do not resume a completed run. |
| Current `grading/approval.json` | Scores approved | Proactively present the post-approval menu below. |
| Report/analytics manifests | Outputs may be current, partial or stale | Validate status; offer remaining menu choices; refresh index. |
| `ai-analysis/review.xlsx` without current analysis approval | Analysis generated | Review/correct every row -> separate `approve-analysis`. |
| Current `ai-analysis/approval.json` | Analysis approved | Offer advanced reports with or without paid infographics; honor a clear existing selection. |
| `email/preview.json` without current approval | Email preview only | Inspect exact recipients/body/attachments -> `approve-email`; sending remains separately gated. |
| `runs/<child>/` | Isolated regrade | Inspect child artifacts; pass `--run CHILD` to supported child stages. |

Unrecognized, corrupt or older checkpoints are not automatically resumable.
Preserve partial outputs and explain supported recovery rather than inventing a
checkpoint filename or overwriting a run.

## Teacher stage flow

1. Read [configuration](references/configuration.md). Reuse existing choices;
   optional [answer sheets](references/answer-sheets.md) can run before scans.
2. Run offline `preflight --exam EXAM` before region preparation. It validates
   inputs, not scan orientation, page sequence, credentials, model access or approval.
   Do not add a separate heuristic scan check or automatically align, flip or
   reorder pages. Review Gemini-extracted identities for all students against
   the roster and crops; resolve unreadable, missing, duplicate or unmatched IDs
   with the teacher. Readable IDs do not verify other pages.
   No mandatory paid printed-marker stage is required. If attendance is configured,
   follow [attendance](references/attendance.md) and obtain its separate approval.
3. Import an XLSX rubric or preview/consent to DOCX extraction. The teacher compares
   every question, marking criterion and maximum to the source, then explicitly
   approves. Rubric approval precedes region suggestions.
4. Follow [regions](references/regions.md): render pages offline, recommend Gemini
   AI proposals with a separate cost preview, and offer an explicit offline manual
   drawing fallback/choice. Review all metadata/question boxes and every page,
   then approve separately. AI proposals never imply approval.
5. Follow [grading](references/grading.md): retain the chosen mode, or explain
   `batch` (default, question chunks) versus `item` (per-answer grading plus
   moderation) if unresolved. Preview actual planned bounds before consent.
   After region approval, read metadata for **all students first**, with no
   answer crops sent. Continue automatically only if every ID is readable,
   unique and the set exactly matches the expected present-student roster.
   Otherwise stop, report the exact PDF page/script numbers from
   `grading/id-check.json`, and have the teacher check the source; never
   bypass the ID gate or start answer processing while IDs are unresolved.
   The Gemini application image cap is **105 images per request**, not a Google
   provider limit; this retains the old 35-script metadata chunk across three
   fields. Payload/context/quota constraints may require smaller chunks.
6. After errors, report failed counts/questions and error classes immediately.
   Offer manual review, quota investigation/waiting, or separately consented
   supported recovery. Use SDK retries, never blind retry loops.
   Incomplete and completed recovery branches are distinct; see
   [grading recovery](references/grading.md#failure-recovery).
7. `cluster-answers` and `review-grading` default offline, using cached/local
   lexical grouping. Paid embeddings require `--preview-embedding`, optional
   `--model-config`, then new consent with `--confirm-cost --revision REVISION`;
   the printed **Review and configuration revision** binds both review/grading
   state and the selected model-config SHA-256. Changes to either require a new
   preview and consent. Native embeddings require explicit `vertex_ai.project_id`
   and `vertex_ai.location` in the resolved configuration; offline grouping does
   not. Never overwrite clusters. Review every identity and score; manual marks are
   required for failed, flagged or corrected answers. Saved edits are not approval.
   Retain the status dashboard (`Alt+S`), grouped view (`📌 Grouped`), batch review
   (`Ctrl+Enter`) and final explicit approval (`Alt+A`).
8. Immediately after current score approval, **proactively offer all choices**:
   - **Standard reports (`report`)**: offline marked PDFs, combined PDF, audit
     samples and score workbooks.
   - **Statistics (`analytics`)**: offline cohort/class/question workbooks/charts.
   - **AI analysis (`ai-analysis`)**: separately paid, reviewed feedback and
     misconceptions -> separate `approve-analysis`; then advanced teacher DOCX
     and 16:9 PPTX. Ask whether optional paid infographics are wanted if unclear;
     honor an already clear choice. `advanced-report` is offline by default,
     compatible with `--no-infographics`; paid images need explicit
     `--infographics --confirm-cost`, separate image settings as needed, and
     a fresh `--report-name` if preserving another version. Keep approved analysis
     configuration, editable content and full speaker notes unchanged.
   - **Moodle/LMS (`moodle-export`)**: offline PDFs and optional CSV/ZIP upload.
   - **Email**: exact preview -> separate preview approval -> separately
     authorized send, following [email](references/email.md).
9. After every output-changing action or failure, refresh the private index.
   Offer separately confirmed `clean --exam EXAM` only after describing its plan
   and verifying backup. Preserve sources, `regions/images/`, `grading/images/`,
   reviews/history, approvals, journals and final deliverables; do not present
   cleanup as permission to remove approval-bound evidence.
10. For a selected-question regrade, follow [regrading](references/regrading.md):
    approved parent, new child, separate cost consent and fresh complete review.

See [workflow gates](references/workflow.md) and
[flow and notebook parity](references/flow-and-parity.md) for supporting details.
