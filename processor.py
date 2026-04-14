"""
Background removal algorithms for PNG images.
Pure Pillow — no UI imports, safe to run on a worker thread.
"""

import os
from PIL import Image, ImageDraw, ImageChops, ImageFilter

# Sentinel color used as flood-fill target marker.
# Chosen to be visually absurd so it won't appear in real images.
_MARKER = (1, 254, 127)

# Max dimension for the downscaled flood-fill working copy (performance).
_FLOOD_MAX_DIM = 512

# Checker pattern constants (AI-generated "transparent" images often use these)
_CHECKER_DARK = (192, 192, 192)
_CHECKER_LIGHT = (255, 255, 255)

# Tolerances
SOLID_TOLERANCE = 40       # Flood fill: how far from bg color to still fill
ENCLOSED_TOLERANCE = 20    # Global color-match: tight, for clean digital art
CHECKER_TOLERANCE = 35


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _luminance(rgb):
    r, g, b = rgb[0], rgb[1], rgb[2]
    return 0.299 * r + 0.587 * g + 0.114 * b


def _color_distance(c1, c2):
    return max(abs(int(c1[0]) - int(c2[0])),
               abs(int(c1[1]) - int(c2[1])),
               abs(int(c1[2]) - int(c2[2])))


def _make_checker_tile(tile_size, color_a, color_b):
    """Return a 2*tile_size square PIL Image with a 2x2 checker pattern."""
    ts = tile_size
    tile = Image.new('RGB', (ts * 2, ts * 2))
    pixels = []
    for y in range(ts * 2):
        for x in range(ts * 2):
            if (x // ts + y // ts) % 2 == 0:
                pixels.append(color_a)
            else:
                pixels.append(color_b)
    tile.putdata(pixels)
    return tile


def _tile_pattern(w, h, tile_size, color_a, color_b):
    """Fill a (w, h) image by tiling the 2x2 checker pattern."""
    tile = _make_checker_tile(tile_size, color_a, color_b)
    tw, th = tile.size
    canvas = Image.new('RGB', (w, h))
    for y in range(0, h, th):
        for x in range(0, w, tw):
            canvas.paste(tile, (x, y))
    return canvas


def _flood_fill_all_borders(work: Image.Image, pw: int, ph: int,
                             marker: tuple, bg_ref_color: tuple, tolerance: int):
    """
    Flood fill background from every pixel on the image border.

    Only seeds from pixels whose color is close to `bg_ref_color` so we
    don't accidentally eat into character content that touches the edge.
    """
    borders = []
    for x in range(pw):
        borders.append((x, 0))
        borders.append((x, ph - 1))
    for y in range(1, ph - 1):
        borders.append((0, y))
        borders.append((pw - 1, y))

    for seed in borders:
        px = work.getpixel(seed)
        if px != marker and _color_distance(px, bg_ref_color) <= tolerance:
            ImageDraw.floodfill(work, seed, marker, thresh=tolerance)


def _remove_color_globally(rgba: Image.Image, bg_color: tuple,
                            tolerance: int = ENCLOSED_TOLERANCE) -> Image.Image:
    """
    Fast O(w*h) pass: zero the alpha of any pixel whose color is within
    `tolerance` of `bg_color` (max-channel distance).

    This catches enclosed background regions (e.g., the white gap between an
    arm and torso in a black-BG image) that flood fill can never reach because
    they have no path to the image border.

    Tolerance is intentionally tight (default 15) because clean digital/AI art
    has flat, pure background colors — character content won't be this close to
    the bg color unless it truly is background.
    """
    r, g, b, a = rgba.split()
    br, bg_c, bb = bg_color

    # Per-channel absolute difference from bg color
    diff_r = r.point(lambda v: abs(v - br))
    diff_g = g.point(lambda v: abs(v - bg_c))
    diff_b = b.point(lambda v: abs(v - bb))

    # Max channel distance
    max_diff = ImageChops.lighter(ImageChops.lighter(diff_r, diff_g), diff_b)

    # Pixels within tolerance → alpha 0; outside → keep
    color_mask = max_diff.point(lambda v: 0 if v < tolerance else 255, 'L')
    new_alpha = ImageChops.multiply(a, color_mask)
    return Image.merge('RGBA', (r, g, b, new_alpha))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _sample_border(rgb: Image.Image, n_samples: int = 32) -> list:
    """
    Sample `n_samples` evenly-spaced pixels around all 4 edges.
    Returns a list of (r, g, b) tuples.
    """
    w, h = rgb.size
    samples = []
    # Top and bottom edges
    for i in range(n_samples // 4):
        x = int(i * w / (n_samples // 4))
        samples.append(rgb.getpixel((min(x, w - 1), 0)))
        samples.append(rgb.getpixel((min(x, w - 1), h - 1)))
    # Left and right edges
    for i in range(n_samples // 4):
        y = int(i * h / (n_samples // 4))
        samples.append(rgb.getpixel((0, min(y, h - 1))))
        samples.append(rgb.getpixel((w - 1, min(y, h - 1))))
    return samples


def _dominant_border_color(rgb: Image.Image) -> tuple:
    """
    Return the single most-common background color found on the border,
    chosen from candidates: pure black (0,0,0) and pure white (255,255,255).
    Whichever has more border samples within tolerance wins.
    Returns the winning color, or None if neither dominates.
    """
    samples = _sample_border(rgb)
    dark_votes = sum(1 for p in samples if _luminance(p) < 60)
    light_votes = sum(1 for p in samples if _luminance(p) > 200)
    total = len(samples)
    if dark_votes / total >= 0.35:
        # Average the dark samples to get the actual bg color (handles near-black)
        dark_px = [p for p in samples if _luminance(p) < 60]
        r = int(sum(p[0] for p in dark_px) / len(dark_px))
        g = int(sum(p[1] for p in dark_px) / len(dark_px))
        b = int(sum(p[2] for p in dark_px) / len(dark_px))
        return 'black', (r, g, b)
    if light_votes / total >= 0.35:
        light_px = [p for p in samples if _luminance(p) > 200]
        r = int(sum(p[0] for p in light_px) / len(light_px))
        g = int(sum(p[1] for p in light_px) / len(light_px))
        b = int(sum(p[2] for p in light_px) / len(light_px))
        return 'white', (r, g, b)
    return None, None


def classify_background(img: Image.Image) -> tuple:
    """
    Return (bg_type, bg_color) where bg_type is one of:
    'black', 'white', 'checkered', 'unknown'
    and bg_color is the measured average border color (or None).

    Uses a majority-vote over ~32 border samples so portrait images
    (where the subject fills the bottom edge) are still classified correctly.
    Checker check runs first since checker corners are light and would
    otherwise false-positive as white.
    """
    rgb = img.convert('RGB')
    w, h = rgb.size

    # --- Checker detection (unchanged — samples a tile grid, not corners) ---
    tile_size = max(4, min(16, min(w, h) // 16))
    sample_w = min(w, 256)
    sample_h = min(h, 256)
    n_tiles_x = sample_w // tile_size
    n_tiles_y = sample_h // tile_size
    total = 0
    matches = 0
    for ty in range(n_tiles_y):
        for tx in range(n_tiles_x):
            cx = tx * tile_size + tile_size // 2
            cy = ty * tile_size + tile_size // 2
            expected = _CHECKER_DARK if (tx + ty) % 2 == 0 else _CHECKER_LIGHT
            pixel = rgb.getpixel((cx, cy))
            total += 1
            if _color_distance(pixel, expected) < CHECKER_TOLERANCE:
                matches += 1
    if total > 0 and matches / total > 0.55:
        return 'checkered', None

    # --- Solid black / white via border majority vote ---
    bg_type, bg_color = _dominant_border_color(rgb)
    if bg_type:
        return bg_type, bg_color

    return 'unknown', None


def remove_solid_background(img: Image.Image, bg_color: tuple,
                             tolerance: int = SOLID_TOLERANCE) -> Image.Image:
    """
    Remove a solid-color background (black or white).

    Pass 1 — flood fill from all border pixels on a downscaled copy.
              Handles the connected outer background quickly.
    Pass 2 — global color-match with tight tolerance (ENCLOSED_TOLERANCE).
              Handles enclosed regions (gaps inside the character silhouette)
              that are unreachable by any flood fill.

    bg_color: the measured average background color from classify_background().
    """
    rgba = img.convert('RGBA')
    w, h = rgba.size

    # Build a downscaled RGB working copy for speed
    scale = min(1.0, _FLOOD_MAX_DIM / max(w, h))
    pw = max(1, int(w * scale))
    ph = max(1, int(h * scale))
    work = rgba.resize((pw, ph), Image.LANCZOS).convert('RGB')

    _flood_fill_all_borders(work, pw, ph, _MARKER, bg_color, tolerance)

    # Extract sentinel mask
    r_band, g_band, b_band = work.split()
    r_mask = r_band.point(lambda v: 255 if v == _MARKER[0] else 0, 'L')
    g_mask = g_band.point(lambda v: 255 if v == _MARKER[1] else 0, 'L')
    b_mask = b_band.point(lambda v: 255 if v == _MARKER[2] else 0, 'L')
    bg_mask_small = ImageChops.multiply(ImageChops.multiply(r_mask, g_mask), b_mask)

    # Upscale mask back to original size
    bg_mask = bg_mask_small.resize((w, h), Image.NEAREST) if scale < 1.0 else bg_mask_small

    # Invert and apply
    inv_mask = bg_mask.point(lambda v: 0 if v > 128 else 255, 'L')
    r2, g2, b2, a2 = rgba.split()
    new_alpha = ImageChops.multiply(a2, inv_mask)
    return Image.merge('RGBA', (r2, g2, b2, new_alpha))


def remove_checker_background(img: Image.Image, tile_size: int = 8) -> Image.Image:
    """
    Remove a checkered background by comparing each pixel against the
    expected checker pattern color and zeroing alpha where they match.
    Returns an RGBA image.
    """
    rgba = img.convert('RGBA')
    w, h = rgba.size
    r, g, b, a = rgba.split()
    orig_rgb = Image.merge('RGB', (r, g, b))

    # Build the expected checker pattern for the full image
    expected = _tile_pattern(w, h, tile_size, _CHECKER_DARK, _CHECKER_LIGHT)

    # Per-pixel max channel difference from expected checker
    diff = ImageChops.difference(orig_rgb, expected)
    dr, dg, db = diff.split()
    max_diff = ImageChops.lighter(ImageChops.lighter(dr, dg), db)

    # Pixels close to expected checker color → transparent
    alpha_mask = max_diff.point(lambda v: 0 if v < CHECKER_TOLERANCE else 255, 'L')
    new_alpha = ImageChops.multiply(a, alpha_mask)
    return Image.merge('RGBA', (r, g, b, new_alpha))


def smooth_alpha_edges(rgba: Image.Image, radius: float = 1.0) -> Image.Image:
    """
    Soften hard transparency edges by applying a tiny Gaussian blur to the
    alpha channel only.

    Interior pixels (solid 255 or 0) are surrounded by same-value neighbors
    so the blur leaves them essentially unchanged — only the thin transition
    band at the edge is affected. Single filter op, very fast.
    """
    r, g, b, a = rgba.split()
    a_smooth = a.filter(ImageFilter.GaussianBlur(radius=radius))
    return Image.merge('RGBA', (r, g, b, a_smooth))


def make_preview_thumbnail(img: Image.Image, size=(150, 150)) -> Image.Image:
    """
    Composite the RGBA image over a gray checkerboard and return
    a 150×150 RGB image suitable for ImageTk.PhotoImage.
    """
    bg = _tile_pattern(size[0], size[1], 10, (200, 200, 200), (255, 255, 255))
    thumb = img.copy()
    thumb.thumbnail(size, Image.LANCZOS)
    offset_x = (size[0] - thumb.width) // 2
    offset_y = (size[1] - thumb.height) // 2
    if thumb.mode == 'RGBA':
        bg.paste(thumb, (offset_x, offset_y), mask=thumb.split()[3])
    else:
        bg.paste(thumb.convert('RGB'), (offset_x, offset_y))
    return bg.convert('RGB')


# ---------------------------------------------------------------------------
# Grid slicing
# ---------------------------------------------------------------------------

def _is_separator_row(rgb: Image.Image, y: int, bg_color: tuple,
                       bg_type: str, tolerance: int, tile_size: int = 8) -> bool:
    """
    Return True if row `y` is entirely background (a gutter between cells).
    Samples 64 evenly-spaced pixels rather than every pixel for speed.
    Requires 90% of samples to match background to account for AA fringe.
    """
    w = rgb.width
    step = max(1, w // 64)
    xs = range(0, w, step)
    hits = 0
    total = 0
    for x in xs:
        total += 1
        px = rgb.getpixel((x, y))
        if bg_type == 'checkered':
            expected = _CHECKER_DARK if ((x // tile_size + y // tile_size) % 2 == 0) \
                       else _CHECKER_LIGHT
            if _color_distance(px, expected) <= tolerance:
                hits += 1
        else:
            if _color_distance(px, bg_color) <= tolerance:
                hits += 1
    return total > 0 and hits / total >= 0.90


def _is_separator_col(rgb: Image.Image, x: int, bg_color: tuple,
                       bg_type: str, tolerance: int, tile_size: int = 8) -> bool:
    """Same as _is_separator_row but for a column."""
    h = rgb.height
    step = max(1, h // 64)
    ys = range(0, h, step)
    hits = 0
    total = 0
    for y in ys:
        total += 1
        px = rgb.getpixel((x, y))
        if bg_type == 'checkered':
            expected = _CHECKER_DARK if ((x // tile_size + y // tile_size) % 2 == 0) \
                       else _CHECKER_LIGHT
            if _color_distance(px, expected) <= tolerance:
                hits += 1
        else:
            if _color_distance(px, bg_color) <= tolerance:
                hits += 1
    return total > 0 and hits / total >= 0.90


def _collapse_to_boundaries(separator_indices: list, max_dim: int) -> list:
    """
    Convert a list of separator pixel indices into boundary coordinates.
    Consecutive indices are grouped; the midpoint of each group is the boundary.
    Image edges (0 and max_dim) are always included.

    E.g. [0,1,2, 200,201,202, 400,401] → [0, 201, 400, max_dim]
    """
    if not separator_indices:
        return []

    boundaries = []
    group = [separator_indices[0]]
    for idx in separator_indices[1:]:
        if idx <= group[-1] + 3:   # allow tiny gaps (1-2px content in a gutter)
            group.append(idx)
        else:
            boundaries.append((group[0] + group[-1]) // 2)
            group = [idx]
    boundaries.append((group[0] + group[-1]) // 2)

    if not boundaries or boundaries[0] > 4:
        boundaries.insert(0, 0)
    if boundaries[-1] < max_dim - 4:
        boundaries.append(max_dim)

    return boundaries


def find_grid_boundaries(img: Image.Image,
                          bg_type: str = None,
                          bg_color: tuple = None,
                          tolerance: int = None) -> dict:
    """
    Auto-detect a grid layout in a sprite sheet / emotion grid.

    Scans every row and column on a downscaled copy to find separator lines,
    collapses adjacent separators into single boundaries, then maps back to
    original coordinates.

    Returns a dict:
      status      : 'ok' | 'failed'
      row_boundaries : list of y pixel coordinates (includes 0 and img.height)
      col_boundaries : list of x pixel coordinates (includes 0 and img.width)
      n_rows, n_cols : int
      reason      : error description (only when status=='failed')
    """
    if bg_type is None:
        bg_type, bg_color = classify_background(img)

    if bg_type == 'unknown':
        return {'status': 'failed', 'reason': 'Could not determine background color.'}

    if tolerance is None:
        tolerance = 30 if bg_type == 'checkered' else 25

    orig_w, orig_h = img.size

    # Work on a downscaled copy for speed; boundaries are mapped back afterward
    scale = min(1.0, 1024 / max(orig_w, orig_h))
    sw = max(1, int(orig_w * scale))
    sh = max(1, int(orig_h * scale))
    small = img.resize((sw, sh), Image.LANCZOS).convert('RGB')

    # Detect tile size for checkered (use same heuristic as classify_background)
    tile_size = max(4, min(16, min(sw, sh) // 16))

    sep_rows = [y for y in range(sh)
                if _is_separator_row(small, y, bg_color, bg_type, tolerance, tile_size)]
    sep_cols = [x for x in range(sw)
                if _is_separator_col(small, x, bg_color, bg_type, tolerance, tile_size)]

    row_bounds_small = _collapse_to_boundaries(sep_rows, sh)
    col_bounds_small = _collapse_to_boundaries(sep_cols, sw)

    if len(row_bounds_small) < 2 or len(col_bounds_small) < 2:
        return {'status': 'failed',
                'reason': f'Too few separators found '
                          f'(rows: {len(row_bounds_small)}, cols: {len(col_bounds_small)}). '
                          f'Try adjusting tolerance or use manual grid.'}

    # Map boundaries back to original image coordinates
    def _scale_bounds(bounds, factor):
        return [min(int(round(b / factor)), int(1 / factor * 1000)) for b in bounds]

    inv = 1.0 / scale
    row_bounds = [min(int(round(b * inv)), orig_h) for b in row_bounds_small]
    col_bounds = [min(int(round(b * inv)), orig_w) for b in col_bounds_small]

    # Ensure edges are exact
    row_bounds[0] = 0;  row_bounds[-1] = orig_h
    col_bounds[0] = 0;  col_bounds[-1] = orig_w

    n_rows = len(row_bounds) - 1
    n_cols = len(col_bounds) - 1

    return {
        'status': 'ok',
        'row_boundaries': row_bounds,
        'col_boundaries': col_bounds,
        'n_rows': n_rows,
        'n_cols': n_cols,
        'bg_type': bg_type,
        'bg_color': bg_color,
    }


def find_grid_boundaries_manual(img: Image.Image, n_rows: int, n_cols: int) -> dict:
    """Divide image into a fixed n_rows × n_cols grid — equal cell sizes."""
    w, h = img.size
    row_bounds = [int(round(h * i / n_rows)) for i in range(n_rows + 1)]
    col_bounds = [int(round(w * i / n_cols)) for i in range(n_cols + 1)]
    row_bounds[-1] = h;  col_bounds[-1] = w
    return {
        'status': 'ok',
        'row_boundaries': row_bounds,
        'col_boundaries': col_bounds,
        'n_rows': n_rows,
        'n_cols': n_cols,
        'bg_type': None,
        'bg_color': None,
    }


def draw_grid_overlay(img: Image.Image,
                       row_boundaries: list, col_boundaries: list,
                       line_color: tuple = (0, 220, 80),
                       line_width: int = 2) -> Image.Image:
    """
    Return a copy of `img` with grid boundary lines drawn over it.
    Used for the preview before slicing.
    """
    display = img.convert('RGB').copy()
    draw = ImageDraw.Draw(display)
    w, h = display.size
    for y in row_boundaries:
        draw.line([(0, y), (w, y)], fill=line_color, width=line_width)
    for x in col_boundaries:
        draw.line([(x, 0), (x, h)], fill=line_color, width=line_width)
    return display


def slice_and_save_grid(img: Image.Image,
                         row_boundaries: list, col_boundaries: list,
                         output_dir: str, base_name: str,
                         remove_bg: bool = True,
                         tolerance_dark: int = 10,
                         tolerance_light: int = 20,
                         blur_radius: float = 2.0) -> list:
    """
    Crop each cell from the grid, optionally remove its background,
    save to output_dir, and return a list of result dicts (one per cell).
    """
    os.makedirs(output_dir, exist_ok=True)
    results = []
    n_rows = len(row_boundaries) - 1
    n_cols = len(col_boundaries) - 1
    total = n_rows * n_cols
    idx = 0

    for row in range(n_rows):
        for col in range(n_cols):
            y1 = row_boundaries[row]
            y2 = row_boundaries[row + 1]
            x1 = col_boundaries[col]
            x2 = col_boundaries[col + 1]

            cell = img.crop((x1, y1, x2, y2))
            fname = f"{base_name}_r{row:02d}_c{col:02d}.png"
            out_path = os.path.join(output_dir, fname)

            try:
                if remove_bg:
                    bg_type, bg_color = classify_background(cell)
                    if bg_type in ('black', 'white'):
                        enclosed_tol = tolerance_dark if bg_type == 'black' else tolerance_light
                        result_img = remove_solid_background(cell, bg_color)
                        result_img = _remove_color_globally(result_img, bg_color, tolerance=enclosed_tol)
                    elif bg_type == 'checkered':
                        result_img = remove_checker_background(cell)
                    else:
                        result_img = cell.convert('RGBA')
                    if blur_radius > 0 and bg_type != 'unknown':
                        result_img = smooth_alpha_edges(result_img, radius=blur_radius)
                else:
                    result_img = cell.convert('RGBA')

                result_img.save(out_path, 'PNG')
                thumb = make_preview_thumbnail(result_img)
                results.append({
                    'status': 'ok', 'output_path': out_path,
                    'thumb': thumb, 'row': row, 'col': col,
                    'current': idx + 1, 'total': total,
                })
            except Exception as exc:
                results.append({
                    'status': 'error', 'error': str(exc),
                    'row': row, 'col': col,
                    'current': idx + 1, 'total': total,
                })
            idx += 1

    return results


def crop_grid_cells(img: Image.Image,
                    row_boundaries: list, col_boundaries: list,
                    remove_bg: bool = True,
                    tolerance_dark: int = 10,
                    tolerance_light: int = 20,
                    blur_radius: float = 2.0) -> list:
    """
    Crop each cell and optionally remove its background, returning results
    entirely in memory (no files written).

    Returns list of dicts:
        status      'ok' | 'error'
        pil_img     processed PIL.Image (RGBA) — only present when status=='ok'
        thumb       preview thumbnail PIL.Image
        row / col   grid coordinates
        current / total  progress counters
    """
    results = []
    n_rows = len(row_boundaries) - 1
    n_cols = len(col_boundaries) - 1
    total = n_rows * n_cols
    idx = 0

    for row in range(n_rows):
        for col in range(n_cols):
            y1, y2 = row_boundaries[row], row_boundaries[row + 1]
            x1, x2 = col_boundaries[col], col_boundaries[col + 1]
            cell = img.crop((x1, y1, x2, y2))
            try:
                if remove_bg:
                    bg_type, bg_color = classify_background(cell)
                    if bg_type in ('black', 'white'):
                        enclosed_tol = (tolerance_dark if bg_type == 'black'
                                        else tolerance_light)
                        result_img = remove_solid_background(cell, bg_color)
                        result_img = _remove_color_globally(
                            result_img, bg_color, tolerance=enclosed_tol)
                    elif bg_type == 'checkered':
                        result_img = remove_checker_background(cell)
                    else:
                        result_img = cell.convert('RGBA')
                    if blur_radius > 0 and bg_type != 'unknown':
                        result_img = smooth_alpha_edges(result_img,
                                                        radius=blur_radius)
                else:
                    result_img = cell.convert('RGBA')

                thumb = make_preview_thumbnail(result_img)
                results.append({
                    'status': 'ok',
                    'pil_img': result_img,
                    'thumb': thumb,
                    'row': row, 'col': col,
                    'current': idx + 1, 'total': total,
                })
            except Exception as exc:
                results.append({
                    'status': 'error', 'error': str(exc),
                    'row': row, 'col': col,
                    'current': idx + 1, 'total': total,
                })
            idx += 1

    return results


def process_image(input_path: str, output_path: str,
                  tolerance_dark: int = 10,
                  tolerance_light: int = 20,
                  blur_radius: float = 2.0) -> dict:
    """
    Full pipeline: open → classify → remove background → smooth → save → thumbnail.
    Returns a result dict (safe to pass over queue.Queue to the UI thread).

    tolerance_dark:  enclosed-region color-match tolerance for black backgrounds
    tolerance_light: enclosed-region color-match tolerance for white backgrounds
    blur_radius:     Gaussian blur radius applied to the alpha channel edge
    """
    try:
        img = Image.open(input_path)
        bg_type, bg_color = classify_background(img)

        if bg_type in ('black', 'white'):
            enclosed_tol = tolerance_dark if bg_type == 'black' else tolerance_light
            result = remove_solid_background(img, bg_color)
            result = _remove_color_globally(result, bg_color, tolerance=enclosed_tol)
        elif bg_type == 'checkered':
            result = remove_checker_background(img)
        else:
            result = img.convert('RGBA')

        if bg_type != 'unknown' and blur_radius > 0:
            result = smooth_alpha_edges(result, radius=blur_radius)

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        result.save(output_path, 'PNG')

        thumb = make_preview_thumbnail(result)
        return {
            'status': 'ok',
            'bg_type': bg_type,
            'output_path': output_path,
            'thumb': thumb,
        }
    except Exception as exc:
        return {
            'status': 'error',
            'error': str(exc),
            'input': input_path,
        }
