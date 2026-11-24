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

    # Reject a single outlier quadrant (e.g. heavy handwriting/diagram) if the other 3 agree
    dx_all = np.array([p[2] for p in pts], dtype=np.float64)
    dy_all = np.array([p[3] for p in pts], dtype=np.float64)
    med_dx = float(np.median(dx_all))
    med_dy = float(np.median(dy_all))
    dists = np.hypot(dx_all - med_dx, dy_all - med_dy)
    inliers = dists <= 15.0
    if int(np.sum(inliers)) >= 3:
        weights = np.where(inliers, weights, 0.0)
        if float(weights.sum()) <= 0.0:
            return 0.0, 0.0, 0.0

    weights /= weights.sum()

    px = np.array([p[0] for p in pts], dtype=np.float64)
    py = np.array([p[1] for p in pts], dtype=np.float64)
    dx = dx_all
    dy = dy_all

    tx = float(np.sum(weights * dx))
    ty = float(np.sum(weights * dy))
    denom = float(np.sum(weights * (px ** 2 + py ** 2)))
    theta = float(np.sum(weights * ((dy - ty) * px - (dx - tx) * py)) / denom) if denom > 0.0 else 0.0

    # Different answers can yield unrelated peaks; require a shared rigid motion.
    residuals = np.hypot(dx - (tx - theta * py), dy - (ty + theta * px))
    if np.count_nonzero((weights > 0.0) & (residuals <= 15.0)) < 3:
        return 0.0, 0.0, 0.0

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


