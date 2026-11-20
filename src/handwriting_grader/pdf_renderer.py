"""Cross-platform PDF page rendering using pypdfium2 with pdftoppm fallback."""

import shutil
import subprocess
from pathlib import Path
from typing import List, Optional
from PIL import Image


def is_pypdfium2_available() -> bool:
    try:
        import pypdfium2  # noqa: F401
        return True
    except ImportError:
        return False


def render_pdf_to_images(
    pdf_path: Path,
    output_dir: Path,
    prefix: str = "page",
    dpi: int = 200,
    first_page: int = 1,
    last_page: Optional[int] = None,
) -> List[Path]:
    """Renders PDF pages to JPEG images.

    Uses pypdfium2 (pure Python / Google PDFium) by default.
    Falls back to pdftoppm if pypdfium2 is not installed.
    """
    pdf_path = Path(pdf_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if is_pypdfium2_available():
        import pypdfium2 as pdfium

        doc = pdfium.PdfDocument(str(pdf_path))
        total_in_doc = len(doc)
        start_idx = max(0, first_page - 1)
        end_idx = total_in_doc if last_page is None else min(total_in_doc, last_page)

        rendered_paths: List[Path] = []
        scale = dpi / 72.0
        for idx in range(start_idx, end_idx):
            page_num = idx + 1
            page = doc[idx]
            pil_image = page.render(scale=scale).to_pil()
            # Ensure RGB
            if pil_image.mode != "RGB":
                pil_image = pil_image.convert("RGB")
            out_file = output_dir / f"{prefix}-{page_num:02d}.jpg"
            pil_image.save(out_file, "JPEG", quality=92)
            rendered_paths.append(out_file)

        return rendered_paths

    # Fallback to system pdftoppm if pypdfium2 is not present
    renderer = shutil.which("pdftoppm")
    if not renderer:
        raise ValueError("Neither pypdfium2 nor pdftoppm is available. Install pypdfium2 with 'pip install pypdfium2'.")

    cmd = [renderer, "-r", str(dpi), "-jpeg", "-f", str(first_page)]
    if last_page is not None:
        cmd.extend(["-l", str(last_page)])
    cmd.extend([str(pdf_path), str(output_dir / prefix)])

    result = subprocess.run(cmd, capture_output=True, timeout=600, check=False)
    if result.returncode:
        raise ValueError(f"PDF page rendering failed: {result.stderr.decode('utf-8', errors='replace')}")

    rendered = sorted(
        output_dir.glob(f"{prefix}-*.jpg"),
        key=lambda p: int(p.stem.removeprefix(f"{prefix}-")),
    )
    return rendered
