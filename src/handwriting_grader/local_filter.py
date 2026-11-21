"""Cross-platform local blank-cell pre-filter using Pillow (with safe fallback)."""

import io
from PIL import Image


def is_blank_cell(image_bytes: bytes, ink_threshold: int = 160, max_ink_ratio: float = 0.0015) -> bool:
    """Return True if an answer crop is clearly blank; return False on any doubt or error.

    Uses only Pillow so it runs identically on Windows, macOS, and Linux without GPU or OpenCV.
    Crops an inset margin to ignore printed table borders, then checks dark-pixel density.
    """
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            gray = img.convert("L")
            w, h = gray.size
            if w < 16 or h < 16:
                return False
            pad_x = min(4, max(2, w // 30))
            pad_y = min(4, max(2, h // 20))
            if w - 2 * pad_x <= 4 or h - 2 * pad_y <= 4:
                return False
            inner = gray.crop((pad_x, pad_y, w - pad_x, h - pad_y))
            pixels = getattr(inner, "get_flattened_data", inner.getdata)()
            total = len(pixels)
            if total == 0:
                return False
            dark = sum(1 for p in pixels if p < ink_threshold)
            return (dark / total) < max_ink_ratio
    except Exception:
        # Fallback cleanly to remote AI grading if local check is unsupported or fails
        return False
