"""Grok Images, video mode: what Grok Build's video tool accepts, how a request becomes the
instruction for Grok's agent, and how "image 1" / "voice 1" in a prompt become Grok's tags.

The rules follow Grok Build's public source (github.com/xai-org/grok-build, synced 2026-09-29;
crates/codegen/xai-grok-tools/src/implementations/grok_build/video_gen/mod.rs, model grok-imagine-video-1.5)
and its reference_to_video tool:

    prompt            required, not empty
    aspect_ratio      required: 1:1, 16:9, 9:16, 4:3, 3:4, 3:2 or 2:3
    duration          1 to 15 seconds (default 6)
    resolution_name   480p or 720p (default 480p)
    images            up to 14 reference images; the prompt refers to them as <IMAGE_0>, <IMAGE_1> ...
    first_frame       an image pinned as the exact first frame
    last_frame        an image pinned as the exact last frame
    keyframes         up to 4 {image, timestamp_s}; each image appears at that time, strictly inside the
                      clip (0 < t < duration), snapped by Grok to a 1/3-second grid
    voices            up to 3 preset voice ids (the xAI text-to-speech voices); the prompt refers to
                      them as <AUDIO_0>, <AUDIO_1> ...
    at least one of images, voices, first_frame, last_frame, keyframes is required (there is no
    text-only video tool); tag numbers follow the order first_frame, images, keyframes, last_frame;
    video and audio files are not accepted.

This module has no ComfyUI imports, so it can be tested on its own.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

ASPECT_RATIOS = ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3")
RESOLUTIONS = ("480p", "720p")
DURATIONS = tuple(range(1, 16))
DEFAULT_ASPECT_RATIO = "16:9"
DEFAULT_RESOLUTION = "720p"
DEFAULT_DURATION = 6
MAX_REFERENCES = 14
MAX_VOICES = 3
FRAME_SLOTS = ("first_frame", "middle_frame", "last_frame")

NO_VOICE = "none"
# The xAI text-to-speech voices (docs.x.ai, text to speech, read 2026-10-28). Grok Build's video tool
# takes "a voice identifier from the built-in roster ... same voices as the xAI text-to-speech API";
# it answers an unknown identifier with the list it knows, which this node then shows.
VOICE_IDS = (
    "altair", "ara", "atlas", "aurora", "carina", "castor", "celeste", "cosmo", "eve", "helios",
    "helix", "iris", "kepler", "leo", "liora", "lumen", "luna", "lux", "naksh", "orion",
    "perseus", "rex", "rigel", "sal", "sirius", "ursa", "zagan", "zenith",
)
VOICE_CHOICES = (NO_VOICE,) + VOICE_IDS


class VideoRequestError(ValueError):
    pass


@dataclass(frozen=True)
class VideoRequest:
    """One video. The paths are image files the node wrote into this run's own folder."""

    prompt: str  # exactly what Grok's video tool must receive (tags already in place)
    aspect_ratio: str = DEFAULT_ASPECT_RATIO
    duration: int = DEFAULT_DURATION
    resolution: str = DEFAULT_RESOLUTION
    first_frame: str | None = None
    middle_frame: str | None = None
    last_frame: str | None = None
    references: tuple = ()
    voices: tuple = ()

    @property
    def make_first_frame(self) -> bool:
        """With no image and no voice there is nothing for the video tool to start from, so the
        prompt first makes a still (image_gen), which becomes the exact first frame."""
        return not (self.first_frame or self.middle_frame or self.last_frame or self.references or self.voices)

    @property
    def image_paths(self) -> list:
        """Every input image file, for the check that a result is never one of them."""
        return [p for p in (self.first_frame, self.middle_frame, self.last_frame, *self.references) if p]

    @property
    def tools(self) -> tuple:
        return ("image_gen", "reference_to_video") if self.make_first_frame else ("reference_to_video",)

    def validate(self) -> None:
        if not isinstance(self.prompt, str) or not self.prompt.strip():
            raise VideoRequestError("The prompt is empty.")
        if self.aspect_ratio not in ASPECT_RATIOS:
            raise VideoRequestError(
                f"Unknown aspect ratio '{self.aspect_ratio}'. Grok Build accepts: {', '.join(ASPECT_RATIOS)}.")
        if self.resolution not in RESOLUTIONS:
            raise VideoRequestError(
                f"Unknown resolution '{self.resolution}'. Grok Build accepts: {', '.join(RESOLUTIONS)}.")
        if not isinstance(self.duration, int) or isinstance(self.duration, bool) or self.duration not in DURATIONS:
            raise VideoRequestError(f"Duration must be a whole number of seconds from {DURATIONS[0]} to {DURATIONS[-1]}.")
        if len(self.references) > MAX_REFERENCES:
            raise VideoRequestError(
                f"Grok's video tool accepts at most {MAX_REFERENCES} reference images; {len(self.references)} are connected.")
        if len(self.voices) > MAX_VOICES:
            raise VideoRequestError(f"Grok's video tool accepts at most {MAX_VOICES} voices.")
        unknown = [v for v in self.voices if v not in VOICE_IDS]
        if unknown:
            raise VideoRequestError(f"Unknown voice: {', '.join(unknown)}.")
        if self.references and (self.first_frame or self.middle_frame or self.last_frame):
            raise VideoRequestError("Reference images and pinned frames cannot be used in the same run.")


