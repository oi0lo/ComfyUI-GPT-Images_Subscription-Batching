"""One ComfyUI node with two modes.

Subscription:   prompt + optional references -> Codex CLI (ChatGPT sign-in) -> IMAGE.
Sunburst Batch: prompt + settings + optional references -> one OpenAI Batch job;
                outputs its batch id and dashboard link, no image.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
import uuid

import numpy as np
import torch
from PIL import Image

import comfy.model_management as model_management
from comfy_api.latest import io
from comfy_execution.graph_utils import ExecutionBlocker

from . import (batch_http, batch_request, codex_exec, credentials, key_prompt, key_setup, presets,
               references)

log = logging.getLogger("comfyui-gpt-images")

MODE_SUBSCRIPTION = "Subscription"
MODE_BATCH = "Sunburst Batch"
IMAGE_SOCKETS = [f"image_{i}" for i in range(1, 17)]
KEY_WINDOW_TIMEOUT = 10 * 60
KEY_NOTICE = (
    "If Windows asks for your OpenAI API key: Windows Credential Manager stores it for your Windows user. "
    "It is never saved in this node, the workflow, or ComfyUI's files and logs. The node reads it only to "
    "send requests to OpenAI."
)


def _single(value, name: str):
    """With list inputs enabled, every value arrives as a list. One run takes one value."""
    if isinstance(value, (list, tuple)):
        if not value:
            raise ValueError(f"No value for {name}.")
        first = value[0]
        if any(other != first for other in value[1:]):
            raise ValueError(
                f"{name} received a list of {len(value)} different values. This node makes one "
                "image per run; use the run count to repeat."
            )
        return first
    return value


def _load_image(path: str) -> torch.Tensor:
    with Image.open(path) as image:
        image.load()
        if image.width < 1 or image.height < 1:
            raise ValueError(f"The generated image has no pixels: {path}")
        rgb = image.convert("RGB")
    pixels = np.asarray(rgb, dtype=np.float32) / 255.0
    return torch.from_numpy(pixels)[None,]


class GPTImagesNode(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        aspect_options = [
            io.DynamicCombo.Option(
                aspect,
                [
                    io.Combo.Input(
                        "resolution",
                        options=list(presets.PRESETS[aspect]),
                        default=presets.default_resolution(aspect),
                        tooltip=(
                            "Output size in pixels. OpenAI's docs call outputs above 3,686,400 pixels "
                            "(2560 x 1440) experimental."
                        ),
                    )
                ],
            )
            for aspect in presets.ASPECTS
        ]
        return io.Schema(
            node_id="GPTImagesSubscriptionSunburstBatch",
            display_name="GPT Images: Subscription / Batching gpt-image-2.5 sunburst",
            category="GPT Images",
            description=(
                "Subscription: generates one image through Codex with your ChatGPT sign-in and returns it. "
                "Sunburst Batch: submits one gpt-image-2.5-sunburst job to the OpenAI Batch API "
                "(billed to your API account) and returns its batch id and link."
            ),
            search_aliases=["gpt image", "sunburst", "openai batch", "codex", "chatgpt image"],
            inputs=[
                io.DynamicCombo.Input(
                    "mode",
                    options=[
                        io.DynamicCombo.Option(MODE_SUBSCRIPTION, []),
                        io.DynamicCombo.Option(
                            MODE_BATCH,
                            [
                                io.DynamicCombo.Input(
                                    "aspect_ratio",
                                    options=aspect_options,
                                    tooltip="Aspect ratio and orientation of the Sunburst image.",
                                ),
                                io.Combo.Input(
                                    "quality",
                                    options=list(batch_request.QUALITIES),
                                    default="max",
                                    tooltip="Sunburst quality.",
                                ),
                                io.String.Input(
                                    "api_key_notice",
                                    display_name="about your API key",
                                    multiline=True,
                                    default="",
                                    placeholder=KEY_NOTICE,
                                    socketless=True,
                                    tooltip=KEY_NOTICE + " (This box is only a notice; anything typed here is ignored.)",
                                ),
                            ],
                        ),
                    ],
                    tooltip=(
                        "Subscription uses your Codex allowance (ChatGPT sign-in). "
                        "Sunburst Batch uses your OpenAI API balance."
                    ),
                ),
                io.String.Input(
                    "prompt",
                    multiline=True,
                    default="",
                    tooltip="Sent as written. Every queued run makes a new generation or batch job.",
                ),
                io.Autogrow.Input(
                    "images",
                    template=io.Autogrow.TemplateNames(io.Image.Input("image"), names=IMAGE_SOCKETS, min=0),
                    tooltip=(
                        "Optional reference images, used in socket order. Every image in a connected batch "
                        "counts as one reference. Subscription mode accepts up to 5."
                    ),
                ),
            ],
            outputs=[
                io.Image.Output(display_name="image", tooltip="Subscription mode only."),
                io.String.Output(display_name="batch_id", tooltip="Sunburst Batch mode only."),
                io.String.Output(display_name="batch_url", tooltip="Sunburst Batch mode only: dashboard link."),
            ],
            is_output_node=True,
            is_input_list=True,
            not_idempotent=True,
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        # Every deliberately queued run is a new generation, even with unchanged inputs.
        return uuid.uuid4().hex

    @classmethod
    async def execute(cls, mode, prompt, images=None) -> io.NodeOutput:
        settings = mode if isinstance(mode, dict) else {"mode": mode}
        selected = _single(settings.get("mode"), "mode")
        text = _single(prompt, "prompt")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("The prompt is empty.")
        refs = references.collect(images or {})

        if selected == MODE_SUBSCRIPTION:
            image = await cls._subscription(text, refs)
            return io.NodeOutput(image, ExecutionBlocker(None), ExecutionBlocker(None))

        if selected == MODE_BATCH:
            aspect = settings.get("aspect_ratio") or {}
            batch_id = await cls._batch(
                text,
                refs,
                _single(aspect.get("aspect_ratio"), "aspect ratio"),
                _single(aspect.get("resolution"), "resolution"),
                _single(settings.get("quality"), "quality"),
            )
            return io.NodeOutput(ExecutionBlocker(None), batch_id, batch_http.dashboard_url(batch_id))

        raise ValueError(f"Unknown mode '{selected}'.")

    @classmethod
    async def _subscription(cls, prompt: str, refs: list) -> torch.Tensor:
        if len(refs) > codex_exec.MAX_REFERENCES:
            raise ValueError(
                f"Subscription mode accepts at most {codex_exec.MAX_REFERENCES} reference images; "
                f"{len(refs)} are connected (every image in a batch counts)."
            )
        codex = codex_exec.find_codex()
        await asyncio.to_thread(codex_exec.check_login, codex)

        run_dir = tempfile.mkdtemp(prefix="comfyui-gpt-images-")
        try:
            paths: list[str] = []
            if refs:

                def fits(items) -> bool:
                    model_management.throw_exception_if_processing_interrupted()  # Stop works while encoding
                    return all(len(i.data) < codex_exec.MAX_REFERENCE_BYTES for i in items)

                encoded, kind = references.encode_with_fallback(refs, fits)
                if kind == "jpeg":
                    log.warning("[GPT Images] References were over 50 MB as PNG; sent as JPEG quality 95.")
                paths = references.write_files(encoded, run_dir)
            log.info(
                "[GPT Images] Subscription: starting Codex (%s, %s reasoning) with %d reference(s).",
                codex_exec.AGENT_MODEL, codex_exec.AGENT_REASONING, len(paths),
            )
            try:
                result = await codex_exec.generate(
                    codex, prompt, paths, run_dir, is_interrupted=model_management.processing_interrupted
                )
            except codex_exec.CodexInterrupted:
                model_management.throw_exception_if_processing_interrupted()
                raise
            log.info("[GPT Images] Subscription: image ready after %.1f s: %s", result.elapsed, result.image_path)
            return _load_image(result.image_path)
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)

    @classmethod
    async def _ask_for_key(cls, rejected: bool) -> str:
        """Windows key window: paste, free check with OpenAI, save. Returns the accepted key."""
        if not key_prompt.available():
            if rejected:
                raise credentials.CredentialError(
                    "OpenAI rejected the stored key. Store a new one with: " + credentials.helper_command("set")
                )
            raise credentials.NoKeyError(credentials.setup_hint())

        async def prompt(message: str):
            try:
                return await key_prompt.prompt_for_key(
                    message, timeout=KEY_WINDOW_TIMEOUT, is_interrupted=model_management.processing_interrupted
                )
            except key_prompt.KeyPromptInterrupted:
                model_management.throw_exception_if_processing_interrupted()
                raise

        async def validate(key: str) -> None:
            await asyncio.to_thread(batch_http.check_key, key)

        stored = []

        def save(key: str) -> None:
            try:
                credentials.write_store(key)
                stored.append(True)
            except credentials.CredentialError as exc:  # the key works; only storing it failed
                log.warning("[GPT Images] OpenAI accepted the key, but it could not be stored (%s). "
                            "This run continues; you will be asked again next time.", exc)

        log.info("[GPT Images] Waiting for the OpenAI API key to be entered in the Windows key window.")
        key = await key_setup.obtain_key(prompt, validate, save, credentials.check_syntax, rejected=rejected)
        if stored:
            log.info("[GPT Images] OpenAI accepted the key; it is stored in Windows Credential Manager.")
        return key

    @classmethod
    async def _get_key(cls) -> tuple[str, str]:
        try:
            return credentials.resolve_api_key()
        except credentials.NoKeyError:
            return await cls._ask_for_key(rejected=False), credentials.STORE_SOURCE
        except credentials.StoredKeyInvalid:
            return await cls._ask_for_key(rejected=True), credentials.STORE_SOURCE

    @classmethod
    async def _batch(cls, prompt: str, refs: list, aspect: str, resolution: str, quality: str) -> str:
        width, height = presets.resolve(aspect, resolution)
        if quality not in batch_request.QUALITIES:
            raise ValueError(f"Unknown quality '{quality}'.")
        api_key, source = await cls._get_key()

        def fits(items) -> bool:
            model_management.throw_exception_if_processing_interrupted()  # Stop works while encoding
            return batch_request.size_for(prompt, width, height, quality, items) <= batch_request.MAX_JSONL_BYTES

        encoded, kind = references.encode_with_fallback(refs, fits) if refs else ([], "png")
        if kind == "jpeg":
            log.warning("[GPT Images] Request was over 200 MB with PNG references; sent them as JPEG quality 95.")
        request = batch_request.build(prompt, width, height, quality, encoded)
        log.info(
            "[GPT Images] Sunburst Batch: uploading %.1f MB (%d reference(s), %dx%d, quality %s, key from %s).",
            request.size / 1e6, len(encoded), width, height, quality, source,
        )
        try:
            batch_id = await asyncio.to_thread(batch_http.submit, request, api_key)
        except batch_http.KeyRejected:
            # OpenAI refused the upload itself (401), so nothing exists yet and a second
            # attempt with a new key cannot create a duplicate.
            if source == credentials.ENV_SOURCE:
                raise batch_http.BatchError(
                    "OpenAI rejected the key in the OPENAI_API_KEY environment variable (it may have expired "
                    "or been revoked). Nothing was created. Fix or remove the variable, then restart ComfyUI."
                ) from None
            api_key = await cls._ask_for_key(rejected=True)
            log.info("[GPT Images] Sunburst Batch: uploading again with the new key.")
            batch_id = await asyncio.to_thread(batch_http.submit, request, api_key)
        log.info("[GPT Images] Sunburst Batch: created %s", batch_id)
        return batch_id
