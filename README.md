# Handwriting Grader Skills

A skill and Python tools for planning a human-reviewed handwriting grading run.
This project is being migrated from
[gemini-handwriting-grader](https://github.com/wongcyrus/gemini-handwriting-grader).

**Current scope:** offline preflight, manual region review and optional Vertex
AI region suggestions are available. Grading, report generation, email and
downstream enforcement of region approvals are not implemented. Do not use
this repository to grade or send student results.

## Setup

Python 3.10+ and a virtual environment are required:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
cp exam.yaml.template exam.yaml
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
A passing result is not approval to grade.

## Review regions

Install Poppler (`pdftoppm` must be on `PATH`). After confirming scan order,
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
page prevents continuing the run. These local records are not tamper-proof
and no grading command consumes them yet.

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

The skill is in `skills/handwriting-grading/SKILL.md`; install that directory
using your agent's skill installation conventions.

## Tests

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Tests generate synthetic data in temporary directories.
To also test the browser UI with an installed Google Chrome:

```sh
.venv/bin/python -m pip install -e '.[browser-test]'
.venv/bin/python -m unittest discover -s tests -v
```
