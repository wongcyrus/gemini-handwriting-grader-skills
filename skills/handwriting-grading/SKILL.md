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

## Prerequisites

For ADK-style agent workflows, telemetry, and live progress tracing, ensure the Google Agent Development Kit (ADK) skills suite is installed:
```bash
uvx google-agents-cli setup
```
Or install the python package directly into your environment:
```bash
pip install google-adk
```
This skill leverages ADK patterns for structured execution, token/cost auditing (`cost-report`), and real-time step progress updates.

### Cross-platform support
PDF page rendering is powered by pure-Python `pypdfium2` and works out of the box across Windows, macOS, and Linux without external Poppler or `pdftoppm` installs.

### Installation & Execution
Install the tool into your Python environment:
```bash
pip install -e /path/to/gemini-handwriting-grader-skills
```
Once installed, `handwriting-grader` is available globally on your `PATH` (or via `.venv/bin/handwriting-grader` on Unix / `.venv\Scripts\handwriting-grader` on Windows).

All `--exam "/path/to/exam.yaml"` and `--model-config "/path/to/config.yaml"` paths can be located anywhere on the filesystem (always quote paths in shell commands in case directories contain spaces); all relative paths declared inside `exam.yaml` resolve relative to `exam.yaml`'s parent folder, not the current working directory.

## Scope and availability

Use the Python tools in this repository, not notebooks. Answer sheets, preflight, rubric
import/extraction, explicit reviewed attendance, region review, Vertex grading, human score review and
basic reports, offline statistics, optional search-grounded verification,
reviewed AI analysis, resumable checkpoints, opt-in
model caches, targeted question regrading and gated email are implemented. Report formatting and cache
coverage differ from the notebooks.
Stop and tell the user when a requested stage is unavailable; never simulate success,
execute a notebook as a workaround, or claim a human approval occurred.

## Safety

- Treat scans, rosters, feedback, output, cache and notebook output as student
  data. Never commit or share them without authorization.
- Do not overwrite inputs or outputs, delete history, restart a user's session,
  call a paid model or send email without the relevant explicit approval.
- Never infer approval from a saved file, a passing test or a model response.
- Local skill instructions alone are not an enforcement mechanism.
- **Model boundaries and constraints**:
  The pipeline strictly requires:
  1. Multimodal raw image input support (`types.Part.from_bytes`) for handwritten script crops.
  2. Native structured JSON schema outputs (`response_schema` with Pydantic models).
  3. Dynamic thinking budget support (`thinking_budget=-1`) for complex reasoning stages.
  
  **Allowed models (Gemini-only, version 3.8+)**:
  - **Flash Tier** (high-throughput, cost-efficient OCR, item grading, rubric extraction): `gemini-3.8-flash` (or newer Flash >= 3.8).
  - **Reasoning Tier** (high-precision spatial template annotation, cohort-wide batch question grading, moderation, misconception analytics): `gemini-4-argon`, `gemini-4-hydrogen` (or newer Pro/Reasoning >= 3.8).
  - **Embedding Tier** (semantic clustering of student answers): `gemini-embedding-2`.

  **Strictly prohibited and rejected models**:
  - Any non-Gemini models (e.g. Claude, GPT, Llama, Gemma, PaLM, `text-embedding-*`) — only Google Gemini models are permitted.
  - Any Gemini model older than version 3.8 (`gemini-1.x`, `gemini-2.0-*`, `gemini-2.5-*`, `gemini-3.0-*`, `gemini-3.5-*`) — only Gemini 3.8+ is supported.
  - Dedicated media/image generation models (e.g. Imagen, Veo, Chirp) — cannot process grading rubrics or output evaluation JSON.
  - Text-only models — cannot read handwritten student script images.
  - Experimental or non-grading variants (Audio/TTS, Live, Robotics, Computer-Use).

  Never use expensive frontier models (Gemini 4) for all steps, as high-volume tasks (OCR, grading, annotation) become prohibitively slow and expensive. Always run dynamic tiered model discovery:
  `handwriting-grader discover-models --project <PROJECT> [--location <LOCATION>] [--write-config <PATH>]`
  to inspect live model properties and pricing online, allocating cost-efficient,
  high-throughput Flash models (e.g. `gemini-3.8-flash`) to high-volume steps (OCR,
  grading, rubric extraction) and reserving frontier reasoning models
  (e.g. `gemini-4-argon`) for low-volume, high-precision spatial annotation (`annotation`),
  batch moderation, and deep cohort analytics.

