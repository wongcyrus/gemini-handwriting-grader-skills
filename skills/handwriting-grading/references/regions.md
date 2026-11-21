# Manual region review

## Prerequisites

- Successful offline preflight with confirmed scan order, pages per script and
  common layout. Layout extraction uses the first student's pages.
- **Extracted/approved rubric (`output_dir/rubric/rubric.xlsx`)**: Always extract or
  import the rubric before running `suggest-regions` so Gemini receives the exact
  list of expected question IDs and does not miss sub-questions or invent labels.
- Poppler `pdftoppm` on `PATH`, plus the project's Python environment.
- An approved output location; `output_dir/regions` must not already exist.

## Procedure

1. Run `.venv/bin/handwriting-grader prepare-regions --exam /path/to/exam.yaml`.
   It renders the first script into `output_dir/regions/images/`; it does not
   make model calls or modify the original PDF.
2. Automated Gemini AI proposals: run `suggest-regions` with `--exam`, `--model-config`
   pointing to the model config, `--project` and `--location`. Region setup must run
   Gemini for AI suggestion so teachers review and adjust proposed boxes rather than
   drawing them manually from scratch.
   The command previews the target and image count without sending anything.
   Obtain the user's explicit approval for data transfer and charges before
   repeating it with `--confirm-cost`. One page is submitted per request;
   retries or partial failures can add cost. Check model access and current
   pricing separately. A failure is not proof that no requests were billed.
3. Run `.venv/bin/handwriting-grader review-regions --exam /path/to/exam.yaml`.
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
   `annotations.json` and a separate hash-based `approval.json`. Saving another
   edit invalidates approval; source PDF, page images or configuration
   changes block the run.

## Limits

The editor does not compare the rubric, but grading requires the approved
labels to match it. The user must verify the layout applies to all scripts.
Duplicate canonical labels are blocked because the original
grader cannot safely process them. The editor is local-only, not a hosted
multi-user application. Its approval record is not a tamper-proof audit log,
and grading enforces it. The Vertex path has been tested
against a local fake service, not with a live model.

If preparation detects an existing run, stop and ask the user to choose a new
output directory. Do not delete the existing run to retry.
