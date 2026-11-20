# Handwriting Grader Skills

A skill and Python tools for planning a human-reviewed handwriting grading run.
This project is being migrated from
[gemini-handwriting-grader](https://github.com/wongcyrus/gemini-handwriting-grader).

**Current scope:** answer-sheet generation, preflight, rubric import or
extraction, optional reviewed attendance, region review, Vertex OCR/grading/moderation, human score review,
isolated targeted regrading,
basic reports, offline statistics, search-grounded rubric review, reviewed AI
analysis and email preview/dispatch. Advanced report formatting and cache
coverage differ from the notebooks. Validate results before use.

See [detailed workflow and notebook gaps](skills/handwriting-grading/references/flow-and-parity.md)
for stage-by-stage inputs, human decisions, outputs and known missing behavior.

### Technical & Architecture Documentation
- [Cross-Platform Architecture](docs/cross-platform-architecture.md): Pure Python PDF rendering with `pypdfium2`, sub-pixel dimension parity, and universal Windows/Mac/Linux support.
- [Self-Contained Multi-Assignment Design](docs/multi-assignment-design.md): Managing multiple independent exam/homework folders without workspace coupling or collisions.
- [ADK Telemetry & Cost Reporting](docs/telemetry-and-cost-reporting.md): Real-time sub-step progress tracking, Gemini token accounting, and `cost-report` generation.
- [Cleanup, Archival & Retention Design](docs/cleanup-and-retention-design.md): Student data protection principles, automatic cross-platform `.zip` backups, and gated cleanup.
- [Migration & Parity Guide](docs/notebook-to-python.md): Detailed comparison between legacy notebook workflows and modern Python CLI tools.
- [Attendance & Regrading Architecture](docs/attendance-and-regrading-design.md): Detailed lineage tracking for isolated one-question regrades.
- [Agent Skill Testing](docs/testing-skills.md): Testing across Antigravity CLI, Copilot CLI, and Gemini CLI.

## Setup

Python 3.10+ and a virtual environment are required:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
cp exam.yaml.template exam.yaml
```

### Cross-platform compatibility (Windows, macOS, Linux)

PDF rendering is powered by pure-Python `pypdfium2` (Google PDFium engine bundled inside wheels), working out of the box across Windows, macOS, and Linux without requiring system Poppler or `pdftoppm` installations. (If `pdftoppm` is present, it is automatically used as a transparent fallback).

For optional answer sheet generation (`answer-sheets`), LibreOffice (`soffice`) is required to convert Word templates to PDF.

### Google Agent Development Kit (ADK) skills

For ADK-style agent workflows, real-time step progress tracking, and model/token cost auditing:
```sh
uvx google-agents-cli setup
```
Or install directly:
```sh
pip install google-adk
```

Edit `exam.yaml` locally. Relative paths are resolved relative to that file.
Never commit actual exam configurations, student files, results, credentials,
cache entries or saved notebook output. The ignore rules are a precaution,
not a guarantee: check staged files before every commit.

```sh
.venv/bin/handwriting-grader preflight --exam exam.yaml
```

The command only reads inputs. It checks the configured roster sheet, required
columns, blank and duplicate IDs, PDF page count, uniform-layout confirmation
and separation of inputs from the output directory. It does not confirm scan
order, recover leading zeroes lost in Excel, validate the marking scheme's
contents, check Vertex credentials, invoke models, or approve any grading.
A passing result is not approval to grade. For absent students, configure and
explicitly review attendance as described in
[attendance.md](skills/handwriting-grading/references/attendance.md); the CSV
must identify every roster student as present or absent.

## Generate answer sheets

Before scans exist, set `exam.roster`, `exam.roster_sheet`,
`exam.answer_sheet_template` (DOCX) and `exam.output_dir`. The template must
contain `Name:`, `Student ID:` and `Class:` in normal paragraphs, tables,
headers or footers. Placeholders split across text runs are supported;
placeholders in Word text boxes and hyperlink text are also supported.
Placeholders in Word fields and multi-paragraph fields are unsupported; always
inspect the generated PDFs. Install LibreOffice and run:

```sh
.venv/bin/handwriting-grader answer-sheets --exam exam.yaml
```

This requires neither a scanned PDF nor a marking scheme. It creates private
individual PDFs, a roster-to-PDF mapping and `combined.pdf` in roster order
under `output_dir/answer-sheets/`. Review names, IDs, pagination and layout
before printing. All generated sheets must have the same page count; if
`layout.pages_per_student` is set it must match. Existing runs are never
overwritten, and a partial run has no completion manifest. With Snap
LibreOffice, select an output directory it can access (often not `/tmp`).

## Review regions

PDF pages are rendered automatically at 200 DPI via `pypdfium2` (cross-platform). After confirming scan order,
page count and the shared-layout assumption:

```sh
.venv/bin/handwriting-grader prepare-regions --exam exam.yaml
.venv/bin/handwriting-grader review-regions --exam exam.yaml
```

Open the full local URL printed by the second command; press Ctrl+C in the
terminal to stop the server. Keep that URL private while the editor runs. It
binds to localhost, requires a short-lived access token and checks request
origins; it is intended for local use, not as a multi-user server.

The first command renders the first student's page layouts under
`output_dir/regions/` and refuses to replace an existing region run. In the
editor, click **New box**, enter a label, drag on the page, then select boxes
to move, resize, relabel or delete them. Save and mark each page reviewed
before approving. The result is `regions/annotations.json` with original-image
pixel coordinates; `approval.json` records its hash. Subsequent saved edits
invalidate approval. A changed exam configuration, source PDF or rendered
page prevents continuing the run. Grading checks these approvals; the local
records are not tamper-proof.

The editor starts with empty boxes and does not itself call Gemini. It
validates box bounds, required metadata labels and duplicate question labels,
and requires ID on the first page, but cannot verify that boxes match the rubric
or that the layout applies to all scripts. Those are teacher decisions.

### Optional Vertex suggestions

Use the existing grader's `config.yaml`, which must enable Vertex AI and set
`models.annotation`. Authenticate using Application Default Credentials.
First preview the exact target and image count:

```sh
.venv/bin/handwriting-grader suggest-regions --exam exam.yaml \
  --model-config /path/to/config.yaml --project PROJECT_ID --location LOCATION
```

No request is sent without `--confirm-cost`. After the user approves sending
the images and the possible cost, repeat the command with that flag. Requests
already sent can incur charges even if the operation fails. The SDK and
response handling are tested offline; live Vertex model availability and
quality have not been verified.

Suggestions are stored separately and never replace the draft automatically.
In the editor choose **Load AI suggestions**, review and correct each box,
then save and explicitly approve. The student data in the page images is sent
to the specified Vertex project when suggestions are requested.

The skill is in `skills/handwriting-grading/SKILL.md`; see the
[testing guide](docs/testing-skills.md) for Copilot CLI, Antigravity CLI and
Gemini CLI registration and testing.

## Rubric, grading and reports

Configure all settings before starting: changing `exam.yaml` invalidates
earlier approvals. Optional pass-only samples require both `reporting.pass_mark`
and `reporting.passing_includes_equal`; leave both null to skip those samples.
There is no assumed passing threshold.

Import an XLSX with a `Marking Scheme` sheet and `question_number`,
`question_text`, `marking_scheme`, `marks` columns:

```sh
.venv/bin/handwriting-grader import-rubric --exam exam.yaml
```

Alternatively, extract a DOCX with `extract-rubric --exam exam.yaml
--model-config /path/to/config.yaml --project PROJECT_ID --location LOCATION`.
This previews the target; after explicit approval for data transfer and cost,
add `--confirm-cost`. Review `output_dir/rubric/rubric.xlsx` against the
original, then explicitly approve it:

```sh
# Optional paid search-grounded review; preview first, then add --confirm-cost
.venv/bin/handwriting-grader verify-rubric --exam exam.yaml \
  --model-config /path/to/config.yaml --project PROJECT_ID --location LOCATION
.venv/bin/handwriting-grader approve-rubric --exam exam.yaml --confirm-reviewed
```

New verification runs write XLSX and Word reports with AI reviews and returned
source links; this does not prove
factual correctness or approve the rubric. Teachers must check the actual
sources and review the marking scheme.

Complete region review as above. The grading command checks that rubric and
region labels match and that both approvals are current. Its model config
requires `vertex_ai.use_vertexai: true` and `models.ocr`, `models.grading`,
and `models.moderation`. Authenticate separately with Google Application
Default Credentials. Preview the target and maximum request count:

```sh
.venv/bin/handwriting-grader grade --exam exam.yaml \
  --model-config /path/to/config.yaml --project PROJECT_ID --location LOCATION
```

After explicit approval to send student work and incur charges, repeat with
`--confirm-cost`. During execution, real-time step progress and token usage
are displayed following ADK conventions. A partial failure can still incur charges. Supported
interrupted runs can be resumed with `resume-grading --exam exam.yaml`,
followed by `--confirm-cost` after review; in-flight requests may repeat and
be billed. Older checkpoints and existing reviews cannot be resumed. A stale
`processing.lock` requires manual investigation; never remove a live lock.

To inspect token consumption, model breakdown, and estimated costs at any time:
```sh
.venv/bin/handwriting-grader cost-report --exam exam.yaml
```
This generates `cost_report.xlsx` (Summary and API Audit Log sheets) and `cost_summary.json` inside the run directory.
The model configuration's `caching.enabled` and `caching.agents` flags opt
into caches for `ocr`, `grading`, `moderation`, `annotation`, `marking_scheme`
extraction and `analytics`. By default they are private to each run. To share them
across runs, explicitly set `caching.scope: shared` and `caching.directory`
in the model config; relative directories resolve from that config. Its
private cache directory must be owned by the user and inaccessible to group
or others. It contains sensitive answers and is never automatically deleted.
Model aliases can change; avoid shared cache reuse when freshness is required.
Grounded search verification and infographics are not cached; logging settings
are not implemented. Cache hits never replace cost consent or human review.
An isolated regrade can explicitly reuse its approved parent's run cache with
`--reuse-parent-cache`; this may reuse responses instead of regenerating them.
See [regrading.md](skills/handwriting-grading/references/regrading.md).

The AI and its output can be wrong. Start the local review interface:

```sh
.venv/bin/handwriting-grader review-grading --exam exam.yaml
```

Open the full printed URL privately. Review each identity and answer crop or
full page, edit answers, reasoning and marks, and explicitly mark every item
reviewed. The approval button requires confirmation and performs the same
validation as `approve-grading`. Stop the server with Ctrl+C. Keep other
spreadsheet editors closed: edits from non-cooperating applications can race
with saves. Each prior workbook is retained privately in `grading/review-history/`;
saves invalidate earlier approvals. Investigate any stale `.score-review.lock`
and never remove a live lock. Alternatively, review
`output_dir/grading/review.xlsx`: in **Scripts** enter each roster ID and
`yes` in `reviewed`; in **Marks** inspect and correct the `answer` and
`reasoning` alongside the grade and moderation. Enter a `manual_mark` for
every failed or flagged result **and whenever an answer is corrected**,
including when it is cleared. Review the reasoning whenever a mark changes.
Enter `yes` in `reviewed` for every row. Script indices are zero-based;
page images are in `grading/images/`. Verify identities and scan order
against the originals. Then:

```sh
.venv/bin/handwriting-grader approve-grading --exam exam.yaml --confirm-reviewed
.venv/bin/handwriting-grader report --exam exam.yaml
.venv/bin/handwriting-grader analytics --exam exam.yaml
```

Reports are written under `output_dir/reports/`: score and detail workbooks,
numbered individual PDFs, a combined PDF and, when enough scripts exist,
stratified samples. The workbook maps each student to a numbered PDF. Review
the PDFs and scores before sharing. The detail workbook's `Answers` and
`Reasoning` sheets use reviewed text; `AnswersRaw` and `ReasoningRaw` preserve
the original model output. A `manifest.json` marks completion and
records the grading approval and review hashes; partial reports have no
completion marker. Existing reports are not automatically deleted or
invalidated if inputs later change. Do not distribute stale or partial output.
`analytics/statistics.xlsx` contains offline overall, class and question
statistics, question means and a score-distribution chart from approved marks.
`analytics/question-boxplots-*.png` shows reviewed question-score distributions
in groups of up to 20 questions, with 1.5-IQR whiskers and outlier points.
Overall and per-question pass counts and rates are blank unless their
respective thresholds and equality policies were explicitly configured.
Commands refuse to overwrite existing runs; preserve and inspect them before
choosing how to restart. The implementation and SDK request shapes have been
tested with synthetic data and a local fake service, not live Vertex inference.

## Optional AI analysis

After approving grading, preview `ai-analysis --exam exam.yaml --model-config
/path/to/config.yaml --project PROJECT_ID --location LOCATION`. It requires
`models.analytics`, sends answers and marking schemes, and may incur charges.
After explicit approval, repeat with `--confirm-cost`. Review every row of
`ai-analysis/review.xlsx` (Students, Questions and Class), edit inaccurate
feedback, and set each `reviewed` cell to `yes`. Then:

```sh
.venv/bin/handwriting-grader approve-analysis --exam exam.yaml --confirm-reviewed
.venv/bin/handwriting-grader advanced-report --exam exam.yaml
```

The reports include a teacher Word report and a PowerPoint with a question
mean chart. Optionally, after analysis approval, preview `infographics --exam
exam.yaml --model-config /path/to/config.yaml --project PROJECT_ID --location
LOCATION`; it uses `models.analytics_image` and sends reviewed analysis.
After separate cost/data consent, rerun with `--confirm-cost`, review the
images, then add `--include-infographics --confirm-images-reviewed` to `advanced-report`. Reports
contain sensitive information and must be reviewed before sharing. Later edits
invalidate approval; existing reports do not automatically disappear.

## Optional email distribution

Create private email settings and a separate recipient CSV outside the output
directory. Do not put credentials in the settings or command line:

```yaml
sender: teacher@example.test
smtp_host: smtp.example.test
smtp_port: 587
smtp_username: teacher
subject: "{exam} result"
body: "Dear {name}, your score is {marks}.\n{feedback}"
```

The CSV must have exactly `ID,EMAIL` headers and one unique address for every
present student; without an attendance list it must cover the entire roster.
Addresses are never guessed. `{feedback}` is optional and requires
approved AI analysis; omit it if no feedback is wanted. Set
`HANDWRITING_SMTP_PASSWORD` in the local environment, not in Git.

```sh
.venv/bin/handwriting-grader preview-email --exam exam.yaml \
  --settings /private/email-settings.yaml --recipients /private/recipients.csv
.venv/bin/handwriting-grader approve-email --exam exam.yaml --confirm-reviewed
.venv/bin/handwriting-grader send-email --exam exam.yaml --confirm-send
```

Inspect every recipient, body and attached PDF in `email/preview.json` and the
individual `.eml` files before approving. Sending requires the separate
confirmation shown above and uses SMTP STARTTLS. Input, approval or PDF
changes invalidate the preview. `email/delivery.json` records accepted and
in-flight messages; an interrupted or ambiguous attempt is never automatically
retried. Reconcile with the SMTP provider and recipients before deciding what
to do. SMTP acceptance does not guarantee delivery. Only mocked SMTP has been
tested; no real messages were sent.

## Tests

For skill registration, safe Copilot prompts, offline tests and a synthetic
exam walkthrough using the included notebook samples, see the
[testing guide](docs/testing-skills.md).

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Tests generate synthetic data in temporary directories.
To also test the browser UI with an installed Google Chrome:

```sh
.venv/bin/python -m pip install -e '.[browser-test]'
.venv/bin/python -m unittest discover -s tests -v
```
