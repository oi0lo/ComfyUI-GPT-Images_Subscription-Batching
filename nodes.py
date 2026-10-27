"""Three ComfyUI nodes.

GPT Images (two modes):
  Subscription:   prompt + optional references -> Codex CLI (ChatGPT sign-in) -> IMAGE.
  Sunburst Batch: prompt + settings + optional references -> one OpenAI Batch job;
                  outputs its batch id and dashboard link, no image.
Grok Images: Subscription:
  prompt + up to 5 references -> Grok Build (Grok sign-in) -> every image it made, and the
  exact prompt Grok's agent sent to Grok Imagine for each.
Antigravity Images: Subscription:
  prompt + up to 5 references -> Antigravity CLI (Google sign-in) -> every image it saved.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import uuid

import numpy as np
import torch
from PIL import Image

import comfy.model_management as model_management
import folder_paths
from comfy_api.latest import io
from comfy_execution.graph_utils import ExecutionBlocker

from . import (agy_exec, batch_http, batch_request, codex_exec, credentials, grok_exec, key_prompt, key_setup,
               presets, references, run_folder, saved_workflow)

log = logging.getLogger("comfyui-gpt-images")

MODE_SUBSCRIPTION = "Subscription"
MODE_BATCH = "Sunburst Batch"
IMAGE_SOCKETS = [f"image_{i}" for i in range(1, 17)]
FIVE_IMAGE_SOCKETS = IMAGE_SOCKETS[:5]
KEY_WINDOW_TIMEOUT = 10 * 60


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


def _prompt_text(prompt) -> str:
    text = _single(prompt, "prompt")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("The prompt is empty.")
    return text


def _check_reference_count(refs: list, limit: int, what: str) -> None:
    if len(refs) > limit:
        raise ValueError(
            f"{what} accepts at most {limit} reference images; {len(refs)} are connected "
            "(every image in a batch counts)."
        )


def _new_run_folder(prefix: str) -> str:
    """A new folder in the user's temp folder, never in Windows' folder (System32) or ComfyUI's."""
    comfy_folders = [os.path.dirname(os.path.realpath(folder_paths.__file__))]
    if getattr(folder_paths, "base_path", None):
        comfy_folders.append(folder_paths.base_path)
    return run_folder.create(prefix, avoid=comfy_folders)