## Prepare an exam

1. Establish whether the user is starting or resuming, what stage they need,
   and which local paths they intend to use. Do not copy or overwrite data.
   When resuming an existing workspace or `output_dir`, inspect existing files
   and artifacts to orient immediately (do not rely on prior conversation history):

   | Latest Artifact Present | Completed Stage | Next Command / Action |
   |---|---|---|
   | No `exam.yaml` / `config.yaml` | Not configured | `discover-models` (for `config.yaml`) -> configure `exam.yaml` -> `preflight` -> `check-scans` |
   | `exam.yaml` exists (no `rubric/` or `attendance/`) | Configured | `preflight` -> `check-scans` -> `attendance` (if configured) or `extract-rubric`/`import-rubric` |
   | `attendance/review.xlsx` (no `attendance/approval.json`) | Attendance extracted | Teacher reviews `attendance/review.xlsx` -> `approve-attendance` |
   | `rubric/rubric.xlsx` (no `rubric/approval.json`) | Rubric extracted/imported | Teacher reviews `rubric/rubric.xlsx` -> `approve-rubric` |
   | `rubric/approval.json` (no `regions/images/`) | Rubric approved | `prepare-regions` -> `suggest-regions` -> `review-regions` |
   | `regions/images/` (no `regions/ai_suggestions.json`, no `regions/approval.json`) | Region pages rendered | `suggest-regions` (preview first, then `--confirm-cost`) -> `review-regions` |
   | `regions/ai_suggestions.json` or `regions/draft.json` (no `regions/approval.json`) | Regions suggested/drafted | `review-regions` (adjust boxes & approve) or `approve-regions` |
   | `regions/approval.json` (no `grading/`) | Regions approved | `grade` (preview first, then `--confirm-cost`) |
   | `grading/checkpoint.json` (no `grading/manifest.json`) | Grading interrupted | `resume-grading` (preview first, then `--confirm-cost`) |
   | `grading/manifest.json` (no `grading/approval.json`) | Grading done (or failed items) | `cluster-answers` (optional) -> `review-grading` (or `retry-grading` if failures) -> approve |
   | `grading/approval.json` | Scores approved | Present Post-Approval Menu: `report`, `analytics`, `ai-analysis` (optional) |
   | `reports/manifest.json` or `analytics/manifest.json` | Reports / offline stats generated | Offer remaining options: `analytics`/`report` (if missing), `ai-analysis` -> `advanced-report`, `moodle-export` (LMS PDFs/ZIP/CSV), `preview-email` -> `send-email`, or `clean` |
   | `ai-analysis/review.xlsx` (no `ai-analysis/approval.json`) | AI analysis generated | Teacher reviews `ai-analysis/review.xlsx` -> `approve-analysis` |
   | `ai-analysis/approval.json` | AI analysis approved | `advanced-report` (Teacher DOCX & Class PPTX), `preview-email` -> `send-email`, or `clean` |
   | `email/` preview artifact (no `email/approval.json`) | Email preview generated | Teacher inspects preview -> `approve-email` -> `send-email` |
   | `runs/<child>/` directory | Targeted single-question regrade | Inspect artifacts inside `runs/<child>/` and pass `--run <child>` to commands |

2. Read `references/configuration.md` and examine existing settings. Present
   detected values and ask for missing or ambiguous decisions. Keep actual
   configuration outside version control:
   a. Exam settings (`exam.yaml`): define roster, scans, layout, attendance, and reporting.
   b. AI model configuration (`config.yaml`): If a valid `config.yaml` already exists
      (with `vertex_ai.use_vertexai: true` and eligible Gemini 3.8+ models), inspect and
      reuse it rather than overwriting it blindly. Otherwise run online model discovery:
      `handwriting-grader discover-models --project <PROJECT> [--location <LOCATION>] [--write-config <PATH>]`
      to inspect live Vertex AI models and pricing, present the cost allocation table to the user,
      and generate `config.yaml`.
3. If the user needs printable answer sheets before scans exist, read
   `references/answer-sheets.md` and run that independent stage first.
4. Run preflight checks:
   `handwriting-grader preflight --exam /path/to/exam.yaml`.
   It makes no Vertex calls and does not create outputs.
