# Answer sheets

## Inputs and prerequisites

Obtain a roster XLSX and its sheet name, a DOCX template, and a separate
output directory from the user. Configure `exam.roster`, `exam.roster_sheet`,
`exam.answer_sheet_template` and `exam.output_dir` in the exam YAML.
No scanned PDF or rubric is needed. The roster must contain unique, nonempty
ID, NAME and CLASS fields. Check leading zeros with the user: Excel may
already have stored IDs as numbers. The DOCX must contain `Name:`,
`Student ID:` and `Class:` in paragraphs, tables, headers, footers, Word
text boxes or hyperlink text. Placeholders cannot cross a tab or line break,
or appear in Word field results. Multi-paragraph Word fields are unsupported.
LibreOffice must be installed.

## Procedure and review

1. Confirm with the user that creating private output at the selected path is
   intended, then run `handwriting-grader answer-sheets --exam
   /path/to/exam.yaml`.
2. Inspect `output_dir/answer-sheets/manifest.json` for completion. Open
   `combined.pdf` and individual files, verify every student identity and
   layout, and compare `students.xlsx` against the roster before printing.
   The combined PDF follows roster order; the workbook maps IDs to numbered
   PDFs and their combined page numbers. Text box positioning and appearance
   can differ between Word and LibreOffice; inspect each generated page.
3. Confirm page count and ordering when later scanning. If
   `layout.pages_per_student` is configured, generation rejects mismatches.

## Failure handling

Generation refuses to overwrite an existing run. A partial run has no
completion manifest; preserve and inspect it rather than printing or deleting
it automatically. A Snap installation of LibreOffice may not access `/tmp`
or other restricted paths; use a private accessible output directory and
restart with the user's approval. Template and roster changes during
generation invalidate the result. These are local files, not a tamper-proof
audit record; never commit them.
