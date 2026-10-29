"""Three ComfyUI nodes.

GPT Images (two modes):
  Subscription:   prompt + optional references -> Codex CLI (ChatGPT sign-in) -> IMAGE.
  Sunburst Batch: prompt + settings + optional references -> one OpenAI Batch job;
                  outputs its batch id and dashboard link, no image.
Grok Images: Subscription (three modes, one dropdown):
  Image:             prompt + up to 5 references -> Grok Build (Grok sign-in) -> every image it made,
                     and the exact prompt Grok's agent sent to Grok Imagine for each.
  Video: frames:     prompt + a first, a middle and a last frame (any of them) -> one VIDEO.
  Video: references: prompt + up to 14 reference images (+ up to 3 voices) -> one VIDEO.
  A video with no image and no voice first makes its first frame from the prompt (output first_frame).
Antigravity Nano Banana: Subscription:
  prompt + up to 5 references -> Antigravity CLI (Google sign-in) -> every image it saved.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import uuid
from io import BytesIO

import numpy as np
import torch
from PIL import Image

import comfy.model_management as model_management
import folder_paths
from comfy_api.latest import InputImpl, io
from comfy_execution.graph_utils import ExecutionBlocker

from . import (agy_exec, batch_http, batch_request, codex_exec, credentials, grok_exec, grok_video, key_prompt,
               key_setup, presets, references, run_folder, saved_workflow)

log = logging.getLogger("Comfy-CLI-Subscription_Image-Video")

MODE_SUBSCRIPTION = "Subscription"
MODE_BATCH = "Sunburst Batch"
MODE_IMAGE = "Image"
MODE_VIDEO_FRAMES = "Video: frames"
MODE_VIDEO_REFERENCES = "Video: references"
IMAGE_SOCKETS = [f"image_{i}" for i in range(1, 17)]
FIVE_IMAGE_SOCKETS = IMAGE_SOCKETS[:5]
VIDEO_REFERENCE_SOCKETS = IMAGE_SOCKETS[:grok_video.MAX_REFERENCES]
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
                "result per run; use the run count to repeat."
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


def _write_references(refs: list, run_dir: str, max_bytes: int, tag: str, check=None, names=None) -> list[str]:
    """References as files in the run folder: PNG as they reach the node (RGBA keeps its
    transparency), JPEG only if a file would reach `max_bytes` and nothing is transparent.
    `check(sockets, encoded)` may refuse the encoded files before anything is written.
    `names` gives the file names (without extension) instead of reference_01, reference_02, ..."""
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
    return references.write_files(encoded, run_dir, names)


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
            category="Comfy-CLI-Subscription_Image-Video",
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

        run_dir = _new_run_folder("Comfy-CLI-Subscription_Image-Video-")
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


def _whole_number(value, name: str) -> int:
    value = _single(value, name)
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a whole number.")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise ValueError(f"{name} must be a whole number, got '{value}'.")


def _socket_number(socket: str) -> int:
    match = re.search(r"(\d+)$", socket)
    return int(match.group(1)) if match else 0


def _collect_frames(settings: dict) -> list:
    """(slot, image) for each connected frame socket, in the order first, middle, last. A frame is
    one image: a batch of several images in one socket is refused instead of guessed."""
    connected = []
    for slot in grok_video.FRAME_SLOTS:
        value = settings.get(slot)
        if value is None:
            continue
        items = references.collect({slot: value})
        if len(items) > 1:
            raise ValueError(
                f"{slot} received {len(items)} images, but a frame is one image. Pick one image (for example "
                "with an image selector node) before this node. Nothing was sent."
            )
        connected.extend(items)
    return connected


def _chosen_voices(settings: dict) -> list:
    """[(voice number, voice id)] for each voice dropdown that is not 'none'."""
    chosen = []
    for slot in range(1, grok_video.MAX_VOICES + 1):
        voice = _single(settings.get(f"voice_{slot}", grok_video.NO_VOICE), f"voice_{slot}")
        if voice != grok_video.NO_VOICE:
            chosen.append((slot, voice))
    return chosen


def _convert_prompt(tag: str, prompt: str, reference_sockets: list | None, voices: list) -> str:
    """The prompt as Grok's video tool must get it: "image 2" and "voice 1" in the text become
    Grok's own tags (<IMAGE_1>, <AUDIO_0>). `reference_sockets` lists the socket of each reference
    image in order, or is None in frames mode, where image mentions mean nothing to Grok's tags."""
    image_tags = None
    if reference_sockets is not None:
        image_tags = {}
        for index, socket in enumerate(reference_sockets):
            image_tags.setdefault(_socket_number(socket), []).append(index)
    voice_tags = {slot: index for index, (slot, _voice) in enumerate(voices)}
    converted = grok_video.convert_mentions(prompt, image_tags, voice_tags)
    if converted.replaced:
        log.info("[%s] In the prompt, %s.", tag,
                 "; ".join(f'"{said}" became {became}' for said, became in converted.replaced))
    for said in converted.unmatched:
        log.warning('[%s] The prompt says "%s", but no such image or voice is connected or chosen. '
                    "It was left as written.", tag, said)
    text = converted.text
    if reference_sockets is not None:
        silent = [f"image {_socket_number(socket)}" for index, socket in enumerate(reference_sockets)
                  if grok_video.image_tag(index) not in text]
        if silent:
            log.warning("[%s] The prompt does not mention %s. Grok may use a reference only loosely when the "
                        'prompt does not say what it is; write "image 1" and so on where each should appear.',
                        tag, ", ".join(dict.fromkeys(silent)))
    for index, (slot, voice) in enumerate(voices):
        if grok_video.voice_tag(index) not in text:
            log.warning('[%s] The prompt does not mention voice %d (%s). Write "voice %d" where it should speak.',
                        tag, slot, voice, slot)
    return text


