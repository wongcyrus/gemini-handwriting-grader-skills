# Self-Contained Multi-Assignment Architecture

This document explains the technical design enabling `gemini-handwriting-grader-skills` to run across multiple isolated, self-contained assignments and exam directories on any machine.

---

## 1. The Multi-Assignment Challenge

In academic institutions and school environments, teachers manage dozens of exams, assignments, quizzes, and midterm papers throughout a semester:

```text
/home/teacher/courses/
├── CS101_Assignment_1/
├── CS101_Midterm_Exam/
├── CS201_Quiz_1/
└── CS201_Final_Exam/
```

Requirements:
- Each assignment must be completely isolated from others.
- Running grading on `CS101_Assignment_1` must never pollute or conflict with `CS101_Midterm_Exam`.
- The CLI tool must not require copying data into the tool's repository or cloning separate tool repos per course.

---

## 2. Directory Anchor & Relative Path Resolution

### 2.1 The `config_dir` Anchor
All CLI entry points in [`src/handwriting_grader/cli.py`](../src/handwriting_grader/cli.py) anchor paths immediately upon parsing:

```python
path = args.exam.expanduser().resolve()
config_dir = path.parent
config = load_config(path)
```

In [`src/handwriting_grader/regions.py`](../src/handwriting_grader/regions.py):
```python
def resolve_path(base_dir: Path, target: str | Path) -> Path:
    target_path = Path(target).expanduser()
    if target_path.is_absolute():
        return target_path.resolve()
    return (base_dir / target_path).resolve()
```

### 2.2 Consequences of the Anchor
Inside each assignment's `exam.yaml`:
```yaml
exam:
  name: CS101 Assignment 1
  roster: roster.xlsx
  roster_sheet: Students
  scanned_pdf: submissions.pdf
  marking_scheme: criteria.docx
  output_dir: output

layout:
  pages_per_student: 2
  same_layout_for_all: true
```

- When the CLI runs `handwriting-grader preflight --exam /courses/CS101_Assignment_1/exam.yaml`:
  - `roster` resolves strictly to `/courses/CS101_Assignment_1/roster.xlsx`.
  - `scanned_pdf` resolves strictly to `/courses/CS101_Assignment_1/submissions.pdf`.
  - `output_dir` resolves strictly to `/courses/CS101_Assignment_1/output/`.
  - **The current working directory (`cwd`) is completely decoupled from asset resolution.**

---

## 3. Directory Layout Within Each Assignment

Each self-contained folder manages its own complete lifecycle:

```text
CS101_Assignment_1/
├── exam.yaml                           # Exam configuration
├── roster.xlsx                         # Student enrollment & metadata
├── submissions.pdf                     # Scanned PDF submissions
├── criteria.docx                       # Marking criteria scheme
└── output/                             # Generated output directory
    ├── regions/
    │   ├── images/                     # Page raster images (pypdfium2)
    │   ├── annotations.json            # Approved bounding boxes
    │   └── approval.json               # Region approval cryptographic hash
    ├── rubric/
    │   ├── rubric.xlsx                 # Structured question-mark scheme
    │   └── approval.json               # Rubric approval hash
    ├── grading/
    │   ├── images/                     # Student cropped pages
    │   ├── results.json                # Resumable checkpoint ledger
    │   ├── review.xlsx                 # Human score review workbook
    │   ├── approval.json               # Grade approval hash
    │   └── telemetry/                  # Token & cost audit log
    │       └── gemini_usage.jsonl      # CallTrace event stream
    ├── reports/                        # Student grade slips & class score sheets
    ├── analytics/                      # Offline box plots & score stats
    ├── cost_report.xlsx                # Per-assignment financial audit
    └── cost_summary.json               # Machine-readable cost summary
```

---

## 4. Multi-Assignment Operations Cheat Sheet

From any shell or terminal on your machine:

```bash
# 1. Preflight assignment 1
handwriting-grader preflight --exam /courses/CS101/exam.yaml

# 2. Preflight assignment 2
handwriting-grader preflight --exam /courses/CS201/exam.yaml

# 3. Check cost for assignment 1 only
handwriting-grader cost-report --exam /courses/CS101/exam.yaml

# 4. Clean & archive assignment 1 without touching assignment 2
handwriting-grader clean --exam /courses/CS101/exam.yaml --confirm-delete
```
