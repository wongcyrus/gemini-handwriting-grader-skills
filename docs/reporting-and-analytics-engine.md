# Reporting, Offline Analytics & AI Feedback Engine

This document describes the student grade slip generation, class scorebook compilation, offline statistical analytics, and multimodal AI analysis pipeline in `gemini-handwriting-grader-skills`.

---

## 1. Grade Reports & Annotated PDFs (`report`)

Invoked via `handwriting-grader report --exam exam.yaml [--run CHILD_RUN]`:

### 1.1 Excel Scorebooks
- `output/reports/summary.xlsx`: Master class grade sheet mapping each student to raw marks, total marks, percentage, and assigned PDF report index.
- `output/reports/details.xlsx`: Granular sheet containing `Answers` and `Reasoning` from human-reviewed marks, plus `AnswersRaw` and `ReasoningRaw` preserving model initial output for audit comparison.

### 1.2 Individual Scored PDFs
- Generates `output/reports/student_0001.pdf`, `student_0002.pdf`, ...
- Each student's original scanned script is overlayed with teacher-approved question marks, feedback annotations, and overall exam score breakdown.
- `output/reports/combined.pdf`: Concatenates all individual grade slips in roster order for print batching.
- `output/reports/samples/`: Stratified score samples (Top, Median, Borderline, Needs Support) for curriculum review and moderation audits.

---

## 2. Offline Statistical Analytics (`analytics`)

A pure-Python offline computation engine (`analytics.py`) that makes zero network or model calls:

### 2.1 Statistical Calculations (`statistics.xlsx`)
- Mean, Median, Standard Deviation, Interquartile Range (IQR), Minimum, and Maximum scores.
- Per-question facility index (difficulty level) and discrimination index.
- Pass rates and counts based on explicit `reporting.pass_mark` policy.

### 2.2 Question Box Plots (`question-boxplots-*.png`)
- Generates publication-ready box plots using Matplotlib.
- Displays 1.5-IQR whiskers, median lines, and highlighted outlier points.
- Groups up to 20 questions per image for clear legibility.

---

## 3. AI Performance Analysis (`ai-analysis` & `approve-analysis`)

Generates pedagogical feedback using Gemini Pro (`advanced.py`):
1. **Student Strengths & Weaknesses**:
   - Analyzes each student's question-by-question performance and drafts targeted next steps.
2. **Question Misconception Diagnosis**:
   - Compares high-scoring and low-scoring answer samples per question to identify common student misconceptions.
3. **Class-Wide Curriculum Review**:
   - Synthesizes class-level topics requiring re-teaching.
4. **Human Review Gate (`ai-analysis/review.xlsx`)**:
   - Every student feedback item and question analysis must be reviewed by the teacher and marked `reviewed = yes`.
   - Approved via `approve-analysis --confirm-reviewed`.

---

## 4. Infographics & Advanced Office Reports (`infographics`, `advanced-report`)

1. **Infographics**:
   - Uses `gemini-3.8-flash` to render visual summary infographics for class overview and individual question takeaways.
   - Built-in rate-limit backoff and retry handling (`advanced.py`).
2. **Advanced Reports**:
   - Generates executive Word document (`teacher-report.docx`) with embedded tables and analysis.
   - Generates PowerPoint presentation (`class-presentation.pptx`) with native editable bar charts and slide-by-slide question breakdowns.
