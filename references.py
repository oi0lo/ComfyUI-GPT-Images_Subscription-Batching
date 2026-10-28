"""Reference images: collect them in a stable order and encode them.

Agreed encoding rule: lossless PNG first. Only if a size check fails, every
reference is encoded again as high-quality JPEG and checked once more. Files on
disk are never read or written here; everything works on the pixels ComfyUI
passes to the node.

References with transparency (RGBA, 4 channels) are sent as PNG exactly as they
reach the node, including the colour stored under fully transparent pixels. JPEG
cannot hold transparency, so when the JPEG fallback would be needed and any
reference has transparency, the run stops and the user decides: reduce the image
or flatten it before the node (v5 rule; nothing is flattened automatically).
"""

from __future__ import annotations

import io
import os
import re
from dataclasses import dataclass

import numpy as np
from PIL import Image

PNG_OPTIONS = {"format": "PNG", "compress_level": 3, "optimize": False}
# No optimize=True: Pillow then needs one fixed-size buffer, which very noisy 4K images overflow.
JPEG_OPTIONS = {"format": "JPEG", "quality": 95, "subsampling": 0}


class ReferenceError(ValueError):
    """A reference image cannot be used as given."""


@dataclass(frozen=True)
class Encoded:
    mime: str  # "image/png" or "image/jpeg"
    data: bytes
    width: int
    height: int
    alpha: bool = False  # PNG with a transparency channel

    @property
    def extension(self) -> str:
        return ".png" if self.mime == "image/png" else ".jpg"


def _socket_key(name: str) -> tuple[int, str]:
    match = re.search(r"(\d+)$", name)
    return (int(match.group(1)) if match else 0, name)


def _as_float_array(item) -> np.ndarray:
    """One image (H, W, C) as float32 numpy, from a torch tensor or numpy array."""
    if isinstance(item, np.ndarray):
        return item.astype(np.float32, copy=False)
    # torch tensor: one transfer to CPU; .float() is a no-op for float32.
    return item.detach().cpu().float().numpy()


def _split(value, socket: str) -> list:
    shape = tuple(getattr(value, "shape", ()))
    if len(shape) != 4:
        raise ReferenceError(
            f"{socket}: expected an IMAGE batch shaped [batch, height, width, channels], got {list(shape)}."
        )
    if shape[-1] not in (3, 4):
        raise ReferenceError(
            f"{socket}: expected RGB (3 channels) or RGBA (4 channels) images, got {shape[-1]} channels."
        )
    if shape[1] < 1 or shape[2] < 1:
        raise ReferenceError(f"{socket}: image has no pixels.")
    return [value[i] for i in range(shape[0])]


def collect(images) -> list:
    """Flatten the growing image sockets into one ordered list of single images.

    Order: socket number (numeric, so image_2 comes before image_10), then the
    order of a connected list, then the order inside each image batch. Every
    batch item counts as its own reference.
    """
    if not images:
        return []
    if not isinstance(images, dict):
        raise ReferenceError("Unexpected structure for the image inputs.")
    ordered = []
    for socket in sorted(images, key=_socket_key):
        value = images[socket]
        values = value if isinstance(value, (list, tuple)) else [value]
        for entry in values:
            if entry is None:
                continue
            ordered.extend((socket, item) for item in _split(entry, socket))
    return ordered


def to_uint8(item, socket: str = "image") -> np.ndarray:
    pixels = _as_float_array(item)
    if not np.isfinite(pixels).all():
        raise ReferenceError(f"{socket}: image contains NaN or infinite values.")
    # Round instead of truncating, so 8-bit images survive the float round trip exactly.
    return np.rint(np.clip(pixels, 0.0, 1.0) * 255.0).astype(np.uint8)


def has_alpha(item) -> bool:
    """True for an RGBA image (4 channels)."""
    shape = tuple(getattr(item, "shape", ()))
    return bool(shape) and shape[-1] == 4


def encode(item, kind: str, socket: str = "image") -> Encoded:
    pixels = to_uint8(item, socket)
    height, width, channels = pixels.shape
    alpha = channels == 4
    if alpha and kind != "png":
        raise ReferenceError(f"{socket}: has transparency, which JPEG cannot keep.")
    buffer = io.BytesIO()
    image = Image.fromarray(pixels)  # uint8 (H, W, 3) -> RGB, (H, W, 4) -> RGBA, values untouched
    if kind == "png":
        image.save(buffer, **PNG_OPTIONS)
        mime = "image/png"
    elif kind == "jpeg":
        image.save(buffer, **JPEG_OPTIONS)
        mime = "image/jpeg"
    else:
        raise ValueError(f"unknown encoding {kind}")
    return Encoded(mime, buffer.getvalue(), width, height, alpha)


def encode_all(references: list, kind: str) -> list[Encoded]:
    return [encode(item, kind, socket) for socket, item in references]


def _encode_until_too_big(references: list, kind: str, fits) -> list[Encoded] | None:
    """Encode one by one. Adding a reference only ever makes the request bigger,
    so stop as soon as the partial list no longer fits."""
    encoded: list[Encoded] = []
    for socket, item in references:
        encoded.append(encode(item, kind, socket))
        if not fits(encoded):
            return None
    return encoded


def encode_with_fallback(references: list, fits) -> tuple[list[Encoded], str]:
    """PNG first; if `fits(encoded)` is False, JPEG for every reference.

    `fits` must be monotonic: if a list does not fit, a longer one does not either.
    Returns (encoded, "png" | "jpeg"). Raises ReferenceError if JPEG still fails.
    """
    encoded = _encode_until_too_big(references, "png", fits)
    if encoded is not None:
        return encoded, "png"
    transparent = list(dict.fromkeys(socket for socket, item in references if has_alpha(item)))
    if transparent:
        raise ReferenceError(
            "The references are too large as lossless PNG, and the fallback would save them as JPEG, "
            f"which cannot keep transparency. These references have transparency: {', '.join(transparent)}. "
            "Reduce their size, or flatten them (remove the transparency) before this node. Nothing was sent."
        )
    encoded = _encode_until_too_big(references, "jpeg", fits)
    if encoded is not None:
        return encoded, "jpeg"
    raise ReferenceError(
        "The reference images are too large even after JPEG compression. "
        "Use fewer references or reduce their dimensions before this node."
    )


def write_files(encoded: list[Encoded], directory: str, names: list[str] | None = None) -> list[str]:
    """Write encoded references into this run's own temporary folder, as reference_01.png and so
    on, or under `names` (without extension) when given."""
    if names is not None and len(names) != len(encoded):
        raise ValueError("write_files needs one name per image.")
    paths = []
    for index, item in enumerate(encoded, start=1):
        stem = names[index - 1] if names is not None else f"reference_{index:02d}"
        path = os.path.join(directory, f"{stem}{item.extension}")
        with open(path, "xb") as handle:
            handle.write(item.data)
        paths.append(path)
    return paths
