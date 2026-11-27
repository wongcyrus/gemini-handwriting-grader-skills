"""Safe, gated cleanup and archival of grading outputs."""

import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .regions import resolve_path
from .run_context import run_directory


def _cleanup_targets(target_dir: Path, *, preserve_reports: bool, is_child: bool) -> List[Path]:
    if not target_dir.exists():
        return []
    if not preserve_reports:
        return [target_dir] if is_child else sorted(target_dir.iterdir())
    return [sub for sub in sorted(target_dir.iterdir())
            if sub.name.startswith(".grading-") or sub.name == "model-cache"]


def inspect_cleanup_targets(config: dict, config_dir: Path, run_id: Optional[str] = None,
                            *, preserve_reports: bool = True) -> Dict[str, Any]:
    output_root = resolve_path(config_dir, config["exam"]["output_dir"])
    target_dir = run_directory(config, config_dir, run_id) if run_id else output_root

    items: List[Dict[str, Any]] = []
    total_bytes = 0

    if target_dir.exists():
        for sub in sorted(target_dir.iterdir()):
            if sub.is_dir():
                size = sum(f.stat().st_size for f in sub.rglob("*") if f.is_file())
                count = sum(1 for f in sub.rglob("*") if f.is_file())
                items.append({"name": sub.name, "path": str(sub), "type": "directory", "size": size, "files": count})
                total_bytes += size
            elif sub.is_file():
                size = sub.stat().st_size
                items.append({"name": sub.name, "path": str(sub), "type": "file", "size": size, "files": 1})
                total_bytes += size

    return {
        "output_dir": str(target_dir),
        "run_id": run_id or "original",
        "items": items,
        "total_bytes": total_bytes,
        "deletion_targets": [str(path.relative_to(target_dir)) for path in
                             _cleanup_targets(target_dir, preserve_reports=preserve_reports,
                                              is_child=run_id is not None)],
    }


def _is_temporary_data(path: Path, target_dir: Path) -> bool:
    """Return True for temporary grading directories or the local model cache."""
    rel = path.relative_to(target_dir).parts
    if not rel:
        return False
    top = rel[0]
    return top.startswith(".grading-") or top == "model-cache"


def archive_and_clean(
    config: dict,
    config_dir: Path,
    run_id: Optional[str] = None,
    archive: bool = True,
    preserve_reports: bool = True,
    full_archive: bool = False,
) -> Dict[str, Any]:
    output_root = resolve_path(config_dir, config["exam"]["output_dir"])
    target_dir = run_directory(config, config_dir, run_id) if run_id else output_root

    if not target_dir.exists():
        return {"status": "nothing_to_clean", "deleted_items": [], "archive_path": None}

    include_temporary_data = full_archive or not preserve_reports
    archive_path = None
    if archive:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        mode_tag = run_id or ("all" if include_temporary_data else "slim")
        archive_name = f"grading_archive_{mode_tag}_{ts}.zip"
        archive_path = output_root.parent / archive_name
        with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED) as zipf:
            for file_path in target_dir.rglob("*"):
                if file_path.is_file():
                    if not include_temporary_data and _is_temporary_data(file_path, target_dir):
                        continue
                    arcname = file_path.relative_to(target_dir.parent)
                    zipf.write(file_path, arcname=arcname)

    deleted_items: List[str] = []
    for sub in _cleanup_targets(target_dir, preserve_reports=preserve_reports, is_child=run_id is not None):
        deleted_items.append(str(sub.relative_to(target_dir)))
        shutil.rmtree(sub) if sub.is_dir() else sub.unlink()

    return {
        "status": "cleaned",
        "archive_path": str(archive_path) if archive_path else None,
        "deleted_items": deleted_items,
        "preserved_reports": preserve_reports,
    }
