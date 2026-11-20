# Manual region review

## Prerequisites

- Successful offline preflight with confirmed scan order, pages per script and
  common layout. Layout extraction uses the first student's pages.
- Poppler `pdftoppm` on `PATH`, plus the project's Python environment.
- An approved output location; `output_dir/regions` must not already exist.

## Procedure

1. Run `.venv/bin/handwriting-grader prepare-regions --exam /path/to/exam.yaml`.
   It renders the first script into `output_dir/regions/images/`; it does not
   make model calls or modify the original PDF.
2. Optional AI proposals: run `suggest-regions` with `--exam`, `--model-config`
   pointing to the existing grader's config, `--project` and `--location`.
   The command previews the target and image count without sending anything.
   Obtain the user's explicit approval for data transfer and charges before
   repeating it with `--confirm-cost`. One page is submitted per request;
   retries or partial failures can add cost. Check model access and current
   pricing separately. A failure is not proof that no requests were billed.
3. Run `.venv/bin/handwriting-grader review-regions --exam /path/to/exam.yaml`.
   Open the complete localhost URL shown in the terminal. Do not share its
   temporary token. The server runs until stopped with Ctrl+C.
4. On each page click New box, enter a label, then drag to draw. Select a box
   to move or relabel it, use its bottom-right handle to resize, or delete it.
   Optionally load the separately saved AI suggestions; replacing existing
   boxes requires confirmation. Save and mark each page reviewed. Check all
   answer and metadata regions; the ID box must be on the first page.
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
