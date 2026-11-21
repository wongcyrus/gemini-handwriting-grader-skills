"""Local rigid-body page alignment to correct scanner translation and skew."""

import math
from pathlib import Path

import numpy as np
from PIL import Image


def _prepare_ink_map(image: Image.Image) -> np.ndarray:
    """Downsample 2x and extract thresholded ink intensity for registration."""
    w, h = image.size
    small_w = max(1, w // 2)
    small_h = max(1, h // 2)
    small = image.resize((small_w, small_h), Image.Resampling.BILINEAR).convert("L")
    arr = 255.0 - np.asarray(small, dtype=np.float32)
    arr[arr < 35.0] = 0.0
    return arr


def _phase_correlation(ref_patch: np.ndarray, cur_patch: np.ndarray) -> tuple[float, float, float]:
    """Return (dx, dy, peak_score) in full-resolution pixels using windowed FFT phase correlation."""
    h, w = ref_patch.shape
    if h < 16 or w < 16 or float(ref_patch.max()) <= 0.0 or float(cur_patch.max()) <= 0.0:
        return 0.0, 0.0, 0.0
    wy = np.hanning(h)[:, None]
    wx = np.hanning(w)[None, :]
    win = wy * wx
    fa = np.fft.rfft2(ref_patch * win)
    fb = np.fft.rfft2(cur_patch * win)
    cross_power = fa * np.conj(fb)
    cross_power /= np.maximum(np.abs(cross_power), 1e-8)
    cc = np.fft.irfft2(cross_power, s=ref_patch.shape)
    iy, ix = np.unravel_index(np.argmax(cc), cc.shape)
    dy = iy if iy <= h // 2 else iy - h
    dx = ix if ix <= w // 2 else ix - w
    return float(dx * 2), float(dy * 2), float(cc[iy, ix])


def estimate_rigid_transform(
    ref_arr: np.ndarray,
    cur_arr: np.ndarray,
    full_w: int,
    full_h: int,
    *,
    max_shift_px: float = 150.0,
    max_angle_deg: float = 5.0,
) -> tuple[float, float, float]:
    """Estimate (tx, ty, theta_rad) to align cur_arr onto ref_arr via 4-quadrant phase correlation."""
    h, w = ref_arr.shape
    if ref_arr.shape != cur_arr.shape or h < 32 or w < 32:
        return 0.0, 0.0, 0.0
    if float(ref_arr.max()) <= 0.0 or float(cur_arr.max()) <= 0.0:
        return 0.0, 0.0, 0.0

    quads = (
        (0, h // 2, 0, w // 2, full_w * 0.25, full_h * 0.25),
        (0, h // 2, w // 2, w, full_w * 0.75, full_h * 0.25),
        (h // 2, h, 0, w // 2, full_w * 0.25, full_h * 0.75),
        (h // 2, h, w // 2, w, full_w * 0.75, full_h * 0.75),
    )
    pts = []
    for y0, y1, x0, x1, cx, cy in quads:
        dx, dy, score = _phase_correlation(ref_arr[y0:y1, x0:x1], cur_arr[y0:y1, x0:x1])
        pts.append((cx - full_w / 2.0, cy - full_h / 2.0, dx, dy, score))

    weights = np.array([max(p[4], 0.0) for p in pts], dtype=np.float64)
    if float(weights.sum()) < 0.15:
        return 0.0, 0.0, 0.0
    weights /= weights.sum()

    px = np.array([p[0] for p in pts], dtype=np.float64)
    py = np.array([p[1] for p in pts], dtype=np.float64)
    dx = np.array([p[2] for p in pts], dtype=np.float64)
    dy = np.array([p[3] for p in pts], dtype=np.float64)

    tx = float(np.sum(weights * dx))
    ty = float(np.sum(weights * dy))
    denom = float(np.sum(weights * (px ** 2 + py ** 2)))
    theta = float(np.sum(weights * ((dy - ty) * px - (dx - tx) * py)) / denom) if denom > 0.0 else 0.0

    if abs(tx) > max_shift_px or abs(ty) > max_shift_px or abs(math.degrees(theta)) > max_angle_deg:
        return 0.0, 0.0, 0.0
    return tx, ty, theta


def warp_image_to_reference(image: Image.Image, tx: float, ty: float, theta: float) -> Image.Image:
    """Apply inverse rigid transform (tx, ty, theta) to align image onto the reference frame."""
    w, h = image.size
    cx, cy = w / 2.0, h / 2.0
    cos_t = math.cos(-theta)
    sin_t = math.sin(-theta)
    a = cos_t
    b = -sin_t
    c = cx - a * (cx + tx) - b * (cy + ty)
    d = sin_t
    e = cos_t
    f = cy - d * (cx + tx) - e * (cy + ty)
    fillcolor = (255, 255, 255) if image.mode == "RGB" else 255
    return image.transform(
        image.size,
        Image.Transform.AFFINE,
        (a, b, c, d, e, f),
        resample=Image.Resampling.BICUBIC,
        fillcolor=fillcolor,
    )


def align_page_file(
    target_path: Path,
    ref_arr: np.ndarray,
    ref_size: tuple[int, int],
    output_path: Path | None = None,
) -> tuple[bool, tuple[float, float, float]]:
    """Align a scanned page JPEG at target_path to ref_arr; return (modified, (tx, ty, theta))."""
    with Image.open(target_path) as image:
        if image.size != ref_size:
            return False, (0.0, 0.0, 0.0)
        cur_arr = _prepare_ink_map(image)
        tx, ty, theta = estimate_rigid_transform(ref_arr, cur_arr, ref_size[0], ref_size[1])
        if abs(tx) < 0.5 and abs(ty) < 0.5 and abs(math.degrees(theta)) < 0.02:
            if output_path is not None and output_path != target_path:
                output_path.write_bytes(target_path.read_bytes())
            return False, (0.0, 0.0, 0.0)
        aligned = warp_image_to_reference(image, tx, ty, theta)
        if abs(math.degrees(theta)) >= 0.1:
            rtx, rty, rth = estimate_rigid_transform(ref_arr, _prepare_ink_map(aligned), ref_size[0], ref_size[1])
            if abs(rtx) >= 0.5 or abs(rty) >= 0.5 or abs(math.degrees(rth)) >= 0.02:
                aligned = warp_image_to_reference(aligned, rtx, rty, rth)
        dest = output_path or target_path
        aligned.save(dest, format="JPEG", quality=95)
        return True, (tx, ty, theta)


def prepare_reference_maps(regions_root: Path, page_count: int) -> list[tuple[np.ndarray, tuple[int, int]]]:
    """Load and precompute ink maps for each template page in regions/images/."""
    refs = []
    for page_index in range(page_count):
        with Image.open(regions_root / "images" / f"{page_index}.jpg") as image:
            refs.append((_prepare_ink_map(image), image.size))
    return refs