def middle_timestamp(duration: int) -> float:
    """The middle frame is pinned halfway through the clip. Grok snaps it to its 1/3-second grid."""
    return duration / 2


def _seconds(value: float) -> str:
    return repr(float(value))


# ------------------------------------------------------------------ "image 1" and "voice 1" in a prompt

# "image 2", "Image 2", "image2", "image_2", "image #2". Not "images 2", not a word ending in
# "image" ("subimage 2"), and not Grok's own tags (<IMAGE_2>).
IMAGE_MENTION = re.compile(r"(?<![\w<])image[ _]?#?(\d{1,2})(?![\w>])", re.IGNORECASE)
VOICE_MENTION = re.compile(r"(?<![\w<])voice[ _]?#?(\d)(?![\w>])", re.IGNORECASE)


@dataclass
class Converted:
    text: str
    replaced: list  # (what the prompt said, the tag(s) it became)
    unmatched: list  # mentions that name an image or voice that is not there; left as written


def image_tag(index: int) -> str:
    return f"<IMAGE_{index}>"


def voice_tag(index: int) -> str:
    return f"<AUDIO_{index}>"


def convert_mentions(prompt: str, image_tags: dict | None, voice_tags: dict) -> Converted:
    """Replace "image 2" with Grok's tag for the image on socket image_2, and "voice 1" with the tag
    for the voice chosen in voice_1.

    image_tags: socket number -> the 0-based tag numbers of that socket's images (a socket holding a
                batch of images has several), or None to leave image mentions alone (frames mode).
    voice_tags: voice slot number -> the 0-based tag number of that voice.
    """
    replaced, unmatched = [], []

    def swap_image(match: re.Match) -> str:
        number = int(match.group(1))
        indexes = image_tags.get(number)
        if not indexes:
            unmatched.append(match.group(0))
            return match.group(0)
        tags = " ".join(image_tag(i) for i in indexes)
        replaced.append((match.group(0), tags))
        return tags

    def swap_voice(match: re.Match) -> str:
        number = int(match.group(1))
        index = voice_tags.get(number)
        if index is None:
            unmatched.append(match.group(0))
            return match.group(0)
        tag = voice_tag(index)
        replaced.append((match.group(0), tag))
        return tag

    text = prompt
    if image_tags is not None:
        text = IMAGE_MENTION.sub(swap_image, text)
    text = VOICE_MENTION.sub(swap_voice, text)
    return Converted(text, replaced, unmatched)


# ------------------------------------------------------------------ the instruction for Grok's agent

def build_video_instruction(request: VideoRequest) -> str:
    """The text Grok's agent receives. It names every argument of the video tool, so the agent
    only has to pass them on; the prompt follows at the end and is passed unchanged."""
    aspect, resolution = f'"{request.aspect_ratio}"', f'"{request.resolution}"'
    arguments: list = [("prompt", "the video prompt below, unchanged")]
    lines: list = []
    if request.make_first_frame:
        lines += [
            "Make one short video in two steps. Call each tool exactly once, do not use any other tool, and "
            "do not make another attempt if a tool fails.",
            f"1. Call image_gen with the video prompt below, unchanged, as its prompt, and aspect_ratio {aspect}. "
            "This image becomes the first frame of the video.",
            "2. Call reference_to_video with exactly these arguments and no others:",
        ]
        arguments.append(("first_frame", "the absolute path of the image from step 1"))
    else:
        lines += [
            "Make one short video. Call the reference_to_video tool exactly once, do not use any other tool, "
            "and do not make another attempt if the tool fails. Pass exactly these arguments and no others:",
        ]
        if request.first_frame:
            arguments.append(("first_frame", request.first_frame))
        if request.middle_frame:
            arguments.append((
                "keyframes",
                f"a list with one entry: image {request.middle_frame}, timestamp_s {_seconds(middle_timestamp(request.duration))}",
            ))
        if request.last_frame:
            arguments.append(("last_frame", request.last_frame))
        if request.references:
            arguments.append(("images", ["these image files, in this order:", *request.references]))
        if request.voices:
            arguments.append(("voices", json.dumps(list(request.voices))))
    arguments += [("aspect_ratio", aspect), ("duration", str(request.duration)), ("resolution_name", resolution)]

    for name, value in arguments:
        if isinstance(value, list):
            lines.append(f"   {name}: {value[0]}")
            lines.extend(f"      {item}" for item in value[1:])
        else:
            lines.append(f"   {name}: {value}")
    lines += [
        "The prompt may contain tags such as <IMAGE_0> or <AUDIO_0>; leave them exactly as they are.",
        "When " + ("both tools have" if request.make_first_frame else "the tool has")
        + " finished, reply with one short sentence.",
        "",
        "VIDEO PROMPT:",
        request.prompt,
    ]
    return "\n".join(lines) + "\n"


def describe(request: VideoRequest) -> str:
    """One line for the ComfyUI log."""
    parts = [f"{request.aspect_ratio}", f"{request.duration} s", request.resolution]
    if request.make_first_frame:
        parts.append("no input: the prompt makes the first frame")
    if request.first_frame:
        parts.append("first frame connected")
    if request.middle_frame:
        parts.append(f"middle frame connected (at {_seconds(middle_timestamp(request.duration))} s)")
    if request.last_frame:
        parts.append("last frame connected")
    if request.references:
        parts.append(f"{len(request.references)} reference image(s)")
    if request.voices:
        parts.append("voice " + ", ".join(request.voices))
    return ", ".join(parts)
