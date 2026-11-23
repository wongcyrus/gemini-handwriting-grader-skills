"""Safe, gated cleanup and archival of grading outputs."""

import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .regions import resolve_path
from .run_context import run_directory


def inspect_cleanup_targets(config: dict, config_dir: Path, run_id: Optional[str] = None) -> Dict[str, Any]:
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
    }


def _is_regenerable_bulk(path: Path, target_dir: Path) -> bool:
    """Return True for bulky raw images or temp folders that can be regenerated from source PDFs."""
    rel = path.relative_to(target_dir).parts
    if not rel:
        return False
    top = rel[0]
    if top.startswith(".grading-") or top == "model-cache":
        return True
    if top in {"grading", "regions"} and len(rel) >= 2 and rel[1] == "images":
        return True
    return False


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

    include_bulk_in_zip = full_archive or (not preserve_reports)
    archive_path = None
    if archive:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        mode_tag = run_id or ("all" if include_bulk_in_zip else "slim")
        archive_name = f"grading_archive_{mode_tag}_{ts}.zip"
        archive_path = output_root.parent / archive_name
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zipf:
            for file_path in target_dir.rglob("*"):
                if file_path.is_file():
                    if not include_bulk_in_zip and _is_regenerable_bulk(file_path, target_dir):
                        continue
                    arcname = file_path.relative_to(target_dir.parent)
                    zipf.write(file_path, arcname=arcname)

    deleted_items: List[str] = []
    # If run_id is given, we can delete the child run directory
    if run_id:
        deleted_items.append(str(target_dir))
        shutil.rmtree(target_dir)
    elif not preserve_reports:
        for sub in sorted(target_dir.iterdir()):
            deleted_items.append(sub.name)
            if sub.is_dir():
                shutil.rmtree(sub)
            else:
                sub.unlink()
    else:
        # Smart clean: preserve deliverables + HIL/AI metadata; delete only bulky raw JPEGs & temp dirs
        for sub in sorted(target_dir.iterdir()):
            if sub.name.startswith(".grading-") or sub.name == "model-cache":
                deleted_items.append(sub.name)
                shutil.rmtree(sub) if sub.is_dir() else sub.unlink()
            elif sub.name in {"grading", "regions"} and sub.is_dir():
                img_dir = sub / "images"
                if img_dir.exists() and img_dir.is_dir():
                    deleted_items.append(f"{sub.name}/images")
                    shutil.rmtree(img_dir)

    return {
        "status": "cleaned",
        "archive_path": str(archive_path) if archive_path else None,
        "deleted_items": deleted_items,
        "preserved_reports": preserve_reports and not run_id,
    }
