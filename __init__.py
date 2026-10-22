"""ComfyUI GPT Images: one node for Codex subscription images and Sunburst batches."""

from comfy_api.latest import ComfyExtension, io

from .nodes import GPTImagesNode


class GPTImagesExtension(ComfyExtension):
    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        return [GPTImagesNode]


async def comfy_entrypoint() -> GPTImagesExtension:
    return GPTImagesExtension()