5. Fix reported errors with the user's input and rerun. A successful preflight
   does not verify scan order, leading zeroes, rubric contents, credentials,
   model access or approval.
   **Run `check-scans` BEFORE region setup**:
   `handwriting-grader check-scans --exam /path/to/exam.yaml`
   (offline, zero AI cost). It compares scripts by majority consensus so even if
   Script 1 itself is upside-down or out of order, it catches Script 1 rather than
   creating bad region bounding boxes for the whole exam. If any page is flagged
   (upside-down `180°`, sideways `90°/270°`, blank, or out of sequence), **do not
   silently flip or reorder pages in code** — report the exact PDF page numbers and
   script numbers to the user so they fix the physical scan or source PDF manually
   and re-run `check-scans`.
   If attendance is configured, read `references/attendance.md` and obtain
   explicit review and approval before grading.
6. For rubric setup (MUST run BEFORE region suggestion so exact question IDs are known), read `references/grading.md`:
   a. If the marking scheme is a DOCX, run `extract-rubric` (preview first,
      obtain cost consent, then `--confirm-cost`) to extract questions and
      marks into `output_dir/rubric/rubric.xlsx` using Gemini.
   b. If the marking scheme is an existing XLSX, run `import-rubric`.
   c. Have the teacher inspect `rubric.xlsx` and explicitly approve with
      `approve-rubric --exam /path/to/exam.yaml --confirm-reviewed`.
7. For region setup and review, read `references/regions.md`:
   a. Run `handwriting-grader prepare-regions --exam /path/to/exam.yaml`
      to render the first script's page images into `output_dir/regions/images/`.
   b. Region setup must run Gemini (`models.annotation`, Gemini 4 reasoning tier)
      with the extracted rubric's question IDs: preview `suggest-regions`
      (with `--model-config`, `--project`, `--location`), explain the target and
      page count, obtain explicit cost consent, and run with `--confirm-cost`
      to generate `ai_suggestions.json`. Never skip Gemini AI suggestions or ask
      the user to manually draw bounding boxes from scratch without running Gemini
      AI suggestions first.
   c. Start the local server with `review-regions --exam /path/to/exam.yaml`.
      Provide the URL so the user can inspect the automatically loaded AI proposals
      across all pages, adjust boxes (`ID`, `NAME`, `CLASS`, questions), save each
      page, and approve. Alternatively, after manual or programmatic edits to
      `annotations.json`, approve with:
      `handwriting-grader approve-regions --exam /path/to/exam.yaml --confirm-reviewed`.
   d. **Automatic Page Alignment & Scanner Shift Recovery**:
      During `grade`, every scanned page is automatically aligned against `regions/images/`
      before cropping. For an existing unaligned run or tight boxes, follow the recovery
      commands (`retry-grading --align-images ...`) in `references/regions.md`.
