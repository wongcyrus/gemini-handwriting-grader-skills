"""Online model discovery to detect, profile cost/speed, and assign best-fit models per step."""

import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from google import genai

from .telemetry import load_pricing_rates


DISALLOWED_MODEL_KEYWORDS: Tuple[str, ...] = (
    "image",
    "imagen",
    "tts",
    "audio",
    "video",
    "veo",
    "live",
    "robotics",
    "computer-use",
    "chirp",
)

SUPPORTED_FLASH_MODELS: Tuple[str, ...] = (
    "gemini-3.8-flash",
)

SUPPORTED_REASONING_MODELS: Tuple[str, ...] = (
    "gemini-4-argon",
    "gemini-4-hydrogen",
)

SUPPORTED_EMBEDDING_MODELS: Tuple[str, ...] = (
    "gemini-embedding-2",
)


def validate_model_eligibility(model: str, agent: Optional[str] = None) -> None:
    """Validate that a model is a Gemini 3.8+ model meeting multimodal, schema, and thinking requirements.

    The handwriting grading pipeline strictly uses native Google Gemini models (version >= 3.8) because:
    1. Multimodal image input support (types.Part.from_bytes) is required for handwritten student scripts.
    2. Native structured JSON output support (response_schema with Pydantic models) is required.
    3. Dynamic thinking budget support (-1) is required for complex reasoning and calibration steps.

    Allowed models by tier:
    - Flash Tier (OCR, grading, rubric): gemini-3.8-flash (or higher)
    - Reasoning Tier (annotation, batch grading, moderation, analytics): gemini-4-argon, gemini-4-hydrogen
    - Embedding Tier (clustering): gemini-embedding-2

    Explicitly disallowed:
    - Any non-Gemini models (Claude, GPT, Llama, Gemma, PaLM, Mistral, text-embedding-*).
    - Any Gemini model older than 3.8 (e.g. Gemini 1.x, 2.0, 2.5, 3.0, 3.5).
    - Dedicated media generation models (Imagen, Veo, Chirp).
    - Audio, TTS, Live, and Robotics variants.
    """
    if not isinstance(model, str) or not model.strip():
        raise ValueError(f"Invalid model configuration: model name must be a non-empty string (agent={agent}).")
    model_name = model.strip().lower()

    # Strictly Gemini-only across the entire pipeline
    if not model_name.startswith("gemini"):
        raise ValueError(
            f"Unsupported model '{model}' for agent '{agent}': "
            f"Only Google Gemini models are supported. "
            f"Non-Gemini models (such as Claude, GPT, Llama, Gemma, or text-embedding-*) are not permitted."
        )

    if agent == "embedding":
        if "embedding" not in model_name:
            raise ValueError(
                f"Invalid model '{model}' for agent 'embedding': "
                f"The semantic embedding step requires a Gemini embedding model (e.g. 'gemini-embedding-2')."
            )
        return

    if agent == "analytics_image":
        ver_img = re.match(r"^gemini-(\d+)(?:\.(\d+))?(?:-|$)", model_name)
        if not ver_img or int(ver_img.group(1)) < 3 or "image" not in model_name:
            raise ValueError(
                f"Invalid model '{model}' for agent 'analytics_image': "
                f"Infographic generation requires a Gemini 3+ image model (e.g. 'gemini-3-pro-image' or 'gemini-3.1-flash-image')."
            )
        return

    for kw in DISALLOWED_MODEL_KEYWORDS:
        if kw in model_name:
            raise ValueError(
                f"Disallowed model '{model}' for agent '{agent}': "
                f"Dedicated media generation, audio, video, or experimental live/robotics models cannot perform multimodal exam grading or structured text/coordinate extraction."
            )

    if "embedding" in model_name or "embed" in model_name:
        raise ValueError(
            f"Disallowed model '{model}' for agent '{agent}': "
            f"Embedding models cannot generate content, extract regions, or grade student scripts."
        )

    # Enforce minimum Gemini version >= 3.8
    ver_match = re.match(r"^gemini-(\d+)(?:\.(\d+))?(?:-|$)", model_name)
    if not ver_match:
        raise ValueError(
            f"Unsupported model '{model}' for agent '{agent}': "
            f"Model must be a versioned Gemini 3.8+ model (e.g. 'gemini-3.8-flash' or 'gemini-4-argon')."
        )
    major = int(ver_match.group(1))
    minor = int(ver_match.group(2) or 0)
    if (major, minor) < (3, 8):
        raise ValueError(
            f"Unsupported model '{model}' for agent '{agent}': "
            f"Models older than Gemini 3.8 are not supported. "
            f"Please use Gemini 3.8+ models: Flash tier ('gemini-3.8-flash') or Reasoning tier ('gemini-4-argon')."
        )


