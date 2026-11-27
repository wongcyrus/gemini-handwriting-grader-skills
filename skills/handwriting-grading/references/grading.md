# Rubric, grading and reporting

## Prerequisites

- Read [configuration](configuration.md) and [regions](regions.md); inspect
  existing settings and approvals first. Reuse VERIFIED configuration/mode
  choices and ask only unresolved stage-needed questions. Settle relevant
  configuration before approvals; confirm scan order/layout only if unresolved.
- Use a private, separate output directory. Never commit student data.
- For Vertex operations, obtain explicit user approval of the project,
  location, data transfer and costs. Authenticate separately; live model
  availability and quality have not been verified by the offline tests.

## Procedure

1. Import an XLSX with `handwriting-grader import-rubric --exam
   /path/to/exam.yaml`. Its `Marking Scheme` sheet requires the columns
   `question_number`, `question_text`, `marking_scheme`, `marks`. For a DOCX
   use `extract-rubric` with `--model-config`, `--project` and `--location`;
   preview first, then add `--confirm-cost` only after user approval.
   Optional `verify-rubric` uses `models.marking_scheme` and Google Search
   with the same target flags and a separate cost/data consent. New runs write
   `rubric/verification/verification.xlsx` and `verification.docx` from the
   same responses. Review source links and claims; this does not approve the
   rubric or establish that a claim is correct.
2. Ask the teacher to verify `output_dir/rubric/rubric.xlsx` against the
   source. Run `approve-rubric --exam /path/to/exam.yaml --confirm-reviewed`
   only after explicit human approval. Complete region review separately.
