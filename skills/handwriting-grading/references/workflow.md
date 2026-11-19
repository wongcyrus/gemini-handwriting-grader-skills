# Planned workflow and human gates

Only preflight exists in this repository. The following is the migration
contract, not a list of currently available commands.

1. **Preflight:** verify inputs and configuration, then obtain confirmation of
   scan order, page layout, output location and intended Vertex target.
2. **Rubric:** extract the scheme and have a teacher approve questions,
   answers and maximum marks.
3. **Regions:** suggest boxes, then let the teacher draw, move, resize, delete
   and label them in a local browser. Save original-image pixel coordinates in
   the existing `annotations.json` format. Approve all page layouts.
4. **Grading:** request approval for paid calls before OCR, grading and
   moderation. A teacher reviews IDs, OCR, exceptions, moderation flags and
   mark overrides.
5. **Reports:** reconcile the complete roster and marks, validate ranges and
   have the teacher approve final scores and feedback.
6. **Distribution:** preview exact recipients, messages and attachments;
   require separate approval immediately before sending.

Keep human approval records separate from saved edits. Bind each decision to
the relevant artifact hashes; changed inputs invalidate dependent approvals.
Require verified backups before separately approved cleanup. Do not treat
skill instructions as a security boundary: executable stages must enforce
their own gates.

Migration must account for shared page layouts, duplicate annotation labels,
ID formatting, output path differences, stale caches and the original
reporting cleanup order. Do not send email or make live calls to test this
plan without explicit authorization.
