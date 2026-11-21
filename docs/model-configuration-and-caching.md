# Model Configuration & Caching Architecture

This document specifies the AI model configuration schema, private/shared response caching mechanisms, and Vertex AI authentication in `gemini-handwriting-grader-skills`.

---

## 1. Model Configuration Schema (`config.yaml`)

Exam grading operations consume an external YAML model configuration file (`--model-config /path/to/config.yaml`):

```yaml
vertex_ai:
  use_vertexai: true
  project_id: my-gcp-project-id
  location: global

models:
  annotation: gemini-4-argon          # Automated region suggestion (Reasoning tier)
  marking_scheme: gemini-3.8-flash    # Rubric extraction from DOCX (Flash tier)
  ocr: gemini-3.8-flash               # Student ID & Class metadata OCR (Flash tier)
  grading: gemini-3.8-flash           # Per-script multimodal question grading (Flash tier)
  moderation: gemini-4-argon          # Batch consistency moderation (Reasoning tier)
  analytics: gemini-4-argon           # AI feedback & misconception analysis (Reasoning tier)
  analytics_image: gemini-3.8-flash   # Infographic generation (Flash tier)

caching:
  enabled: true
  scope: run                          # 'run' (private) or 'shared'
  agents:
    - ocr
    - grading
    - moderation
```

---

## 2. Dynamic Online Model Discovery (`discover-models`)

Google frequently updates and releases new Gemini models. To prevent relying on static, outdated, or fantasy model names, `gemini-handwriting-grader-skills` provides automated online model discovery:

```bash
python -m handwriting_grader.cli discover-models --project <PROJECT_ID> [--location <LOCATION>] [--write-config /path/to/config.yaml]
```

### Discovery Mechanics:
1. **Live Vertex AI Query**: Calls `client.models.list()` live against your Google Cloud project and location to discover currently deployed publisher models.
2. **Quota & Connectivity Ping**: Conducts a zero-cost live ping to verify that the project has active quota and valid regional routing (e.g. `global` vs regional endpoints).
3. **Capability & Cost Tier Allocation**:
   - **Flash Tier** (e.g. `gemini-3.8-flash` @ ~$0.075/1M input): Assigned to high-volume batch tasks (`ocr`, `grading`, `marking_scheme`, `analytics_image`).
   - **Frontier Reasoning Tier** (e.g. `gemini-4-argon` @ ~$1.250/1M input): Assigned to low-volume spatial precision (`annotation`) and complex cohort synthesis tasks (`moderation`, `analytics`).
4. **Transparent Cost Table**: Outputs an ASCII cost and throughput breakdown before writing the configuration.

---

## 2. Response Caching Engine (`cache.py`)

Implemented via `CachedGrader`:
- Intercepts requests to `ocr()`, `grade()`, and `moderate()`.
- Computes deterministic SHA-256 hash of:
  - Agent name (`ocr` / `grading` / `moderation`).
  - Model identifier.
  - Serialized prompt, image bytes, and temperature settings.
- If a matching cache file exists in `cache_root`, the response is parsed and returned instantly without issuing a remote Vertex AI API call.

### 2.1 Private vs. Shared Caching Scopes
1. **Run-Private Scope (`scope: run`)** [Default]:
   - Stored in `output/grading/cache/`.
   - Never leaks data across different exams or runs.
   - Deleted when the output folder is cleaned.
2. **Shared Scope (`scope: shared`)**:
   - Configured via `caching.directory: /private/user/cache`.
   - Reuses matching responses across runs and child regrades.
   - Must be owned by the user with restricted permissions (`0o700`).

---

## 3. Vertex AI Authentication

Uses Google Cloud standard **Application Default Credentials (ADC)**:
- Developers and agents authenticate via:
  ```bash
  gcloud auth application-default login
  ```
- Or via service account credentials:
  ```bash
  export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json
  ```
- No API keys are ever committed to repository code or stored in exam configuration files.
