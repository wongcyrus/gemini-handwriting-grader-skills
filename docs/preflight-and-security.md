# Preflight, Input Validation & Security Model

This document specifies the preflight verification system, input validation rules, and security model of `gemini-handwriting-grader-skills`.

---

## 1. Security & Privacy Philosophy

The grading pipeline operates on student educational records (scanned handwriting, student IDs, academic rosters, and teacher evaluations). The security architecture enforces strict local containment:

1. **Zero Unintended Network Calls**: Preflight, offline attendance, offline analytics, report generation, and region editing make zero external API or network calls.
2. **Path Containment & Jail Separation**: Input directories (roster, scan PDF, marking scheme) must be strictly separated from output directories to prevent recursive overwrites or accidental data loss:
   ```python
   if output and any(p == output or output in p.parents for key, p in paths.items()):
       errors.append("output_dir contains an input file; choose a separate output directory")
   ```
3. **Explicit Cost & Privacy Gates**: Any operation that transmits student handwriting or answers to Google Vertex AI (`suggest-regions`, `grade`, `regrade`, `ai-analysis`, `verify-rubric`) requires explicit human consent flags (`--confirm-cost`) and warns of financial and data transmission implications.
4. **Email Dispatch Double-Gate**: Email preview generation is read-only. Transmitting emails requires two distinct human gates:
   - `approve-email --confirm-reviewed` (validates exact recipient list and attachments).
   - `send-email --confirm-send` (authorizes actual SMTP dispatch).

---

## 2. Preflight Validation (`preflight`)

Invoked via `handwriting-grader preflight --exam exam.yaml`, this stage validates structural integrity without creating any files:

### 2.1 Exam Configuration Schema
`exam.yaml` must contain `exam` and `layout` mappings:
- `exam.name`: Non-empty exam title.
- `exam.roster`: Path to Excel workbook (`.xlsx`).
- `exam.roster_sheet`: Name of the sheet containing student data.
- `exam.scanned_pdf`: Path to multi-page scanned PDF.
- `exam.marking_scheme`: Path to DOCX or XLSX rubric.
- `exam.output_dir`: Designated output folder.
- `layout.pages_per_student`: Positive integer $N \ge 1$.
- `layout.same_layout_for_all`: Must be confirmed `true` (heterogeneous layouts across students are rejected).

### 2.2 Roster Integrity Checks
The Excel roster must contain:
- Exactly one column matching `ID`, `NAME`, and `CLASS` (case-insensitive).
- No rows with missing `ID`, `NAME`, or `CLASS`.
- Zero duplicate student IDs (fails fast if duplicates exist).
- Formatted leading zeroes: Excel string vs integer preservation.

### 2.3 Scanned PDF Geometry & Page Mathematics
The total page count $P$ in `scanned_pdf` is read via `pypdf.PdfReader`:
- Must satisfy: $P = S \times N$, where $S$ is the present student count and $N = \text{pages\_per\_student}$.
- If $P \pmod N \ne 0$, preflight fails immediately with a page-count mismatch error.
