# Exam configuration

`exam.yaml.template` is a starting point, not a real configuration. Place a
local copy outside version control. Relative paths resolve from its directory.

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
| `reporting.pass_mark`, `reporting.passing_includes_equal` | Both needed for pass-only samples; configure before starting. Never infer a threshold or comparison. |
| `reporting.question_pass_marks`, `reporting.question_passing_includes_equal` | Optional per-question mark thresholds and explicit equality policy for analytics. Supply both; unset questions have blank pass statistics. Configure before approvals. |
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

Use the project's `config.yaml` for Vertex mode and model selection:
- `models.annotation`: Automated bounding box proposals on layout template pages (Reasoning tier).
- `models.marking_scheme`: DOCX rubric extraction and search verification (Flash tier).
- `models.one_pass_metadata_ocr` (or `batch_metadata_ocr`): High-throughput batch student metadata OCR (ID, Class, Name; Flash tier).
- `models.one_pass_grading` (or `batch_grading`): Cohort-wide multimodal structured question batch grading (Reasoning tier).
- `models.ocr`, `models.grading`, `models.moderation`: Legacy item-by-item sequential fallbacks.
- `models.analytics`: Pedagogical feedback and misconception diagnosis (Reasoning tier).
- `models.embedding`: Semantic clustering of student answers before review (default: `gemini-embedding-2`).
`vertex_ai.use_vertexai` must be true.

### Model Eligibility & Boundary Policy

The grading pipeline cannot function with arbitrary models. Every step relies on three non-negotiable capabilities:
1. **Multimodal image bytes input**: The engine sends raw cropped PNG bytes (`types.Part.from_bytes(data=image, mime_type="image/png")`) for handwriting OCR, bounding box annotation, and answer evaluation. Text-only models will immediately crash.
2. **Pydantic structured output schemas**: Complex response schemas (`QuestionBatchGradeResponse`, `MetadataBatchResponse`, `SuggestedBoxes`, `ExtractedRubric`) require Gemini's native JSON schema engine (`response_schema`, `response_mime_type="application/json"`).
3. **Dynamic thinking budget**: Reasoning tasks require `ThinkingConfig(thinking_budget=-1)` (supported on Gemini 3.8 and Gemini 4). Legacy Gemini 1.x models reject this configuration.

| Model Tier | Allowed / Supported Models (Gemini 3.8+ Only) | Assigned Pipeline Steps | Rationale |
|---|---|---|---|
| **Fast / High-Volume (Flash)** | `gemini-3.8-flash` | `marking_scheme`<br>`one_pass_metadata_ocr`<br>`ocr`<br>`grading` (item mode) | High throughput, lowest token cost (~$0.075/1M input). SOTA OCR and structured grading against rubrics. |
| **Deep Reasoning** | `gemini-4-argon`<br>`gemini-4-hydrogen` | `annotation`<br>`one_pass_grading`<br>`moderation`<br>`analytics` | Frontier spatial precision for template box proposals; deep cross-script reasoning for cohort consistency moderation and misconception diagnosis. |
| **Semantic Embedding** | `gemini-embedding-2` | `embedding` | Semantic clustering of student answers in the human review browser interface. |

#### Strictly Incompatible & Prohibited Models
- **Non-Gemini models** (`claude-*`, `gpt-*`, `llama-*`, `gemma-*`, `palm-*`, `text-embedding-*`, etc.): Only Google Gemini models are permitted across the entire pipeline.
- **Gemini models older than 3.8** (`gemini-1.x`, `gemini-2.0-*`, `gemini-2.5-*`, `gemini-3.0-*`, `gemini-3.5-*`): Models below Gemini 3.8 are strictly rejected.
- **Dedicated media/image generation models** (`imagen-*`, `veo-*`, `chirp-*`): Cannot accept bounding box/rubric instructions or output structured grading JSON.
- **Text-only models**: Cannot receive cropped student handwriting images.
- **Specialized variants** (`*-tts`, `*-audio`, `*-live`, `*-robotics`, `*-computer-use`): Unsupported for exam grading.
- **Cross-tier assignment**: Embedding models (`gemini-embedding-2`) cannot be used for grading/OCR/annotation.

Runtime model loading in `load_model_settings` and `write_model_config` strictly validates model eligibility against these rules and raises an immediate, actionable `ValueError` if an unsupported model is detected.

### Dynamic online model discovery & cost-tiered configuration policy
Never hardcode static model versions or recommend non-existent or deprecated models. Google continuously updates and retires models online. Never use expensive frontier reasoning models (Gemini 4) across all stages, as high-volume tasks (OCR, grading, template annotation) become slow and cost-prohibitive.

Always dynamically discover, profile, and allocate models online by capability and cost:
```bash
handwriting-grader discover-models --project <PROJECT_ID> [--location <LOCATION>] [--write-config /path/to/config.yaml]
```
This queries Vertex AI online, inspects live model properties and quota, benchmarks pricing rates, and assigns:
- **Fast / High-Volume Tier** (e.g. `gemini-3.8-flash` at ~$0.075/1M input): for `marking_scheme`, `ocr`, and `grading` (item mode).
- **Deep Reasoning Tier** (e.g. `gemini-4-argon` at ~$1.250/1M input, ~16.7x cost): for low-volume, high-precision spatial reasoning (`annotation` on template pages), cohort-wide question grading (`batch_grading` in batch mode), and cohort-wide reasoning (`moderation` and `analytics`).

The skill runs this command during initial setup and configures `config.yaml` automatically with verified online models.

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
Preflight does not contact Vertex AI or confirm model access. Changing any
exam configuration invalidates existing approvals, so settle settings first.

Excel may store an ID as a number even when it is displayed with leading
zeroes. Confirm IDs against the original roster; do not assume the displayed
format survives import.
