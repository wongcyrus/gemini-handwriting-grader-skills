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

## 2. Layout Preparation (`prepare-regions`)

1. Reads `exam.scanned_pdf` and `layout.pages_per_student`.
2. Uses `render_pdf_to_images` (`pypdfium2`) to render the first script's $N$ pages into `output/regions/images/0.jpg`, `1.jpg`, etc.
3. Generates `output/regions/manifest.json` recording pixel dimensions `(width, height)` and SHA-256 hashes for each layout page.

---

## 3. Vertex AI Bounding Box Proposals (`suggest-regions`)

Teachers can request automated proposal boxes using Gemini Multimodal:
```bash
handwriting-grader suggest-regions --exam exam.yaml \
  --model-config config.yaml --project PROJECT_ID --location LOCATION --confirm-cost
```
- Transmits layout images to Gemini with instructions to locate student identity areas and designated question writing zones.
- Generates `output/regions/suggestions.json`.
- **Safety Rule**: Suggestions are non-destructive proposals. They never automatically overwrite manual bounding boxes.

---

## 4. Browser-Based Region Editor (`review-regions`)

A zero-dependency local web server implemented in [`src/handwriting_grader/editor.py`](../src/handwriting_grader/editor.py):
- Binds strictly to `127.0.0.1` on a free ephemeral or user-selected port (`--port`).
- Enforces access control via a cryptographically random URL fragment token (`http://127.0.0.1:PORT/#TOKEN`).
- Prevents cross-origin attacks via strict `Origin` header validation.
- Interactive UI (`editor.html`):
  - Zoom and pan scanned pages.
  - "Load AI Suggestions" button to import proposals from `suggestions.json`.
  - Draw, drag, resize, and label boxes.
  - "Approve" button saves `annotations.json` and records cryptographic `approval.json`.
