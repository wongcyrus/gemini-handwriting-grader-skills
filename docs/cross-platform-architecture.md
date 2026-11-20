# Cross-Platform Architecture & Technical Design

This document details the technical design and runtime architecture of the `gemini-handwriting-grader-skills` package across operating systems (Linux, Windows, macOS) and developer/AI agent environments (Antigravity CLI, GitHub Copilot, Amazon Q / AWS Quick, Cursor, Claude Code).

## 1. Design Goals

1. **Self-Contained Portability**: Every exam or assignment directory must be 100% self-contained and operable without needing to run inside the repository root.
2. **Zero System Dependencies for PDF Rasterization**: PDF page rendering must run purely in Python without requiring OS-level package managers (`apt-get install poppler-utils`, `brew install poppler`, or `winget install poppler`).
3. **Sub-Pixel Dimensional Determinism**: Page rasterization dimensions `(width, height)` must match exactly across all platforms and align with teacher-approved bounding-box pixel layouts down to the pixel.
4. **Standard Cross-Platform Archival**: Backups must use standard ZIP compression compatible natively with Windows File Explorer, macOS Finder, and Linux without third-party tools.

---

## 2. Component Architecture

```
+-------------------------------------------------------------------------------+
|                                AI Agent Layer                                 |
|      (Antigravity / GitHub Copilot / Amazon Q / Cursor / Claude Code)         |
+-------------------------------------------------------------------------------+
                                      |
                                      v
+-------------------------------------------------------------------------------+
|                            CLI Layer (cli.py)                                 |
|           Resolves --exam /path/to/exam.yaml to config_dir Anchor             |
+-------------------------------------------------------------------------------+
       |                         |                           |
       v                         v                           v
+--------------------+   +-------------------+   +----------------------------+
|    PDF Engine      |   |  ADK Telemetry    |   |      Cleanup Engine        |
|  (pdf_renderer.py) |   |  (telemetry.py)   |   |        (cleanup.py)        |
+--------------------+   +-------------------+   +----------------------------+
       |                         |                           |
       +--> pypdfium2 (Primary)  +--> CallTrace journal      +--> Cross-platform .zip
       +--> pdftoppm (Fallback)  +--> cost_report.xlsx       +--> Report preservation
                                 +--> cost_summary.json      +--> Explicit safety gate
```

---

## 3. Pure-Python PDF Rendering (`pdf_renderer.py`)

### 3.1 Why `pypdfium2`?
Historically, PDF-to-image conversion required calling Poppler's external `pdftoppm` binary via `subprocess.run()`. This introduced major friction on Windows and Mac developer setups where Poppler is not installed by default.

`pypdfium2` packages Google's production C++ PDFium rendering engine inside pre-compiled binary wheels for:
- Linux (`x86_64`, `aarch64`)
- Windows (`x86_64`, `arm64`)
- macOS (`x86_64`, Apple Silicon `arm64`)

### 3.2 Dimension Parity Verification
The pipeline enforces strict dimension matching:
```python
if image.size != (layout["width"], layout["height"]):
    raise ValueError("A scanned page has different dimensions from its approved layout")
```
At 200 DPI (`scale = 200 / 72.0`), `pypdfium2` and `pdftoppm` generate identical output:
- **`VTC Test.pdf` Page 1**: `(1654, 2338)` pixels, RGB JPEG.
- **`VTC Test.pdf` Page 2**: `(1654, 2338)` pixels, RGB JPEG.

### 3.3 Transparent Fallback Engine
[`src/handwriting_grader/pdf_renderer.py`](../src/handwriting_grader/pdf_renderer.py) implements dual-mode execution:
1. If `pypdfium2` is installed, it is used directly in-process with memory buffers.
2. If `pypdfium2` is absent, it automatically delegates to `pdftoppm` via system `PATH`.
3. If neither is available, it provides a clear user-facing error message instructing `pip install pypdfium2`.

---

## 4. Cross-Platform Archival (`cleanup.py`)

### 4.1 Native ZIP Archiving
Instead of POSIX-specific `.tar.gz`, [`src/handwriting_grader/cleanup.py`](../src/handwriting_grader/cleanup.py) uses Python's standard `zipfile.ZipFile` with `ZIP_DEFLATED`:
- Produces: `output_root.parent / f"grading_archive_{run_id}_{timestamp}.zip"`
- Can be inspected and extracted natively on Windows, macOS, and Linux without command-line tools.

### 4.2 Gated Safety Principles
1. **Preview First**: Running `handwriting-grader clean --exam exam.yaml` previews target directories, file counts, and total byte sizes without deleting anything.
2. **Explicit Confirmation**: Files are only deleted when `--confirm-delete` is passed.
3. **Report Preservation**: Top-level `reports/` and `analytics/` directories are preserved by default unless `--purge-reports` is explicitly requested.
