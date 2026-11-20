# Notebook-to-Python migration and usage guide

This guide compares the original
[gemini-handwriting-grader](https://github.com/wongcyrus/gemini-handwriting-grader)
notebook workflow with the Python tools in this repository. It describes the
**current working tree**; uncommitted changes may not be present in the
published repository. It does not claim exact scoring, formatting or
integration parity.

The original notebook directory is spelled `notebbooks`. The new workflow
uses the `handwriting-grader` CLI and local browser editors, not notebooks or
notebook kernels. See the [operational flow](../skills/handwriting-grading/references/flow-and-parity.md)
and [README](../README.md) for the current procedures. The separate
[attendance and regrading design](attendance-and-regrading-design.md)
describes the implemented attendance and targeted regrading workflows.

## What changed for a notebook user

| Notebook practice | Python workflow | Important consequence |
|---|---|---|
| Execute cells in order and rely on notebook variables and files named from an exam prefix. | Give commands an explicit `--exam` YAML; paths are relative to that YAML. | Configure and confirm paths, page layout and pass policy before creating approvals. |
| Run Step 1 for printed answer sheets. | `answer-sheets`. | Works before scans exist. Personalized DOCX files are temporary; retained outputs are individual PDFs, a combined PDF and a roster mapping. |
| Run Step 2 for an AI-extracted rubric. | `import-rubric` for structured XLSX, or `extract-rubric` for DOCX; optionally `verify-rubric`, then `approve-rubric`. | AI extraction and search are separate paid choices. Fact checking is advisory, not rubric approval. |
| Use the Step 3 notebook widget to edit detected boxes. | `prepare-regions`, optionally `suggest-regions`, then `review-regions` in a local browser. | Suggestions do not replace the draft automatically. Every page and its boxes must be reviewed and approved. |
| Run Step 4 OCR, grading and moderation and use the generated question table. | `grade`, then `review-grading`, or review the XLSX directly. | A local browser has individual review, a per-question comparison table, bulk draft suggestions and temporary crop adjustment. Adjusted grading crops require a separately reviewed child-run proposal. |
| Adjust similarity controls to recalculate marks across a question. | Preview per-answer or bulk suggestions with an explicit threshold and increment. | Suggestions ignore moderation. Bulk selection requires confirmation and saves unreviewed manual drafts; no mark is automatically approved. |
| Run Step 5 checks and optionally cleanup/reset history. | Optional reviewed attendance and read-only `check-grading-review`, then `approve-grading`; edits and approvals remain separate. | Absences are explicit; failed/flagged grades and changed answers require manual marks. Automatic cleanup/reset is intentionally absent. |
| Run Step 6.1 to create marked PDFs, workbooks, samples and backups. | `report` after score approval. | Outputs and formatting differ; no automatic archive or history cleanup. Pass-only samples require an explicit policy. |
| Run Step 6.2 to create AI analysis, charts, Word and PowerPoint. | `analytics` for offline statistics; optional `ai-analysis`, `approve-analysis`, `infographics` and `advanced-report`. | AI feedback and images have separate review/consent gates. Workbook sheets, charts, document layout and slide notes are not exact replicas. |
| Configure SMTP and call the Step 7 notebook. | Provide a private settings YAML and present-student `ID,EMAIL` CSV; `preview-email`, `approve-email`, then `send-email`. | Review exact messages and attachments; sending requires separate confirmation. An ambiguous delivery is not automatically retried. |
| Reuse notebook ADK cache and logging settings. | Explicit per-agent Python caches, disabled by default. | Existing notebook cache format is not imported; not all original cache and logging options are implemented. |

### Notebook and file inventory

These are the original files in `notebbooks/`. Their listed responsibilities
are based on notebook source and the original README, not a claim that every
cell has been executed or every advertised feature has been verified.

| Original notebook | Main original artifacts | Python replacement |
|---|---|---|
| `step1_generate_answer_sheet.ipynb` | `data/<exam_prefix> Answer Sheets Combined.pdf` | `answer-sheets/` |
| `step2_generate_marking_scheme_excel.ipynb` | `data/<exam_prefix> Marking Scheme.xlsx`; optional verification DOCX | `rubric/`; optional `rubric/verification/` |
| `step3_question_annotations.ipynb` | `marking_form/<exam_prefix>/images/` and `annotations/annotations.json` | `regions/` |
| `step4_scoring_preprocessing.ipynb` | Per-question `mark.json` and generated review pages | `grading/` and local score editor |
| `step5_post_scoring_checks.ipynb` | Checks, version cleanup and optional reset cells | Attendance and grading review and approval; no cleanup/reset |
| `step6_1_basic_reporting.ipynb` | Score spreadsheets, marked PDFs, samples and archive/cleanup | `reports/`; no automatic archive/cleanup |
| `step6_2_ai_analysis.ipynb` | AI analyses, charts, Word/PPT reports and archive/cleanup | `analytics/` and optional `ai-analysis/`; no automatic archive/cleanup |
| `step7_email_score.ipynb` | SMTP email using a user-supplied lookup function and `smtp.config` | `email/`, separate settings YAML and recipient CSV |

Original inputs conventionally lived under `data/`: `<exam_prefix>.pdf`,
`<exam_prefix> Name List.xlsx`, `<exam_prefix> Answer Sheet.docx` and
`<exam_prefix> Marking Scheme.docx`. In the new workflow, point `exam.yaml`
at the chosen files instead of relying on these names. An existing notebook
rubric XLSX can be imported if its `Marking Scheme` sheet passes the new
validation; check every question and approve the copied workbook separately.
The import does not treat the notebook's verification or other sheets as a
new approval. Original scored reports, `mark.json` files, review UI, cache
and approvals are **not automatically imported** into the Python run.
Preserve them rather than assuming they are compatible.

## Inputs and decisions before starting

Gather these from the teacher rather than guessing:

1. Exam name, roster XLSX and its sheet, scan PDF, rubric source, and a
   separate private output directory. The roster requires nonempty `ID`,
   `NAME`, `CLASS` values and unique IDs. Check that leading zeroes are stored, not
   merely displayed by Excel.
2. Pages per script, scan order and the decision that every script uses the
   same layout. Preflight requires PDF pages = present students × pages per
   script. Without an attendance list, every roster entry is expected.
   It cannot tell whether the scanned order or student identities are correct.
3. Whether a DOCX answer-sheet template or an existing XLSX/DOCX rubric is
   available. An importable XLSX needs a `Marking Scheme` sheet with
   `question_number`, `question_text`, `marking_scheme`, `marks`.
4. If pass-only samples or pass statistics are required, the absolute
   `pass_mark` and whether equality passes. Both are required; neither is
   inferred from the notebook. Set them before approving other stages.
5. Only for a requested AI operation: model configuration, Google Cloud
   project, location, authentication, permission to transfer the specific
   data and approval of possible charges. For email: sender, SMTP settings,
   recipients, content and explicit approval to send.

`exam.yaml.template` lists the exam keys. Keep real inputs, configuration,
outputs, cached student data and credentials private and out of Git. Input
paths must not be inside the output directory. `reporting.advanced_analysis`
and `email.enabled` do **not** automatically run AI analysis or send email.
Changing exam configuration invalidates existing approvals.

## Worked use case: two students and two questions

This example describes a synthetic exam, not a real dataset. The roster has
IDs `001` and `002`, both students submitted a two-page script, and the
combined PDF therefore contains four pages. The rubric has questions `1`
and `2`; both use the same layout for every student. The teacher has
independently confirmed the roster, page order, layout and output location.

The following YAML illustrates the shape of the exam configuration. Replace
all paths with the actual private files; the example does not create them.
Quoted IDs in the source workbook must be stored as text if zeroes matter.

```yaml
exam:
  name: "Synthetic exam"
  roster: "inputs/roster.xlsx"
  roster_sheet: "Roster"
  attendance: null
  answer_sheet_template: "inputs/answer-sheet.docx"
  scanned_pdf: "inputs/scans.pdf"
  marking_scheme: "inputs/rubric.xlsx"
  output_dir: "output"
layout:
  pages_per_student: 2
  same_layout_for_all: true
reporting:
  pass_mark: null
  passing_includes_equal: null
  advanced_analysis: false
email:
  enabled: false
```

In the examples below, `EXAM` and `MODELS` are placeholders: set them to
private files, and replace project and location placeholders. Run commands
from the skills repository after installing its virtual environment as shown
in the [README](../README.md#setup). Poppler (`pdftoppm`) is required to
render scans; LibreOffice is required for answer-sheet conversion.

```sh
EXAM=/private/exam/exam.yaml
MODELS=/private/exam/model-config.yaml
```

### A. Optional answer sheets, before the exam

If requested and LibreOffice is installed:

```sh
.venv/bin/handwriting-grader answer-sheets --exam "$EXAM"
```

Inspect `output/answer-sheets/combined.pdf`, the numbered files under
`pdfs/`, `students.xlsx` and `manifest.json`. Check identity, ordering and
pagination before printing. The template must contain `Name:`, `Student ID:`
and `Class:` in supported paragraphs, tables, headers, footers, Word text
boxes or hyperlink text. Placeholders in Word fields or spanning tabs or line
breaks, and multi-paragraph Word fields, are unsupported. Inspect the PDF
layout because LibreOffice may render it differently. An existing output is
not overwritten.

### B. Preflight, rubric and regions

After the exams are scanned, check the complete exam inputs:

```sh
.venv/bin/handwriting-grader preflight --exam "$EXAM"
```

Preflight reads inputs but does not call Vertex, approve work, establish
correct scan order or check model access. If an input is missing, a page
count is wrong, or layout confirmation is absent, stop and resolve it with
the teacher rather than bypassing the check.

For the example XLSX:

```sh
.venv/bin/handwriting-grader import-rubric --exam "$EXAM"
```

Review `output/rubric/rubric.xlsx` against the source, including question
text, criteria and marks. After the teacher explicitly approves it:

```sh
.venv/bin/handwriting-grader approve-rubric --exam "$EXAM" --confirm-reviewed
.venv/bin/handwriting-grader prepare-regions --exam "$EXAM"
.venv/bin/handwriting-grader review-regions --exam "$EXAM"
```

Open the full URL printed by the region command and keep its token private.
For each page create and check the `NAME`, `ID`, `CLASS` and answer boxes;
ID must be on the first page. Question labels must correspond to the rubric.
For example, `Q1` and `1` canonicalize to the same question, so they cannot
be separate boxes. Use **Save and mark reviewed** on each page, then
**Approve all regions** only after checking every page. Stop the local server
with Ctrl+C.

If a DOCX rubric or AI box suggestions are desired, first use the respective
command **without** `--confirm-cost` to preview. Run `extract-rubric` instead
of `import-rubric`, with `exam.marking_scheme` pointing to the DOCX. For box
suggestions, run `suggest-regions` after `prepare-regions`:

```sh
.venv/bin/handwriting-grader extract-rubric --exam "$EXAM" \
  --model-config "$MODELS" --project PROJECT_ID --location LOCATION
.venv/bin/handwriting-grader suggest-regions --exam "$EXAM" \
  --model-config "$MODELS" --project PROJECT_ID --location LOCATION
```

Review the model, project, location and data transfer; only after
the teacher separately agrees to the possible charges repeat it with
`--confirm-cost`. AI suggestions are saved separately and must be loaded,
corrected and explicitly reviewed. Optional `verify-rubric` similarly
requires a separate search/data and cost decision:

```sh
.venv/bin/handwriting-grader verify-rubric --exam "$EXAM" \
  --model-config "$MODELS" --project PROJECT_ID --location LOCATION
```

Preview first, then add `--confirm-cost` only after consent. Check returned
source links; verification is not approval.

### C. Grade and review

The model configuration needs Vertex enabled and `models.ocr`,
`models.grading` and `models.moderation`; other models are needed only for
their optional stages. Configure authentication outside the YAML and obtain
permission to send the scanned work. Preview:

```sh
.venv/bin/handwriting-grader grade --exam "$EXAM" \
  --model-config "$MODELS" --project PROJECT_ID --location LOCATION
```

For a new run, the preview's maximum is `N × (3 + 2Q) + Q`: for two
students and two questions, **16** possible requests. This includes NAME,
ID and CLASS OCR per student, answer OCR and grading per question/student,
and moderation per question. Cache hits or skipped moderation may reduce
calls; resumed/in-flight calls may repeat and incur charges. Previewing
without `--confirm-cost` sends nothing. Only after explicit authorization,
repeat the same command with `--confirm-cost`.

The resulting `grading/results.json` is the raw model baseline;
`grading/review.xlsx` is the human review. Start:

```sh
.venv/bin/handwriting-grader review-grading --exam "$EXAM"
```

Close other spreadsheet editors. In the browser select each script, compare
the ID/name/class OCR with the metadata crop or full page, choose the actual
roster ID and **Save and mark reviewed**. Then select each question, check
the image, answer, mark, moderation flag and reasoning. Edit the answer and
reasoning if needed. A failed or flagged grade, or any changed or cleared
answer, requires a manual numeric mark within the question's maximum. Save
each mark as reviewed. Changing reasoning alone does not require a manual
mark, but the teacher must ensure it matches the final score.

The browser's read-only comparison table shows saved answers and marks for
one question across scripts. Filter by unreviewed or flagged items, sort by
script or effective mark, and open a row for individual image review. Refresh
the table after external changes. Bulk suggestions can be explicitly applied
to selected rows as unreviewed drafts; the table never approves marks.

For an original OCR answer with similarity `0.5`, maximum `2`, threshold
`0.8` and increment `0.25`, the optional suggestion is `1.00`. This is
`floor(2 × 0.5 / 0.25) × 0.25`; at or above the threshold it suggests full
marks. It ignores moderation. The teacher must decide whether to copy it to
the manual field and still save the review; calculating alone changes no
score. Similarity cannot be used after the OCR answer is changed, and older
runs may not have a saved similarity or NAME OCR.

To find review discrepancies without changing files or recording approval:

```sh
.venv/bin/handwriting-grader check-grading-review --exam "$EXAM"
```

The private output reports identity and mark issues; exit status 1 means
discrepancies or an input error. Exit 0 does not establish human review or
approve the scores. For a child regrade, add `--run CHILD`.

After every identity and every mark is reviewed, use **Approve all grading**
and confirm, or stop the editor and run:

```sh
.venv/bin/handwriting-grader approve-grading --exam "$EXAM" --confirm-reviewed
```

Approval verifies the complete present roster and all questions, distinct IDs,
reviewed flags, manual overrides and mark ranges. A saved draft is not
approval. Browser saves preserve previous workbook versions in
`grading/review-history/`; they invalidate earlier approvals. For a separate
spreadsheet editor, follow the `Scripts` and `Marks` columns described in
[grading.md](../skills/handwriting-grading/references/grading.md), and do not
edit concurrently with the browser.

### D. Reports and optional analysis

After score approval:

```sh
.venv/bin/handwriting-grader report --exam "$EXAM"
.venv/bin/handwriting-grader analytics --exam "$EXAM"
```

`reports/score_report.xlsx` maps students to numbered PDFs;
`details_score_report.xlsx` has `Marks`, `Answers`, `Reasoning`,
`AnswersRaw` and `ReasoningRaw`. Reviewed text is used in the ordinary
sheets; raw sheets retain original model output. Individual PDFs, `all.pdf`
and available stratified samples are in `reports/`. With only two students,
no three-stratum sample is generated. `analytics/statistics.xlsx` includes
overall, class, question and distribution sheets. Overall and per-question
pass counts are blank unless their respective policies are configured.
Inspect completion manifests and all outputs
before sharing. `analytics/question-boxplots-*.png` shows reviewed question
marks, up to 20 questions per image, using 1.5-IQR whiskers and outlier points.

Optional `ai-analysis` previews the target and data first:

```sh
.venv/bin/handwriting-grader ai-analysis --exam "$EXAM" \
  --model-config "$MODELS" --project PROJECT_ID --location LOCATION
```

After separate cost consent repeat with `--confirm-cost`; it creates
`ai-analysis/review.xlsx`. Review all student, question and class feedback,
then explicitly approve it and produce the teacher DOCX and class PPTX:

```sh
.venv/bin/handwriting-grader approve-analysis --exam "$EXAM" --confirm-reviewed
.venv/bin/handwriting-grader advanced-report --exam "$EXAM"
```

Optional `infographics` require another paid-call approval and image review:

```sh
.venv/bin/handwriting-grader infographics --exam "$EXAM" \
  --model-config "$MODELS" --project PROJECT_ID --location LOCATION
```

Only after consent, repeat with `--confirm-cost`. If the images are reviewed
and explicitly approved, include them in an advanced report with
`advanced-report --exam "$EXAM" --include-infographics
--confirm-images-reviewed`. Choose this form **instead of** the previous
`advanced-report` command: an existing report directory is not overwritten.
These reports differ from the notebook's layouts and charts.

### E. Optional email

Use the [email procedure](../skills/handwriting-grading/references/email.md)
to prepare separate settings and an exact `ID,EMAIL` recipient CSV covering
the present roster, or the complete roster when no attendance is configured.
For the synthetic example, the CSV shape is:

```csv
ID,EMAIL
001,student001@example.invalid
002,student002@example.invalid
```

Replace these non-deliverable example addresses with teacher-verified
recipients before any actual dispatch. A settings file must contain only
`sender`, `smtp_host`, `smtp_port`, `smtp_username`, `subject` and `body`.
The subject supports `{name}`, `{marks}` and `{exam}`; the body also supports
`{feedback}`, which requires current approved AI analysis. Passwords come
from `HANDWRITING_SMTP_PASSWORD`, not settings or Git.

With the teacher's private settings and recipients:

```sh
.venv/bin/handwriting-grader preview-email --exam "$EXAM" \
  --settings /private/exam/email.yaml --recipients /private/exam/recipients.csv
```

This creates individual `.eml` files and sends nothing. Review every
recipient, subject, body and scored PDF. After explicit approval:

```sh
.venv/bin/handwriting-grader approve-email --exam "$EXAM" --confirm-reviewed
```

Obtain a **separate** explicit decision immediately before dispatch:

```sh
.venv/bin/handwriting-grader send-email --exam "$EXAM" --confirm-send
```

SMTP acceptance does not prove delivery.

## Other common notebook migration cases

| Situation | Current Python behavior |
|---|---|
| A student is absent. | Configure and explicitly approve an attendance CSV with a status for every roster entry. Reports, analytics and email include present students only; `reports/absences.xlsx` lists absences without scores. Do not infer absence or assign zero. |
| A scan has extra pages. | Preflight blocks a page-count mismatch. Confirm scan order and obtain corrected scans; do not silently change the roster. |
| Different students have different layouts or a missing page. | Unsupported. Preflight requires a shared layout and fixed pages per script. Obtain corrected scans or agree on another workflow. |
| A teacher corrects an answer after AI grading. | Edit the answer and verify reasoning; enter a manual score and review it. Reports and AI analysis use reviewed text; raw sheets retain the model text. |
| A teacher changes the rubric or wants to rerun just one question. | Use the [targeted regrading procedure](../skills/handwriting-grading/references/regrading.md): select an approved parent, separately approve a changed rubric if needed, preview cost, and freshly review a separate child run. |
| A scan, roster, configuration, approved region or rubric changes. | Dependent approvals become stale or a stage refuses to proceed. Preserve existing output and inspect it; generated files do not automatically disappear. |
| A grading run is interrupted. | `resume-grading` supports only its compatible partial checkpoint when no review workbook or approval exists. Preview again and obtain cost consent; an in-flight call can repeat. Other stages do not have general resume. |
| A result or report already exists. | Most stages refuse overwrite. Do not delete, move or replace a user's files automatically. Choose a separate run and follow current prerequisites. |
| A model call fails or moderation is flagged. | Failed scores are not automatically zero. Review the original work and provide a valid manual mark; do not treat partial output as complete. |
| AI feedback is wrong. | Correct the appropriate `ai-analysis/review.xlsx` row and mark it reviewed; approve the whole analysis only after reviewing every required row. |
| An email attempt fails after submission may have begun. | Inspect `email/delivery.json` and reconcile with the SMTP provider and recipients. Do not automatically retry, delete the journal or assume delivery. |
| An old notebook cache or `mark.json` already exists. | There is no importer for these artifacts or their human approvals. Preserve them; the new cache format and run need their own validation and review. |

## Artifact map and security

All paths below are under the configured output directory. They are private
student or assessment data:

| Location | Role |
|---|---|
| `answer-sheets/` | Optional numbered PDFs, `combined.pdf`, `students.xlsx`, completion manifest. |
| `attendance/` | Optional private versioned review and approval. |
| `regrade-rubrics/`, `regrade-crops/`, `runs/` | Optional separately approved replacement rubrics and crops, and isolated child grading, reports, analytics and email. |
| `rubric/` | Reviewable `rubric.xlsx`, manifest and approval; optional search `verification/` with XLSX and Word reports. |
| `regions/` | Rendered pages, draft, optional suggestions, approved annotations and approval. |
| `grading/` | All page images, manifest, raw results, review workbook, approval and browser-review history; optional run cache. |
| `reports/` | Reviewed score workbooks, numbered scored PDFs, combined/sampled PDFs and manifest. |
| `analytics/` | Offline statistics workbook, question box-plot PNGs and manifest. |
| `ai-analysis/` | Review workbook and approval; optional infographic and advanced report directories. |
| `email/` | Exact-message previews, approval and delivery journal. |
| `model-cache/` | Optional per-run rubric extraction cache; other stage caches may be inside their respective output directories. |

Approvals bind relevant file hashes, not a tamper-proof identity or audit
system. The browser binds to localhost with a temporary bearer token and is
not a multi-user server. Workbook edits from other programs can race with
browser saves. Logs, cached outputs and historical reviews may contain
sensitive data; do not share or automatically clean them. Opt-in caches for
annotation, rubric extraction, OCR, grading, moderation and analytics are
private to a run by default; shared caches need explicit private configuration.
For a regrade, `--reuse-parent-cache` explicitly selects the approved parent's
existing private cache; matching responses may be reused rather than rerun.
See the [regrading procedure](../skills/handwriting-grading/references/regrading.md).
Grounded search and infographics are not cached. A model alias can change
without changing a cache key; do not use cached output when freshness matters.

## Verification and boundaries

Current tests use synthetic fixtures, local fake services, browser automation
when available and LibreOffice conversion. They do not prove live Vertex model
availability or output quality, real SMTP delivery, large-exam performance,
or identical notebook scoring and report appearance. No live paid or email
operation is authorized by this guide.
