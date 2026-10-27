"""ComfyUI GPT Images: Codex subscription images and Sunburst batches (GPT Images),
plus Grok Images and Antigravity Images through their own subscription sign-ins."""

from comfy_api.latest import ComfyExtension, io

from .nodes import AntigravityImagesNode, GPTImagesNode, GrokImagesNode

__version__ = "5.0.1"


class GPTImagesExtension(ComfyExtension):
    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        return [GPTImagesNode, GrokImagesNode, AntigravityImagesNode]


async def comfy_entrypoint() -> GPTImagesExtension:
    return GPTImagesExtension()
