"""ComfyUI GPT Images: one node for Codex subscription images and Sunburst batches."""

from comfy_api.latest import ComfyExtension, io

from .nodes import GPTImagesNode

__version__ = "3.0.0"


class GPTImagesExtension(ComfyExtension):
    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        return [GPTImagesNode]


async def comfy_entrypoint() -> GPTImagesExtension:
    return GPTImagesExtension()
