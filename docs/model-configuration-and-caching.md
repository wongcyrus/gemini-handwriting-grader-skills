# Model Configuration & Caching Architecture

This document specifies the AI model configuration schema, private/shared response caching mechanisms, and Vertex AI authentication in `gemini-handwriting-grader-skills`.

---

## 1. Model Configuration Schema (`config.yaml`)

Exam grading operations consume an external YAML model configuration file (`--model-config /path/to/config.yaml`):

```yaml
vertex_ai:
  use_vertexai: true
  project_id: my-gcp-project-id
  location: us-central1

models:
  annotation: gemini-3.8-flash        # Automated region suggestion
  marking_scheme: gemini-3.8-flash    # Rubric extraction from DOCX
  ocr: gemini-3.8-flash               # Student ID & Class metadata OCR
  grading: gemini-3.8-flash           # Per-script multimodal question grading
  moderation: gemini-4-argon          # Batch consistency moderation
  analytics: gemini-4-argon           # AI feedback & misconception analysis
  analytics_image: gemini-3-pro-image # Infographic generation

caching:
  enabled: true
  scope: run                          # 'run' (private) or 'shared'
  agents:
    - ocr
    - grading
    - moderation
```

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
