"""ComfyUI GPT Images: Codex subscription images and Sunburst batches (GPT Images),
plus Grok Images (images and videos) and Antigravity Nano Banana through their own subscription sign-ins."""

from comfy_api.latest import ComfyExtension, io

from .nodes import AntigravityNanoBananaNode, GPTImagesNode, GrokImagesNode

__version__ = "6.0.0"


class GPTImagesExtension(ComfyExtension):
    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        return [GPTImagesNode, GrokImagesNode, AntigravityNanoBananaNode]


async def comfy_entrypoint() -> GPTImagesExtension:
    return GPTImagesExtension()
