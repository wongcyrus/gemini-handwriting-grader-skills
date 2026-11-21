# Manual region review

## Prerequisites

- Successful offline preflight AND `check-scans --exam /path/to/exam.yaml` passing
  (run `check-scans` **before** `prepare-regions` so an upside-down or misordered
  first script cannot corrupt the template layout and bounding boxes for all scripts).
- **Approved rubric (`output_dir/rubric/rubric.xlsx` + `approval.json`)**: Always extract/import
  and approve the rubric (`approve-rubric --confirm-reviewed`) before running `suggest-regions`
  so Gemini receives the finalized list of question IDs and does not waste calls or miss sub-questions.
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
   `annotations.json` and a separate hash-based `approval.json`. Alternatively,
   after editing `annotations.json` directly, run:
   `handwriting-grader approve-regions --exam /path/to/exam.yaml --confirm-reviewed`.
   Saving another edit invalidates approval; source PDF, page images or configuration
   changes block the run.

## Scanner Shift, Orientation, and Page Alignment

Scanners commonly introduce vertical/horizontal shifts, slight skew, upside-down feeds, or out-of-order pages:
- **Orientation & sequence validation (`check-scans`)**:
  Run `handwriting-grader check-scans --exam /path/to/exam.yaml` (offline, 0 AI cost) **before** `prepare-regions` (and `grade` also runs it automatically before any Vertex AI calls). Using majority consensus across scripts, it flags any upside-down (`180°`), sideways (`90°/270°`), blank, or out-of-sequence page (including Script 1 itself) by exact PDF page number and script number. **Never auto-flip or auto-reorder pages** — instruct the user to fix the physical paper or source PDF manually and re-run `check-scans`.
- **Automatic alignment during `grade`**: Every rendered page is automatically aligned (rigid translation + small rotation via 4-quadrant phase correlation with outlier-quadrant rejection) to `output_dir/regions/images/` before cropping.
- **Recovering an existing unaligned run**:
  1. Run offline alignment (0 AI cost) and targeted re-OCR / regrade:
     - Align + re-OCR IDs: `handwriting-grader retry-grading --exam /path/to/exam.yaml --align-images --re-ocr-id`
     - Align + re-OCR all metadata (`ID`, `CLASS`, `NAME`): `--align-images --re-ocr-metadata`
     - Align + re-grade a specific question: `--align-images --question <Q_NUM>`
  2. If a template bounding box itself is too tight, adjust it in `review-regions` (click **Save page** and **Approve all regions**) or edit `output_dir/regions/annotations.json` directly and run:
     `handwriting-grader approve-regions --exam /path/to/exam.yaml --confirm-reviewed`
  Always preview without `--confirm-cost`, check the action plan, obtain cost confirmation, and rerun with `--confirm-cost`. This updates `results.json`, syncs the run manifest, and updates `review.xlsx` without discarding existing manual edits.

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
