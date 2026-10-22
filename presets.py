"""Sunburst size presets: aspect ratio plus orientation, then a valid resolution.

Pure Python with no ComfyUI imports, so it can be tested on its own.
"""

from __future__ import annotations

TIERS = ("1k", "1.2k", "1.5k", "2k / Max")

# Landscape (or square) sizes per ratio, one per tier, in TIERS order.
_LANDSCAPE: dict[tuple[int, int], list[tuple[int, int]]] = {
    (1, 1): [(1024, 1024), (1280, 1280), (1536, 1536), (2880, 2880)],
    (5, 4): [(1120, 896), (1360, 1088), (1680, 1344), (3200, 2560)],
    (4, 3): [(1152, 864), (1472, 1104), (1728, 1296), (3264, 2448)],
    (3, 2): [(1248, 832), (1536, 1024), (1872, 1248), (3504, 2336)],
    (16, 10): [(1280, 800), (1536, 960), (1920, 1200), (3584, 2240)],
    (16, 9): [(1280, 720), (1536, 864), (2048, 1152), (3840, 2160)],
    (2, 1): [(1440, 720), (1792, 896), (2144, 1072), (3840, 1920)],
    (21, 9): [(1456, 624), (1904, 816), (2240, 960), (3808, 1632)],
    (3, 1): [(1728, 576), (2208, 736), (2640, 880), (3840, 1280)],
}

# Documented Sunburst size rules.
MAX_EDGE = 3840
MIN_PIXELS = 655_360
MAX_PIXELS = 8_294_400
EXPERIMENTAL_ABOVE_PIXELS = 3_686_400

DEFAULT_ASPECT = "16:9 Landscape"
DEFAULT_TIER = "2k / Max"


def size_problem(width: int, height: int) -> str | None:
    """Return why a size breaks the documented rules, or None if it is valid."""
    if width % 16 or height % 16:
        return "both edges must be divisible by 16"
    if width > MAX_EDGE or height > MAX_EDGE:
        return f"no edge may exceed {MAX_EDGE}"
    if max(width, height) > 3 * min(width, height):
        return "aspect ratio must be between 1:3 and 3:1"
    pixels = width * height
    if not MIN_PIXELS <= pixels <= MAX_PIXELS:
        return f"total pixels must be between {MIN_PIXELS:,} and {MAX_PIXELS:,}"
    return None


def resolution_label(tier: str, width: int, height: int) -> str:
    label = f"{tier}: {width} x {height}"
    if width * height > EXPERIMENTAL_ABOVE_PIXELS:
        label += " (experimental)"
    return label


def _build() -> dict[str, dict[str, tuple[int, int]]]:
    """aspect label -> {resolution label -> (width, height)}, in dropdown order."""
    table: dict[str, dict[str, tuple[int, int]]] = {}
    for (rw, rh), sizes in _LANDSCAPE.items():
        orientations = [("Square", False)] if rw == rh else [("Landscape", False), ("Portrait", True)]
        for name, transpose in orientations:
            aspect = f"{rh}:{rw} {name}" if transpose else f"{rw}:{rh} {name}"
            entries: dict[str, tuple[int, int]] = {}
            for tier, (lw, lh) in zip(TIERS, sizes):
                if lw * rh != lh * rw:
                    raise AssertionError(f"preset {lw}x{lh} does not match {rw}:{rh}")
                w, h = (lh, lw) if transpose else (lw, lh)
                problem = size_problem(w, h)
                if problem:
                    raise AssertionError(f"preset {aspect} {w}x{h} is invalid: {problem}")
                entries[resolution_label(tier, w, h)] = (w, h)
            table[aspect] = entries
    return table


PRESETS = _build()
# ComfyUI's dynamic dropdown always starts on its first entry, so the default goes first.
ASPECTS = [DEFAULT_ASPECT] + [a for a in PRESETS if a != DEFAULT_ASPECT]


def default_resolution(aspect: str) -> str:
    for label in PRESETS[aspect]:
        if label.startswith(DEFAULT_TIER + ":"):
            return label
    return next(iter(PRESETS[aspect]))


def resolve(aspect: str, resolution: str) -> tuple[int, int]:
    """Map dropdown values to pixels. Rejects stale or mismatched selections."""
    entries = PRESETS.get(aspect)
    if entries is None:
        raise ValueError(f"Unknown aspect ratio '{aspect}'. Pick one from the list.")
    size = entries.get(resolution)
    if size is None:
        raise ValueError(
            f"Resolution '{resolution}' does not belong to {aspect}. "
            "Pick the resolution again after changing the aspect ratio."
        )
    return size
