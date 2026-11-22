# Reviewed attendance

## Inputs and preparation

An attendance list is optional. Without one, every roster entry must have a
complete scanned script, as before. To represent absences, obtain a private
UTF-8 CSV with exactly `ID,STATUS` headers and one row for every roster ID.
IDs must exactly match the roster, including leading zeroes; each status is
`present` or `absent`. Do not infer absences from missing pages, failed OCR or
unrecognized IDs. At least one student must be present.

Set `exam.attendance` to the CSV path before creating approvals. Paths are
relative to the exam YAML; the CSV must be outside the output directory.
The scanned PDF must contain exactly the present students' complete scripts
in the teacher-confirmed order, with the same layout for each student.
Preflight checks the count, not the order or identity.

## Procedure

1. Run `handwriting-grader preflight --exam /path/to/exam.yaml`.
   Check its present and absent counts with the teacher.
2. Run `handwriting-grader prepare-attendance --exam /path/to/exam.yaml`.
   Ask the teacher to inspect every ID, name, class and status in the generated
   Excel workbook (`attendance_review.xlsx`, alongside `review.json`) and compare it
   with the original list and roster.
3. Only after explicit review, run `handwriting-grader
   approve-attendance --exam /path/to/exam.yaml --confirm-reviewed`.
   The command without the flag records no approval.
4. Continue with rubric, region and grading review. Only present roster IDs
   are valid in score review. `reports/absences.xlsx` lists absent students
   separately, with no numerical mark. Reports, analytics, AI feedback and
   email cover present students only. The email CSV must cover exactly them;
   decide separately how to communicate with absent students.

Answer sheets remain independent and use the full roster, including students
later marked absent. Configuring attendance does not change this.

## Staleness and recovery

The approval is bound to the exact configuration, roster, attendance CSV and
review file. Changing any of them invalidates it and dependent grading.
Preparing a new review selects a new version and leaves previous reviews and
approvals intact; it invalidates a grading run tied to an earlier approval,
even if the list has the same contents. Do not overwrite or delete old runs,
or share stale reports; agree on a new run when inputs change.

Review files, approvals and absence reports contain private student data.
Approval records are local hash-bound records, not tamper-proof identity
verification. This workflow does not support missing pages, mixed layouts, or
automatic attendance inference. For targeted regrading, see
`regrading.md`.
