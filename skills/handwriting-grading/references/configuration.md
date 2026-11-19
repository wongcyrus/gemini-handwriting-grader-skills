# Exam configuration

`exam.yaml.template` is a starting point, not a real configuration. Place a
local copy outside version control. Relative paths resolve from its directory.

| Field | User decision or check |
|---|---|
| `exam.name` | User identifies the exam. |
| `exam.roster`, `exam.roster_sheet` | Select the workbook and sheet; preflight requires ID, NAME and CLASS columns. |
| `exam.scanned_pdf` | Select the combined scan; verify its page count and human-confirmed ordering. |
| `exam.marking_scheme` | Select the source file; current preflight checks existence only. |
| `exam.output_dir` | Select a separate location; inspect any existing outputs before writing. |
| `layout.pages_per_student` | User confirms a positive integer; preflight compares it with the PDF and roster counts. |
| `layout.same_layout_for_all` | User explicitly confirms the current shared-layout assumption. Unsupported otherwise. |
| `reporting.pass_mark`, `reporting.passing_includes_equal` | Needed for pass-based reports; don't infer a threshold or comparison. |
| `reporting.advanced_analysis` | User decides whether to request AI analysis and accept associated cost. |
| `email.enabled` | Disabled by default. Distribution is not implemented. |

Use the original project's `config.yaml` for Vertex mode and annotation model
selection. `suggest-regions` reads `vertex_ai.use_vertexai` and
`models.annotation`; it does not currently apply its logging or cache
settings. Credentials must remain outside configuration and Git. Preflight
does not contact Vertex AI or confirm model access.

Excel may store an ID as a number even when it is displayed with leading
zeroes. Confirm IDs against the original roster; do not assume the displayed
format survives import.