8. For grading, reporting and AI analysis read `references/grading.md`.
   Obtain the specified human confirmations; never infer approval.
   - **Choosing Grading Mode (`--mode batch` vs `--mode item`)**:
     - `--mode batch` (**Default — Recommended**): Best for almost all exams (short-to-medium answers, math/STEM steps, diagrams, ~10–150+ scripts) and when RPM quota is tight. Sends all non-blank crops per question together (`models.batch_grading`, `gemini-4-argon`), cutting API calls by ~90% with intrinsic cohort consistency.
     - `--mode item` (**Sequential Fallback**): Choose only when answers per question are very long/multi-page (risking single-request image payload/token limits), when grading very large cohorts with verbose answers to save cost using Flash (`models.grading`, `gemini-3.8-flash`) + text moderation (`models.moderation`, `gemini-4-argon`), or when isolating individual script timeouts.
     - Both modes output identical `results.json` and `review.xlsx` schemas. Confirm the chosen mode with the user during cost preview.
   - Blank answer cells are automatically detected and scored 0 locally by Pillow before calling the AI, minimizing quota and cost.
   - Student metadata (`ID`, `CLASS`, `NAME`) OCR automatically injects candidate rosters from `attendance` or `roster` to eliminate character confusion and ensure exact roster matching.
   
   **Failure Diagnostics, Quota Protection, and User-Decision Gating**:
   - The engine uses built-in Google GenAI SDK retry options (`types.HttpRetryOptions`). **Never write ad-hoc Python retry loops.**
   - If grading finishes with failed marks (`grading_failed=True`) or moderation errors, **notify the user immediately** with the failed count, affected question numbers, and error types (`429`, `504`, `5xx`), and ask them to choose:
     - **Option A (Targeted Retry)**: Preview `handwriting-grader retry-grading --exam /path/to/exam.yaml`, obtain cost consent, and rerun with `--confirm-cost`.
     - **Option B (Manual Review)**: Grade failed items manually in `review-grading` / `review.xlsx` with no further API calls.
     - **Option C (Wait / Investigate)**: Wait for GCP quota reset before proceeding.

   **Semantic Answer Clustering (Optional, Before Review)**:
   - Optionally run `handwriting-grader cluster-answers --exam /path/to/exam.yaml [--threshold 0.82]` (`gemini-embedding-2`, with cross-assignment disk caching) to enable semantic answer grouping (`📌`) in `review-grading`. If skipped, `review-grading` falls back locally to `Word Overlap (Local)` with zero AI cost (details in `references/grading.md`).
   - In `review-grading`: use `Alt+S` for the All Questions Status dashboard, `📌 Grouped` to toggle answer grouping, `Ctrl+Enter` to confirm questions in batch, and `Alt+A` to approve only after 100% human review is complete (or use "Reset Review" to rollback if needed).

   **Post-Approval Reporting & Analytics Menu (Proactively Present to User)**:
   - Immediately after `grading/approval.json` is recorded (or whenever the user finishes reviewing), **always proactively present all post-approval options** so a general user knows what is available:
     1. **Standard Reports (`report`)** *(Offline, zero AI cost)*: Individual marked student PDFs, combined PDF, stratified audit samples, and score spreadsheets (`score_report.xlsx`, `details_score_report.xlsx`).
     2. **Offline Statistical Analytics (`analytics`)** *(Offline, zero AI cost)*: Cohort & class statistics (`statistics.xlsx`) and per-question box-plot PNGs.
     3. **Gemini AI Qualitative Analysis (`ai-analysis`) & Advanced Reports (`advanced-report`)** *(Uses Gemini AI — preview cost & obtain explicit consent first)*: Analyzes class misconceptions and generates personalized student feedback (`ai-analysis/review.xlsx` -> `approve-analysis`), unlocking a **Teacher Executive Report (`.docx`)** and **16:9 Class Review Slides (`.pptx`)** with **Gemini infographics for the class and each question (enabled by default; inform the user they can optionally turn them off with `--no-infographics`)**. If any infographics fail (`failed_infographics` in `ai-analysis/reports/manifest.json`), **never auto-retry** — report the failed slides and ask the user whether to retry failed images, keep the text-card fallback, or wait.
     4. **Moodle / LMS Export (`moodle-export`)** *(Offline, zero AI cost)*: Exports combined per-student PDFs (`student_pdfs/<ID>_<Name>.pdf` with AI feedback cover page + marked script), builds `recipients.csv` from a Moodle participants CSV, and (with `--worksheet`) fills a Moodle Assignment grading worksheet CSV + `feedback_files.zip` for bulk Moodle upload.
     5. **Email Distribution (`preview-email` -> `approve-email` -> `send-email`)**: Previews and emails marked PDFs and feedback to students (accepts `ID,EMAIL` or Moodle participants CSV directly).

   After grading and reporting are complete, always inform the user that results
   and review history have been preserved, and ask if they would like to clean
   or archive intermediate output using:
   `handwriting-grader clean --exam /path/to/exam.yaml`
   (creates a slim `.zip` backup by default, preserves HIL reviews, AI outputs, and final
   deliverables `reports/`, `analytics/`, `ai-analysis/`, `moodle/` while removing only
   bulky raw JPEGs and temp folders; pass `--full-archive` to include raw JPEGs in the `.zip`,
   and requires explicit `--confirm-delete`).
9. For distribution read `references/email.md`; never send without reviewing
   the exact preview and separate dispatch approval.
   For targeted regrading read `references/regrading.md`; require a current
   approved parent, separate cost consent and fresh review of the child.
10. If the requested next stage is unimplemented, stop and say so.

For the detailed stage flow and known notebook gaps read
`references/flow-and-parity.md`; for the short gate overview read
`references/workflow.md`. Ask for email details only when distribution is
requested; never send without explicit approval immediately before dispatch.
