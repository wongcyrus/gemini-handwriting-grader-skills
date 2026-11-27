# Exam configuration

[The exam template](../../../exam.yaml.template) is a starting point, not a real
configuration. Place a local copy outside version control. Relative paths resolve
from its directory. Inspect existing exam/model files and approval bindings first.
Reuse VERIFIED values and clear stage selections; ask only for unresolved,
stage-needed decisions. Do not repeat setup because a new stage starts.
Offline preparation/review does not require model credentials. Defer optional
embedding, image and email settings until those stages are requested.

| Field | User decision or check |
|---|---|
| `exam.name` | User identifies the exam. |
| `exam.roster`, `exam.roster_sheet` | Select the workbook and sheet; preflight requires ID, NAME and CLASS columns. |
| `exam.attendance` | Optional reviewed `ID,STATUS` CSV; see attendance.md. Without it every roster entry is expected to have a complete script. |
| `exam.answer_sheet_template` | DOCX template for optional personalized answer sheets, usable before scans exist. |
| `exam.scanned_pdf` | Select the combined scan; verify its page count and human-confirmed ordering. |
| `exam.marking_scheme` | Select the source file; current preflight checks existence only. |
| `exam.output_dir` | Select a separate location; inspect any existing outputs before writing. |
| `layout.pages_per_student` | User confirms a positive integer; preflight compares it with the PDF and present-student counts. |
| `layout.same_layout_for_all` | User explicitly confirms the current shared-layout assumption. Unsupported otherwise. |
| `reporting.pass_mark`, `reporting.passing_includes_equal` | Both needed when pass-only samples/statistics are requested; settle before approvals. Never infer a threshold or comparison. |
| `reporting.question_pass_marks`, `reporting.question_passing_includes_equal` | Optional per-question thresholds and explicit equality policy. Ask only if needed, supply both, and settle before approvals; unset questions have blank pass statistics. |
| `reporting.advanced_analysis` | Not used as an automatic trigger; run AI analysis explicitly after consent. |
| `email.enabled` | Not an automatic send trigger; distribution requires separate settings and explicit approval. |

For optional per-question analytics, set explicit thresholds and a comparison
policy before creating approvals, for example:

```yaml
reporting:
  question_pass_marks:
    "Q1": 1.5
    "Q2": 2
  question_passing_includes_equal: true
```

Thresholds are marks, not percentages; each must be within its question's
maximum. Only listed questions receive pass counts and rates. Both fields
must be supplied together, and canonical question labels must be unique.

Use the existing verified `config.yaml` for Vertex mode and model selection.
Credentials stay in the environment/Google authentication, never YAML or Git.
Reuse the selected Google Cloud project and Vertex location; do not ask again
unless unresolved or intentionally changing target. Save changes to a fresh
private configuration instead of modifying one bound to approvals.

Google `google-genai` settings include:
- `models.annotation`: Automated bounding box proposals on layout template pages (Reasoning tier).
- `models.marking_scheme`: DOCX rubric extraction and search verification (Flash tier).
- `models.one_pass_metadata_ocr` (or `batch_metadata_ocr`): High-throughput batch student metadata OCR (ID, Class, Name; Flash tier).
- `models.one_pass_grading` (or `batch_grading`): Cohort-wide multimodal structured question batch grading (Reasoning tier).
- `models.ocr`, `models.grading`, `models.moderation`: Legacy item-by-item sequential fallbacks.
- `models.analytics`: Pedagogical feedback and misconception diagnosis (Reasoning tier).
- `models.embedding`: Semantic clustering of student answers before review (default: `gemini-embedding-2`).
`vertex_ai.use_vertexai` must be true.

### Gemini capability and thinking semantics

Keep the existing Google model policy; do not import Azure deployment restrictions.
Image stages send `types.Part.from_bytes` for crops/pages. Evaluation uses
Pydantic `response_schema` and `response_mime_type="application/json"`.
Reasoning uses the supported Gemini `ThinkingConfig` semantics, including
`thinking_budget=-1` where compatible; do not substitute another provider's
reasoning controls or assume every model accepts every thinking option.
Embedding and image-generation models are separate capabilities, not grading
model replacements. Runtime name validation is not a live capability guarantee.