def is_eligible_model(model: str, tier: Optional[str] = None) -> bool:
    """Check if model is eligible for a given tier without raising an exception."""
    try:
        validate_model_eligibility(model, agent="grading" if tier == "flash" else ("analytics" if tier == "reasoning" else None))
        return True
    except ValueError:
        return False


STEP_REQUIREMENTS: Dict[str, Dict[str, str]] = {
    "annotation": {
        "tier": "reasoning",
        "description": "Bounding-box proposal on layout template pages",
        "rationale": "Low-volume (template pages only), high-precision spatial reasoning; Gemini 4 prevents missed sub-questions and misaligned boxes.",
    },
    "marking_scheme": {
        "tier": "flash",
        "description": "DOCX answer key extraction into structured rubric",
        "rationale": "Direct structured text parsing; Flash extracts questions and marks rapidly at minimal cost.",
    },
    "one_pass_metadata_ocr": {
        "tier": "flash",
        "description": "One-Pass batch student ID, Class, and Name OCR across all scripts",
        "rationale": "High-throughput chunked metadata extraction with roster fuzzy matching.",
    },
    "one_pass_grading": {
        "tier": "reasoning",
        "description": "One-Pass batch multimodal question grading across all student scripts",
        "rationale": "Holistic cohort evaluation and self-moderation in structured batch output.",
    },
    "batch_grading": {
        "tier": "reasoning",
        "description": "Alias for one_pass_grading",
        "rationale": "Backward-compatible alias for one-pass question grading.",
    },
    "ocr": {
        "tier": "flash",
        "description": "Fallback sequential Student ID, Name, and Class metadata OCR across all scripts",
        "rationale": "High volume fallback (single-crop sequential OCR).",
    },
    "grading": {
        "tier": "flash",
        "description": "Multimodal question grading against rubric across all student scripts",
        "rationale": "Highest volume stage; Flash provides the optimal accuracy, throughput, and cost-efficiency trade-off.",
    },
    "moderation": {
        "tier": "reasoning",
        "description": "Batch cohort consistency moderation and grade calibration",
        "rationale": "Deep cross-script reasoning and score drift detection; Frontier/Reasoning model provides high calibration.",
    },
    "analytics": {
        "tier": "reasoning",
        "description": "Cohort-wide misconception diagnosis and pedagogical feedback",
        "rationale": "Complex synthesis of student misconceptions and recommendations; benefits from deep reasoning.",
    },
}


def parse_flash_version(name: str) -> Tuple[int, int]:
    match = re.search(r"gemini-(\d+)(?:\.(\d+))?-flash", name)
    if match:
        return (int(match.group(1)), int(match.group(2) or 0))
    return (0, 0)


def parse_reasoning_rank(name: str) -> Tuple[int, int]:
    m4 = re.search(r"gemini-(\d+)-([a-z0-9]+)", name)
    if m4:
        major = int(m4.group(1))
        variant = m4.group(2)
        weight = 10 if variant == "argon" else (8 if variant == "hydrogen" else 5)
        return (major, weight)
    m_pro = re.search(r"gemini-(\d+)(?:\.(\d+))?-pro", name)
    if m_pro:
        return (int(m_pro.group(1)), int(m_pro.group(2) or 0))
    return (0, 0)


