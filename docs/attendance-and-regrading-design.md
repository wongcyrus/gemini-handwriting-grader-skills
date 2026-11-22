# Attendance and targeted regrading design

**Status:** Reviewed attendance and targeted regrading are implemented in the
current working tree. This design document is outside the agent skill and is
not authorization to perform paid calls. See
`skills/handwriting-grading/references/attendance.md` and `regrading.md` for
the executable procedures, and `flow-and-parity.md` for current limitations.

## Decisions already made

- The teacher supplies and explicitly reviews an attendance list. Absences
  must never be inferred from missing scans or OCR.
- Regrading creates a separate version and reruns the selected question for
  **all present students**. It does not modify the parent run. The new
  version requires fresh human review and approval.
- Never send data to Vertex or email without the existing separate consent
  gates. Never delete existing data, reviews, approvals or delivery journals.

## Goals and non-goals

The attendance workflow should retain the complete roster while allowing a
scan containing only present students. Reports must distinguish absent
students from zero scores and from failed or unreviewed grading.

The targeted-regrading workflow should let a teacher rerun one question
without unnecessarily re-OCRing or regrading other questions. Moderation
must use the newly graded results for the entire selected question, not a
mixture of old and new marks.

The later crop extension supports separately approved shared and individual
crop coordinates, while still rerunning the question for every present
student. Remaining non-goals include automatic absence detection, mixed page
layouts, selecting individual students for rerun, changing scan order,
automatic approval, automatic dispatch, overwriting an existing report, and
unattended retry of ambiguous external operations.

## Attendance contract

1. Retain the full roster as the authoritative set of students. Add an
   optional attendance CSV path to the exam configuration. No attendance
   file preserves today's behavior: every roster student is expected.
2. The CSV has exactly `ID,STATUS` headers, UTF-8 encoding and one row per
   roster ID. `STATUS` is explicitly `present` or `absent`. Preserve IDs as
   strings, including leading zeroes; reject duplicates, unknown IDs,
   missing IDs, unsupported statuses and a list with no present students.
3. Before approval, generate a private, read-only-for-review attendance
   summary showing roster ID, name, class and status. A teacher compares it
   with the source list and explicitly approves it. Corrections are made in
   the source list and require a new review, not silent edits to the summary.
4. Bind approval to the roster, attendance file, exam configuration and
   generated review artifact hashes. Changing any bound input invalidates
   approval. Preserve earlier files and approvals; never reuse an old approval
   for changed attendance.
5. Preflight reports present and absent counts, checks that the scanned page
   count equals present students times pages per student, and asks the teacher
   to confirm scan order and shared layout. Page counts alone cannot prove
   that the correct students were scanned.
6. Require current attendance approval before paid grading. The identity
   review maps scripts one-to-one to the **present** roster. Missing,
   duplicate, unrecognized or absent IDs block score approval. NAME, ID and
   CLASS OCR remain suggestions for a human, not identity decisions.
7. Grading, score reports, statistics, sampling, feedback and email operate
   only on present students. A separate attendance report lists absent
   students without a numeric mark or attachment. Email previews require one
   recipient per present student and must never implicitly email absentees.
   The teacher decides separately how to communicate with absent students.
8. Answer-sheet generation remains independent and uses the full roster; it
   may occur before attendance is known.

The first implementation should require an explicit list whenever an absence
is to be represented. It must not infer the meaning of an omitted row, blank
status, missing page, failed OCR, special consideration or late submission.

## Versioned runs

The current commands assume fixed `output_dir/grading`, `reports`, and other
paths, and approvals bind to the complete exam configuration. A child run
cannot safely be implemented by merely copying files, changing `output_dir`
and bypassing these checks.

Introduce an explicit run context and run selector across grading review,
reporting, analytics, AI analysis and email commands. The
existing root-level run remains the default for backward compatibility.
Each new child run has a unique, privately created directory and a manifest
identifying its parent, selected question, immutable input hashes, model
configuration, project/location, run format and status. All downstream
artifacts and approvals resolve inside that selected run. Paths and IDs must
be validated; do not allow traversal or silently fall back to another run.

A child may reuse parent data only after validating the parent approval and
all referenced hashes. It must retain the original approved attendance,
roster, scan order, page images and regions. If these inputs changed, stop:
the teacher must start an appropriate new exam run. Existing runs without
the fields necessary to establish provenance must not be silently upgraded.

