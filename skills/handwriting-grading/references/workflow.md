# Workflow and human gates

Answer sheets, preflight, rubric extraction and optional search-grounded
review, region review, grading, reports, AI analysis and gated distribution
are implemented. Formatting and cache coverage differ from the notebooks.

1. **Answer sheets:** optionally generate from a roster and DOCX before scans;
   ask the teacher to verify identities and pagination before printing.
2. **Preflight and attendance:** verify inputs and configuration, then obtain
   confirmation of scan order, page layout, output location and intended
   Vertex target. When attendance is configured, review and approve the
   complete list separately; see `attendance.md`.
3. **Rubric:** extract the scheme and have a teacher approve questions,
   answers and maximum marks.
4. **Regions:** optionally request Vertex suggestions after explicit cost
   approval. Draw, move, resize, delete and label boxes in a local browser.
   Save original-image pixel coordinates in `annotations.json` and approve all
   layouts.
5. **Grading:** request approval for paid calls before OCR, grading and
   moderation. Select between `--mode batch` (recommended default: evaluates
   all student responses for each question simultaneously using multimodal
   structured output with Argon and local Pillow blank filtering) and
   `--mode item` (legacy sequential per-student per-question evaluation with Flash).
   Built-in SDK retry options handle transient connection errors.
   If any items still fail or moderation encounters errors, notify the user
   with diagnostics to decide whether to retry via `retry-grading` (after
   preview and cost consent) or proceed to manual review.
6. **Semantic Clustering (Optional, After OCR/Grading, Before Review):** optionally
   pre-compute semantic answer clusters using Google's frontier `gemini-embedding-2` model:
   `handwriting-grader cluster-answers --exam /path/to/exam.yaml`. Deduplicates
   identical answers across all questions and across all assignments on the machine
   via a shared embedding cache (`~/.cache/gemini-handwriting-grader/embeddings/<model>.json`),
   ensuring any unique answer text is embedded at most once.
   Groups conceptually equivalent student answers for fast, consistent blind moderation.
   If skipped or disabled, `review-grading` seamlessly falls back to local word-overlap
   clustering with zero API calls.
7. **Score Review:** A teacher reviews IDs, OCR, exceptions, moderation flags and
   mark overrides in `review-grading`.
8. **Reports:** reconcile the complete present roster and marks, validate ranges and
   have the teacher approve final scores and feedback. After reporting, prompt
   the user if they want to archive or clean intermediate grading runs with `clean`.
9. **Distribution:** preview exact recipients, messages and attachments;
   require separate approval immediately before sending.

Keep human approval records separate from saved edits. Bind each decision to
the relevant artifact hashes; changed inputs invalidate dependent approvals.
Require verified backups before separately approved cleanup. Do not treat
skill instructions as a security boundary: executable stages must enforce
their own gates.

Migration must account for shared page layouts, duplicate annotation labels,
ID formatting, output path differences, stale caches and the original
reporting cleanup order. Do not send email or make live calls without
explicit authorization.
