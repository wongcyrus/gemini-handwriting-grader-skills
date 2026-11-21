# Detailed workflow and notebook parity

This describes the Python implementation in the working tree, not an
assertion that it is identical to the notebooks or that unpublished changes
are available in a released version. Use `grading.md`, `regions.md`,
`answer-sheets.md` and `email.md` for executable details.

## Decisions and prerequisites

Before creating or modifying an exam run, ask the teacher to supply and
confirm:

1. Exam name, roster XLSX and sheet, and private output directory. Confirm
   each ID, including any leading zeroes, and that ID, NAME and CLASS are
   present and unique where required. Confirm the full roster and whether
   a separately reviewed attendance list is needed.
2. The scanned PDF and its ordering, pages per student, and whether every
   script has the same layout. Preflight requires exactly one complete script
   per present student, or per roster entry without an attendance list; it
   does not infer absences, extra pages or ordering.
3. The source rubric and whether to import an existing structured XLSX or
   extract a DOCX with AI. Confirm the maximum marks and question labels.
4. If pass-only reports or pass statistics are wanted, both the pass mark
   and whether a score equal to it passes. No threshold is inferred.
5. Only when needed: answer-sheet DOCX template; model configuration, Google
   Cloud project, location and authentication; or email recipients, sender
   and SMTP settings. Never put passwords in configuration files.

Use the repository's virtual environment. Poppler is required for rendering
scans; LibreOffice is required for answer-sheet conversion. Resolve paths
relative to the exam YAML, and keep configuration, data, caches and outputs
outside version control.

## Stage-by-stage flow

Each command below is invoked as `.venv/bin/handwriting-grader COMMAND`.
The relevant references specify required arguments and error handling.

