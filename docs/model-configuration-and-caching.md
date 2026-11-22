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
  # --- Setup & Rubric ---
  annotation: gemini-4-argon              # Automated region suggestion (Reasoning tier)
  marking_scheme: gemini-3.8-flash        # Rubric extraction from DOCX (Flash tier)

  # --- Fast One-Pass Pipeline (Default) ---
  one_pass_metadata_ocr: gemini-3.8-flash # One-Pass Batch Student Metadata OCR (ID, Class, Name; Flash tier)
  batch_metadata_ocr: gemini-3.8-flash    # Alias for one_pass_metadata_ocr
  one_pass_grading: gemini-4-argon        # One-Pass Multimodal Question Batch Grading (Reasoning tier)
  batch_grading: gemini-4-argon           # Alias for one_pass_grading

  # --- Legacy Item-by-Item Fallbacks ---
  ocr: gemini-3.8-flash                   # Sequential single-crop Metadata OCR fallback
  grading: gemini-3.8-flash               # Sequential per-student Question Grading fallback
  moderation: gemini-4-argon              # Sequential Question Consistency Moderation fallback

  # --- Reporting & Insights ---
  analytics: gemini-4-argon               # AI feedback & misconception analysis (Reasoning tier)

  # --- Semantic Embedding & Clustering ---
  embedding: gemini-embedding-2           # Semantic clustering of student answers (Gemini-only)

caching:
  enabled: true
  scope: run                              # 'run' (private) or 'shared'
  agents:
    annotation: true
    marking_scheme: true
    one_pass_metadata_ocr: true
    batch_metadata_ocr: true
    ocr: true
    one_pass_grading: true
    batch_grading: true
    grading: true
    moderation: true
    analytics: true
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
   - **Flash Tier** (e.g. `gemini-3.8-flash` @ ~$0.075/1M input): Assigned to high-volume batch tasks (`ocr`, `grading`, `marking_scheme`).
   - **Frontier Reasoning Tier** (e.g. `gemini-4-argon` @ ~$1.250/1M input): Assigned to low-volume spatial precision (`annotation`) and complex cohort synthesis tasks (`moderation`, `analytics`).
4. **Transparent Cost Table**: Outputs an ASCII cost and throughput breakdown before writing the configuration.

---

## 2.1 Model Eligibility & Boundary Policy (Gemini 3.8+ Only)

The handwriting grading pipeline cannot operate on arbitrary models. Every stage depends on:
1. **Multimodal image byte inputs** (`types.Part.from_bytes`) for cropped handwritten scripts.
2. **Native structured JSON schemas** (`response_schema` with Pydantic models).
3. **Dynamic thinking budget** (`ThinkingConfig(thinking_budget=-1)`) for reasoning and calibration stages.

### Allowed Models
- **Fast / High-Volume Tier**: `gemini-3.8-flash` (or newer Flash $\ge 3.8$)
- **Deep Reasoning Tier**: `gemini-4-argon`, `gemini-4-hydrogen` (or newer Reasoning/Pro $\ge 3.8$)
- **Semantic Embedding Tier**: `gemini-embedding-2`

### Strictly Prohibited & Rejected Models
- **Any non-Gemini model** (`claude-*`, `gpt-*`, `llama-*`, `gemma-*`, `palm-*`, `text-embedding-*`, etc.)
- **Any Gemini model older than 3.8** (`gemini-1.x`, `gemini-2.0-*`, `gemini-2.5-*`, `gemini-3.0-*`, `gemini-3.5-*`)
- **Dedicated media/image/video/audio generation models** (`imagen-*`, `veo-*`, `chirp-*`)
- **Specialized non-grading variants** (`*-tts`, `*-audio`, `*-live`, `*-robotics`, `*-computer-use`)
- **Cross-tier assignments** (e.g. assigning `gemini-embedding-2` to grading, OCR, or annotation)

### Runtime Enforcement
Model eligibility is validated automatically via `validate_model_eligibility()` in [discovery.py](../src/handwriting_grader/discovery.py):
- **Initial setup (`discover-models`)**: Filters candidate publisher models via `is_eligible_model()` and validates every step entry before writing `config.yaml`.
- **Every execution stage**: `load_model_settings()` in [suggestions.py](../src/handwriting_grader/suggestions.py) and `get_embedding_client()` in [score_editor.py](../src/handwriting_grader/score_editor.py) reject unsupported or outdated models immediately before making any Vertex AI API call.

---

## 3. Response Caching Engine (`cache.py`)

Implemented via `CachedGrader`:
- Intercepts requests to `ocr()`, `grade()`, and `moderate()`.
- Computes deterministic SHA-256 hash of:
  - Agent name (`ocr` / `grading` / `moderation`).
  - Model identifier.
  - Serialized prompt, image bytes, and temperature settings.
  - Known candidate list hash (`candidates_sha256`) for metadata OCR (ensures cache invalidates if the student roster changes).
- If a matching cache file exists in `cache_root`, the response is parsed and returned instantly without issuing a remote Vertex AI API call.

### 3.1 Private vs. Shared Caching Scopes
1. **Run-Private Scope (`scope: run`)** [Default]:
   - Stored in `output/grading/cache/`.
   - Never leaks data across different exams or runs.
   - Deleted when the output folder is cleaned.
2. **Shared Scope (`scope: shared`)**:
   - Configured via `caching.directory: /private/user/cache`.
   - Reuses matching responses across runs and child regrades.
   - Must be owned by the user with restricted permissions (`0o700`).

### 3.2 Cross-Question & Cross-Assignment Embedding Cache
For semantic answer clustering (`cluster-answers`), embedding vectors generated by `gemini-embedding-2` are stored in a global shared cache:
- **Location**: `~/.cache/gemini-handwriting-grader/embeddings/<model>.json` (or `<caching.directory>/embeddings/<model>.json` if `caching.directory` is set).
- **Structure**: A key-value JSON store mapping normalized answer text to its 3072-dimensional float vector.
- **Cross-Question & Cross-Assignment Sharing**: Identical or repeated student answers (e.g. `"True"`, `"False"`, `"mitochondria"`, or common definitions) across different questions in the exam—or across entirely separate exam assignments on the same machine—are retrieved instantly from disk with **zero GCP API calls and zero cost**.
- **Atomic Concurrency Safety**: When new embeddings are computed, updates are written via temporary files and atomic rename (`os.replace`) to prevent file corruption.
- **Local Mirroring**: For self-contained offline portability, embeddings are also mirrored to the assignment directory at `output/grading/.embedding_cache.json`.

---

## 4. Vertex AI Authentication

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