Existing configurations commonly use Flash (such as `gemini-3.8-flash`) for
rubric/metadata/item grading, reasoning models (such as `gemini-4-argon` or
`gemini-4-hydrogen`) for annotation/batch grading/moderation/analysis, and
`gemini-embedding-2` for optional clustering. Retain a verified eligible selection;
do not rediscover or reallocate it at every stage, or quote fixed token prices,
accuracy, consistency or savings. Stage workload and current rates determine cost.

### Metadata discovery versus paid probes

If model choices remain unresolved, metadata listing is the default:

```bash
handwriting-grader discover-models --project <PROJECT_ID> [--location <LOCATION>] [--write-config /path/to/config.yaml]
```

This lists model metadata without paid inference/capability pings. It does not
verify quota, live pricing or model quality. If paid capability probing is needed,
preview the target, data, request bounds and current price estimate; only after
explicit consent use `--probe --confirm-cost`. A probe is limited evidence of
capability at that time, not verified quota/pricing or consent to later grading.
Review any proposed allocation; write only a fresh private configuration and never
overwrite an approved one. Discovery/access errors should be explained with a
manual-configuration alternative, not repeated setup questions.

Vertex model listing does not return prices. Independently review current Vertex
rates or a current reviewed private pricing cache before each paid estimate.
Report unpriced/unknown usage honestly. Offline `cost-report` uses recorded usage
and estimated rates, not an invoice or every SDK retry/failed-response charge.
Unknown embedding usage persists as `cost_usd: null`, with **UNPRICED** audit
rows/subtotals rather than an invented zero-cost amount.

### Optional embedding and image settings

`cluster-answers` and `review-grading` default offline. To request paid embeddings,
preview with `--preview-embedding` and optional `--model-config PATH`, inspect the
resolved Google target/model and rates, then obtain new consent and execute with
the exact `--revision REVISION --confirm-cost`. An override is optional, not a
requirement to repeat setup. Keep local lexical grouping available.
The printed **Review and configuration revision** binds both the review/grading
revision and the selected model-config SHA-256. Changing review state or the
selected configuration (including target/model) requires a new preview and
consent; do not reuse the old token. Native embedding calls require explicit
`vertex_ai.project_id` and `vertex_ai.location` in the resolved configuration,
without silently inferring a target. Offline grouping remains the default and
does not require these settings.

For advanced-report images, first inspect settings recorded with the approved
analysis. An optional `--image-model` selects the compatible Google image model;
`--image-model-config PATH` supplies separate image settings/Vertex target when
needed. Do not change the approved analysis configuration or auto-discover an
unrelated adjacent image file. Ask only unresolved image-stage choices.
No root model/image configuration template is assumed: use existing verified
settings and the installed CLI/config schema, not links to nonexistent templates.
The advanced-report image adapter is not a standalone image-generation skill;
an unrelated skill/provider is not a substitute and has its own consent contract.

Enabled caches apply to `ocr`, `grading`, `batch_grading`, `moderation`,
`annotation`, `marking_scheme` extraction and `analytics`; they
default to one run. Cross-run reuse requires explicit `caching.scope: shared`
and `caching.directory`, relative to the model configuration. The directory
must be private and user-owned; it contains sensitive answers and has no
automatic retention policy. Model aliases can change without cache-key
changes, so do not reuse cached results when freshness is required. Grounded
verification is not cached; logging settings are not applied.
Cache hits never substitute for cost consent or review. Credentials must remain
outside configuration and Git.
Preflight is offline input validation; it does not check scan orientation,
page sequence or model access. Rendered pages are not automatically aligned.
Changing exam configuration invalidates approvals: settle relevant settings first,
then explicitly review again if an intentional later change requires it.

Excel may store an ID as a number even when it is displayed with leading
zeroes. Confirm IDs against the original roster; do not assume the displayed
format survives import.
