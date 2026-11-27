# Manual region review

## Prerequisites

- Successful offline preflight for input validation. There is no separate
  heuristic scan-orientation/sequence check or automatic page alignment.
- **Approved rubric (`output_dir/rubric/rubric.xlsx` + `approval.json`)**: Always extract/import
  and approve the rubric (`approve-rubric --confirm-reviewed`) before running `suggest-regions`
  so Gemini receives the finalized list of question IDs and does not waste calls or miss sub-questions.
- The project's Python environment (PDF rendering uses pure-Python `pypdfium2`; no Poppler install needed).
- A verified output location. Inspect existing region images/drafts/proposals
  before resuming; do not recreate or overwrite an existing region directory.

## Procedure

1. Run `handwriting-grader prepare-regions --exam /path/to/exam.yaml`.
   It renders the first script into `output_dir/regions/images/`; it does not
   make model calls or modify the original PDF.
2. Recommend automated Gemini AI proposals: run `suggest-regions` with `--exam`,
   `--model-config`, `--project` and `--location`, reusing VERIFIED settings.
   Teachers can review and adjust proposed boxes rather than draw from scratch.
   Also offer an explicit **offline manual choice/fallback**: open `review-regions`
   and draw every required box without any model call. Honor that selection;
   AI suggestions are recommended, not mandatory, and unavailable access or
   declined cost consent must not block offline manual setup.
   The command previews the target and image count without sending anything.
   Obtain the user's explicit approval for data transfer and charges before
   repeating it with `--confirm-cost`. One page is submitted per request;
   retries or partial failures can add cost. Check model access and current
   pricing separately. A failure is not proof that no requests were billed.
   Refresh the private [output index](output-index.md) after proposals or failures.
3. Run `handwriting-grader review-regions --exam /path/to/exam.yaml`.
   Open the complete localhost URL shown in the terminal. Do not share its
   temporary token. The server runs until stopped with Ctrl+C; once regions are
   approved, stop the local `review-regions` server.
4. When AI suggestions exist, they are automatically loaded across all pages
   on startup. The Load AI suggestions button can also reload them across all
   pages. On each page, adjust boxes (drag to draw with live preview, move,
   resize with handle, relabel, delete, or nudge a selected box with Arrow
   keys / `Shift+Arrow` and remove with `Delete`/`Backspace` when the canvas
   is focused). Save and mark each page reviewed. Check all answer and
   metadata regions; the ID box must be on the first page.
5. Approve all regions only after reviewing every page. The editor writes
   `annotations.json` and a separate hash-based `approval.json`. Alternatively,
   after editing `annotations.json` directly, run:
   `handwriting-grader approve-regions --exam /path/to/exam.yaml --confirm-reviewed`.
   Saving another edit invalidates approval; source PDF, page images or configuration
   changes block the run. Refresh the private index after edits and approval;
   never describe saved boxes or AI suggestions as approval.

## Scan and crop review

Grading copies rendered pages unchanged before cropping; it does not heuristically
reject, align, rotate or reorder them. Basic page-count and approved-layout
dimension validation remains in place. Review the first script's orientation and
page order when approving regions.

Review Gemini-extracted IDs, names and classes for every student against the
roster and crops. Before any answers are processed, the engine requires all IDs
to be nonempty, unique and cover the expected present-student roster exactly.
Failures stop the run and record affected PDF pages/scripts in `grading/id-check.json`;
have the teacher inspect those source pages before authorizing recovery.
A readable ID is not proof that later pages are complete or ordered.
Inspect expanded/full-page views when an identity or answer looks clipped or
inconsistent; never silently fix the source PDF.

For incorrect boxes, review and approve corrected regions, then preview targeted
`retry-grading --re-ocr-id`, `--re-ocr-metadata` or `--question Q` for an unapproved
completed run. For separately reviewed shared or per-script answer-crop changes,
follow [child regrading](regrading.md). Obtain new cost consent before model calls.
Existing grading images and approval-bound evidence are not automatically
regenerated or changed, including images from older runs.

## Limits

The editor does not compare the rubric, but grading requires the approved
labels to match it. The user must verify the layout applies to all scripts.
Duplicate canonical labels are blocked because the original
grader cannot safely process them. The editor is local-only, not a hosted
multi-user application. Its approval record is not a tamper-proof audit log,
and grading enforces it. The Vertex path has been tested
against a local fake service, not with a live model.

If preparation detects an existing run, stop and ask the user to choose a new
output directory only if it cannot be safely reused for the requested review.
Do not delete the existing run to retry. Preserve `regions/images/` and
`grading/images/` during cleanup: these are approval-bound review evidence.
Missing referenced images require an explained, separately authorized exact
backup restoration, not silent regeneration.