## Targeted regrading flow

1. **Select and inspect.** The teacher selects a completed, currently
   approved parent run and exactly one canonical question. Verify its
   reviewed scores, identities, attendance and all original inputs.
2. **Choose the rubric.** Reuse the current approved rubric, or create and
   separately approve a new rubric artifact for the child. Compare canonical
   question identifiers, full question text, marking criteria and maximum
   marks. Initially allow a change only to the selected question; reject
   additions, removals, renumbering or changes affecting other questions,
   including a general guide that changes their effective criteria.
3. **Preview cost and data.** Optionally prepare and separately approve shared
   or per-script crop coordinates for the selected question. Show the parent and child identifiers, selected
   question, model/project/location, affected present-student count, changed
   rubric, data sent and the maximum request count. A conservative upper bound
   is one answer OCR and one grading request per present student plus one
   whole-question moderation request. Cache hits may reduce calls, but do
   not remove the need for explicit cost/data consent. In-flight retries may
   incur additional charges.
4. **Create a separate child.** After explicit consent, atomically reserve a
   unique private child directory and persist its provenance and checkpoint.
   Do not modify the parent, its review workbook, reports or approvals.
5. **Rerun the whole question.** Crop each present student's inherited region
   or separately approved adjustment,
   OCR and grade it against the child rubric, then moderate all successful
   results together. A failure is not zero and must not silently fall back
   to an earlier score. If complete moderation is impossible, flag the
   question for manual review.
6. **Build a review draft.** Preserve previous reviewed values for
   unchanged questions as clearly identified draft values with provenance;
   preserve the parent's artifacts unchanged. For the rerun question show
   new OCR/grades and provide the previous reviewed answer, reasoning and
   mark for comparison. Never silently replace a corrected answer with old
   or new text. Clear review flags for the child, including identities and
   scores, so all values receive fresh review and approval.
7. **Approve and regenerate.** Review every identity, answer and score under
   the normal validation rules, including mandatory manual marks for flagged,
   failed or corrected answers. The child's approval binds its inputs,
   provenance, results and review. Generate child-specific reports and, if
   requested, new analytics, feedback and email previews. Parent outputs
   remain historical artifacts and are not child reports.

An interrupted child may only resume after validating its immutable inputs,
checkpoint format and exclusive run lock, and obtaining fresh cost consent.
Persist completed calls before proceeding; in-flight calls can repeat.
Do not silently recover from an invalid checkpoint or stale lock. Leave
partial files intact for investigation and never publish a completion marker
until the child is complete.

## Implementation boundaries and checks

- Refactor roster handling so attendance filtering is explicit; do not
  accidentally filter answer sheets or mutate the original roster.
- Review every current use of roster length, script indices, question keys,
  configuration hashes and fixed output paths. Update email recipient checks
  and every downstream approval to use the selected run and present roster.
- Guard against a reviewed attendance or rubric changing while a run is
  prepared, graded, approved or reported. Validate inputs before and after
  consequential operations and use atomic publication with exclusive
  creation; do not promise safety against uncooperative spreadsheet writers.
- Enforce and display new approval boundaries. The presence of an attendance
  CSV, a draft workbook, a previous approval or cached model output is not
  proof of review.
- Version manifests and checkpoint schemas explicitly. Preserve old behavior
  for existing runs; reject unsupported migration cases rather than
  reinterpreting their data.
- Treat all roster, attendance, student work, caches and emails as private.
  Keep the design, examples and test fixtures free of real student data.
- Keep proposed commands and schemas out of `SKILL.md` and operational
  references until implemented and tested. Update the skill only after
  behavior, CLI help and examples match the implementation.

## Required verification before claiming implementation

Use synthetic rosters and local fake services. Cover all-present legacy runs;
one and multiple absences; duplicate, missing, unknown or invalid attendance
rows; stale attendance/roster/review approvals; scan count mismatches; no
absent student treated as zero or emailed; answer sheets still including the
full roster; and ID matching for present students.

For child runs cover unchanged and changed rubrics, rejection of changes to
other questions, parent immutability, whole-question moderation, failed OCR or
grading, manual corrections, complete fresh review, stale input and approval
rejection, isolated reports/email, concurrent attempts, supported resume and
cost-preview/consent enforcement. Exercise browser and CLI paths as well as
data-level checks. Live Vertex and SMTP tests require separate authorization;
offline fakes cannot establish live service behavior.