def get_model_rates(model_name: str) -> Dict[str, float]:
    table = load_pricing_rates()
    rates = table.get(model_name)
    if rates:
        return rates
    for k, v in table.items():
        if model_name.startswith(k) or k in model_name:
            return v
    return {"input": 0.10, "output": 0.40, "cached": 0.025, "image": 0.0}


def discover_best_tier_models(
    project: str,
    location: str = "global",
    candidate_locations: Optional[List[str]] = None,
    probe: bool = False,
) -> Dict[str, Any]:
    """Query Vertex AI online and discover the best active Flash and Reasoning models."""
    locs = [location]
    if candidate_locations:
        for loc in candidate_locations:
            if loc not in locs:
                locs.append(loc)
    else:
        for fallback in ("global", "us-central1", "us-east4", "europe-west1"):
            if fallback not in locs:
                locs.append(fallback)

    best_flash = None
    best_reasoning = None
    working_location = None
    all_flash_cands = []
    all_reasoning_cands = []

    for loc in locs:
        try:
            client = genai.Client(vertexai=True, project=project, location=loc)
            models_list = [
                m.name.split("/")[-1]
                for m in client.models.list()
                if getattr(m, "name", None)
            ]

            flash_cands = [
                m
                for m in models_list
                if "flash" in m
                and is_eligible_model(m, tier="flash")
            ]
            flash_cands.sort(key=parse_flash_version, reverse=True)

            reasoning_cands = [
                m
                for m in models_list
                if ("gemini-4" in m or "-pro" in m)
                and is_eligible_model(m, tier="reasoning")
            ]
            reasoning_cands.sort(key=parse_reasoning_rank, reverse=True)

            if not flash_cands and not reasoning_cands:
                continue

            all_flash_cands = flash_cands
            all_reasoning_cands = reasoning_cands

            if not probe:
                best_flash = flash_cands[0] if flash_cands else None
                best_reasoning = reasoning_cands[0] if reasoning_cands else best_flash
                working_location = loc
                break

            # Probe Flash candidates
            for cand in flash_cands:
                try:
                    client.models.generate_content(model=cand, contents="ping")
                    best_flash = cand
                    working_location = loc
                    break
                except Exception:
                    continue

            # Probe Reasoning candidates
            for cand in reasoning_cands:
                try:
                    client.models.generate_content(model=cand, contents="ping")
                    best_reasoning = cand
                    working_location = loc
                    break
                except Exception:
                    continue

            if best_flash:
                break
        except Exception:
            continue

    if not best_flash and not best_reasoning:
        raise RuntimeError(f"Could not discover any active Gemini models in project {project} across regions {locs}")

    # Fallback reasoning to flash if no reasoning model is available
    if not best_reasoning:
        best_reasoning = best_flash
    if not best_flash:
        best_flash = best_reasoning

    return {
        "flash": best_flash,
        "reasoning": best_reasoning,
        "location": working_location,
        "all_flash_candidates": all_flash_cands,
        "all_reasoning_candidates": all_reasoning_cands,
    }


def resolve_step_models(tier_models: Dict[str, Any]) -> Dict[str, str]:
    """Assign models per step based on step requirements and tier profiles."""
    step_models = {}
    for step, meta in STEP_REQUIREMENTS.items():
        tier = meta["tier"]
        step_models[step] = tier_models.get(tier, tier_models.get("flash"))
    return step_models


