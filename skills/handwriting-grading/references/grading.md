# Rubric, grading and reporting

## Prerequisites

- Read configuration.md and regions.md; settle the exam configuration before
  creating approvals. Obtain user confirmation of scan order and layout.
- Use a private, separate output directory. Never commit student data.
- For Vertex operations, obtain explicit user approval of the project,
  location, data transfer and costs. Authenticate separately; live model
  availability and quality have not been verified by the offline tests.

## Procedure

1. Import an XLSX with `.venv/bin/handwriting-grader import-rubric --exam
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

   **Grading Modes**:
   - `--mode batch` (default): Fast multimodal structured output grading per question.
     Batches all student answer crops for a question into a single model pass (using `models.batch_grading`,
     typically `gemini-4-argon`). Blank cells are filtered locally with Pillow and pre-scored as 0 (0 AI cost).
     Multiple questions run concurrently (`--concurrency 3` by default). Greatly reduces API round-trips
     (e.g. 45 calls vs 3,375 calls) and improves cross-cohort grading consistency.
   - `--mode item`: Classical student-by-student grading (`models.grading`, e.g. `gemini-3.8-flash`) followed
     by whole-question moderation (`models.moderation`, e.g. `gemini-4-argon`).
   Both modes produce identical downstream schemas (`results.json`, `review.xlsx`, telemetry) and support
   resuming and retrying.

   During grading, real-time step progress and token usage are monitored
   following ADK observability conventions.
   To inspect model usage, token count, and cost breakdown at any time, run:
   `.venv/bin/handwriting-grader cost-report --exam /path/to/exam.yaml`
   (generates `cost_report.xlsx` and `cost_summary.json` in the run directory).

   **Candidate-Assisted Metadata OCR (Roster Matching)**:
   When extracting student metadata (`ID`, `CLASS`, `NAME`), the grading engine automatically checks for an approved attendance roster or configured exam roster (`attendance` or `roster` in `exam.yaml`). If available, known valid candidate values are injected directly into Gemini's extraction prompt:
   - Constrained candidate matching eliminates handwriting ambiguity (e.g. `0` vs `O`, `1` vs `I` vs `7`, `8` vs `B`) and guarantees exact roster conformity.
   - Preserves safety guards: if an image is blank or contains text not matching any enrolled candidate, Gemini returns empty or the raw text as read without guessing or forcing false matches.
   - If no roster or attendance is configured, the engine gracefully falls back to open unconstrained OCR.

   **Handling Errors & User Decision Gating**:
   Transient network or server errors are handled at the connection layer using built-in
   Google GenAI SDK retry options (`types.HttpRetryOptions` on `genai.Client`).
   If any items still fail after SDK retries:
   - **Do NOT auto-retry in Python**: Never write ad-hoc loops or restart scripts. If GCP
     quotas (429 ResourceExhausted) or deadlines (504) are reached, Python loops will
     only burn quota or fail repeatedly.
   - **Notice user for decision**: Immediately report the failure diagnostics (number of failed marks,
     question numbers, error codes) to the teacher/user and ask whether they want to:
     a) Wait for quota reset or investigate GCP quota, then run targeted `retry-grading`, or
     b) Manually grade the failed items in `review.xlsx` without additional API calls.
   - **User-Approved Retry**: Only upon explicit user decision and cost consent, run:
     `.venv/bin/handwriting-grader retry-grading --exam /path/to/exam.yaml`
     Preview the failed marks and unmoderated questions, obtain cost confirmation,
     and rerun with `--confirm-cost`. `retry-grading` isolates and regrades only the
     failed crops using exponential backoff, runs moderation for affected questions once
     all student marks are present, and refreshes `grading/review.xlsx` while preserving
     any existing manual reviews.
3b. **Semantic Answer Clustering (Optional, After OCR/Grading, Before Review)**:
    Optionally pre-compute semantic embedding clusters across all questions:
    `.venv/bin/handwriting-grader cluster-answers --exam /path/to/exam.yaml [--threshold 0.82]`
    - Generates 3072-dimensional vector embeddings for unique student answers using `gemini-embedding-2`.
    - **How Cluster Count is Decided**: Uses greedy threshold clustering over pairwise cosine similarity rather than fixed $K$-means. The number of clusters is data-driven by how many conceptually distinct answers students wrote (e.g. 2–4 clusters for True/False; 15–20+ for complex reasoning). Tune with `--threshold`: higher (e.g. `0.90`) yields more/tighter clusters; lower (e.g. `0.75`) yields fewer/broader clusters.
    - **Identity Exclusion**: Student metadata (`ID`, `Class`, `Name`) is strictly excluded from clustering, and the Grouping selector is hidden in Identity review to preserve student privacy and blind grading.
    - **Cross-Question & Cross-Assignment Caching**: Embeddings are cached per embedding model in `~/.cache/gemini-handwriting-grader/embeddings/<model>.json` (or `caching.directory`). Any answer text embedded once is reused indefinitely across all questions and across all assignments on the machine with zero GCP calls and zero cost.
    - Saves clusters to `clusters.json` and syncs embeddings to assignment-local `.embedding_cache.json`.
    - Subsequent runs and review editor sessions load directly from cache with zero GCP calls and zero cost.
    - **Fallback**: If skipped or if the user selects "Word Overlap (Local)" in the review UI, the score editor automatically groups similar answers using client-side word-overlap similarity (Jaccard) without making any AI or network calls. The dropdown automatically disables "AI Embedding" if clusters were not pre-computed.
4. Ask the teacher to run `review-grading --exam /path/to/exam.yaml` and open
   the full printed local URL. The score editor presents a one-page blind
   marksheet per question (hiding student IDs during question review), groups
   similar answers together, lets the teacher sort by clicking table headers
   (`Answer`, `Script`, `Mark`, `Similarity`, `Status`), expand bounding boxes
   (`Expand Box`), resize or zoom/pan handwriting crops (`+Context` / `Full Page`),
   toggle columns, and edit marks, answers, and reasoning inline. Review
   identities and each answer against the crop or expanded/full page, edit text
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
7. For optional AI feedback preview `ai-analysis --exam /path/to/exam.yaml
   --model-config /path/to/config.yaml --project PROJECT_ID --location
   LOCATION`. This sends student answers and costs money. After explicit
   consent add `--confirm-cost`. Review and correct every row in
   `ai-analysis/review.xlsx`, mark reviewed=yes, then explicitly run
   `approve-analysis --exam /path/to/exam.yaml --confirm-reviewed`.
   `advanced-report --exam /path/to/exam.yaml` writes a teacher DOCX and
   class PPTX with a question-score chart. Review both before sharing.

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

### Transient errors, GCP quota exhaustion, and retry governance

Gemini and Vertex AI calls employ built-in SDK retry options (`types.HttpRetryOptions` with exponential backoff and jitter on HTTP 429, 500, 502, 503, 504).

When API operations exhaust their built-in retry attempts:
- **No blind Python loops**: Never write ad-hoc retry scripts (`scratch/*.py`), while-loops, or automatic restart scripts. If GCP quota is exhausted or rate limits are reached, auto-retrying in Python will only prolong failures and burn quota.
- **Human-in-the-loop notification**: The agent must inspect `results.json` and report error diagnostics to the user (failure count, question numbers, error types: 429 Quota / 504 Timeout / 5xx Server Error).
- **User decision required**: The agent must prompt the user to choose:
  1. Inspect/wait for GCP quota reset, then execute targeted `retry-grading --exam /path/to/exam.yaml [--confirm-cost]`.
  2. Grade the failed items manually in `review.xlsx` without additional API calls.
- **Targeted retry and re-run commands**: When the user approves retrying or after scanner-shift alignment / bounding box adjustments (see `regions.md`):
  - `handwriting-grader retry-grading --exam /path/to/exam.yaml`: Retries failed marks and completes unmoderated questions.
  - `handwriting-grader retry-grading --exam /path/to/exam.yaml --align-images`: Aligns all scanned page images to the approved reference layout (offline, 0 AI cost). Can be combined with `--re-ocr-id`, `--re-ocr-metadata`, or `--question`.
  - `handwriting-grader retry-grading --exam /path/to/exam.yaml --re-ocr-id`: Re-runs student ID OCR for all scripts using the approved bounding box.
  - `handwriting-grader retry-grading --exam /path/to/exam.yaml --re-ocr-metadata`: Re-runs student ID, class, and name OCR across scripts in fast multimodal batch chunks (chunk size 35) cross-referenced against the enrolled roster, reducing hundreds of sequential calls to 1–3 batch requests and achieving 100% roster triangulation accuracy in ~40s.
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
