"""Sunburst size presets: aspect ratio plus orientation, then a valid resolution.

The sizes and their labels are the user's v2 table, used as written. The
"Experimental" marks match OpenAI's documented rule (more than 3,686,400 pixels).
Pure Python with no ComfyUI imports, so it can be tested on its own.
"""

from __future__ import annotations

# (width, height, megapixels as written in the table, classification as written)
_TABLE: dict[str, list[tuple[int, int, str, str]]] = {
    "1:1 Square": [
        (1024, 1024, "1.05", "Recommended"),
        (1536, 1536, "2.36", "Supported custom"),
        (1920, 1920, "3.69", "Supported custom"),
        (2048, 2048, "4.19", "Documented example + Experimental"),
        (2560, 2560, "6.55", "Supported custom + Experimental"),
        (2880, 2880, "8.29", "Supported maximum square + Experimental"),
    ],
    "16:9 Landscape": [
        (1280, 720, "0.92", "Supported custom"),
        (1536, 864, "1.33", "Documented example"),
        (1792, 1008, "1.81", "Supported custom"),
        (2048, 1152, "2.36", "Documented example"),
        (2304, 1296, "2.99", "Supported custom"),
        (2560, 1440, "3.69", "Documented example"),
        (2816, 1584, "4.46", "Supported custom + Experimental"),
        (3072, 1728, "5.31", "Supported custom + Experimental"),
        (3584, 2016, "7.23", "Supported custom + Experimental"),
        (3840, 2160, "8.29", "Documented maximum + Experimental"),
    ],
    "9:16 Portrait": [
        (720, 1280, "0.92", "Supported custom"),
        (864, 1536, "1.33", "Supported custom"),
        (1008, 1792, "1.81", "Supported custom"),
        (1152, 2048, "2.36", "Supported custom"),
        (1296, 2304, "2.99", "Supported custom"),
        (1440, 2560, "3.69", "Supported custom"),
        (1584, 2816, "4.46", "Supported custom + Experimental"),
        (1728, 3072, "5.31", "Supported custom + Experimental"),
        (2016, 3584, "7.23", "Supported custom + Experimental"),
        (2160, 3840, "8.29", "Documented maximum + Experimental"),
    ],
    "4:5 Portrait": [
        (768, 960, "0.74", "Supported custom"),
        (1024, 1280, "1.31", "Supported custom"),
        (1280, 1600, "2.05", "Supported custom"),
        (1536, 1920, "2.95", "Supported custom"),
        (1664, 2080, "3.46", "Supported custom"),
        (1728, 2160, "3.73", "Supported custom + Experimental"),
        (2048, 2560, "5.24", "Supported custom + Experimental"),
        (2304, 2880, "6.64", "Supported custom + Experimental"),
        (2560, 3200, "8.19", "Supported maximum 4:5 + Experimental"),
    ],
    "5:4 Landscape": [
        (960, 768, "0.74", "Supported custom"),
        (1280, 1024, "1.31", "Supported custom"),
        (1600, 1280, "2.05", "Supported custom"),
        (1920, 1536, "2.95", "Supported custom"),
        (2080, 1664, "3.46", "Supported custom"),
        (2160, 1728, "3.73", "Supported custom + Experimental"),
        (2560, 2048, "5.24", "Supported custom + Experimental"),
        (2880, 2304, "6.64", "Supported custom + Experimental"),
        (3200, 2560, "8.19", "Supported maximum 5:4 + Experimental"),
    ],
    "3:2 Landscape": [
        (1152, 768, "0.88", "Supported custom"),
        (1536, 1024, "1.57", "Recommended"),
        (1920, 1280, "2.46", "Supported custom"),
        (2304, 1536, "3.54", "Supported custom"),
        (2352, 1568, "3.69", "Supported custom + Experimental"),
        (3072, 2048, "6.29", "Supported custom + Experimental"),
        (3456, 2304, "7.96", "Supported custom + Experimental"),
        (3504, 2336, "8.19", "Supported maximum 3:2 + Experimental"),
    ],
    "2:3 Portrait": [
        (768, 1152, "0.88", "Supported custom"),
        (1024, 1536, "1.57", "Recommended"),
        (1280, 1920, "2.46", "Supported custom"),
        (1536, 2304, "3.54", "Supported custom"),
        (1568, 2352, "3.69", "Supported custom + Experimental"),
        (2048, 3072, "6.29", "Supported custom + Experimental"),
        (2304, 3456, "7.96", "Supported custom + Experimental"),
        (2336, 3504, "8.19", "Supported maximum 2:3 + Experimental"),
    ],
}

# Documented Sunburst size rules.
MAX_EDGE = 3840
MIN_PIXELS = 655_360
MAX_PIXELS = 8_294_400
EXPERIMENTAL_ABOVE_PIXELS = 3_686_400

# ComfyUI's dynamic dropdown always starts on its first entry: the table's order,
# so "1:1 Square" is the default. The default size is each ratio's largest.
ASPECTS = list(_TABLE)
DEFAULT_ASPECT = ASPECTS[0]


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


def resolution_label(width: int, height: int, megapixels: str, classification: str) -> str:
    return f"{width} × {height} ({megapixels} MP) {classification}"


def _build() -> dict[str, dict[str, tuple[int, int]]]:
    """aspect label -> {resolution label -> (width, height)}, in dropdown order."""
    table: dict[str, dict[str, tuple[int, int]]] = {}
    for aspect, rows in _TABLE.items():
        ratio_w, ratio_h = (int(part) for part in aspect.split()[0].split(":"))
        entries: dict[str, tuple[int, int]] = {}
        for width, height, megapixels, classification in rows:
            problem = size_problem(width, height)
            if problem:
                raise AssertionError(f"{aspect} {width}x{height} is invalid: {problem}")
            if width * ratio_h != height * ratio_w:
                raise AssertionError(f"{width}x{height} is not {ratio_w}:{ratio_h}")
            entries[resolution_label(width, height, megapixels, classification)] = (width, height)
        table[aspect] = entries
    return table


PRESETS = _build()


def table_rows(aspect: str) -> list[tuple[int, int, str, str]]:
    return list(_TABLE[aspect])


def default_resolution(aspect: str) -> str:
    """The largest size of the ratio (last row of the table)."""
    return list(PRESETS[aspect])[-1]


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
