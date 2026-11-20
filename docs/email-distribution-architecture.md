# Gated Email Distribution & Dispatch Architecture

This document specifies the private preview generation, attachment binding, human review gate, and SMTP STARTTLS dispatch architecture in `gemini-handwriting-grader-skills`.

---

## 1. Distribution Security & Privacy Requirements

Direct student email dispatch carries high risks of misdirection, privacy leaks, or accidental mass delivery. The architecture enforces three independent stages:

```
[Approved Grading & Reports]
             |
             v
+-----------------------------------------------------------+
| Stage 1: Private Email Preview (preview-email)            |
| Map roster IDs to emails, draft body & attach scored PDFs |
+-----------------------------------------------------------+
             |
             v
+-----------------------------------------------------------+
| Stage 2: Human Review & Approval (approve-email)          |
| Validate every recipient address and attachment mapping   |
+-----------------------------------------------------------+
             |
             v
+-----------------------------------------------------------+
| Stage 3: SMTP STARTTLS Dispatch (send-email)              |
| Deliver with delivery journal; explicit --confirm-send    |
+-----------------------------------------------------------+
```

---

## 2. Stage 1: Private Preview Generation (`preview-email`)

Invoked via:
```bash
handwriting-grader preview-email --exam exam.yaml \
  --settings email_settings.yaml --recipients recipients.csv [--run CHILD_RUN]
```

- **Input Validation**:
  - `recipients.csv`: Must contain `ID,EMAIL` headers with valid, parseable email addresses for every present roster student.
  - `email_settings.yaml`: Configures SMTP server host, port, sender address, subject line template, and body template.
- **Output (`output/email/preview.json`)**:
  - Contains exact message drafts, resolved template placeholders (`{NAME}`, `{ID}`, `{TOTAL_MARKS}`), and absolute paths to attached individual student PDFs.
  - **No network connection is opened and no email is sent.**

---

## 3. Stage 2: Human Preview Approval (`approve-email`)

A human must inspect `output/email/preview.json` and approve the dispatch manifest:
```bash
handwriting-grader approve-email --exam exam.yaml --confirm-reviewed [--run CHILD_RUN]
```

- Verifies that:
  - Every present student has an approved grade slip attachment.
  - No attachment path is missing or points outside the approved report directory.
- Computes SHA-256 hash of `preview.json` and writes `output/email/approval.json`.
- Subsequent changes to reports or email settings invalidate this approval.

---

## 4. Stage 3: SMTP STARTTLS Dispatch (`send-email`)

```bash
handwriting-grader send-email --exam exam.yaml --confirm-send [--run CHILD_RUN]
```

- **Safety Gate**: Exits with status 1 if `--confirm-send` is omitted.
- **Transport Security**:
  - Connects using secure SMTP over TLS or explicit STARTTLS.
  - Credentials supplied via environment variables (`SMTP_USERNAME`, `SMTP_PASSWORD`), never stored in exam YAML.
- **Delivery Journal (`output/email/delivery_journal.jsonl`)**:
  - Append-only log recording recipient, timestamp, message ID, SMTP response code, and delivery status.
  - If a connection drops mid-batch, re-running skips already delivered messages.
