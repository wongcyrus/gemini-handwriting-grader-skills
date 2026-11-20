# Cleanup, Archival & Retention Design

This document details the lifecycle, safety gates, and cross-platform archival mechanisms implemented in `gemini-handwriting-grader-skills`.

---

## 1. Safety Principles for Student Data

Grading systems manage sensitive student data: handwritten scans, national/student IDs, academic marks, and teacher reasoning notes. Automatic or silent deletion poses significant legal and operational risks.

Core safety tenets:
1. **Never Silently Overwrite**: Stages check for existing outputs and refuse to overwrite without explicit, separate human confirmation.
2. **Never Silently Delete**: Intermediate data is never deleted automatically at the end of a run.
3. **Audit Preservation by Default**: The review history (`grading/review-history/`), final student report PDFs (`reports/`), and statistics (`analytics/`) are preserved even when intermediate scratch data is cleaned.
4. **Backup Before Purge**: An archive is created immediately before any destructive action takes place.

---

## 2. The `clean` Command Lifecycle

Execution flows through three mandatory gates:

```
[User invokes clean command]
           |
           v
[Gate 1: Target Inspection & Dry-Run Preview]
   - Shows output_dir path & run ID
   - Lists subdirectories & files
   - Prints total byte size
   - Exits with status 1 if --confirm-delete is missing
           |
           v (User approves with --confirm-delete)
[Gate 2: Automatic Cross-Platform .zip Archival]
   - Bundles output directory into .zip
   - Names: grading_archive_<run_id>_<timestamp>.zip
   - Stored in parent directory
           |
           v
[Gate 3: Selective Purge / Report Preservation]
   - Preserves reports/ and analytics/ (unless --purge-reports passed)
   - Removes intermediate OCR crops, checkpoints, and caches
   - Logs deleted item names to stdout
```

---

## 3. Command Usage & Options

### 3.1 Standard Safe Cleanup
Inspect and archive:
```bash
# 1. Preview targets and size (no files deleted)
handwriting-grader clean --exam /path/to/exam.yaml

# 2. Archive to .zip and clean intermediate scratch (keeps reports/ & analytics/)
handwriting-grader clean --exam /path/to/exam.yaml --confirm-delete
```

Output:
```text
Target directory: /courses/CS101/output (run: original)
Target contents:
  - grading (directory, 4,215,820 bytes, 18 files)
  - regions (directory, 1,120,400 bytes, 6 files)
  - reports (directory, 2,850,110 bytes, 12 files)
  - rubric (directory, 18,400 bytes, 2 files)
Total size: 8,204,730 bytes

Backup archive created: /courses/CS101/grading_archive_original_20261121_000500.zip
Cleaned items: grading, regions, rubric
Final reports/ and analytics/ directories were preserved.
```

### 3.2 Cleaning an Isolated Child Regrade Run
```bash
handwriting-grader clean --exam /path/to/exam.yaml --run revision-1 --confirm-delete
```
This removes only `/courses/CS101/output/runs/revision-1/` while leaving the approved original parent run completely untouched.

### 3.3 Full Purge (Including Reports)
```bash
handwriting-grader clean --exam /path/to/exam.yaml --purge-reports --confirm-delete
```
Removes all contents of `output/` after creating the backup `.zip` archive.