| Stage | Inputs and command | Output and required human decision |
|---|---|---|
| Optional answer sheets | Roster and DOCX template; `answer-sheets --exam EXAM` | Numbered PDFs, roster mapping, `combined.pdf` and completion manifest in `answer-sheets/`. Personalized DOCX files are temporary. Check identity, order and pagination before printing. |
| Preflight | Exam YAML, roster, scan and rubric; `preflight --exam EXAM` | Read-only check of paths, roster columns, page count and shared layout. The teacher must still confirm scan order and layout. |
| Optional attendance | Complete `ID,STATUS` CSV; `prepare-attendance`, then `approve-attendance --confirm-reviewed` | Private versioned review and approval. The teacher checks every status; absences are never inferred. Only present students proceed to grading. |
| Rubric | Structured XLSX via `import-rubric`, or DOCX via `extract-rubric` with model config, project and location | `rubric/rubric.xlsx` and manifest. DOCX extraction requires a preview and separate `--confirm-cost`. Compare every question, marking criterion and maximum to the source. |
| Optional rubric fact check | Imported/extracted rubric; `verify-rubric` with model target | Search-grounded `rubric/verification/verification.xlsx` and `verification.docx` with sources. Separate cost/data consent is required; check actual sources. It does not approve or change the rubric. |
| Rubric approval | Reviewed rubric; `approve-rubric --exam EXAM --confirm-reviewed` | Separate hash-bound approval. Reapprove after relevant changes. |
| Region preparation | Preflight-confirmed scan; `prepare-regions --exam EXAM` | First script's pages and a draft in `regions/`. No model call. |
| Region suggestions | Prepared pages + extracted rubric (`rubric.xlsx`); `suggest-regions` with Gemini 4 (`models.annotation`) | Separate AI proposals using the rubric's exact question IDs, not approved annotations. Preview first; paid call requires `--confirm-cost`. |
| Region review | `review-regions --exam EXAM` | Local browser draws/edits labels and boxes. Verify every page and save it reviewed; approve only after review. ID must be on the first page. Grading additionally checks labels against the approved rubric. |
| OCR, grading and moderation | Current rubric and region approvals; `grade` with model target | `grading/images/`, `results.json`, manifest and `review.xlsx`. Preview maximum requests and get explicit consent before `--confirm-cost`. AI can fail or be wrong; failure is not a zero mark. |
| Targeted failure retry | Completed grading run with failed marks or moderation errors; `retry-grading` | Isolates failed crops, re-evaluates local blank filter, re-grades failed items using SDK HttpRetryOptions, re-moderates affected questions, and refreshes review.xlsx preserving existing manual marks. Preview and cost consent required before `--confirm-cost`. |
| Resume interrupted grading | Supported partial checkpoint; `resume-grading --exam EXAM` | Preview target, then obtain fresh cost consent for `--confirm-cost`. Already checkpointed work is retained; an in-flight call may be repeated and billed. Existing review workbooks cannot be resumed. |
| Targeted regrading | Approved parent; optional separately approved replacement rubric; `regrade` | One question is rerun for every present student in an isolated child. Preview and obtain cost consent; all identities and scores need fresh review. Use `--run CHILD` for child review and subsequent stages. |
| Identity and mark review | Completed run; `review-grading --exam EXAM`, or edit `grading/review.xlsx` | Compare every identity and answer with its crop or full page. Select distinct roster IDs and explicitly review each identity and mark. Correct answers and reasoning; failed, flagged or corrected answers require a manual mark. No saved edit is an approval. |
| Optional discrepancy check | Completed run and review workbook; `check-grading-review --exam EXAM` | Read-only private diagnostics for identity and mark discrepancies. Use `--run CHILD` for a child. Exit 0 is not human approval; exit 1 indicates discrepancies or an input error. |
| Score approval | Browser approval after explicit confirmation, or `approve-grading --exam EXAM --confirm-reviewed` | Validates complete present roster and question coverage, flags, score ranges and corrections; writes a separate hash-bound approval. |
| Basic reports | Current grading approval; `report --exam EXAM` | `reports/` contains score workbooks, individual and combined marked PDFs, available stratified samples and a completion manifest. Inspect scores and PDFs before sharing. Reviewed and raw answers/reasoning are kept separately. |
| Offline analytics | Current grading approval; `analytics --exam EXAM` | `analytics/statistics.xlsx` with overall, class and question statistics and charts, plus question box-plot PNGs. Pass results remain blank without an explicit policy. |
| Optional AI analysis | Current grading approval; `ai-analysis` with model target | `ai-analysis/review.xlsx` for student feedback, questions and class overview. Preview and obtain separate cost/data consent before `--confirm-cost`; review and correct every item. |
| Analysis approval | Reviewed feedback; `approve-analysis --exam EXAM --confirm-reviewed` | Separate approval required before advanced reports or feedback in email. |
| Optional infographics | Current analysis approval; `infographics` with `models.analytics_image` (`gemini-3.8-flash`) | Generated visual summaries and manifest. Preview and obtain separate consent. Inspect every asset; generation is not approval. |
| Advanced reports | Current analysis approval; `advanced-report --exam EXAM` | Teacher DOCX and class PPTX. Including images additionally requires `--include-infographics --confirm-images-reviewed`. Review the outputs before sharing. |
| Optional email | Current grading approval and scored PDFs, explicit CSV for present recipients and separate settings YAML; `preview-email` | Private individual `.eml` files and preview manifest; nothing is sent. Check every recipient, body and attachment. `{feedback}` also requires current analysis approval. |
| Email approval and send | Reviewed preview; `approve-email --confirm-reviewed`, then separate `send-email --confirm-send` | Requires a second explicit authorization immediately before dispatch. SMTP STARTTLS acceptance is not delivery. Inspect the delivery journal after errors; ambiguous attempts are not retried automatically. |

Similarity in newly generated runs is saved with each grade and shown during
review. The teacher may enter a similarity threshold and mark increment to
calculate a **per-answer suggestion** or preview suggestions across a
question using original OCR answers. These ignore moderation. Bulk selections
require explicit confirmation and are saved only as unreviewed manual drafts.
Corrected answers cannot use the original similarity. Older runs without a
saved similarity show it as unavailable.

## State, safety and recovery

- Approval records bind to specific inputs and review hashes. Changes can
  invalidate downstream approvals; existing generated files do not disappear
  automatically and must not be shared as if current.
- Most stages refuse to overwrite existing output. Preserve partial runs and
  choose a new output directory with the user's agreement. Only supported
  incomplete grading checkpoints resume; there is no general resume for
  other stages.
