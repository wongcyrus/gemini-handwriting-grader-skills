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

## Scope and availability

Use the Python tools in this repository, not notebooks. Answer sheets, preflight, rubric
import/extraction, explicit reviewed attendance, region review, Vertex grading, human score review and
basic reports, offline statistics, optional search-grounded verification,
reviewed AI analysis, optional infographics, resumable checkpoints, opt-in
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

## Prepare an exam

1. Establish whether the user is starting or resuming, what stage they need,
   and which local paths they intend to use. Do not copy or overwrite data.
2. Read `references/configuration.md` and examine existing settings. Present
   detected values and ask for missing or ambiguous decisions. Keep actual
   configuration outside version control.
3. If the user needs printable answer sheets before scans exist, read
   `references/answer-sheets.md` and run that independent stage first.
4. From the repository root, run
   `.venv/bin/handwriting-grader preflight --exam /path/to/exam.yaml`.
   It makes no Vertex calls and does not create outputs.
5. Fix reported errors with the user's input and rerun. A successful preflight
   does not verify scan order, leading zeroes, rubric contents, credentials,
   model access or approval. Ask the user to confirm the layout and scan order.
   If attendance is configured, read `references/attendance.md` and obtain
   explicit review and approval before grading.
6. For region setup and review, read `references/regions.md`:
   a. Run `.venv/bin/handwriting-grader prepare-regions --exam /path/to/exam.yaml`
      to render the first script's page images into `output_dir/regions/images/`.
   b. Ask the user if they want Gemini to automatically detect bounding boxes.
      If yes, preview `suggest-regions` (with `--model-config`, `--project`,
      `--location`), explain the target and page count, obtain explicit cost
      consent, and run with `--confirm-cost` to generate `suggestions.json`.
   c. Start the local server with `review-regions --exam /path/to/exam.yaml`.
      Provide the URL so the user can inspect the automatically loaded AI proposals
      across all pages, adjust boxes (`ID`, `NAME`, `CLASS`, questions), save each
      page, and approve.
7. For rubric setup, read `references/grading.md`:
   a. If the marking scheme is a DOCX, run `extract-rubric` (preview first,
      obtain cost consent, then `--confirm-cost`) to extract questions and
      marks into `output_dir/rubric/rubric.xlsx` using Gemini.
   b. If the marking scheme is an existing XLSX, run `import-rubric`.
   c. Have the teacher inspect `rubric.xlsx` and explicitly approve with
      `approve-rubric --exam /path/to/exam.yaml --confirm-reviewed`.
8. For grading, reporting and AI analysis read `references/grading.md`.
   Obtain the specified human confirmations; never infer approval.
   After grading and reporting are complete, always inform the user that results
   and review history have been preserved, and ask if they would like to clean
   or archive intermediate output using:
   `.venv/bin/handwriting-grader clean --exam /path/to/exam.yaml`
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
