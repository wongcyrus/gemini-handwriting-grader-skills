"""Live progress and estimated token/cost telemetry for Gemini calls."""

import json
import os
import sys
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Fallback Vertex AI Gemini on-demand pricing (USD per 1,000,000 tokens / per image).
# Prefer live rates cached at ~/.cache/gemini-handwriting-grader/pricing.json or `pricing:` in config.yaml.
DEFAULT_RATES: Dict[str, Dict[str, float]] = {
    "gemini-3.8-flash": {"input": 0.075, "output": 0.30, "cached": 0.01875, "image": 0.0},
    "gemini-4-argon": {"input": 1.25, "output": 5.00, "cached": 0.3125, "image": 0.0},
    "gemini-3-pro-image": {"input": 1.25, "output": 5.00, "cached": 0.3125, "image": 0.06},
    "gemini-3.1-flash-image": {"input": 0.075, "output": 0.30, "cached": 0.01875, "image": 0.039},
    "gemini-3.1-flash-lite-image": {"input": 0.075, "output": 0.30, "cached": 0.01875, "image": 0.02},
}


def load_pricing_rates(config_path: Optional[Path] = None) -> Dict[str, Dict[str, float]]:
    """Load model rates from fallback defaults, ~/.cache/gemini-handwriting-grader/pricing.json, and config.yaml."""
    rates: Dict[str, Dict[str, float]] = {k: dict(v) for k, v in DEFAULT_RATES.items()}
    cache_file = Path.home() / ".cache" / "gemini-handwriting-grader" / "pricing.json"
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if isinstance(cached, dict):
                for m, vals in cached.items():
                    if isinstance(vals, dict):
                        rates[m] = {k: float(v) for k, v in vals.items()}
        except (OSError, ValueError, TypeError):
            pass
    if config_path and Path(config_path).exists():
        try:
            import yaml
            cfg = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
            custom = cfg.get("pricing") if isinstance(cfg, dict) else None
            if isinstance(custom, dict):
                for m, vals in custom.items():
                    if isinstance(vals, dict):
                        rates[m] = {k: float(v) for k, v in vals.items()}
        except (OSError, ValueError, TypeError):
            pass
    return rates


@dataclass
class CallTrace:
    timestamp: str
    phase: str
    step_label: str
    model: str
    prompt_tokens: int
    candidate_tokens: int
    total_tokens: int
    cached_tokens: int
    thoughts_tokens: int
    images_count: int
    latency_ms: float
    cost_usd: Optional[float]
    cached_call: bool = False


