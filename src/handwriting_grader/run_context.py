"""Resolve isolated grading runs without changing the exam configuration."""

import re

from .regions import resolve_path


def run_directory(config, config_dir, run_id=None):
    root = resolve_path(config_dir, config["exam"]["output_dir"])
    if run_id is None:
        return root
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", run_id):
        raise ValueError("Run ID must contain only letters, digits, hyphens or underscores")
    runs = root / "runs"
    target = runs / run_id
    if runs.resolve().parent != root or target.resolve().parent != runs.resolve():
        raise ValueError("Run directory cannot escape the exam output")
    return target