def _write_references(refs: list, run_dir: str, max_bytes: int, tag: str, check=None) -> list[str]:
    """References as files in the run folder: PNG as they reach the node (RGBA keeps its
    transparency), JPEG only if a file would reach `max_bytes` and nothing is transparent.
    `check(sockets, encoded)` may refuse the encoded files before anything is written."""
    if not refs:
        return []

    def fits(items) -> bool:
        model_management.throw_exception_if_processing_interrupted()  # Stop works while encoding
        return all(len(i.data) < max_bytes for i in items)

    encoded, kind = references.encode_with_fallback(refs, fits)
    if check is not None:
        check([socket for socket, _ in refs], encoded)
    if kind == "jpeg":
        log.warning("[%s] References were over %d MB as PNG; sent as JPEG quality 95.", tag, max_bytes // 1_000_000)
    transparent = sum(1 for item in encoded if item.alpha)
    if transparent:
        log.info("[%s] %d reference(s) with transparency, sent as RGBA PNG.", tag, transparent)
    return references.write_files(encoded, run_dir)


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
                                io.Boolean.Input(
                                    "transparent_background",
                                    default=False,
                                    label_on="transparent",
                                    label_off="opaque",
                                    tooltip="On: transparent background (PNG). Off: opaque background.",
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
            # The workflow sent with the run, to read the switch's raw saved value (see execute).
            hidden=[io.Hidden.unique_id, io.Hidden.extra_pnginfo],
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
        text = _prompt_text(prompt)
        refs = references.collect(images or {})

        if selected == MODE_SUBSCRIPTION:
            image = await cls._subscription(text, refs)
            return io.NodeOutput(image, ExecutionBlocker(None), ExecutionBlocker(None))

        if selected == MODE_BATCH:
            aspect = settings.get("aspect_ratio") or {}
            transparent = _single(settings.get("transparent_background", False), "transparent background")
            if not isinstance(transparent, bool):
                raise ValueError("transparent_background must be on or off.")
            # ComfyUI turns any text in an on/off input into True. A Batch workflow saved with
            # version 3 and restored by position puts its prompt text into this switch.
            leftover = saved_workflow.leftover_text(saved_workflow.saved_value(
                cls.hidden.extra_pnginfo, cls.hidden.unique_id, "mode.transparent_background"))
            if leftover is not None:
                raise ValueError(
                    f"The transparent_background switch holds leftover text from an older workflow "
                    f"(\"{leftover}\"), which ComfyUI would read as on. Set the switch to opaque or "
                    "transparent, check the prompt, and run again. Nothing was sent."
                )
            batch_id = await cls._batch(
                text,
                refs,
                _single(aspect.get("aspect_ratio"), "aspect ratio"),
                _single(aspect.get("resolution"), "resolution"),
                _single(settings.get("quality"), "quality"),
                transparent,
            )
            return io.NodeOutput(ExecutionBlocker(None), batch_id, batch_http.dashboard_url(batch_id))

        raise ValueError(f"Unknown mode '{selected}'.")

    @classmethod
    async def _subscription(cls, prompt: str, refs: list) -> torch.Tensor:
        _check_reference_count(refs, codex_exec.MAX_REFERENCES, "Subscription mode")
        codex = codex_exec.find_codex()
        await asyncio.to_thread(codex_exec.check_login, codex)

        run_dir = _new_run_folder("comfyui-gpt-images-")
        try:
            paths = _write_references(refs, run_dir, codex_exec.MAX_REFERENCE_BYTES, "GPT Images")
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
    async def _batch(cls, prompt: str, refs: list, aspect: str, resolution: str, quality: str,
                     transparent: bool = False) -> str:
        width, height = presets.resolve(aspect, resolution)
        if quality not in batch_request.QUALITIES:
            raise ValueError(f"Unknown quality '{quality}'.")
        api_key, source = await cls._get_key()

        def fits(items) -> bool:
            model_management.throw_exception_if_processing_interrupted()  # Stop works while encoding
            size = batch_request.size_for(prompt, width, height, quality, items, transparent)
            return size <= batch_request.MAX_JSONL_BYTES

        encoded, kind = references.encode_with_fallback(refs, fits) if refs else ([], "png")
        if kind == "jpeg":
            log.warning("[GPT Images] Request was over 200 MB with PNG references; sent them as JPEG quality 95.")
        request = batch_request.build(prompt, width, height, quality, encoded, transparent)
        with_alpha = sum(1 for item in encoded if item.alpha)
        log.info(
            "[GPT Images] Sunburst Batch: uploading %.1f MB (%d reference(s)%s, %dx%d, quality %s, %s background, "
            "key from %s).",
            request.size / 1e6, len(encoded), f", {with_alpha} with transparency" if with_alpha else "",
            width, height, quality, "transparent" if transparent else "opaque", source,
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


def _subscription_inputs(prompt_tooltip: str, references_tooltip: str) -> list:
    return [
        io.String.Input("prompt", multiline=True, default="", tooltip=prompt_tooltip),
        io.Autogrow.Input(
            "images",
            template=io.Autogrow.TemplateNames(io.Image.Input("image"), names=FIVE_IMAGE_SOCKETS, min=0),
            tooltip=references_tooltip,
        ),
    ]


class GrokImagesNode(io.ComfyNode):
    TAG = "Grok Images"

    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="GrokImagesSubscription",
            display_name="Grok Images: Subscription",
            category="Grok Images",
            description=(
                "Generates an image with Grok Imagine through Grok Build and your Grok sign-in (a plan with "
                "image generation, such as SuperGrok), and returns it with the exact prompt Grok's agent sent "
                "to Imagine. Never uses an xAI API key."
            ),
            search_aliases=["grok", "grok imagine", "xai", "grok build", "supergrok"],
            inputs=_subscription_inputs(
                "Sent as written to Grok's agent, which passes it to Grok Imagine; the imagine_prompt output "
                "shows what Imagine actually got. Ask for an aspect ratio here if you need one. Every queued "
                "run makes a new generation.",
                "Optional reference images, up to 5, used in socket order. Every image in a connected batch "
                "counts as one reference. Grok's image tool shrinks references over 400 KB to at most 768 pixels.",
            ),
            outputs=[
                io.Image.Output(display_name="image", is_output_list=True,
                                tooltip="Every image Grok made in this run, one by one (normally one)."),
                io.String.Output(display_name="imagine_prompt", is_output_list=True,
                                 tooltip="The exact prompt Grok's agent sent to Grok Imagine, one per image."),
            ],
            is_output_node=True,
            is_input_list=True,
            not_idempotent=True,
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        return uuid.uuid4().hex  # every queued run is a new generation

    @classmethod
    async def execute(cls, prompt, images=None) -> io.NodeOutput:
        text = _prompt_text(prompt)
        refs = references.collect(images or {})
        _check_reference_count(refs, grok_exec.MAX_REFERENCES, "Grok Images")
        grok = grok_exec.find_grok()

        run_dir = _new_run_folder("comfyui-grok-images-")
        try:
            paths = _write_references(refs, run_dir, grok_exec.MAX_REFERENCE_BYTES, cls.TAG,
                                      check=grok_exec.check_reference_sizes)
            log.info("[%s] Starting Grok Build (image tools only, Grok sign-in) with %d reference(s).",
                     cls.TAG, len(paths))
            try:
                result = await grok_exec.generate(
                    grok, text, paths, run_dir, is_interrupted=model_management.processing_interrupted
                )
            except grok_exec.GrokInterrupted:
                model_management.throw_exception_if_processing_interrupted()
                raise
            tensors, prompts = [], []
            for number, image in enumerate(result.images, start=1):
                tensor = _load_image(image.path)
                tensors.append(tensor)
                prompts.append(image.prompt)
                log.info("[%s] Image %d of %d: %dx%d, aspect ratio %s, %s: %s", cls.TAG, number, len(result.images),
                         tensor.shape[2], tensor.shape[1], grok_exec.aspect_note(image, len(paths)), image.tool,
                         image.path)
                if paths and image.tool == "image_gen":
                    log.warning("[%s] Image %d was made with image_gen, which takes no reference images: Grok's "
                                "agent did not use your references for it.", cls.TAG, number)
            cost = f"${result.cost_usd:.4f}" if result.cost_usd is not None else "not reported"
            log.info("[%s] Done after %.1f s (session %s). Cost reported by Grok Build: %s.", cls.TAG,
                     result.elapsed, result.session_id, cost)
            if len(result.images) > 1:
                log.warning("[%s] Grok made %d images in this run (one was asked for); all are output.",
                            cls.TAG, len(result.images))
            return io.NodeOutput(tensors, prompts)
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)


class AntigravityImagesNode(io.ComfyNode):
    TAG = "Antigravity Images"

    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AntigravityImagesSubscription",
            display_name="Antigravity Images: Subscription",
            category="Antigravity Images",
            description=(
                "Generates an image through the Antigravity CLI and your Google sign-in, and returns every "
                "image Antigravity saved in the run. Refuses Gemini API-key mode."
            ),
            search_aliases=["antigravity", "agy", "gemini image", "google image"],
            inputs=_subscription_inputs(
                "Sent as written to Antigravity's agent, which hands it to its image generator; that writes its "
                "own final prompt. Ask for an aspect ratio here if you need one. Every queued run makes a new "
                "generation.",
                "Optional reference images, up to 5, used in socket order. Every image in a connected batch "
                "counts as one reference.",
            ),
            outputs=[
                io.Image.Output(display_name="image", is_output_list=True,
                                tooltip="Every image Antigravity saved in this run, one by one, oldest first "
                                        "(one is asked for; its image generator may make up to three attempts)."),
            ],
            is_output_node=True,
            is_input_list=True,
            not_idempotent=True,
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        return uuid.uuid4().hex  # every queued run is a new generation

    @classmethod
    async def execute(cls, prompt, images=None) -> io.NodeOutput:
        text = _prompt_text(prompt)
        refs = references.collect(images or {})
        _check_reference_count(refs, agy_exec.MAX_REFERENCES, "Antigravity Images")
        agy = agy_exec.find_agy()
        agy_exec.check_sign_in_mode()

        run_dir = _new_run_folder("comfyui-antigravity-images-")
        try:
            paths = _write_references(refs, run_dir, agy_exec.MAX_REFERENCE_BYTES, cls.TAG)
            log.info("[%s] Starting Antigravity (agent model %s, Google sign-in) in %s with %d reference(s).",
                     cls.TAG, agy_exec.AGENT_MODEL, run_dir, len(paths))
            try:
                result = await agy_exec.generate(
                    agy, text, paths, run_dir, is_interrupted=model_management.processing_interrupted
                )
            except agy_exec.AgyInterrupted:
                model_management.throw_exception_if_processing_interrupted()
                raise
            tensors = []
            for number, path in enumerate(result.images, start=1):
                tensor = _load_image(path)
                tensors.append(tensor)
                log.info("[%s] Image %d of %d: %dx%d: %s", cls.TAG, number, len(result.images),
                         tensor.shape[2], tensor.shape[1], path)
            usage = result.usage or {}
            tokens = ", ".join(f"{key.replace('_', ' ')} {usage[key]}" for key in
                               ("input_tokens", "output_tokens", "total_tokens") if key in usage)
            log.info("[%s] Done after %.1f s (conversation %s)%s.", cls.TAG, result.elapsed,
                     result.conversation_id, f"; agent usage: {tokens}" if tokens else "")
            if len(result.images) > 1:
                log.warning("[%s] Antigravity saved %d images in this run (one was asked for); all are output, "
                            "oldest first.", cls.TAG, len(result.images))
            return io.NodeOutput(tensors)
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)
