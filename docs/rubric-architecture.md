# Rubric Extraction, Verification & Approval Architecture

This document describes the schema, AI extraction, search-grounded verification, and cryptographic approval architecture for exam marking schemes in `gemini-handwriting-grader-skills`.

---

## 1. Rubric Architecture & Excel Schema

A valid marking scheme is structured into an Excel workbook (`output/rubric/rubric.xlsx`) on sheet `Marking Scheme` with the following mandatory columns:

| Column Header | Type | Description |
| :--- | :--- | :--- |
| `question_number` | String/Int | Question key (e.g. `Q1`, `1a`, `Question 2`) |
| `question_text` | String | Full question text provided to students |
| `marking_scheme` | String | Detailed criteria, model answer, mark allocation rules |
| `marks` | Float/Int | Maximum marks allotted (must be finite, $> 0$) |

Validation rules (`rubric.py`):
- No empty question numbers or zero maximum marks.
- No formula cells (values must be evaluated text or numbers).
- Question numbers must resolve to unique keys via `question_key()`.

---

## 2. DOCX Extraction Pipeline (`extract-rubric`)

When a teacher supplies a Word document (`exam.marking_scheme: scheme.docx`), `extract-rubric` extracts questions and marks into structured Excel using Gemini:

1. **Extraction Pipeline**:
   - `mammoth` extracts raw text and tables from DOCX.
   - Sends criteria to Vertex AI using structured Pydantic response schema:
     ```python
     class RubricItem(BaseModel):
         question_number: StrictStr
         question_text: StrictStr
         marking_scheme: StrictStr
         marks: float
     ```
   - Writes `output/rubric/rubric.xlsx`.
2. **Safety Gate**:
   - Preview shows prompt shape and target model.
   - Requires explicit `--confirm-cost`.

---

## 3. Search-Grounded Verification (`verify-rubric`)

An optional verification stage using Google Search grounding (`verification.py`):
- Connects to Google Vertex AI with `tools=[types.Tool(google_search=types.GoogleSearch())]`.
- Cross-references model answers, formulas, dates, and domain claims against live web sources.
- Produces:
  - `output/rubric/verification/verification.xlsx`
  - `output/rubric/verification/verification.docx`
- **Important**: Verification output is strictly informational for the teacher; it does **not** constitute rubric approval.

---

## 4. Cryptographic Binding & Approval (`approve-rubric`)

Before grading can commence, a human teacher must explicitly review `rubric.xlsx` and run:
```bash
handwriting-grader approve-rubric --exam exam.yaml --confirm-reviewed
```

This generates `output/rubric/approval.json`:
```json
{
  "version": 1,
  "rubric_sha256": "3a7bd2...",
  "approved_at": "2026-11-20T10:20:00Z"
}
```

**Anti-Tampering Rule**: During grading, the SHA-256 hash of `output/rubric/rubric.xlsx` is recomputed. If any mark, criteria, or question text was modified without re-approval, the grading pipeline immediately halts.
