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

All `--exam /path/to/exam.yaml` and `--model-config /path/to/config.yaml` paths can be located anywhere on the filesystem; all relative paths declared inside `exam.yaml` resolve relative to `exam.yaml`'s parent folder, not the current working directory.

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
  
  **Allowed models by tier**:
  - **Flash Tier** (high-throughput, cost-efficient OCR, item grading, rubric extraction): `gemini-3.8-flash` (recommended), `gemini-2.5-flash` (fallback).
  - **Reasoning Tier** (high-precision spatial template annotation, cohort-wide batch question grading, moderation, misconception analytics): `gemini-4-argon` (recommended), `gemini-2.5-pro` (fallback).
  - **Embedding Tier** (semantic clustering of student answers): `gemini-embedding-2`.

  **Strictly prohibited and rejected models**:
  - Any non-Gemini models (e.g. Claude, GPT, Llama, Gemma, PaLM) — incompatible with Vertex AI GenAI schemas and payloads.
  - Dedicated media/image generation models (e.g. Imagen, Veo, Chirp) — cannot process grading rubrics or output evaluation JSON.
  - Text-only models — cannot read handwritten student script images.
  - Legacy Gemini 1.x models (`gemini-1.0-*`, `gemini-1.5-*`) — lack dynamic thinking budget (`-1`) and modern batch output schemas.
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
2. Read `references/configuration.md` and examine existing settings. Present
   detected values and ask for missing or ambiguous decisions. Keep actual
   configuration outside version control:
   a. Exam settings (`exam.yaml`): define roster, scans, layout, attendance, and reporting.
   b. AI model configuration (`config.yaml`): The skill automatically runs online
      model discovery:
      `handwriting-grader discover-models --project <PROJECT> [--location <LOCATION>] [--write-config <PATH>]`
      to inspect live model properties on Vertex AI, benchmark token pricing and throughput,
      present the cost allocation table to the user, and automatically generate or update
      `config.yaml` with the optimal model tier for each pipeline step.
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
      During `grade`, every scanned page is automatically registered (translation +
      small rotation) against the approved template pages in `regions/images/` before
      cropping OCR or question answers.
      If an existing unaligned run has scanner drift (e.g. clipped `ID` or shifted
      questions), align the pages offline first and then re-run only affected items:
      - Align pages offline (0 AI cost): `retry-grading --exam /path/to/exam.yaml --align-images`
      - Re-run Student ID OCR: `retry-grading --exam /path/to/exam.yaml --align-images --re-ocr-id [--confirm-cost]`
      - Re-run all metadata: `retry-grading --exam /path/to/exam.yaml --re-ocr-metadata [--confirm-cost]`
      - Re-run specific question: `retry-grading --exam /path/to/exam.yaml --question <Q_NUM> [--confirm-cost]`
      (If a bounding box itself is too tight, adjust it in `review-regions` or edit
      `annotations.json` and run `approve-regions --exam /path/to/exam.yaml --confirm-reviewed`.)
8. For grading, reporting and AI analysis read `references/grading.md`.
   Obtain the specified human confirmations; never infer approval.
   - **Grading Modes**: `grade` supports `--mode batch` (default, fast multimodal structured output pass per question with `models.batch_grading`) and `--mode item` (sequential student-by-student + cohort moderation). Both modes output identical `results.json` and `review.xlsx` schemas.
   - Blank answer cells are automatically detected and scored 0 locally by Pillow before calling the AI, minimizing quota and cost.
   - Student metadata (`ID`, `CLASS`, `NAME`) OCR automatically injects candidate rosters from `attendance` or `roster` to eliminate character confusion and ensure exact roster matching.
   
   **Failure Diagnostics, Quota Protection, and User-Decision Gating**:
   - The Python code uses built-in Google GenAI SDK retry primitives (`types.HttpRetryOptions` on `genai.Client`) with exponential backoff, jitter, and status code targeting (`[429, 500, 502, 503, 504]`). Never write ad-hoc Python retry loops or hack scripts.
   - If grading finishes with any failed marks (`grading_failed=True`) or moderation errors after SDK-level retries:
     **DO NOT automatically retry in Python**. Retrying in a Python loop is dangerous because GCP quotas (e.g. 429 ResourceExhausted) may be exhausted or backend limits reached; blind loops are futile, risk exhausting quota further, and can incur unapproved billing.
     **NOTIFY THE USER IMMEDIATELY** with error diagnostics:
     1. Exact count of failed marks and affected question numbers.
     2. Specific error categories (e.g. 429 Quota Exhausted, 504 Deadline Exceeded, 5xx Server Error).
     3. Inform the user that GCP quotas or backend stability may be affected.
     4. Ask the user to decide how to proceed:
        - **Option A (Targeted Retry)**: Inspect GCP quota / wait for quota window reset, preview with `handwriting-grader retry-grading --exam /path/to/exam.yaml`, obtain cost confirmation, and rerun with `--confirm-cost`.
        - **Option B (Manual Review)**: Proceed to `review-grading` and assign manual marks in `review.xlsx` for those failed items without further API calls.
        - **Option C (Wait / Investigate)**: Wait for GCP quota reset or investigate quota limits in the Google Cloud Console before proceeding.
   - Never proceed with `retry-grading` without the user's explicit decision and cost confirmation.

   **Semantic Answer Clustering (Optional, After OCR/Grading, Before Review)**:
   - Pre-computing semantic answer clusters is optional:
     `handwriting-grader cluster-answers --exam /path/to/exam.yaml [--threshold 0.82]`
   - Uses Google's frontier `gemini-embedding-2` model to embed unique student answers, compute cosine similarities, and group conceptually equivalent handwritten answers together into `clusters.json` and `.embedding_cache.json`.
   - **Cross-Question & Cross-Assignment Caching**: Embeddings are keyed per embedding model in a shared cache (`~/.cache/gemini-handwriting-grader/embeddings/<model>.json` or `caching.directory`). Any answer text embedded once is reused across all questions and across all assignments on the machine with zero GCP calls and zero cost.
   - Enables instantaneous, intelligent answer grouping (📌) in `review-grading`.
   - **Skipping Clustering**: If the user skips this step or prefers to work offline, `review-grading` automatically falls back to client-side Jaccard word-overlap similarity (`Word Overlap (Local)`) with zero API calls. The teacher can also switch grouping modes at any time using the `Grouping` selector in the review table header.

   After grading and reporting are complete, always inform the user that results
   and review history have been preserved, and ask if they would like to clean
   or archive intermediate output using:
   `handwriting-grader clean --exam /path/to/exam.yaml`
   (which creates a cross-platform `.zip` archive backup by default, preserves `reports/`
   and `analytics/`, and requires explicit `--confirm-delete`).
9. For distribution read `references/email.md`; never send without reviewing
   the exact preview and separate dispatch approval.
   For targeted regrading read `references/regrading.md`; require a current
   approved parent, separate cost consent and fresh review of the child.
10. If the requested next stage is unimplemented, stop and say so.

For the detailed stage flow and known notebook gaps read
`references/flow-and-parity.md`; for the short gate overview read
`references/workflow.md`. Ask for email details only when distribution is
requested; never send without explicit approval immediately before dispatch.
