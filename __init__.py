"""Comfy-CLI-Subscription_Image-Video: GPT Images (Codex subscription and Sunburst batches),
Grok Images (images and videos) and Antigravity Nano Banana, through their own subscription sign-ins."""

from comfy_api.latest import ComfyExtension, io

from .nodes import AntigravityNanoBananaNode, GPTImagesNode, GrokImagesNode

__version__ = "6.1.0"


class CLISubscriptionImageVideoExtension(ComfyExtension):
    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        return [GPTImagesNode, GrokImagesNode, AntigravityNanoBananaNode]


async def comfy_entrypoint() -> CLISubscriptionImageVideoExtension:
    return CLISubscriptionImageVideoExtension()