class TelemetryTracker:
    def __init__(self, log_dir: Optional[Path] = None, config_path: Optional[Path] = None):
        self.log_dir = Path(log_dir) if log_dir else None
        self.rates_table = load_pricing_rates(config_path)
        self.traces: List[CallTrace] = []
        self._lock = threading.Lock()

    def calculate_cost(
        self,
        model: str,
        prompt_tokens: int,
        candidate_tokens: int,
        cached_tokens: int,
        images_count: int = 0,
    ) -> float:
        # Match model family prefixes against live/cached/configured rates
        rates = self.rates_table.get(model)
        if not rates:
            for k, v in self.rates_table.items():
                if model.startswith(k) or k in model:
                    rates = v
                    break
        if not rates:
            if "flash" in model.lower():
                rates = {"input": 0.075, "output": 0.30, "cached": 0.01875, "image": 0.0}
            elif any(r in model.lower() for r in ["gemini-4", "argon", "pro"]):
                rates = {"input": 1.25, "output": 5.00, "cached": 0.3125, "image": 0.0}
            else:
                rates = {"input": 0.075, "output": 0.30, "cached": 0.01875, "image": 0.0}

        non_cached_prompt = max(0, prompt_tokens - cached_tokens)
        prompt_cost = (non_cached_prompt / 1_000_000.0) * rates.get("input", 0.0)
        cached_cost = (cached_tokens / 1_000_000.0) * rates.get("cached", 0.0)
        candidate_cost = (candidate_tokens / 1_000_000.0) * rates.get("output", 0.0)
        image_cost = images_count * rates.get("image", 0.0)
        return round(prompt_cost + cached_cost + candidate_cost + image_cost, 6)

    def record_call(
        self,
        phase: str,
        step_label: str,
        model: str,
        usage_metadata: Any,
        duration_sec: float,
        cached_call: bool = False,
        images_count: int = 0,
    ) -> CallTrace:
        prompt_tokens = getattr(usage_metadata, "prompt_token_count", 0) or 0
        candidate_tokens = getattr(usage_metadata, "candidates_token_count", 0) or 0
        total_tokens = getattr(usage_metadata, "total_token_count", 0) or (prompt_tokens + candidate_tokens)
        cached_tokens = getattr(usage_metadata, "cached_content_token_count", 0) or 0
        thoughts_tokens = getattr(usage_metadata, "thoughts_token_count", 0) or 0

        usage_known = any(getattr(usage_metadata, key, None) is not None for key in
                          ("prompt_token_count", "candidates_token_count", "total_token_count"))
        cost = (self.calculate_cost(model, prompt_tokens, candidate_tokens, cached_tokens, images_count)
                if usage_known else (0.0 if cached_call else None))
        trace = CallTrace(
            timestamp=datetime.now(timezone.utc).isoformat(),
            phase=phase,
            step_label=step_label,
            model=model,
            prompt_tokens=prompt_tokens,
            candidate_tokens=candidate_tokens,
            total_tokens=total_tokens,
            cached_tokens=cached_tokens,
            thoughts_tokens=thoughts_tokens,
            images_count=images_count,
            latency_ms=round(duration_sec * 1000.0, 1),
            cost_usd=cost,
            cached_call=cached_call,
        )
        with self._lock:
            self.traces.append(trace)
            if self.log_dir:
                try:
                    self.log_dir.mkdir(parents=True, exist_ok=True)
                    log_file = self.log_dir / "gemini_usage.jsonl"
                    with log_file.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(asdict(trace), ensure_ascii=False) + "\n")
                except OSError:
                    pass

        return trace

    def load_existing_log(self, log_path: Path):
        path = Path(log_path)
        if not path.is_file():
            return
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    trace = CallTrace(**data)
                    self.traces.append(trace)
                except Exception:
                    continue

    def get_summary(self) -> Dict[str, Any]:
        total_p = sum(t.prompt_tokens for t in self.traces)
        total_c = sum(t.candidate_tokens for t in self.traces)
        total_cached = sum(t.cached_tokens for t in self.traces)
        total_imgs = sum(t.images_count for t in self.traces)
        total_cost = sum(t.cost_usd or 0.0 for t in self.traces)

        by_phase: Dict[str, Dict[str, Any]] = {}
        by_model: Dict[str, Dict[str, Any]] = {}

        for t in self.traces:
            if t.phase not in by_phase:
                by_phase[t.phase] = {"calls": 0, "total_tokens": 0, "cost_usd": 0.0, "unpriced_calls": 0}
            by_phase[t.phase]["calls"] += 1
            by_phase[t.phase]["total_tokens"] += t.total_tokens
            by_phase[t.phase]["cost_usd"] = round(by_phase[t.phase]["cost_usd"] + (t.cost_usd or 0.0), 5)
            by_phase[t.phase]["unpriced_calls"] += t.cost_usd is None

            if t.model not in by_model:
                by_model[t.model] = {"calls": 0, "total_tokens": 0, "cost_usd": 0.0, "unpriced_calls": 0}
            by_model[t.model]["calls"] += 1
            by_model[t.model]["total_tokens"] += t.total_tokens
            by_model[t.model]["cost_usd"] = round(by_model[t.model]["cost_usd"] + (t.cost_usd or 0.0), 5)
            by_model[t.model]["unpriced_calls"] += t.cost_usd is None

        return {
            "total_calls": len(self.traces),
            "total_input_tokens": total_p,
            "total_output_tokens": total_c,
            "total_cached_tokens": total_cached,
            "total_images": total_imgs,
            "total_cost_usd": round(total_cost, 5),
            "unpriced_calls": sum(t.cost_usd is None for t in self.traces),
            "by_phase": by_phase,
            "by_model": by_model,
        }

    def show_progress(
        self,
        phase: str,
        current: int,
        total: int,
        item_label: str,
        trace: Optional[CallTrace] = None,
        duration_sec: Optional[float] = None,
        extra_note: str = "",
    ) -> str:
        percent = (current / total * 100.0) if total > 0 else 100.0
        header = f"[ADK Progress] [{phase}] ({current}/{total} - {percent:.1f}%)"
        parts = [header, item_label]
        if trace:
            tok_str = f"{trace.prompt_tokens:,} in / {trace.candidate_tokens:,} out"
            if trace.cached_tokens > 0:
                tok_str += f" ({trace.cached_tokens:,} cached)"
            parts.append(tok_str)
            parts.append(f"${trace.cost_usd:.5f}" if trace.cost_usd is not None else "unpriced usage")
        if duration_sec is not None:
            parts.append(f"{duration_sec:.1f}s")
        elif trace and trace.latency_ms:
            parts.append(f"{trace.latency_ms / 1000.0:.1f}s")
        if extra_note:
            parts.append(extra_note)

        line = " ── ".join(parts)
        print(line, flush=True)
        return line

    def write_cost_report(self, target_dir: Path) -> Path:
        import openpyxl
        from openpyxl.styles import Alignment, Font, PatternFill

        target_dir = Path(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        report_path = target_dir / "cost_report.xlsx"

        wb = openpyxl.Workbook()

        header_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
        header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")

        # 1. Summary Sheet
        ws_sum = wb.active
        ws_sum.title = "Summary"
        ws_sum.append(["Phase", "Calls", "Prompt Tokens", "Candidate Tokens", "Cached Tokens", "Images",
                       "Estimated Priced Subtotal (USD)", "Unpriced Calls"])
        for cell in ws_sum[1]:
            cell.fill = header_fill
            cell.font = header_font

        phases: Dict[str, Dict[str, Any]] = {}
        for t in self.traces:
            if t.phase not in phases:
                phases[t.phase] = {"calls": 0, "p": 0, "c": 0, "cached": 0, "img": 0, "cost": 0.0,
                                   "unpriced_calls": 0}
            d = phases[t.phase]
            d["calls"] += 1
            d["p"] += t.prompt_tokens
            d["c"] += t.candidate_tokens
            d["cached"] += t.cached_tokens
            d["img"] += t.images_count
            d["cost"] += t.cost_usd or 0.0
            d["unpriced_calls"] += t.cost_usd is None

        for p_name, data in phases.items():
            ws_sum.append([
                p_name,
                data["calls"],
                data["p"],
                data["c"],
                data["cached"],
                data["img"],
                round(data["cost"], 5),
                data["unpriced_calls"],
            ])

        total_cost = sum(t.cost_usd or 0.0 for t in self.traces)
        unpriced_calls = sum(t.cost_usd is None for t in self.traces)
        total_p = sum(t.prompt_tokens for t in self.traces)
        total_c = sum(t.candidate_tokens for t in self.traces)
        total_cached = sum(t.cached_tokens for t in self.traces)
        total_imgs = sum(t.images_count for t in self.traces)
        ws_sum.append(["TOTAL", len(self.traces), total_p, total_c, total_cached, total_imgs,
                       round(total_cost, 5), unpriced_calls])
        total_row = ws_sum[ws_sum.max_row]
        for cell in total_row:
            cell.font = Font(bold=True)

        # 2. Detailed Journal Sheet
        ws_det = wb.create_sheet("API Audit Log")
        ws_det.append([
            "Timestamp", "Phase", "Step/Item", "Model", "Prompt Tokens",
            "Candidate Tokens", "Cached Tokens", "Thoughts", "Images", "Latency (s)", "Cost (USD)", "Cache Hit"
        ])
        for cell in ws_det[1]:
            cell.fill = header_fill
            cell.font = header_font

        for t in self.traces:
            ws_det.append([
                t.timestamp,
                t.phase,
                t.step_label,
                t.model,
                t.prompt_tokens,
                t.candidate_tokens,
                t.cached_tokens,
                t.thoughts_tokens,
                t.images_count,
                round(t.latency_ms / 1000.0, 2),
                round(t.cost_usd, 5) if t.cost_usd is not None else "UNPRICED",
                "YES" if t.cached_call else "NO",
            ])

        notes = wb.create_sheet("Billing Notes")
        notes.append(["These are estimates for recorded calls, not provider invoices."])
        notes.append(["UNPRICED means usage is unknown, not free; priced subtotals exclude these calls."])
        notes.append(["Unlogged failures and SDK retry attempts may still be billed. Verify current rates."])
        for ws in (ws_sum, ws_det, notes):
            for col in ws.columns:
                max_len = max(len(str(cell.value or "")) for cell in col)
                col_letter = openpyxl.utils.get_column_letter(col[0].column)
                ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

        wb.save(report_path)
        wb.close()

        # Also output JSON summary
        json_summary = {
            "total_calls": len(self.traces),
            "total_prompt_tokens": total_p,
            "total_candidate_tokens": total_c,
            "total_cached_tokens": total_cached,
            "total_images": total_imgs,
            "total_cost_usd": round(total_cost, 5),
            "unpriced_calls": unpriced_calls,
            "phases": phases,
        }
        with (target_dir / "cost_summary.json").open("w", encoding="utf-8") as f:
            json.dump(json_summary, f, ensure_ascii=False, indent=2)

        return report_path

    def flush_jsonl(self, log_dir: Path) -> Path:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        log_file = log_path / "gemini_usage.jsonl"
        with log_file.open("a", encoding="utf-8") as f:
            for trace in self.traces:
                f.write(json.dumps(asdict(trace), ensure_ascii=False) + "\n")
        return log_file


# Global instance
_ACTIVE_TRACKER: Optional[TelemetryTracker] = None


def set_active_tracker(tracker: Optional[TelemetryTracker]) -> None:
    global _ACTIVE_TRACKER
    _ACTIVE_TRACKER = tracker


def get_thinking_config(model: str, *, is_reasoning: bool = True) -> Optional[Any]:
    """Return appropriate thinking configuration for thinking-capable models.
    - Gemini 4+ models (e.g. gemini-4-argon) require thinking_level (MEDIUM/LOW) instead of thinking_budget.
    - Gemini 2.5 / 3.x models (e.g. gemini-3.8-flash) use thinking_budget (-1 for reasoning, 0 for OCR).
    - Legacy models (e.g. Gemini 1.5) return None.
    """
    if not model or "1.5" in model.lower():
        return None
    try:
        from google.genai import types
        m = model.lower()
        if "gemini-4" in m or "-4-" in m:
            level = types.ThinkingLevel.MEDIUM if is_reasoning else types.ThinkingLevel.LOW
            return types.ThinkingConfig(thinking_level=level)
        return types.ThinkingConfig(thinking_budget=-1 if is_reasoning else 0)
    except (ImportError, AttributeError):
        return None


def get_active_tracker() -> Optional[TelemetryTracker]:
    return _ACTIVE_TRACKER


def get_tracker(log_dir: Optional[Path] = None) -> TelemetryTracker:
    global _ACTIVE_TRACKER
    if _ACTIVE_TRACKER is None:
        _ACTIVE_TRACKER = TelemetryTracker(log_dir=log_dir)
    elif log_dir and _ACTIVE_TRACKER.log_dir is None:
        _ACTIVE_TRACKER.log_dir = Path(log_dir)
        _ACTIVE_TRACKER.log_dir.mkdir(parents=True, exist_ok=True)
    return _ACTIVE_TRACKER
