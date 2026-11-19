# Handwriting Grader Skills

A skill and Python tools for planning a human-reviewed handwriting grading run.
This project is being migrated from
[gemini-handwriting-grader](https://github.com/wongcyrus/gemini-handwriting-grader).

**Current scope:** the skill and offline exam preflight are available. The
annotation editor, AI grading, approval enforcement, reports, and email
workflow are not implemented here yet. Do not use this repository to grade or
send student results.

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

The skill is in `skills/handwriting-grading/SKILL.md`; install that directory
using your agent's skill installation conventions.

## Tests

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Tests generate synthetic data in temporary directories.
