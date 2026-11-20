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
   moderation. A teacher reviews IDs, OCR, exceptions, moderation flags and
   mark overrides.
6. **Reports:** reconcile the complete present roster and marks, validate ranges and
   have the teacher approve final scores and feedback.
7. **Distribution:** preview exact recipients, messages and attachments;
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
