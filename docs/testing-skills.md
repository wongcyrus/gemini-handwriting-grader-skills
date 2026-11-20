# Testing the handwriting-grading skill

The skill is a set of instructions for an agent; the Python package implements
the grading tools. Test both separately. This guide is for users and does not
change the instructions loaded by agents.

## 1. Register and check the skill

Choose the instructions for your agent below. Run the setup commands from the
root of this repository, replacing the path with your checkout:

```sh
cd /path/to/gemini-handwriting-grader-skills
```

### GitHub Copilot CLI

The commands in this section were checked against GitHub Copilot CLI 1.0.113.

```sh
copilot skill add "$PWD/skills"
copilot skill list
```

Look for `handwriting-grading` in the list. Adding the directory registers it
in your Copilot settings; it does not copy or edit the skill files. Register
the **directory**, not just `SKILL.md`, so its bundled references remain
available. To unregister it later, run `copilot skill remove "$PWD/skills"`
from the same checkout; this removes the registration, not the files.

Start `copilot` from the repository root. In an existing Copilot session, use
`/skills reload` after registration or edits. `/skills list` and `/env` show
available or loaded skills. Their presence does not by itself prove the agent
followed every instruction.

### Antigravity CLI (`agy`) and Gemini CLI

These agents can discover a workspace skill under `.agents/skills/`. The
repository stores the original in `skills/`, so copy the **whole directory**
into that discovery location, including its references. The following
commands leave an existing destination untouched:

```sh
mkdir -p .agents/skills
if [ -e .agents/skills/handwriting-grading ] || [ -L .agents/skills/handwriting-grading ]; then
  echo "Skill already exists; inspect it rather than overwriting it."
else
  cp -R skills/handwriting-grading .agents/skills/handwriting-grading
fi
```

This is a workspace-local copy; it does not edit the original skill. It does
not update automatically when the original changes, so inspect and refresh
it deliberately. The copy may appear as untracked files in Git; review before
committing. If another skill with the same name is present, check which path
the agent loaded.

