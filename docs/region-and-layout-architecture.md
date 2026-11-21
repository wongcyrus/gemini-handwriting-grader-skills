# Region Definition, Layout Review & Suggestion Engine

This document describes the page layout coordinate system, AI bounding-box suggestion engine, browser-based editor, and region approval architecture in `gemini-handwriting-grader-skills`.

---

## 1. Coordinate System & Geometry

Exam answer sheets use physical pixel bounding boxes anchored to rendered 200 DPI page images:
- Origin `(0, 0)` is at the top-left of the page image.
- Bounding Box Schema:
  ```json
  {
    "page": 0,
    "x": 120,
    "y": 340,
    "width": 1410,
    "height": 480
  }
  ```
- Mandatory Identification Boxes (Page 0):
  - `ID`: Student ID handwriting zone.
  - `CLASS`: Student Class handwriting zone.
  - `NAME`: (Optional) Student Name handwriting zone.
- Question Boxes:
  - Labeled matching the approved rubric (e.g. `Q1`, `Q2`, `Q3`, ...).
  - Every question in the approved rubric must have exactly one bounding box across the page set.

---

## 2. Scan Validation (`check-scans`) & Layout Preparation (`prepare-regions`)

1. **Before `prepare-regions`**, run `handwriting-grader check-scans --exam exam.yaml` (offline, 0 AI cost). Using majority-consensus 4x downsampled FFT phase correlation across scripts, it detects upside-down (`180°`), sideways (`90°/270°`), blank, or out-of-sequence pages — even if Script 1 itself is wrong — and reports exact 1-based PDF page numbers for manual correction.
2. `prepare-regions` reads `exam.scanned_pdf` and `layout.pages_per_student`.
3. Uses `render_pdf_to_images` (`pypdfium2`) to render the first script's $N$ pages into `output/regions/images/0.jpg`, `1.jpg`, etc.
4. Generates `output/regions/manifest.json` recording pixel dimensions `(width, height)` and SHA-256 hashes for each layout page.
5. Regions can be approved interactively in `review-regions` or headlessly after editing `annotations.json` via `handwriting-grader approve-regions --exam exam.yaml --confirm-reviewed`.

---

## 3. Vertex AI Bounding Box Proposals (`suggest-regions`)

Teachers can request automated proposal boxes using Gemini 4 (`models.annotation`):
```bash
handwriting-grader suggest-regions --exam exam.yaml \
  --model-config config.yaml --project PROJECT_ID --location LOCATION --confirm-cost
```
- **Approved-Rubric-First Requirement**: `suggest-regions` requires an extracted/imported and approved `output/rubric/rubric.xlsx` (`approve-rubric --confirm-reviewed`) and refuses to run if no question ID list exists.
- **Dynamic Prompt Injection**: `load_rubric_questions()` reads every `question_number` and a short text summary from `rubric.xlsx`, and `build_prompt()` injects them dynamically into the prompt (with zero hardcoded label examples). `question_ids` is included in the cache key so rubric changes invalidate stale suggestions.
- **Model Tier**: Uses the reasoning tier (`gemini-4-argon`) for high spatial precision across all sub-question cells and metadata fields (`NAME`, `ID`, `CLASS`).
- Generates `output/regions/ai_suggestions.json`.
- **Safety Rule**: Suggestions are non-destructive proposals. They never automatically overwrite manual bounding boxes.

---

## 4. Browser-Based Region Editor (`review-regions`)

A zero-dependency local web server implemented in [`src/handwriting_grader/editor.py`](../src/handwriting_grader/editor.py):
- Binds strictly to `127.0.0.1` on a free ephemeral or user-selected port (`--port`).
- Enforces access control via a cryptographically random URL fragment token (`http://127.0.0.1:PORT/#TOKEN`).
- Prevents cross-origin attacks via strict `Origin` header validation.
- `/api/state` returns `expected_questions` from `rubric.xlsx` alongside page dimensions and draft annotations.
- Interactive UI (`editor.html`):
  - Zoom and pan scanned pages.
  - Auto-loads proposals from `ai_suggestions.json` (or via "Load AI suggestions").
  - **Live Missing-Box Warning**: Normalizes labels across all pages with `questionKey()` and displays an alert banner whenever `ID` on Page 1 or any expected rubric question is missing a bounding box.
  - Draw, drag, resize, and label boxes.
  - "Approve all regions" saves `annotations.json` and records cryptographic `approval.json`.