def _load_video(path: str):
    """The finished video as ComfyUI's VIDEO value. It is read into memory, so Grok's own file stays
    untouched and the run folder can be deleted. Returns (video, width, height, seconds, megabytes)."""
    with open(path, "rb") as handle:
        data = handle.read()
    if not data:
        raise ValueError(f"The video Grok saved is empty: {path}")
    video = InputImpl.VideoFromFile(BytesIO(data))
    try:
        width, height = video.get_dimensions()
        seconds = video.get_duration()
    except Exception as exc:  # PyAV raises its own error types
        raise ValueError(f"The file Grok saved cannot be read as a video ({exc}): {path}") from None
    return video, width, height, seconds, len(data) / 1e6


def _video_settings() -> list:
    inputs = [
        io.Combo.Input(
            "aspect_ratio", options=list(grok_video.ASPECT_RATIOS), default=grok_video.DEFAULT_ASPECT_RATIO,
            tooltip="Shape of the video. These are the seven ratios Grok Build's video tool accepts."),
        io.Combo.Input(
            "seconds", options=list(grok_video.DURATIONS), default=grok_video.DEFAULT_DURATION,
            tooltip="Length of the video in seconds. Grok Build's video tool accepts 1 to 15."),
        io.Combo.Input(
            "resolution", options=list(grok_video.RESOLUTIONS), default=grok_video.DEFAULT_RESOLUTION,
            tooltip="480p or 720p, the two sizes Grok Build's video tool accepts."),
    ]
    for slot in range(1, grok_video.MAX_VOICES + 1):
        inputs.append(io.Combo.Input(
            f"voice_{slot}", options=list(grok_video.VOICE_CHOICES), default=grok_video.NO_VOICE,
            tooltip=(f'Voice {slot}. Grok Build\'s video tool takes up to {grok_video.MAX_VOICES} preset voices. '
                     f'Write "voice {slot}" in the prompt where this voice should speak. "none" leaves it out.')))
    return inputs