3. Run `grade --exam /path/to/exam.yaml --model-config /path/to/config.yaml
   --project PROJECT_ID --location LOCATION [--mode batch|item] [--concurrency 3]` to preview.
   Explain the request count and data sent; repeat with `--confirm-cost` only after approval.

   **Choosing `--mode batch` vs `--mode item`**:
   | Mode | Models Used | Best For | When to Switch |
   |---|---|---|---|
   | `--mode batch` **(Default)** | Configured `models.batch_grading` reasoning model | Question-grouped short/medium answers, math steps or diagrams; potentially fewer application requests. | Consider smaller chunks/item mode for payload/context constraints. |
   | `--mode item` **(Fallback)** | Configured `models.grading` + `models.moderation` | Per-answer fault isolation or long answers; separate question moderation. | Consider when batch constraints make it unsuitable. |

   - Batch mode sends nonblank crops grouped by question in bounded chunks,
     with local Pillow blank filtering. The **Gemini application image cap is
     105 images per request**, not a Google provider limit or an Azure cap.
     Metadata still fits the legacy **35 scripts × three fields** (ID/CLASS/NAME).
     Answer batches and metadata must honor the cap; payload, context,
     output-token budgets and quota can require smaller chunks. Successful
     metadata chunks are checkpointed; an interrupted in-flight call can repeat.
   - Item mode evaluates each nonblank answer, followed by whole-question text
     moderation. This isolates some per-answer failures but can require more
     application requests. Neither mode promises a fixed saving, accuracy or
     consistency; both can hit context, payload, quota or model errors.
   - Preserve the selected mode; if unresolved explain tradeoffs during preview.
     Preview request bounds exclude SDK retry attempts, which can increase bills.
     Both modes use downstream `results.json` and `review.xlsx` schemas.

   The engine uses Google Gen AI SDK progress/usage recording, not required ADK.
   To inspect model usage, token count, and cost breakdown at any time, run:
   `handwriting-grader cost-report --exam /path/to/exam.yaml`
   (generates `cost_report.xlsx` and `cost_summary.json` in the run directory).
   These are recorded usage and priced estimates, **not invoices** or a verified
   total of all failed calls/SDK retries. Report unknown usage/rates honestly
   after both success and failure; Google billing is authoritative.

   Rendered pages are copied unchanged before cropping, without a heuristic scan
   check or automatic page alignment. **All-student ID gate:** metadata crops
   (`ID`, `CLASS`, `NAME`) are extracted for all students before any answer crop
   is read or submitted. Every ID must be nonempty, unique and match the expected
   present-student roster exactly; scan order need not match roster order and
   leading zeroes are preserved. If all pass, answer grading continues automatically.
   Otherwise the engine stops with exact 1-based PDF page/script numbers,
   `grading/id-check.json`, and an incomplete metadata checkpoint without a review
   workbook. Have the teacher inspect the flagged source pages. Do not start
   answer OCR/grading or infer that the source file is definitely wrong: model
   transcription or a tight crop can also be the cause.

   After inspection, a separately previewed/consented `resume-grading` re-reads
   only invalid or missing scripts' metadata from the saved page images, validates
   every ID again, then proceeds only if all pass. It never silently reloads an
   edited PDF. Source changes require fresh preparation and approvals; preserve
   the blocked run. Passing IDs do not validate other pages or replace the later
   human identity review. Existing completed runs are not automatically regenerated.

   **Candidate-Assisted Metadata OCR (Roster Matching)**:
   When extracting student metadata (`ID`, `CLASS`, `NAME`), the grading engine automatically checks for an approved attendance roster or configured exam roster (`attendance` or `roster` in `exam.yaml`). If available, known valid candidate values are injected directly into Gemini's extraction prompt:
   - Candidate matching can reduce handwriting ambiguity (e.g. `0` vs `O`,
     `1` vs `I`, `8` vs `B`); it does not guarantee correct identity matching.
   - Preserves safety guards: if an image is blank or contains text not matching any enrolled candidate, Gemini returns empty or the raw text as read without guessing or forcing false matches.
   - Always have the teacher verify exact identities against the roster and crop;
     a candidate-constrained model response is not human verification.

   **Handling Errors & User Decision Gating**:
   Transient network or server errors are handled at the connection layer using built-in
   Google GenAI SDK retry options (`types.HttpRetryOptions` on `genai.Client`).
   If any items still fail after SDK retries:
   - **Do NOT auto-retry in Python**: Never write ad-hoc loops or restart scripts. If GCP
     quotas (429 ResourceExhausted) or deadlines (504) are reached, Python loops will
     only burn quota or fail repeatedly.
   - **Notice user for decision**: Immediately report the failure diagnostics (number of failed marks,
     question numbers, error codes) to the teacher/user and ask whether they want to:
     a) Wait/investigate GCP quota, then choose the supported recovery branch, or
     b) Review completed failed items offline, or export an incomplete run's
        separate manual worksheet without additional API calls.
     First check `results.json.completed`; see [failure recovery](#failure-recovery).
   - **User-Approved Completed-Run Retry**: Only upon explicit user decision and cost consent, run:
     `handwriting-grader retry-grading --exam /path/to/exam.yaml`
     Preview the failed marks and unmoderated questions, obtain cost confirmation,
     and rerun with `--confirm-cost`. `retry-grading` isolates and regrades only the
     failed crops using exponential backoff, runs moderation for affected questions once
     all student marks are present, and refreshes `grading/review.xlsx` while preserving
     any existing manual reviews.
3b. **Optional grouping before review**:
    `cluster-answers --exam /path/to/exam.yaml [--threshold 0.82]` and
    `review-grading` default offline with cached/local lexical grouping, not eager
    embedding calls. Local "Word Overlap (Local)" remains available with zero
    model cost. Opening the editor does not authorize paid embeddings.

    For paid semantic clustering, preview
    `cluster-answers --exam /path/to/exam.yaml --preview-embedding
    [--model-config /private/config.yaml]`. Reuse verified embedding settings;
    inspect the resolved model/Vertex target, data, request bounds and current
    prices. After separate consent, rerun the selected action with
    `--confirm-cost --revision REVISION` using the exact printed **Review and
    configuration revision**. It binds both the review/grading revision and the
    selected model-config SHA-256; changing either, including target/model settings,
    requires a new preview and consent, not a bypass.
    Native embeddings require explicit `vertex_ai.project_id` and
    `vertex_ai.location` in the resolved configuration. Offline grouping does not
    require them. Existing `clusters.json` must not be overwritten.

    Semantic groups use cosine thresholds, not fixed K-means cluster counts.
    Higher thresholds yield tighter groups; embedding dimensions depend on the
    selected compatible Google model. Identities are excluded from grouping.
    Caches are sensitive, opt-in and can become stale when aliases change.
    Shared caching requires explicitly configured private scope/directory;
    do not promise indefinite freshness or zero calls for uncached answers.
    Unknown embedding usage remains `cost_usd: null`, with **UNPRICED** audit
    rows/subtotals; missing usage is not evidence of free inference.
    Grouping does not review, score or approve answers.
4. Ask the teacher to run `review-grading --exam /path/to/exam.yaml` and open
   the full printed local URL. This is offline by default; optional
   `--model-config` and `--confirm-cost` are only for a separately requested,
   previewed and consented embedding stage. Review must not automatically send
   answers to a model or require a complete model profile. The score editor presents a one-page blind
   marksheet per question (hiding student IDs during question review), groups
   similar answers together under similarity banners with a dedicated `📌 Grouped`
   toggle button, lets the teacher sort by clicking table headers (`Answer`, `Script`,
   `Mark`, `Similarity`, `Status`), filter to only rows requiring a mark/review
   (`Needs Mark ⚠`), expand bounding boxes (`Expand Box`), resize or zoom/pan
   handwriting crops (`+Context` / `Full Page`), toggle columns, and edit marks,
   answers, and reasoning inline (display, grouping, filter, and sort preferences
   persist across questions).
   - **All Questions Status Modal (`Alt+S`)**: A live overview dialog showing
     review completion for all questions and identity matching, filtering by
     "Incomplete Questions Only", and allowing instant keyboard (`Enter`) or mouse
     jump to any question.
   - **Batch Review Confirmation (`Ctrl+Enter`)**: "Confirm Question Reviewed"
     and "Match OCR IDs" save all items in a single instant batch request (`/api/save-batch`)
     without blocking keyboard shortcuts.
   - **Review Reset / Rollback (`/api/reset`)**: The "Reset Review" button allows
     safely rolling back all edits and review flags to the fresh AI grading output,
     automatically creating a timestamped backup in `grading/review-history/`.
   - **Strict Human Approval Gate (`Alt+A`)**: "Approve Entire Assignment"
     strictly requires 100% human review of student identities and all questions
     before approval can be granted, and writes approval in a single instant batch
     without slow sequential workbook writes.
   - **Keyboard Shortcuts**: `Alt+S` (Questions Status), `Alt+A` (Approve Assignment),
     `Ctrl+Enter` (Confirm Question Reviewed), `Alt+G` (Focus 1st Row), `Alt+R` (Needs Mark),
     `Enter` / `↑↓` (Row navigation), `Alt+M` (Toggle Row Reviewed), `Alt+Z` (Zoom Row),
     `Alt+V` (View Mode: Crop/+Context/Full Page).
   Review identities and each answer against the crop or expanded/full page, edit text
   and marks, then explicitly mark every item reviewed. The approval button asks
   for confirmation and uses the same validator as `approve-grading`. Once review
   and approval are complete, stop the local `review-grading` server. Do not leave spreadsheet editors open:
   non-cooperating applications can race with saves. Prior workbook versions
   remain in private `grading/review-history/`; edits invalidate approvals.
   Investigate a stale `.score-review.lock` before removing it; never remove a
   live lock. Alternatively inspect `grading/review.xlsx` against the scripts.
   `Scripts` needs distinct roster IDs and `reviewed=yes` for each script.
   `Marks` requires every row reviewed. Edit `answer` and `reasoning` to
   correct the OCR or feedback. Failed or flagged grades, and any corrected
   answer (including clearing it), require a `manual_mark`. When changing a
   mark, also verify that the reasoning remains appropriate. Script indices
   start at zero and images are in `grading/images`. A human must check OCR
   and feedback, not just numbers.
   The editor displays similarity for new runs and can calculate a per-answer
   suggestion after the teacher enters a threshold and increment. It does
   not use moderation and is unavailable for corrected or older answers
   lacking similarity. Explicitly copy and save any desired manual mark;
   calculating a suggestion does not change the grade.
   The read-only comparison table shows one question across students; filter
   by review status or flags, sort by script or effective mark, and open a
   row for individual review. It shows saved values, not unsaved edits; use
   its refresh button after external changes. It never approves or saves marks.
   The crop controls preview pixel-coordinate adjustments on the current
   question page without saving them or changing grading. To use changed
   shared or per-script crops for a paid regrade, follow the separate
   proposal, review and consent procedure in `regrading.md`.
   The comparison table can also preview similarity-based suggestions for
   that question. After checking the saved answers and moderation, explicitly
   select suggestions and confirm applying them. Selected manual marks become
   unreviewed drafts, even if they were previously reviewed; other rows remain
   unchanged. Corrected answers and unavailable similarities are ineligible.
   Existing approval becomes stale and the saved review history is retained.
   Optionally run `check-grading-review --exam /path/to/exam.yaml` for
   read-only discrepancy diagnostics; add `--run CHILD` for a child run.
   It reports missing, duplicate, unreviewed and invalid identities or marks,
   plus the first error from the approval validator. Output is private.
   Exit status 1 means discrepancies or an input error; status 0 does not
   mean the teacher has reviewed or approved the work.
5. After explicit approval in the editor or via `approve-grading --exam
   /path/to/exam.yaml --confirm-reviewed`, run `report --exam /path/to/exam.yaml`. Inspect the
   workbooks and scored PDFs before sharing. The score workbook maps reviewed
   students to numbered PDFs; samples use ranked score strata. `Answers` and
   `Reasoning` use reviewed text; their `Raw` counterparts retain model output.
6. For offline overall, class and question statistics run `analytics --exam
   /path/to/exam.yaml`. It requires current approved scores and writes
   `analytics/statistics.xlsx` and question box-plot PNGs from reviewed marks;
   it makes no AI calls. Box plots use 1.5-IQR whiskers and show outliers,
   with at most 20 questions per image. Pass counts and rates are blank unless
   the corresponding overall or per-question pass settings were explicitly
   configured. Per-question thresholds and equality behavior must be set
   before approvals; see configuration.md.
7. For optional **Gemini AI Qualitative Analysis & Advanced Reports** (proactively offer this option alongside `report` and `analytics` so users know it exists), preview `ai-analysis --exam /path/to/exam.yaml
   --model-config /path/to/config.yaml --project PROJECT_ID --location
   LOCATION`. This sends student answers and costs money. After explicit
   consent add `--confirm-cost`. Review and correct every row in
   `ai-analysis/review.xlsx`, mark reviewed=yes, then explicitly run
   `approve-analysis --exam /path/to/exam.yaml --confirm-reviewed`.
   Offer **advanced reports with or without optional paid infographics**.
   Ask if the teacher's choice is unclear; honor an already clear selection for
   this report instead of asking again or silently choosing a different mode.
   `advanced-report --exam /path/to/exam.yaml` generates a teacher executive DOCX
   and 16:9 PPTX **offline by default**. Existing `--no-infographics` remains
   compatible. The teacher DOCX remains text-only.

   Paid images require previewing `advanced-report --exam /path/to/exam.yaml
   --infographics [--image-model MODEL] [--image-model-config /private/image.yaml]
   [--report-name reports-infographics]`, then new explicit consent before adding
   `--confirm-cost`. Inspect the Google image configuration recorded with the
   approved analysis; do not auto-discover unrelated adjacent image files.
   Reuse a verified compatible image model or request only unresolved choices.
   A separate image configuration can select its own Vertex project/location;
   it must **not modify the approved analysis configuration or approval**.
   The integrated report image adapter is not a standalone image-generation
   skill; never substitute another provider/skill behind this consent.

   Preserve an existing report using a **fresh `--report-name` folder**.
   Keep full speaker notes, editable analysis and reviewed-score summaries.
   Each available infographic goes on its own slide after its editable content,
   with a visible warning that image text/numbers can be inaccurate or sample-based.
   Keep editable fallback slides for failed images. Image generation is
   single-attempt; reuse compatible cached PNGs without promising free cache misses.
   Inspect the selected report folder's `manifest.json` for `failed_infographics`;
   report failed count/slides/error classes, refresh the private index and ask
   whether to retain fallback, wait, or separately consent to supported recovery.
   Never overwrite a report or auto-retry failures. Review both documents,
   speaker notes and images before sharing.

For an approved parent needing one-question regrading, read
`regrading.md`. Child-specific review, reports and later stages use `--run`
and never replace the original run.

## Validation and failure handling

Approvals bind to input hashes; changes invalidate them. Reports have a
completion marker at `reports/manifest.json` and contain the review and
approval hashes. Reports already generated do not automatically disappear
when input changes; never share partial or stale reports. Pass-only samples
require both explicit pass settings before the run; no threshold is assumed.
Failed or flagged model outputs are not silently accepted as zero.
Refresh the private [output index](output-index.md) after every output change,
failure, recovery, review save, approval, report, cost summary or cleanup.

### Failure recovery

Inspect `grading/manifest.json` and `grading/results.json.completed`, current
input hashes, locks and review state. No separate checkpoint filename is assumed.
Only supported **incomplete** runs (`completed: false`, no review workbook or
approval) use `resume-grading`; preview and obtain new consent. It processes
remaining work, not an automatic repair of already failed answers. In-flight
requests can repeat and be billed. Preserve partial results and history.

For an incomplete original batch run:

- Preview `retry-failed-batches --exam /private/exam.yaml`. After the teacher
  selects this recovery and consents to target/data/cost, execute with
  `--confirm-cost --checkpoint-sha256 PREVIEW_HASH`. A changed checkpoint requires
  a new preview. Preserve successful rows and original checkpoint history;
  retry only failed crops in rubric order, stop on another failure, and retain
  the incomplete status. If supported, use the same `--exclude-question ID`
  selection in preview/execution to leave a blocked question for human attention.
  Remaining unprocessed questions require separately previewed/consented resume.
- `export-failed-review --exam /private/exam.yaml --output /private/fresh-directory`
  is **offline** and creates a separate failed-answer worksheet/crop collection,
  grouped by question, without overwriting a directory. It imports no manual marks,
  makes no paid call, does not resume/complete grading and records no approval.
  Exported identities/crops remain private; the HTML index must not enumerate them.

For a **completed** original batch run (`completed: true`) with unreviewed,
unedited failed answers, do not resume it. Offer manual review or targeted retry.
Only when failure details establish an **actual Gemini safety block** may the
teacher choose bounded splitting:

```sh
handwriting-grader bisect-failed-batch --exam /private/exam.yaml \
  --question Q1 --max-requests 39
```

Preview the question/model/Vertex target, transferred data, application request
limit (excluding SDK retries), current rates and review revision. Execute only
after separate explicit consent, with the exact `--revision REVISION --confirm-cost`.
Classification must come from actual Gemini response safety/block details, not
Azure policy messages, a guessed substring, timeout, HTTP 429/5xx, schema failure
or missing output. Other errors stop splitting. It must not bypass safety filters,
alter prompts/images to evade policy, or imply a singleton caused the block.
Successful recovered grades are retained; blocked singletons stay **unscored for
manual review**. Revision checks/writer locking protect existing reviews, edited
rows and approvals. Inspect staged retry history after interruption or review
conflict; never auto-import, discard, or approve staged grades. Start a fresh
score editor and compare recovered results with the workbook; recovery itself
is not synchronization or approval of manual marks.

### Transient errors, GCP quota exhaustion, and retry governance

Gemini and Vertex AI calls employ built-in SDK retry options (`types.HttpRetryOptions` with exponential backoff and jitter on HTTP 429, 500, 502, 503, 504).

When API operations exhaust their built-in retry attempts:
- **No blind Python loops**: Never write ad-hoc retry scripts (`scratch/*.py`), while-loops, or automatic restart scripts. If GCP quota is exhausted or rate limits are reached, auto-retrying in Python will only prolong failures and burn quota.
- **Human-in-the-loop notification**: The agent must inspect `results.json` and report error diagnostics to the user (failure count, question numbers, error types: 429 Quota / 504 Timeout / 5xx Server Error).
- **User decision required**: The agent must prompt the user to choose:
  1. Inspect/wait for GCP quota reset, then preview the supported recovery branch
     and obtain separate cost consent.
  2. Review completed failed items manually in `review.xlsx`, or use separate
     offline `export-failed-review` for an incomplete run. No additional API calls.
- **Targeted retry and re-run commands**: When the user approves retrying or after reviewed bounding-box adjustments (see `regions.md`):
  - `handwriting-grader retry-grading --exam /path/to/exam.yaml`: Retries failed marks and completes unmoderated questions.
  - `handwriting-grader retry-grading --exam /path/to/exam.yaml --re-ocr-id`: Re-runs student ID OCR for all scripts using the approved bounding box.
  - `handwriting-grader retry-grading --exam /path/to/exam.yaml --re-ocr-metadata`:
    Re-runs ID, class and name OCR in bounded metadata chunks (up to 35 scripts ×
    three fields = the 105-image application cap), assisted by the roster.
    Human identity verification remains required; no fixed accuracy/time saving.
  - `handwriting-grader retry-grading --exam /path/to/exam.yaml --question <Q_NUM>`: Re-grades and re-moderates a specific question using its approved box.
  Always preview the action plan without `--confirm-cost`, obtain explicit user cost confirmation, and rerun with `--confirm-cost`. The command updates `results.json`, syncs manifest hashes to current region approvals, and updates `review.xlsx` while preserving existing manual reviews.

Runs refuse overwrite. Supported interrupted grading checkpoints can resume
with `resume-grading --exam /path/to/exam.yaml`; preview the recorded target
and obtain consent before adding `--confirm-cost`. In-flight requests can
repeat and be billed; completed failures still need manual review. Older
checkpoints and runs with existing review workbooks cannot be resumed.
Investigate any stale `processing.lock` and never remove a live lock.
Optional OCR/grading/moderation, annotation, rubric-extraction and analytics
caches contain sensitive data.
Shared caching needs an explicitly configured private, user-owned directory;
it has no automatic cleanup and can become stale when model aliases change.
Grounded verification does not use these caches. Preserve
partial runs and never delete them automatically.
Calls already made may be billed even if grading fails. These approvals are
local records, not tamper-proof audit logs. Email requires separate review.
Cleanup needs separate confirmation and verified backup; preserve source scans,
`regions/images/`, `grading/images/`, review/history, approvals, delivery journals
and final deliverables. Missing bound evidence is not fixed by silently
regenerating images or writing a new approval.
