"""Build the one-line Batch JSONL request for Luna + Sunburst.

The request shape matches the jobs that already worked in the chat:
/v1/responses, model gpt-6-luna with reasoning effort none, one forced
image_generation tool on gpt-image-2.5-sunburst.

Images are not pasted into one big string. The JSON is serialised once with
placeholders, then the exact byte size is computed from the encoded image sizes,
and the body is produced as a sequence of chunks (see iter_chunks). Joining the
chunks gives exactly json.dumps(...) of the full request plus a newline.
"""

from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # only for type hints; keeps this module importable on its own
    from references import Encoded

ENDPOINT = "/v1/responses"
MODEL = "gpt-6-luna"
IMAGE_MODEL = "gpt-image-2.5-sunburst"
QUALITIES = ("auto", "low", "medium", "high", "xhigh", "max")

# Documented Batch input file limit is 200 MB; counted conservatively in bytes,
# including the final newline. The documented per-request payload limit
# (512 MB) is higher, so with one request per file this is the binding limit.
MAX_JSONL_BYTES = 200_000_000

INSTRUCTIONS_WITH_REFERENCES = (
    "Generate exactly one final image using the configured image_generation tool. "
    "Follow the user prompt. The attached images are references, in the order supplied."
)
INSTRUCTIONS_NO_REFERENCES = (
    "Generate exactly one final image using the configured image_generation tool. "
    "Follow the user prompt."
)


@dataclass
class BatchRequest:
    custom_id: str
    segments: list[bytes]  # len(images) + 1 pieces of serialised JSON
    images: list[Encoded]

    @property
    def size(self) -> int:
        return sum(len(s) for s in self.segments) + sum(data_url_length(i) for i in self.images)

    def iter_chunks(self):
        """Yield the JSONL bytes in order. Each image is base64-encoded only when reached."""
        for index, image in enumerate(self.images):
            yield self.segments[index]
            yield data_url_prefix(image)
            yield base64.b64encode(image.data)
        yield self.segments[-1]

    def to_bytes(self) -> bytes:
        return b"".join(self.iter_chunks())


def data_url_prefix(image: Encoded) -> bytes:
    return f"data:{image.mime};base64,".encode("ascii")


def data_url_length(image: Encoded) -> int:
    return len(data_url_prefix(image)) + 4 * ((len(image.data) + 2) // 3)


def build(prompt: str, width: int, height: int, quality: str, images: list[Encoded]) -> BatchRequest:
    if quality not in QUALITIES:
        raise ValueError(f"Unknown quality '{quality}'.")
    custom_id = f"gpt-images-{uuid.uuid4().hex}"
    token = uuid.uuid4().hex  # placeholder marker that cannot occur in a prompt by accident
    placeholders = [f"@@IMAGE_{index}_{token}@@" for index in range(len(images))]

    content = [{"type": "input_text", "text": prompt}]
    content += [{"type": "input_image", "image_url": marker} for marker in placeholders]
    tool = {
        "type": "image_generation",
        "model": IMAGE_MODEL,
        "action": "edit" if images else "generate",
        "size": f"{width}x{height}",
        "quality": quality,
        "output_format": "png",
        "background": "auto",
        "moderation": "low",
        "partial_images": 0,
    }
    request = {
        "custom_id": custom_id,
        "method": "POST",
        "url": ENDPOINT,
        "body": {
            "model": MODEL,
            "reasoning": {"effort": "none"},
            "instructions": INSTRUCTIONS_WITH_REFERENCES if images else INSTRUCTIONS_NO_REFERENCES,
            "input": [{"role": "user", "content": content}],
            "tools": [tool],
            "tool_choice": {"type": "image_generation"},
            "max_tool_calls": 1,
            "parallel_tool_calls": False,
            "stream": False,
        },
    }
    # ensure_ascii keeps the line pure ASCII: valid JSON, no encoding surprises.
    text = json.dumps(request, ensure_ascii=True, separators=(",", ":")) + "\n"
    segments = []
    rest = text
    for marker in placeholders:
        before, found, rest = rest.partition(marker)
        if not found:
            raise AssertionError("image placeholder missing from serialised request")
        segments.append(before.encode("ascii"))
    segments.append(rest.encode("ascii"))
    return BatchRequest(custom_id, segments, list(images))


def size_for(prompt: str, width: int, height: int, quality: str, images: list[Encoded]) -> int:
    return build(prompt, width, height, quality, images).size
