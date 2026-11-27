# Workflow and human gates

Inspect existing state first. Reuse VERIFIED settings and clear stage choices;
ask only unresolved questions needed now. Configuration reuse is not paid consent.
The engine uses `google-genai`; Google ADK is optional external integration only.

1. **Answer sheets:** optional offline generation before scans. Confirm identities
   and pagination before printing; reuse the chosen roster/template/output.
2. **Preflight/attendance:** offline input validation before regions, without a
   heuristic orientation/sequence check or automatic image alignment. Review all
   extracted identities against the roster and crops during score review; never
   silently reorder or rotate scans. No mandatory paid scan-marker stage. Review and
   approve attendance's current version separately; see [attendance](attendance.md).
3. **Rubric:** import offline or preview/consent to extraction. Teacher approval
   of every question, marking criterion and maximum is separate from generation.
4. **Regions:** recommend Gemini proposals after explicit cost consent, with an
   explicit offline manual choice/fallback. Review every page and box before
   approval. See [regions](regions.md).
5. **Grading:** retain the selected mode, or explain batch/item choices. Preview
   target, data, current price estimates and request bounds excluding SDK retries.
   Obtain a new `--confirm-cost` for this paid stage. Extract all students'
   metadata before any answer crops: IDs must be readable, unique and exactly
   cover the expected present-student roster before answers proceed. On failure,
   stop and report source PDF page/script numbers from `grading/id-check.json`;
   have the teacher inspect the source before separately consented recovery.
   Google SDK bounded retries
   handle transient failures; never add automatic retry loops. Report failures
   and offer manual review, quota investigation or separately consented recovery.
   Incomplete checkpoints and completed failed runs use different branches;
   see [grading recovery](grading.md#failure-recovery).
6. **Optional grouping:** `cluster-answers` defaults offline/cached/local lexical.
   Paid embeddings: `--preview-embedding [--model-config PATH]`, then consented
   `--confirm-cost --revision REVISION`. The printed review/configuration revision
   binds review/grading state and selected model-config SHA-256; changes require
   a new preview and consent. Native calls require explicit `vertex_ai.project_id`
   and `vertex_ai.location`; offline grouping does not.
   Do not overwrite existing clusters or
   include identities. Caches are private, not proof of freshness or approval.
7. **Score review:** `review-grading` defaults offline; optional paid grouping
   requires explicit model settings/consent. Review all identities/marks and
   exceptions; edits and calculated suggestions do not approve anything.
8. **Post-approval menu:** proactively offer offline `report`, `analytics`,
   optional paid `ai-analysis` followed by separate review/`approve-analysis`,
   advanced reports, Moodle/LMS export, and email. Advanced DOCX/PPTX default
   offline; ask about images if unclear, honor an existing clear choice.
   Infographics need `--infographics --confirm-cost`, optional separate image
   configuration and a fresh report folder without modifying approved analysis.
   Preserve editable slides and full speaker notes; see [grading](grading.md).
9. **Distribution:** exact preview -> separate `approve-email` -> immediately
   authorized `send-email`. SMTP acceptance is not delivery.
10. **Navigation/cleanup:** refresh the assistant-maintained private offline
    [HTML index](output-index.md) on resume and after every output change/failure,
    archive or cleanup. Separately confirm cleanup and verify backups; preserve
    sources, approval-bound region/grading images, reviews/history and outputs.

Approval records bind to relevant hashes; changed inputs make downstream approvals
stale. Never infer approval from file presence. Do not share partial/stale reports.
Recorded usage and priced estimates are not invoices, and request bounds do not
include SDK retries. Unknown embedding usage is `cost_usd: null` with **UNPRICED**
audit rows/subtotals, not zero cost. Skill instructions are not an executable
security boundary.
