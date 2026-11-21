# Three-Stage Grading & Consistency Moderation Engine

This document details the multimodal OCR, per-script question grading, and batch consistency moderation pipeline in `gemini-handwriting-grader-skills`.

---

## 1. Pipeline Overview

Grading runs in three strictly decoupled, resilient phases:

```
[Scanned Script Pages]
         |
         v
+-----------------------------------------------------------+
| Stage 1: Student Metadata OCR                             |
| Extract handwritten ID, CLASS, NAME from Page 0           |
+-----------------------------------------------------------+
         |
         v
+-----------------------------------------------------------+
| Stage 2: Question Grading (Per-Question, Per-Script)      |
| Extract student answer, drawings/diagrams, score & reason |
+-----------------------------------------------------------+
         |
         v
+-----------------------------------------------------------+
| Stage 3: Consistency Moderation (Per-Question Batch)      |
| Evaluate all student answers together to eliminate bias   |
+-----------------------------------------------------------+
         |
         v
[Results Checkpoint (results.json) & Review Workbook (review.xlsx)]
```

---

## 2. Stage 1: Student Metadata OCR

- **Input**: High-resolution crop of `ID`, `CLASS`, and `NAME` bounding boxes from each student's page 0.
- **Image Preprocessing**:
  ```python
  crop = ImageEnhance.Sharpness(crop).enhance(3)
  ```
- **Prompt**: Strict extraction directive:
  > *"Extract only the handwritten {field} from this image. Preserve text and line breaks. Return an empty response if blank; do not guess."*
- **Model**: Fast flash model (`gemini-3.8-flash`), `temperature=0`, `thinking_budget=0` (disables unnecessary thinking tokens on single-field extraction).

---

## 3. Stage 2: Multimodal Question Grading

- **Input**:
  - Image crop of the student's answer box (supports handwriting, equations, flowcharts, ER diagrams, drawings).
  - Question text, maximum marks, and approved marking scheme criteria.
- **Structured Response Schema (`GradeResponse`)**:
  ```python
  class GradeResponse(BaseModel):
      answer: StrictStr          # Verbatim transcription or diagram description
      mark: float                # Points awarded (0 <= mark <= max_marks)
      similarity_score: float    # Semantic similarity to marking criteria (0.0 to 1.0)
      reasoning: StrictStr       # Justification referencing rubric criteria
  ```
- **Local Blank-Cell Pre-Filter (`local_filter.py`)**: Before sending a crop to Vertex AI, `is_blank_cell()` performs a cross-platform Pillow inset ink-density check (`Windows` / `macOS` / `Linux`, no GPU/OpenCV needed). Clearly blank white boxes are scored `0` locally (`"Blank answer (local filter)."`), while any cell with ink, printed labels, or an unsupported/error condition falls back seamlessly to `provider.grade()`.
- **Throughput & Model Tier**: Uses `gemini-3.8-flash` (`temperature=0`, `thinking_budget=0`) with bounded concurrency (`ThreadPoolExecutor(max_workers=10)`) across scripts per question, checkpointing atomically to `results.json` on the main thread with thread-safe telemetry locking.
- **Prompt Guard**: Student handwriting is explicitly framed as data, not instructions, to defend against prompt injection in student submissions.

---

## 4. Stage 3: Batch Consistency Moderation

Single-pass LLM grading can exhibit drift or variance across different scripts. To ensure fairness, moderation evaluates all student answers for a question simultaneously using the Frontier Reasoning tier (`gemini-4-argon`, full reasoning enabled):

- **Input**: Array of all student answers, raw marks, and reasoning for question $Q$.
- **Structured Response Schema (`ModerationResponse`)**:
  ```python
  class ModerationItem(BaseModel):
      script_index: StrictInt
      moderated_mark: float
      flag: StrictBool           # True if mark was adjusted or concerns noted
      note: StrictStr            # Explanation of consistency adjustment
  ```
- **Moderator Actions**:
  - Detects harshness/leniency inconsistencies across identical or similar answers.
  - Flags borderline or ambiguous cases for human teacher inspection.
  - Populates `moderator_flag` and `moderator_note` in the review sheet.

---

## 5. Resumable Checkpoint Engine (`results.json`)

To prevent loss of work or duplicate billing if a network error occurs:
- `output/grading/results.json` records every completed script and question atomically via `atomic_json()`.
- `resume-grading --exam exam.yaml --confirm-cost` skips already graded and moderated questions, continuing from the exact failure point.
- Protected by `processing.lock` to prevent concurrent execution races.

---

## 6. Built-in SDK Retry Primitives & Targeted `retry-grading` Recovery

### Built-in GenAI SDK / ADK Retry Architecture
Connection-level transient errors (429 RateLimit/ResourceExhausted, 500 Internal, 502 Bad Gateway, 503 ServiceUnavailable, 504 DeadlineExceeded) are handled automatically by the Google GenAI SDK client via `types.HttpOptions`:
```python
http_options=types.HttpOptions(
    timeout=300_000,
    retry_options=types.HttpRetryOptions(
        attempts=3,
        initial_delay=2.0,
        max_delay=30.0,
        exp_base=2.0,
        jitter=True,
        http_status_codes=[429, 500, 502, 503, 504],
    ),
)
```
No custom `while True` or `time.sleep()` loops are used around API calls.

### Human-in-the-Loop Governance: User Decision Gating
When SDK-level retries are exhausted and items fail (`grading_failed=True` or moderation errors):
- **Prohibition on Blind Python Loops**: Auto-retrying in Python loops is strictly forbidden because GCP quotas or service outages make blind loops futile, risk exhausting daily quotas, and run up unapproved billing.
- **Diagnostic Notification**: The agent must immediately report failure counts, question numbers, and error status codes to the user.
- **User Decision Required**: The user decides whether to:
  1. Wait for GCP quota reset, preview `retry-grading`, and run with explicit `--confirm-cost`.
  2. Manually grade failed items directly in `review.xlsx` without additional API calls.

### Targeted `retry-grading` Command
When the user approves retry:
```sh
handwriting-grader retry-grading --exam exam.yaml [--confirm-cost]
```
1. **Isolated Execution**: Identifies only items with `grading_failed=True` or `raw_mark is None`.
2. **Local Pre-Filter**: Runs `is_blank_cell` before any API call.
3. **Question Re-moderation**: Re-moderates affected questions once all student marks are restored.
4. **Preserved Manual Reviews**: Refreshes `review.xlsx` while merging and preserving any previously entered manual marks and reviewed flags.