def format_model_cost_summary(
    project: str,
    location: str,
    tier_models: Dict[str, Any],
    step_models: Dict[str, str],
) -> str:
    """Produce a human-readable table comparing tiers, rates, and step allocations."""
    lines = []
    lines.append("=" * 110)
    lines.append(f"ONLINE MODEL DISCOVERY & STEP-BY-STEP COST ALLOCATION (Project: {project}, Location: {location})")
    lines.append("=" * 110)
    lines.append(
        f"{'Pipeline Step':<16} | {'Assigned Model':<18} | {'Tier':<10} | {'Input $/1M':<10} | {'Output $/1M':<11} | {'Rationale'}"
    )
    lines.append("-" * 110)

    for step, model in step_models.items():
        meta = STEP_REQUIREMENTS[step]
        rates = get_model_rates(model)
        tier_label = meta["tier"].upper()
        in_cost = f"${rates['input']:.3f}"
        out_cost = f"${rates['output']:.3f}"
        lines.append(
            f"{step:<16} | {model:<18} | {tier_label:<10} | {in_cost:<10} | {out_cost:<11} | {meta['rationale']}"
        )

    lines.append("-" * 110)
    flash_rates = get_model_rates(tier_models["flash"])
    reasoning_rates = get_model_rates(tier_models["reasoning"])
    cost_diff = reasoning_rates["input"] / flash_rates["input"] if flash_rates["input"] > 0 else 1.0

    lines.append(f"Cost Summary:")
    lines.append(
        f"  * Fast / High-Volume Tier ({tier_models['flash']}): "
        f"Input: ${flash_rates['input']:.3f}/1M tokens, Output: ${flash_rates['output']:.3f}/1M tokens."
    )
    lines.append(
        f"  * Deep Reasoning Tier     ({tier_models['reasoning']}): "
        f"Input: ${reasoning_rates['input']:.3f}/1M tokens, Output: ${reasoning_rates['output']:.3f}/1M tokens "
        f"({cost_diff:.1f}x cost factor, reserved strictly for moderation & analytics)."
    )
    lines.append("=" * 110)
    return "\n".join(lines)


def write_model_config(
    path: Path,
    project: str,
    location: str,
    step_models: Dict[str, str],
) -> Dict[str, Any]:
    """Write a new AI model config without replacing an existing configuration."""
    config_data = {
        "vertex_ai": {
            "use_vertexai": True,
            "project_id": project,
            "location": location,
        },
        "models": {
            **{
                step: step_models.get(
                    step,
                    step_models.get("ocr" if "ocr" in step else ("grading" if "grading" in step else next(iter(step_models.values()))))
                )
                for step in STEP_REQUIREMENTS.keys()
            },
            "analytics_image": step_models.get("analytics_image", "gemini-3-pro-image"),
            "embedding": step_models.get("embedding", "gemini-embedding-2"),
        },
        "caching": {
            "enabled": False,
            "scope": "run",
            "agents": {
                "annotation": True,
                "marking_scheme": True,
                "ocr": True,
                "grading": True,
                "moderation": True,
                "analytics": True,
            },
        },
    }
    # Validate all assigned step models
    for step, assigned in config_data["models"].items():
        validate_model_eligibility(assigned, agent=step)

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as file:
        yaml.safe_dump(config_data, file, sort_keys=False, allow_unicode=True)
    return config_data


def discover_and_report(
    project: str,
    location: str = "global",
    write_config_path: Optional[Path] = None,
    *,
    probe: bool = False,
) -> Dict[str, Any]:
    """Discover best models online by tier, allocate per-step, and optionally write config."""
    print(f"Discovering active models and pricing tiers online for project={project}...")
    tier_models = discover_best_tier_models(project, location, probe=probe)
    working_loc = tier_models["location"]
    step_models = resolve_step_models(tier_models)

    summary_table = format_model_cost_summary(project, working_loc, tier_models, step_models)
    print(summary_table)

    if write_config_path:
        target = Path(write_config_path)
        write_model_config(target, project, working_loc, step_models)
        print(f"\nWrote cost-optimized model configuration to {target}")

    return {
        "tier_models": tier_models,
        "step_models": step_models,
        "location": working_loc,
    }