- The grading editor retains prior workbook versions in
  `grading/review-history/`. Keep spreadsheet editors closed while using it:
  independent programs do not honor its lock and can race with a save. Never
  remove a live lock; investigate stale locks. The bearer-token localhost
  URL is private and is not a multi-user authorization system.
- Caches are opt-in for annotation, rubric extraction, OCR, grading,
  moderation and AI analysis. Shared reuse requires an explicitly selected
  private user-owned directory. Cache files can contain student data and are
  never automatically cleaned up. Model aliases can change without cache-key
  changes; cost consent and human review remain mandatory even on hits.
- A regrade can explicitly reuse an approved parent's run cache with
  `--reuse-parent-cache`; matching responses may not be freshly generated.
  Follow `regrading.md` for prerequisites and preview.
- Preserve source files, review history, delivery journals and partial
  outputs. No destructive notebook cleanup is run automatically.

## Notebook comparison and remaining gaps

The comparison uses the original repository's `notebbooks/step1` through
`step7` sources and `templates/questions/`, not saved notebook outputs.
“Implemented” means a corresponding workflow exists, not exact parity.

| Original area | Current status and missing or intentionally different behavior |
|---|---|
| Step 1: personalized answer sheets | Core generation and merge implemented, including ordinary Word text boxes and hyperlink text. Placeholders in Word fields and multi-paragraph fields are unsupported; LibreOffice and template appearance still need real-environment review. Original cleanup is not automated. |
| Step 2: rubric | Extraction, XLSX import, review and optional grounded verification implemented, with XLSX and Word review outputs. Verification format, prompts and agent orchestration differ; extraction quality and live search grounding remain unverified. |
| Step 3: annotations | Browser editing and optional suggestions replace notebook widgets. The initial layout must be shared; duplicate canonical labels are rejected. Later crop proposals can adjust a selected question per script for a child regrade, but cannot change its page or support mixed page layouts. |
| Step 4: OCR and scoring | Core ID, NAME and CLASS OCR, grading and moderation implemented through the Google Gen AI SDK, not the original ADK agent orchestration. Older runs may not contain NAME OCR. A selected question can be rerun in a separate reviewed child, including with a separately approved replacement rubric. Prompting, retries and output files are not identical. |
| Step 4: review screen | Local individual review and a per-question comparison table support filtering, sorting and navigation. Bulk similarity suggestions can be explicitly applied as unreviewed drafts. Crop adjustments are temporary visual previews unless a separate shared/per-script crop proposal is reviewed and used for a child regrade. Old results cannot recover discarded similarity. |
| Step 5: post-scoring checks | Read-only discrepancy diagnostics assist review; approval enforces complete unique present-student IDs, reviewed rows and score bounds. Explicit reviewed attendance provides a separate absence report; formatting differs from the notebook. History cleanup and reset tools are not implemented. |
| Step 6.1: reports | Score workbooks, marked PDFs and stratified samples implemented. Layout, filenames and annotations differ; the original archive/cleanup workflow and exact workbook formatting are not ported. Explicit pass settings replace the notebook's assumed threshold. |
| Step 6.2: analytics | Student/question/class AI reviews, offline statistics, charts including question box plots, teacher DOCX, PPTX and optional infographics exist. Explicit per-question thresholds enable pass rates; none are inferred. Chart layout, detailed formatting and analysis prompts are not fully reproduced. Review all generated material. |
| Step 7: distribution | Individual preview, review, approval, SMTP STARTTLS send and delivery journal implemented. Recipient lookup and settings differ from the notebook; no live SMTP delivery has been verified. |
| Caching and operations | The original ADK callback format and existing notebook cache are not imported. Search-grounded verification and infographics are deliberately not cached; notebook logging settings are not ported. No automatic archive, retention cleanup or generalized interrupted-stage recovery is provided. |

## Verification status

The test suite uses synthetic data, browser automation where available,
LibreOffice conversion, and local fake Vertex and SMTP services. These checks
do not establish live Vertex model availability, quality, cost, real SMTP
delivery, performance on large exams, or exact visual and scoring parity
with the notebooks. Run the tests on the current working tree after changes;
never treat test success as a teacher's approval.
