# Gemini Telemetry, Live Progress & Estimated Cost Reporting

The engine uses the Google GenAI SDK directly; ADK is not required. Progress
messages retain an ADK-style prefix for compatibility, not as evidence of ADK execution.

---

## 1. Observability Goals

1. **Sub-Step Visibility**: Long-running batch operations (identifier OCR, question grading, moderation, AI feedback) must display immediate visual progress without waiting for the batch to finish.
2. **Deterministic Token Accounting**: Capture input, candidate, cached, and thought tokens directly from Gemini's `usage_metadata`.
3. **Financial Transparency**: Provide auditable cost tracking both during execution and via offline reports.
4. **Resilience**: Append-only JSONL retains recorded usage after interruption.
   Unlogged failures and SDK retries may still be billed; this is not an invoice.

---

## 2. Telemetry Architecture

```
                                Google GenAI SDK Call
                             (Vertex AI Gemini Models)
                                        |
                                        v
                               response.usage_metadata
                                        |
                                        v
+---------------------------------------------------------------------------------+
|                       TelemetryTracker (telemetry.py)                           |
+---------------------------------------------------------------------------------+
  |                                 |                                 |
  v                                 v                                 v
[Real-Time Progress]      [Append-Only Journal]             [Excel & JSON Audit]
  stdout flush               gemini_usage.jsonl                cost_report.xlsx
  "[ADK Progress] ..."       (CallTrace schema)                cost_summary.json
```

---

## 3. Data Schema: `CallTrace`

Successfully recorded responses generate a `CallTrace`; it is not a complete
ledger of every billable attempt:

```python
@dataclass
class CallTrace:
    timestamp: str          # ISO 8601 UTC timestamp
    phase: str              # 'ocr', 'grading', 'moderation', 'ai_analysis'
    step_label: str         # Human-readable step label (e.g. 'Grade Q1 Script #2')
    model: str              # Vertex AI model name
    prompt_tokens: int      # Input tokens
    candidate_tokens: int   # Output tokens
    total_tokens: int       # Total tokens
    cached_tokens: int      # Cached tokens billed at reduced rates
    thoughts_tokens: int    # Model thinking tokens
    images_count: int       # Number of generated images (0 or 1)
    latency_ms: float       # API roundtrip latency in milliseconds
    cost_usd: Optional[float]  # Estimate; None means unknown usage, not free
    cached_call: bool       # True if served from local cache
```

---

## 4. Vertex AI Pricing Model

Fallback estimates configured in [`src/handwriting_grader/telemetry.py`](../src/handwriting_grader/telemetry.py)
may be outdated. Verify current provider prices and use explicit configuration
rates; the table below is not a current pricing attestation:

| Model Family | Input / 1M Tokens | Output / 1M Tokens | Cached / 1M Tokens | Role |
| :--- | :--- | :--- | :--- | :--- |
| `gemini-3.8-flash` | $0.075 | $0.300 | $0.01875 | High-Throughput (OCR, Grading, Marking Scheme, Annotations) |
| `gemini-4-argon` | $1.250 | $5.000 | $0.31250 | Frontier Reasoning (Moderation, Misconception Analytics) |

Calculation Formula:
$$\text{Cost} = \left(\frac{\text{Prompt} - \text{Cached}}{10^6} \times \text{Rate}_{\text{in}}\right) + \left(\frac{\text{Cached}}{10^6} \times \text{Rate}_{\text{cached}}\right) + \left(\frac{\text{Candidates}}{10^6} \times \text{Rate}_{\text{out}}\right) + (\text{Images} \times \text{Rate}_{\text{img}})$$

---

## 5. Live Progress Conventions

Progress updates are emitted in real time:

```text
[ADK Progress] [grading_script] (3/10 - 30.0%) ── Q1 Script #3 ── 1,420 in / 210 out ── $0.00017 ── 1.2s
```

Components:
- `[ADK Progress]`: Machine-parseable prefix.
- `[phase]`: Current execution stage.
- `(current/total - percent%)`: Sub-step counter and completion percentage.
- `item_label`: Script, question, or analysis identifier.
- `tokens`: Input / output token counts (with cached token callouts).
- `cost`: Incremental cost for that specific API request.
- `latency`: Roundtrip execution duration in seconds.

---

## 6. Audit Reports: `cost_report.xlsx` and `cost_summary.json`

Generated at the end of `grade`, `ai-analysis`, or anytime via:
```bash
handwriting-grader cost-report --exam /path/to/exam.yaml [--run CHILD_RUN]
```

### 6.1 `cost_report.xlsx` Sheets:
1. **Summary**: Recorded usage and estimated priced subtotals grouped by phase,
   with a separate count of unpriced calls.
2. **API Audit Log**: Recorded calls with token metrics, latency and cache status;
   missing usage produces `UNPRICED`, not a zero-dollar call.
3. **Billing Notes**: Limitations on unknown usage, retry accounting and estimates.

### 6.2 `cost_summary.json`:
JSON estimates are suitable for local dashboards, not authoritative billing.
`unpriced_calls` counts records excluded from priced subtotals; native embedding
responses without token usage retain `cost_usd: null` in the journal.
```json
{
  "total_calls": 24,
  "total_prompt_tokens": 31200,
  "total_candidate_tokens": 4850,
  "total_cached_tokens": 0,
  "total_images": 6,
  "total_cost_usd": 0.00842,
  "by_phase": {
    "ocr": { "calls": 12, "total_tokens": 3360, "cost_usd": 0.00035 },
    "grading": { "calls": 8, "total_tokens": 11200, "cost_usd": 0.00192 },
    "moderation": { "calls": 2, "total_tokens": 4900, "cost_usd": 0.00615 }
  }
}
```