class GrokImagesNode(io.ComfyNode):
    TAG = "Grok Images"

    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="GrokImagesSubscription",
            display_name="Grok Images: Subscription",
            category="Comfy-CLI-Subscription_Image-Video",
            description=(
                "Image: generates an image with Grok Imagine through Grok Build and your Grok sign-in (a plan "
                "with image generation, such as SuperGrok), and returns it with the exact prompt Grok's agent "
                "sent to Imagine. Video: makes a video with Grok's video tool from the prompt, optional first, "
                "middle and last frames or reference images, and up to 3 voices. Never uses an xAI API key."
            ),
            search_aliases=["grok", "grok imagine", "grok video", "xai", "grok build", "supergrok"],
            inputs=[
                io.DynamicCombo.Input(
                    "mode",
                    options=[
                        io.DynamicCombo.Option(MODE_IMAGE, []),
                        io.DynamicCombo.Option(MODE_VIDEO_FRAMES, [
                            *_video_settings(),
                            io.Image.Input(
                                "first_frame", optional=True,
                                tooltip="The video starts on exactly this image. One image."),
                            io.Image.Input(
                                "middle_frame", optional=True,
                                tooltip="This image appears halfway through the video. One image."),
                            io.Image.Input(
                                "last_frame", optional=True,
                                tooltip="The video ends on exactly this image. One image."),
                        ]),
                        io.DynamicCombo.Option(MODE_VIDEO_REFERENCES, _video_settings()),
                    ],
                    tooltip=(
                        "Image: one picture from the prompt (and up to 5 reference images). "
                        "Video: frames: a video from the prompt and a first, middle and/or last frame (connect "
                        "any of the three; the image sockets below are not used). "
                        f"Video: references: a video from the prompt and up to {grok_video.MAX_REFERENCES} "
                        "reference images. Video needs Grok Build's privacy mode off and a plan with video "
                        "generation."
                    ),
                ),
                io.String.Input(
                    "prompt", multiline=True, default="",
                    tooltip=(
                        "Sent as written to Grok's agent, which passes it to Grok Imagine (Image) or to Grok's "
                        "video tool (Video); the imagine_prompt output shows what it got. In Video: references, "
                        'write "image 1", "image 2" and so on where a reference should appear, and "voice 1" for '
                        "a voice; the node turns them into Grok's own tags. A video with no image and no voice "
                        "first makes a picture from this prompt, which becomes its first frame (output "
                        "first_frame). Every queued run makes a new generation."
                    ),
                ),
                io.Autogrow.Input(
                    "images",
                    template=io.Autogrow.TemplateNames(
                        io.Image.Input("image"), names=VIDEO_REFERENCE_SOCKETS, min=0),
                    tooltip=(
                        f"Reference images, used in socket order. Image mode takes up to {grok_exec.MAX_REFERENCES}; "
                        f"Video: references takes up to {grok_video.MAX_REFERENCES}; Video: frames does not use "
                        "these sockets. Every image in a connected batch counts as one. In Video: references, call "
                        'them "image 1", "image 2" and so on in the prompt.'
                    ),
                ),
            ],
            outputs=[
                io.Image.Output(display_name="image", is_output_list=True,
                                tooltip="Image mode: every image Grok made in this run, one by one (normally one)."),
                io.String.Output(display_name="imagine_prompt", is_output_list=True,
                                 tooltip="The exact prompt Grok's agent sent to Grok Imagine (Image mode, one per "
                                         "image) or to the video tool (Video modes)."),
                io.Video.Output(display_name="video", tooltip="Video modes: the finished video."),
                io.Image.Output(display_name="first_frame",
                                tooltip="Video modes: the first frame, only when this node made it from your "
                                        "prompt (no image and no voice connected). Otherwise nothing."),
            ],
            is_output_node=True,
            is_input_list=True,
            not_idempotent=True,
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        return uuid.uuid4().hex  # every queued run is a new generation

    @classmethod
    async def execute(cls, mode=None, prompt=None, images=None) -> io.NodeOutput:
        if mode is None:
            # ComfyUI leaves a dropdown out when its value is not one of its options. That is how a Grok
            # workflow saved with version 5 arrives: its prompt text was restored into the mode dropdown.
            raise ValueError(
                "The mode dropdown holds text that is not one of its choices. A Grok workflow saved with "
                "version 5 opens like this: its old prompt text sits in the mode dropdown and the prompt box "
                "is empty. Choose Image, Video: frames or Video: references in the mode dropdown, write the "
                "prompt again, and run. Nothing was sent."
            )
        settings = mode if isinstance(mode, dict) else {"mode": mode}
        selected = _single(settings.get("mode"), "mode")
        text = _prompt_text(prompt)
        if selected == MODE_IMAGE:
            return await cls._image(text, images)
        if selected in (MODE_VIDEO_FRAMES, MODE_VIDEO_REFERENCES):
            return await cls._video(selected, settings, text, images)
        raise ValueError(f"Unknown mode '{selected}'.")

    @classmethod
    async def _image(cls, text: str, images) -> io.NodeOutput:
        refs = references.collect(images or {})
        _check_reference_count(refs, grok_exec.MAX_REFERENCES, "Grok Images in Image mode")
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
            return io.NodeOutput(tensors, prompts, ExecutionBlocker(None), ExecutionBlocker(None))
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)

    @classmethod
    async def _video(cls, selected: str, settings: dict, prompt: str, images) -> io.NodeOutput:
        aspect = _single(settings.get("aspect_ratio"), "aspect_ratio")
        seconds = _whole_number(settings.get("seconds"), "seconds")
        resolution = _single(settings.get("resolution"), "resolution")
        voices = _chosen_voices(settings)
        extra = references.collect(images or {})

        if selected == MODE_VIDEO_FRAMES:
            if extra:
                raise ValueError(
                    f"Video: frames takes its images from the first_frame, middle_frame and last_frame sockets, but "
                    f"{len(extra)} image(s) are connected to the image sockets ({', '.join(dict.fromkeys(s for s, _ in extra))}). "
                    "Disconnect them, or switch the mode to Video: references. Nothing was sent."
                )
            refs = _collect_frames(settings)
            names = [slot for slot, _ in refs]
            reference_sockets = None
        else:
            refs = extra
            _check_reference_count(refs, grok_video.MAX_REFERENCES, "Grok's video tool")
            names = None
            reference_sockets = [socket for socket, _ in refs]
        grok = grok_exec.find_grok()
        text = _convert_prompt(cls.TAG, prompt, reference_sockets, voices)

        run_dir = _new_run_folder("comfyui-grok-images-")
        try:
            paths = _write_references(refs, run_dir, grok_exec.MAX_REFERENCE_BYTES, cls.TAG, names=names)
            frame_paths = dict(zip(names or [], paths))
            request = grok_video.VideoRequest(
                prompt=text, aspect_ratio=aspect, duration=seconds, resolution=resolution,
                first_frame=frame_paths.get("first_frame"),
                middle_frame=frame_paths.get("middle_frame"),
                last_frame=frame_paths.get("last_frame"),
                references=tuple(paths) if selected == MODE_VIDEO_REFERENCES else (),
                voices=tuple(voice for _slot, voice in voices),
            )
            log.info("[%s] Starting Grok Build (video, Grok sign-in): %s.", cls.TAG, grok_video.describe(request))
            try:
                result = await grok_exec.generate_video(
                    grok, request, run_dir, is_interrupted=model_management.processing_interrupted
                )
            except grok_exec.GrokInterrupted:
                model_management.throw_exception_if_processing_interrupted()
                raise

            video, width, height, length, megabytes = _load_video(result.video.path)
            sent = result.video.prompt
            if not sent:
                log.warning("[%s] Grok Build's report did not include the prompt its agent sent to the video tool, "
                            "so imagine_prompt is empty.", cls.TAG)
            elif " ".join(sent.split()) != " ".join(text.split()):
                log.warning("[%s] Grok's agent changed the prompt before sending it to the video tool. "
                            "The imagine_prompt output shows what it sent.", cls.TAG)
            for label, wanted, got in (("aspect ratio", request.aspect_ratio, result.video.aspect_ratio),
                                       ("seconds", request.duration, result.video.duration),
                                       ("resolution", request.resolution, result.video.resolution)):
                if got is not None and got != wanted:
                    log.warning("[%s] Grok's agent set the %s to %s instead of %s.", cls.TAG, label, got, wanted)

            first_frame = ExecutionBlocker(None)
            if result.first_frame is not None:
                first_frame = _load_image(result.first_frame.path)
                log.info("[%s] First frame made from the prompt (%s): %dx%d: %s", cls.TAG, result.first_frame.tool,
                         first_frame.shape[2], first_frame.shape[1], result.first_frame.path)
            elif request.make_first_frame:
                log.warning("[%s] Grok's report did not say where the first frame it made was saved, so "
                            "first_frame has no image.", cls.TAG)
            cost = f"${result.cost_usd:.4f}" if result.cost_usd is not None else "not reported"
            log.info("[%s] Video ready after %.1f s: %dx%d, %.1f s long, %.1f MB. Cost reported by Grok Build: %s. "
                     "Grok keeps its own copy: %s", cls.TAG, result.elapsed, width, height, length, megabytes, cost,
                     result.video.path)
            if result.extra_videos:
                log.warning("[%s] Grok made %d more video(s) in this run (one was asked for); only the first is "
                            "output. The others stay in Grok's own folder: %s", cls.TAG, len(result.extra_videos),
                            ", ".join(result.extra_videos))
            return io.NodeOutput(ExecutionBlocker(None), [sent], video, first_frame)
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)


class AntigravityNanoBananaNode(io.ComfyNode):
    TAG = "Antigravity Nano Banana"

    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AntigravityNanoBananaSubscription",
            display_name="Antigravity Nano Banana: Subscription",
            category="Comfy-CLI-Subscription_Image-Video",
            description=(
                "Generates an image through the Antigravity CLI and your Google sign-in, and returns every "
                "image Antigravity saved in the run. Refuses Gemini API-key mode."
            ),
            search_aliases=["antigravity", "agy", "nano banana", "gemini image", "google image"],
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
        _check_reference_count(refs, agy_exec.MAX_REFERENCES, "Antigravity Nano Banana")
        agy = agy_exec.find_agy()
        agy_exec.check_sign_in_mode()

        run_dir = _new_run_folder("comfyui-nano-banana-")
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