def _prepare_ink_map_4x(image: Image.Image) -> np.ndarray:
    """Downsample 4x and extract thresholded ink intensity for fast orientation/sequence checks."""
    w, h = image.size
    small = image.resize((max(1, w // 4), max(1, h // 4)), Image.Resampling.BILINEAR).convert("L")
    arr = 255.0 - np.asarray(small, dtype=np.float32)
    arr[arr < 35.0] = 0.0
    return arr


def _max_phase_score(a: np.ndarray, b: np.ndarray) -> float:
    """Return peak FFT phase correlation score between two same-sized 2D arrays."""
    h, w = a.shape
    if h < 16 or w < 16 or float(a.max()) <= 0.0 or float(b.max()) <= 0.0:
        return 0.0
    wy = np.hanning(h)[:, None]
    wx = np.hanning(w)[None, :]
    win = wy * wx
    fa = np.fft.rfft2(a * win)
    fb = np.fft.rfft2(b * win)
    cross_power = fa * np.conj(fb)
    cross_power /= np.maximum(np.abs(cross_power), 1e-8)
    cc = np.fft.irfft2(cross_power, s=a.shape)
    return float(np.max(cc))


def _find_consensus_references(
    page_paths: list[Path],
    pages_per_student: int,
) -> tuple[list[tuple[np.ndarray, tuple[int, int]] | None], list[str]]:
    """Select majority-consensus upright reference ink maps for each page offset in a script."""
    num_scripts = len(page_paths) // pages_per_student
    sample_indices = (
        sorted({0, num_scripts // 4, num_scripts // 2, (3 * num_scripts) // 4, num_scripts - 1})
        if num_scripts >= 5
        else list(range(num_scripts))
    )

    # Determine majority page dimensions overall (or per offset)
    refs: list[tuple[np.ndarray, tuple[int, int]] | None] = []
    early_issues: list[str] = []

    for offset in range(pages_per_student):
        candidates: list[tuple[int, np.ndarray, tuple[int, int]]] = []
        size_counts: dict[tuple[int, int], int] = {}
        for si in sample_indices:
            p = page_paths[si * pages_per_student + offset]
            with Image.open(p) as im:
                size_counts[im.size] = size_counts.get(im.size, 0) + 1
        if not size_counts:
            refs.append(None)
            continue
        majority_size = max(size_counts, key=lambda s: size_counts[s])

        for si in sample_indices:
            p = page_paths[si * pages_per_student + offset]
            with Image.open(p) as im:
                if im.size != majority_size:
                    continue
                arr = _prepare_ink_map_4x(im)
                if float(arr.max()) > 0.0:
                    candidates.append((si, arr, im.size))

        if not candidates:
            refs.append(None)
            continue
        if len(candidates) == 1:
            refs.append((candidates[0][1], candidates[0][2]))
            continue

        # Score each candidate by how many other candidates match it at 0 deg (vs 180 deg)
        best_cand = candidates[0]
        best_votes = -1.0
        for i, (_, arr_i, size_i) in enumerate(candidates):
            votes = 0.0
            for j, (_, arr_j, _) in enumerate(candidates):
                if i == j:
                    continue
                s0 = _max_phase_score(arr_i, arr_j)
                s180 = _max_phase_score(arr_i, arr_j[::-1, ::-1])
                if s0 >= s180 * 1.1:
                    votes += 1.0 + s0
            if votes > best_votes:
                best_votes = votes
                best_cand = candidates[i]
        refs.append((best_cand[1], best_cand[2]))

    return refs, early_issues


def check_scan_orientation_and_sequence(
    page_paths: list[Path],
    regions_root: Path | None,
    pages_per_student: int,
) -> list[str]:
    """Verify every scanned page matches expected orientation (0 deg) and template sequence.

    If regions_root is provided and has images, uses those as references; otherwise uses
    majority-consensus references across the PDF so an upside-down or misordered first
    script is detected rather than treated as ground truth.
    """
    if regions_root is not None and (regions_root / "images" / "0.jpg").is_file():
        refs_4x: list[tuple[np.ndarray, tuple[int, int]] | None] = []
        for i in range(pages_per_student):
            with Image.open(regions_root / "images" / f"{i}.jpg") as im:
                refs_4x.append((_prepare_ink_map_4x(im), im.size))
    else:
        refs_4x, _ = _find_consensus_references(page_paths, pages_per_student)

    issues: list[str] = []
    for idx, page_path in enumerate(page_paths):
        script_num = (idx // pages_per_student) + 1
        expected = idx % pages_per_student
        pdf_page = idx + 1
        ref_entry = refs_4x[expected]
        if ref_entry is None:
            continue
        ref_arr, (ref_w, ref_h) = ref_entry

        with Image.open(page_path) as im:
            w, h = im.size
            if (w, h) == (ref_h, ref_w) and ref_w != ref_h:
                issues.append(
                    f"PDF page {pdf_page} (Script #{script_num}, page {expected + 1}): "
                    f"appears rotated 90°/270° ({w}x{h} vs expected {ref_w}x{ref_h}). Fix orientation manually."
                )
                continue
            if (w, h) != (ref_w, ref_h):
                issues.append(
                    f"PDF page {pdf_page} (Script #{script_num}, page {expected + 1}): "
                    f"dimensions {w}x{h} do not match expected {ref_w}x{ref_h}."
                )
                continue
            if float(ref_arr.max()) <= 0.0:
                continue
            cur = _prepare_ink_map_4x(im)

        if float(cur.max()) <= 0.0:
            issues.append(
                f"PDF page {pdf_page} (Script #{script_num}, page {expected + 1}): "
                "page appears completely blank. Check physical scan."
            )
            continue

        s_exp = _max_phase_score(ref_arr, cur)
        cur_180 = cur[::-1, ::-1]
        s_180 = _max_phase_score(ref_arr, cur_180)

        best_other_idx = None
        best_other_score = 0.0
        best_other_flipped = False
        for other in range(pages_per_student):
            if other == expected:
                continue
            other_entry = refs_4x[other]
            if other_entry is None:
                continue
            o_arr, o_size = other_entry
            if o_size != (w, h) or float(o_arr.max()) <= 0.0:
                continue
            s0 = _max_phase_score(o_arr, cur)
            s180 = _max_phase_score(o_arr, cur_180)
            if s0 > best_other_score:
                best_other_score = s0
                best_other_idx = other
                best_other_flipped = False
            if s180 > best_other_score:
                best_other_score = s180
                best_other_idx = other
                best_other_flipped = True

        if s_180 > s_exp * 1.15 and s_180 >= best_other_score:
            issues.append(
                f"PDF page {pdf_page} (Script #{script_num}, page {expected + 1}): "
                "appears upside-down (180° rotated). Fix orientation manually."
            )
        elif best_other_idx is not None and best_other_score > s_exp * 1.15:
            flip_note = " and upside-down (180° rotated)" if best_other_flipped else ""
            issues.append(
                f"PDF page {pdf_page} (Script #{script_num}, page {expected + 1}): "
                f"out of sequence — matches template page {best_other_idx + 1}{flip_note} "
                f"instead of page {expected + 1}. Fix page order manually."
            )

    return issues
