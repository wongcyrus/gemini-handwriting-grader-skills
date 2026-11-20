# Testing the handwriting-grading skill

The skill is a set of instructions for an agent; the Python package implements
the grading tools. Test both separately. This guide is for users and does not
change the instructions loaded by agents.

## 1. Register and check the skill

The commands in this section were checked against GitHub Copilot CLI 1.0.113.
From a terminal, replace the path with your checkout:

```sh
cd /path/to/gemini-handwriting-grader-skills
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

Ask Copilot:

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

## 3. Try a synthetic exam

For a manual workflow test, prepare a **new private directory** with a
synthetic roster, scan and rubric. Copy `exam.yaml.template` there and set
its input and output paths; do not overwrite an existing exam or output.
Run the read-only preflight:

```sh
.venv/bin/handwriting-grader preflight --exam /path/to/private/exam.yaml
```

Preflight makes no Vertex calls and does not create output. It does not verify
scan order, rubric correctness, credentials or model availability, and passing
it is not grading approval. Follow the [README](../README.md) for subsequent
rubric, region and review steps.

Live Vertex and SMTP behavior are not established by the offline suite. Use
only authorized synthetic data and a properly configured test account.
Inspect each paid request preview and obtain separate cost and data-transfer
approval before `--confirm-cost`; review and approve any email separately
before sending. Do not assume a successful test approves scores or delivery.