For Antigravity, start `agy` from the repository root. In its interactive
session run `/skills` and select `handwriting-grading` to inspect its path.
If you added it during the session, run `/skills reload` first. The CLI also
exposes discovered skills as slash commands, including
`/handwriting-grading`. See the [Antigravity skill documentation](https://antigravity.google/docs/skills)
and [discovery guide](https://antigravity.google/docs/customization-discovery).

For Gemini CLI, start `gemini` from the repository root, run `/skills list`,
and check for `handwriting-grading`. If you added it during the session, run
`/skills reload`. When Gemini asks to activate the skill, inspect the path and
approve only if it is the intended copy. See the [Gemini CLI skill documentation](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/skills.md).

The discovery locations and commands above were checked against the providers'
documentation on 2026-11-20; CLI behavior may change. The installed `agy`
help was checked locally, but an authenticated agent session was not tested.

### Check the selected agent

Ask your agent:

> Use the handwriting-grading skill. Explain how to preflight a synthetic
> exam and what requires human approval. Do not call Vertex, send email, create
> files, or delete anything.

Check that the response identifies the exam YAML and input checks, asks for
human confirmation of scan order and layout, and distinguishes preflight from
grading approval. Ask it to identify the relevant skill reference files. A
plausible response alone is not proof of actual model calls or grading.

## 2. Run offline implementation tests

In the repository root, set up the Python environment if needed:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m unittest discover -s tests -v
```

The suite uses synthetic data and fake services, not live Vertex or SMTP. A
successful run ends with `OK`; check the output for skipped tests too. Poppler
(`pdftoppm`) is needed for scan-related tests, and LibreOffice (`libreoffice`
or `soffice`) for real DOCX-to-PDF conversion tests. Browser tests additionally
need Google Chrome and the optional dependency:

```sh
.venv/bin/python -m pip install -e '.[browser-test]'
.venv/bin/python -m unittest discover -s tests -v
```

Missing optional tools cause relevant tests to be skipped, not verified. Do
not use actual student data or credentials in test fixtures.

## 3. Test the included sample

The original notebook's fake example files are in `examples/notebook-sample/`.
They include a roster, scanned PDF, answer-sheet template, marking scheme,
exam document and prior verification document. The original workbook's extra
verification sheets and the Word verification document are not new approvals.
The example configuration uses four roster entries and eight scan pages, or
two pages per student. The sample rubric has five questions, `Q1` through
`Q5`. All commands below run from the skills repository root.

To test the agent as well as the tools, first register the skill as in step 1,
start your chosen agent from this directory, and ask:

> Use the handwriting-grading skill to guide me through testing
> `examples/notebook-sample/example.yaml`. Start with preflight. Explain each
> operation and wait for my approval before creating output, calling Vertex,
> approving scores or sending email.

You can run the commands below yourself instead. Don't run an output-producing
command twice if your agent already ran it: stages refuse to overwrite outputs.

### A. Preflight and inspect

Open the sample PDF and roster, and confirm the scan order, two pages per
script and shared layout. Then run:

```sh
.venv/bin/handwriting-grader preflight --exam examples/notebook-sample/example.yaml
```

Expected: `Preflight passed: 4 students, 0 absent, 8 pages, 2 pages per
script.` Preflight makes no Vertex calls and creates no output. It does not
verify scan order, rubric correctness, credentials or model availability, and
passing it is not grading approval.

Later stages write to `examples/notebook-sample/output/`, which is ignored by
Git. They refuse to overwrite existing stage outputs. Inspect and preserve any
existing outputs before testing; see **Repeat or troubleshoot** below.

### B. Optional: generate answer sheets without AI

Install LibreOffice, then run:

```sh
.venv/bin/handwriting-grader answer-sheets --exam examples/notebook-sample/example.yaml
```

Inspect `output/answer-sheets/combined.pdf`, the individual PDFs and the
roster mapping under `examples/notebook-sample/`. Check all four identities,
the layout and page counts. This does not grade the existing scan.

### C. Import and review the sample rubric

```sh
.venv/bin/handwriting-grader import-rubric --exam examples/notebook-sample/example.yaml
```

Compare `examples/notebook-sample/output/rubric/rubric.xlsx` with the original
sample marking scheme. Check all five questions, criteria and maximum marks.
The copied workbook and original verification document are not approvals.
Only after you have reviewed and accepted the rubric, run:

```sh
.venv/bin/handwriting-grader approve-rubric --exam examples/notebook-sample/example.yaml --confirm-reviewed
```

These steps use the existing XLSX and do not call AI. Close the workbook after
review and before approving.

### D. Draw and review regions

Install Poppler (`pdftoppm`), then prepare and open the local editor:

```sh
.venv/bin/handwriting-grader prepare-regions --exam examples/notebook-sample/example.yaml
.venv/bin/handwriting-grader review-regions --exam examples/notebook-sample/example.yaml
```

Open the full URL printed by the second command; keep its token private. The
editor displays the first script's two pages. For each page:

1. Click **New box**, enter a label, and drag around the corresponding field
   or complete answer area. On page 1, draw `NAME`, `ID` and `CLASS` boxes,
   using those exact uppercase labels. These fields also appear on page 2,
   but do not draw them again: labels must be unique across both pages.
2. Draw `Q1`, `Q2` and `Q3` answer boxes on page 1 and `Q4` and `Q5` on
   page 2. Draw one box for each complete answer area.
3. Check box placement and click **Save and mark reviewed** on each page.

Confirm these regions apply to all four scripts. After reviewing both pages,
click **Approve all regions** and confirm. Stop the editor with Ctrl+C.
Preparing and manually reviewing regions makes no AI calls.

### E. Optional: live Vertex grading

This step sends sample images and answers to Vertex AI and may incur charges.
Offline tests do not establish that your selected models, project or location
work. First configure Google Application Default Credentials and access to
Vertex AI. Use a private model configuration YAML containing valid model IDs:

```yaml
vertex_ai:
  use_vertexai: true
models:
  ocr: YOUR_OCR_MODEL_ID
  grading: YOUR_GRADING_MODEL_ID
  moderation: YOUR_MODERATION_MODEL_ID
```

Replace the placeholders with models available to your project and region;
do not put credentials in the YAML or commit it. The original grader's model
configuration can also be used if it has these fields.

In the following commands, replace `/private/model-config.yaml`,
`PROJECT_ID` and `LOCATION` with your own values. Preview first:

```sh
.venv/bin/handwriting-grader grade --exam examples/notebook-sample/example.yaml \
  --model-config /private/model-config.yaml --project PROJECT_ID --location LOCATION
```

The sample's preview shows up to 57 requests: OCR, grading and moderation.
It exits without sending requests because `--confirm-cost` is absent. Check
the target, model names, data transfer and potential cost; retries may cost
more. Only after explicitly accepting them, repeat the same command with
`--confirm-cost`. A partial failure can still incur charges.

On success, `output/grading/review.xlsx` is an **unapproved draft**, not a
final result. If you do not want paid calls, stop after section D and use the
offline suite in step 2.

### F. Review scores and produce reports

After live grading succeeds, open the score editor:

```sh
.venv/bin/handwriting-grader review-grading --exam examples/notebook-sample/example.yaml
```

Open the printed private URL. For every script, verify the roster identity
against the scan and save it as reviewed. For each answer, check the OCR,
reasoning, moderation and mark against the rubric and image, correct mistakes,
and click **Save and mark reviewed**. Failed or flagged grades and corrected
answers require a manual mark. The comparison table can help find unreviewed
items; it does not approve them.

After reviewing every identity and mark, optionally run the read-only check
in a separate terminal:

```sh
.venv/bin/handwriting-grader check-grading-review --exam examples/notebook-sample/example.yaml
```

A clean check is not approval. Only if you accept every reviewed score,
click **Approve all grading** in the editor and confirm; then stop the editor
with Ctrl+C. Alternatively, after closing the editor, explicitly approve with
`approve-grading --exam examples/notebook-sample/example.yaml --confirm-reviewed`.
Create and inspect reports and offline statistics:

```sh
.venv/bin/handwriting-grader report --exam examples/notebook-sample/example.yaml
.venv/bin/handwriting-grader analytics --exam examples/notebook-sample/example.yaml
```

Check `output/reports/` and `output/analytics/` under the example directory.
The example has no pass thresholds, so pass statistics are blank. This test
does not send email or run optional AI analysis.

### Repeat or troubleshoot

An existing stage directory causes its creation command to refuse to run.
Preserve previous work rather than deleting it. For a fresh run, make a new
private copy of the sample files and `example.yaml` in an empty directory;
its relative paths and `output` directory resolve there. Use that copy's YAML
path for every command. Don't modify an exam configuration after approvals:
changes invalidate dependent approvals.

For a supported interrupted grading checkpoint, use `resume-grading --exam
PATH` to preview and obtain fresh cost approval before adding `--confirm-cost`.
An in-flight request can repeat. Other partial stages may require a new output
directory. Investigate stale locks; never remove a live lock.

For your own data, prepare a **new private directory**, copy
`exam.yaml.template` there and set its input and output paths. Do not
overwrite an existing exam or output, or put actual student data in the
tracked example files.

Live Vertex and SMTP behavior are not established by the offline suite. Use
only authorized synthetic data and a properly configured test account.
Inspect each paid request preview and obtain separate cost and data-transfer
approval before `--confirm-cost`; review and approve any email separately
before sending. Do not assume a successful test approves scores or delivery.
