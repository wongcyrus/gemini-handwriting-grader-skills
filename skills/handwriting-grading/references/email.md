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
