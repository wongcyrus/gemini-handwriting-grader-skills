# Email distribution

## Inputs and privacy

Require approved grading and completed, current scored PDFs. Ask the user
for an explicit `ID,EMAIL` CSV matching every present roster ID and a separate YAML
with `sender`, `smtp_host`, `smtp_port`, `smtp_username`, `subject` and
`body`. Never guess email addresses or store a password in these files.
Without an attendance list, every roster entry is present. Never infer or
email absentees automatically. The subject supports `{name}`, `{marks}`, `{exam}`; the body also supports
`{feedback}`, which requires reviewed and approved AI analysis. Credentials
are read from the local `HANDWRITING_SMTP_PASSWORD` environment variable.
Never print or commit credentials, previews, student data or generated PDFs.

## Procedure

1. Run `handwriting-grader preview-email --exam /path/to/exam.yaml
   --settings /private/email.yaml --recipients /private/recipients.csv`.
   (`--recipients` accepts either `ID,EMAIL` or a Moodle participants CSV with
   `ID number` and `Email address` columns; it automatically matches present roster
   students.)
   This creates private `.eml` files and `email/preview.json`; it sends nothing.
2. Ask the user to review every exact recipient, subject, body and PDF
   attachment. Then run `approve-email --exam /path/to/exam.yaml
   --confirm-reviewed` only after explicit human approval.
3. Immediately before dispatch obtain separate explicit authorization. Run
   `send-email --exam /path/to/exam.yaml --confirm-send`. SMTP uses STARTTLS;
   the program rechecks source, preview, approval and attachments.

## Failure handling

No automatic retry is performed: `email/delivery.json` records accepted
messages and any in-flight message. SMTP success means accepted, not delivered.
An error after submission can be ambiguous; reconcile with the SMTP provider
and recipients before manually deciding on any further dispatch. Never delete
or reset the journal automatically. Existing previews are not overwritten.
Changing inputs or feedback invalidates approval; preserve stale output and
discuss how to start again. SMTP has only been tested with a mock, not live.

## Moodle / LMS Bulk Release (`moodle-export`)

For releasing individual scores, personalized AI feedback, and marked PDFs via **Moodle** (or any LMS) without sending emails:

```bash
handwriting-grader moodle-export --exam /path/to/exam.yaml \
  [--worksheet /path/to/grading_worksheet.csv] \
  [--participants /path/to/courseid_participants.csv]
```

- **Per-Student Combined PDFs** (`output/moodle/student_pdfs/<ID>_<Name>.pdf`): Always generated. Merges an A4 personalized AI feedback & score cover page (if `ai-analysis` is approved) ahead of each student's marked exam script PDF.
- **Email Recipients Helper** (`output/moodle/recipients.csv`): When `--participants` is passed, extracts matched `ID,EMAIL` pairs for `preview-email`.
- **Moodle Assignment Bulk Upload** (`output/moodle/grading_worksheet_filled.csv` + `output/moodle/feedback_files.zip`): When `--worksheet` (exported from Moodle Assignment -> *Download grading worksheet*) is passed, fills `Grade` and `Feedback comments` and packages each student's combined PDF under Moodle's `<Full name>_<ParticipantID>_assignsubmission_file_/` naming format for *Upload grading worksheet* and *Upload multiple feedback files in a zip*.
