"""Grok Images: one image or one video through Grok Build (`grok`) and the user's Grok sign-in.

The command follows the user's live test (2026-10-24: headless run, tool limit and
sign-in worked; xAI then refused the image for account reasons) and Grok Build's
public source (github.com/xai-org/grok-build, synced 2026-09-29):

    grok --no-auto-update --prompt-file <instruction> --verbatim --cwd <run dir>
         -s <new uuid> --tools image_gen,image_edit --always-approve
         --output-format streaming-json --max-turns 4

Video runs use the same command with `--tools reference_to_video` (or `image_gen,reference_to_video`
when the prompt first makes the still that becomes the first frame) and more turns. The user's own
test (2026-10-28) showed Grok's agent calling image_gen and then reference_to_video with the
arguments it was given.

No --effort: on the user's machine (2026-10-27) it stopped the run before the agent
started ("Couldn't apply reasoning effort: Invalid params: "unknown model id""). In
headless.rs, --effort without -m sends a model change for the session's current model,
which Grok's agent did not recognise; without -m and --effort that step is skipped.

`streaming-json` prints one JSON object per line (headless/reducer/acp.rs):
    {"type":"tool_call","toolCallId":..,"toolName":"image_gen"|"image_edit"|"reference_to_video","rawInput":{..}}
    {"type":"tool_call_update","toolCallId":..,"status":"completed"|"failed","rawOutput":..}
        rawOutput: {"type":"ImageGen"|"ImageEdit"|"ReferenceToVideo","path":<absolute path>,..}  (saved)
                   {"type":"ReferenceToVideo","path":"","uploaded_url":..}   (only with a ZDR bucket set up)
                   {"type":"Text","text":..}            (for example the SuperGrok upgrade message)
                   {"error":"tool_execution_failed","message":<the tool's exact error>}
    {"type":"text","data":..}  {"type":"error","message":..}  {"type":"max_turns_reached"}
    {"type":"end","stopReason":..,"sessionId":..,..usage and cost..}
The file path comes from the tool's own result and the prompt from the tool call's
input, so neither depends on what the agent says afterwards.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
import uuid
from collections import deque
from dataclasses import dataclass, field

try:
    from . import agent_process, grok_video
except ImportError:  # loaded on its own (tests)
    import agent_process
    import grok_video

TIMEOUT_SECONDS = 15 * 60
VIDEO_TIMEOUT_SECONDS = 25 * 60  # Grok Build waits up to 300 s for a video, plus the still and the download
MAX_REFERENCES = 5  # Imagine edits take "up to five source images" (xAI docs)
MAX_REFERENCE_BYTES = 50_000_000  # same per-file ceiling as Subscription mode
IMAGE_TOOLS = ("image_gen", "image_edit")
VIDEO_TOOLS = ("reference_to_video", "image_to_video")
MAX_TURNS = 4  # one tool call and the closing reply, with room to spare
VIDEO_MAX_TURNS = 4  # reference_to_video alone
VIDEO_WITH_FIRST_FRAME_MAX_TURNS = 6  # image_gen, then reference_to_video (the user's test used 6)
STRIPPED_ENV = ("XAI_API_KEY",)
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")
VIDEO_EXTENSIONS = (".mp4",)
MEDIA_OUTPUT_TYPES = ("ImageGen", "ImageEdit", "ReferenceToVideo", "ImageToVideo")
# Grok Build's image_edit (image_edit/mod.rs): a PNG or JPEG of at most 400 KB is used as it
# is; a larger one is decoded, refused above 12,000,000 pixels, otherwise shrunk to at most
# 768 pixels and re-encoded under 400 KB as PNG or JPEG, whichever is smaller.
PASSTHROUGH_BYTES = 400 * 1024
MAX_DECODE_PIXELS = 12_000_000


class GrokError(RuntimeError):
    pass


class GrokInterrupted(Exception):
    pass


# ------------------------------------------------------------------ discovery

def grok_home() -> str:
    return os.environ.get("GROK_HOME") or os.path.join(os.path.expanduser("~"), ".grok")


def child_env() -> dict:
    """Grok sign-in only: no API key, and Grok's own switch that refuses API-key login."""
    env = dict(os.environ)
    for name in STRIPPED_ENV:
        env.pop(name, None)
    env["GROK_DISABLE_API_KEY_AUTH"] = "1"
    return env


def find_grok() -> str:
    """The installer's copy in <GROK_HOME>\\bin first, then PATH."""
    name = "grok.exe" if sys.platform == "win32" else "grok"
    candidates = [os.path.join(grok_home(), "bin", name)]
    on_path = shutil.which("grok")
    if on_path and (sys.platform != "win32" or on_path.lower().endswith(".exe")):
        candidates.append(on_path)
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    raise GrokError(
        "Grok Build not found. Install it (PowerShell: irm https://x.ai/cli/install.ps1 | iex), "
        "then run 'grok login' once in a terminal and sign in with your Grok account."
    )


def check_reference_sizes(sockets: list[str], encoded: list) -> None:
    """Stop before running when Grok's image tool would refuse a reference for its size,
    instead of spending a run on that refusal. Nothing is resized here: the user decides."""
    refused = [
        f"{socket} ({item.width} x {item.height})"
        for socket, item in zip(sockets, encoded)
        if len(item.data) > PASSTHROUGH_BYTES and item.width * item.height > MAX_DECODE_PIXELS
    ]
    if refused:
        raise GrokError(
            "Grok's image tool refuses references over 12,000,000 pixels (unless the file is at most 400 KB): "
            f"{', '.join(refused)}. Reduce them before this node. Nothing was sent."
        )


# ------------------------------------------------------------------ one run

def build_instruction(prompt: str, reference_paths: list[str]) -> str:
    if reference_paths:
        lines = [
            "Call the image_edit tool exactly once. Pass the image prompt below unchanged as its prompt, "
            "and pass these reference images as its image list, by their absolute paths, in this order:"
        ]
        lines.extend(reference_paths)
    else:
        lines = ["Call the image_gen tool exactly once. Pass the image prompt below unchanged as its prompt."]
    lines += [
        "Do not use any other tool and do not make another attempt if the tool fails. "
        "When the tool has finished, reply with one short sentence.",
        "",
        "IMAGE PROMPT:",
        prompt,
    ]
    return "\n".join(lines) + "\n"


def build_command(grok: str, instruction_path: str, run_dir: str, session_id: str,
                  tools: tuple = IMAGE_TOOLS, max_turns: int = MAX_TURNS) -> list[str]:
    return [
        grok, "--no-auto-update",
        "--prompt-file", instruction_path, "--verbatim",
        "--cwd", run_dir, "-s", session_id,
        "--tools", ",".join(tools), "--always-approve",
        "--output-format", "streaming-json",
        "--max-turns", str(max_turns),
    ]


@dataclass
class ToolCall:
    """One call of one of Grok's media tools, as the stream reports it."""
    tool: str
    prompt: str | None = None
    aspect_ratio: str | None = None
    duration: int | None = None
    resolution: str | None = None
    status: str | None = None
    path: str | None = None
    uploaded_url: str | None = None
    error: str | None = None
    text: str | None = None


ImageCall = ToolCall  # the name used before video support


@dataclass
class GrokEvents:
    calls: dict = field(default_factory=dict)  # toolCallId -> ToolCall, in arrival order
    other_tools: list = field(default_factory=list)
    agent_text: str = ""
    errors: deque = field(default_factory=lambda: deque(maxlen=6))
    max_turns: bool = False
    end: dict | None = None
    stderr_tail: deque = field(default_factory=lambda: deque(maxlen=40))

    def feed_stdout(self, stream) -> None:
        for raw in stream:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                self.stderr_tail.append(line[:500])  # not an event: keep it like other output
                continue
            if isinstance(event, dict):
                self.handle(event)

    def feed_stderr(self, stream) -> None:
        for raw in stream:
            text = raw.decode("utf-8", "replace").rstrip()
            if text:
                self.stderr_tail.append(text[:500])

    def handle(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "tool_call":
            name = str(event.get("toolName") or "")
            call_id = str(event.get("toolCallId") or len(self.calls))
            if name in IMAGE_TOOLS or name in VIDEO_TOOLS:
                call = self.calls.setdefault(call_id, ToolCall(tool=name))
                raw = event.get("rawInput")
                if isinstance(raw, dict):
                    if isinstance(raw.get("prompt"), str):
                        call.prompt = raw["prompt"]
                    if isinstance(raw.get("aspect_ratio"), str):
                        call.aspect_ratio = raw["aspect_ratio"]
                    if isinstance(raw.get("duration"), int) and not isinstance(raw.get("duration"), bool):
                        call.duration = raw["duration"]
                    if isinstance(raw.get("resolution_name"), str):
                        call.resolution = raw["resolution_name"]
                if event.get("status"):
                    call.status = str(event["status"])
            elif name:
                self.other_tools.append(name)
        elif kind == "tool_call_update":
            call = self.calls.get(str(event.get("toolCallId") or ""))
            if call is None:
                return
            if event.get("status"):
                call.status = str(event["status"])
            output = event.get("rawOutput")
            if isinstance(output, dict):
                if output.get("type") in MEDIA_OUTPUT_TYPES and (
                        isinstance(output.get("path"), str) or isinstance(output.get("uploaded_url"), str)):
                    if isinstance(output.get("path"), str) and output["path"]:
                        call.path = output["path"]
                    if isinstance(output.get("uploaded_url"), str) and output["uploaded_url"]:
                        call.uploaded_url = output["uploaded_url"][:500]
                elif output.get("type") == "Text" and isinstance(output.get("text"), str):
                    call.text = output["text"][:2000]
                elif output.get("error") or output.get("message"):
                    call.error = str(output.get("message") or output.get("error"))[:2000]
            if call.status == "failed" and not call.error:
                call.error = _content_text(event.get("content"))[:2000] or "the tool reported a failure"
        elif kind == "text":
            if len(self.agent_text) < 4000:
                self.agent_text += str(event.get("data") or "")
        elif kind == "error":
            self.errors.append(str(event.get("message") or "")[:1000])
        elif kind == "max_turns_reached":
            self.max_turns = True
        elif kind == "end":
            self.end = event

    def cost(self) -> float | None:
        value = (self.end or {}).get("total_cost_usd")
        return float(value) if isinstance(value, (int, float)) else None


def _content_text(content) -> str:
    """Text blocks of an ACP tool-call content list."""
    parts = []
    if isinstance(content, list):
        for entry in content:
            block = entry.get("content") if isinstance(entry, dict) else None
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
    return " ".join(parts).strip()


@dataclass
class GrokImage:
    path: str
    prompt: str  # the exact prompt the agent sent to Imagine ("" if the tool call carried none)
    aspect_ratio: str | None
    tool: str


@dataclass
class GrokVideo:
    path: str
    prompt: str  # the exact prompt the agent sent to the video tool ("" if the tool call carried none)
    aspect_ratio: str | None
    duration: int | None
    resolution: str | None
    tool: str


@dataclass
class GrokResult:
    images: list
    session_id: str
    elapsed: float
    cost_usd: float | None


@dataclass
class GrokVideoResult:
    video: GrokVideo
    first_frame: GrokImage | None  # the still this run made as the first frame, if it did
    extra_videos: list  # paths of further videos Grok made in the same run (one was asked for)
    session_id: str
    elapsed: float
    cost_usd: float | None


def aspect_note(image: GrokImage, reference_count: int) -> str:
    """The aspect ratio as Grok's image tools apply it (image_gen/image_edit source):
    image_gen takes the agent's value, "auto" when it gives none; an edit of one
    reference keeps the reference's ratio and ignores the value; an edit of several
    sends the value, "auto" when none."""
    if image.tool == "image_edit" and reference_count == 1:
        return "taken from the reference (Grok ignores the aspect ratio when editing one image)"
    if image.tool not in IMAGE_TOOLS:
        return "not reported"
    return image.aspect_ratio or "auto (the tool's default)"


def _norm(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def _session_folders(session_id: str, name: str):
    """<GROK_HOME>/sessions/<folder for the run dir>/<session id>/<name> folders that exist."""
    sessions = os.path.join(grok_home(), "sessions")
    if os.path.isdir(sessions):
        for cwd_folder in os.listdir(sessions):
            folder = os.path.join(sessions, cwd_folder, session_id, name)
            if os.path.isdir(folder):
                yield folder


def pick_images(events: GrokEvents, started: float, reference_paths: list[str], session_id: str) -> list[GrokImage]:
    """Every image the image tools saved in this run, in order. Never an input or an older file."""
    references = {_norm(p) for p in reference_paths}

    def acceptable(path: str) -> bool:
        return (
            bool(path) and os.path.isfile(path)
            and _norm(path) not in references
            and path.lower().endswith(IMAGE_EXTENSIONS)
            and os.path.getmtime(path) >= started - 2
        )

    images, seen = [], set()
    for call in events.calls.values():
        if call.tool in IMAGE_TOOLS and call.path and acceptable(call.path) and _norm(call.path) not in seen:
            seen.add(_norm(call.path))
            images.append(GrokImage(call.path, call.prompt or "", call.aspect_ratio, call.tool))
    if images:
        return images
    # The stream did not name a file: look in this session's own images folder.
    for folder in _session_folders(session_id, "images"):
        for name in sorted(os.listdir(folder), key=lambda n: os.path.getmtime(os.path.join(folder, n))):
            path = os.path.join(folder, name)
            if acceptable(path) and _norm(path) not in seen:
                seen.add(_norm(path))
                prompts = [c.prompt for c in events.calls.values() if c.prompt]
                images.append(GrokImage(path, prompts[0] if len(prompts) == 1 else "", None, "unknown"))
    return images


def pick_videos(events: GrokEvents, started: float, input_paths: list[str], session_id: str) -> list[GrokVideo]:
    """Every video the video tool saved in this run, in order. Never an input or an older file."""
    inputs = {_norm(p) for p in input_paths}

    def acceptable(path: str) -> bool:
        return (
            bool(path) and os.path.isfile(path) and os.path.getsize(path) > 0
            and _norm(path) not in inputs
            and path.lower().endswith(VIDEO_EXTENSIONS)
            and os.path.getmtime(path) >= started - 2
        )

    videos, seen = [], set()
    for call in events.calls.values():
        if call.tool in VIDEO_TOOLS and call.path and acceptable(call.path) and _norm(call.path) not in seen:
            seen.add(_norm(call.path))
            videos.append(GrokVideo(call.path, call.prompt or "", call.aspect_ratio, call.duration,
                                    call.resolution, call.tool))
    if videos:
        return videos
    for folder in _session_folders(session_id, "videos"):
        for name in sorted(os.listdir(folder), key=lambda n: os.path.getmtime(os.path.join(folder, n))):
            path = os.path.join(folder, name)
            if acceptable(path) and _norm(path) not in seen:
                seen.add(_norm(path))
                prompts = [c.prompt for c in events.calls.values() if c.tool in VIDEO_TOOLS and c.prompt]
                videos.append(GrokVideo(path, prompts[0] if len(prompts) == 1 else "", None, None, None, "unknown"))
    return videos


ZDR_PATTERN = re.compile(r"zero data retention|\bzdr\b", re.IGNORECASE)


def explain_failure(events: GrokEvents, exit_code: int | None, kind: str = "image",
                    still_path: str | None = None) -> str:
    """Why a run gave no file. `kind` is "image" or "video"; `still_path` is the first frame a
    video run did make before it failed."""
    noun = "video" if kind == "video" else "image"
    output = " / ".join(list(events.stderr_tail)[-4:])
    everything = " ".join([output, " ".join(events.errors)] + [c.error or "" for c in events.calls.values()]).lower()
    upgrade = next((c.text for c in events.calls.values() if c.text and "supergrok" in c.text.lower()), None)
    failed = next((c for c in events.calls.values() if c.error), None)
    tool_error = failed.error if failed else None
    saved = f" The first frame Grok made is saved at: {still_path}" if still_path else ""

    if "not signed in" in everything or "no cached credentials" in everything or "grok login" in everything:
        return ("Grok Build is not signed in. Run 'grok login' once in a terminal and sign in with your Grok "
                "account." + (f" Grok said: {output[:400]}" if output else ""))
    if upgrade:
        return (f"Your Grok plan does not include {noun} generation (the Free and X Basic plans do not). "
                f"Grok said: {upgrade[:600]}" + saved)
    if tool_error and ZDR_PATTERN.search(tool_error):
        return ("Grok Build refused to make a video because its privacy mode (zero data retention) is on. "
                "Turn it off in Grok Build: type /privacy and set \"Coding data, retention, and training\" to "
                "Opt in, then run again. No video was made." + saved + f" Grok said: {tool_error[:600]}")
    if tool_error:
        what = "video" if failed.tool in VIDEO_TOOLS else "image"
        message = f"Grok's {what} tool failed: {tool_error[:800]}"
        if re.search(r"\b403\b", tool_error):
            message += (f" (xAI refused the request for this account. Check that your Grok plan includes {what} "
                        "generation and has usage left.)")
        if re.search(r"\b429\b", tool_error):
            message += (" (xAI answered \"too many requests\": a limit on xAI's side for your account or team, "
                        "not a fault of this node. Wait a little and run again. If you only just subscribed or "
                        "changed plan, run 'grok logout' and then 'grok login' once.)")
        if re.search(r"did not complete within", tool_error):
            message += " (Grok Build waits at most 300 seconds for a video. Nothing was retried.)"
        return message + saved
    uploaded = next((c.uploaded_url for c in events.calls.values() if c.uploaded_url), None)
    if kind == "video" and uploaded:
        return ("Grok Build uploaded the video to the storage bucket set up in its configuration instead of saving "
                "it on this computer, so there is nothing for this node to load. Grok's remote copy: "
                f"{uploaded[:300]}" + saved)
    if re.search(r"unexpected argument|unrecognized|invalid value", everything):
        return ("Grok Build did not accept the command this node uses; it may be a different version than "
                f"the one the node was built against. Grok said: {output[:600]}")
    parts = []
    if not events.calls and events.errors:
        parts.append(f"Grok Build stopped with an error before its agent called the {noun} tool.")
    elif not events.calls:
        parts.append(f"Grok's agent did not call the {noun} tool.")
    elif kind == "video" and not any(c.tool in VIDEO_TOOLS for c in events.calls.values()):
        parts.append("Grok's agent did not call the video tool." + (" It made the first frame." if still_path else ""))
    else:
        parts.append(f"Grok finished without a new {noun}.")
    if events.other_tools:
        parts.append(f"It tried other tools: {', '.join(events.other_tools[:5])}.")
    if events.max_turns:
        parts.append("It reached the turn limit.")
    if events.agent_text.strip():
        parts.append(f"Grok said: {events.agent_text.strip()[:600]}")
    if events.errors:
        parts.append(f"Errors: {' | '.join(e for e in events.errors if e)[:600]}")
    if exit_code not in (0, None):
        parts.append(f"(exit code {exit_code})")
        if output and not events.errors:
            parts.append(f"Last output: {output[:600]}")
    if still_path:
        parts.append(f"The first frame Grok made is saved at: {still_path}")
    return " ".join(parts)


async def _run(command: list, run_dir: str, events: GrokEvents, timeout: float, is_interrupted, poll_interval: float):
    try:
        return await agent_process.run(
            command,
            cwd=run_dir,
            env=child_env(),
            stdin_data=None,
            on_stdout=events.feed_stdout,
            on_stderr=events.feed_stderr,
            timeout=timeout,
            is_interrupted=is_interrupted,
            poll_interval=poll_interval,
        )
    except OSError as exc:
        raise GrokError(f"Could not start Grok Build: {exc}") from None
    except agent_process.ProcessInterrupted:
        raise GrokInterrupted() from None
    except agent_process.ProcessTimedOut:
        raise GrokError(
            f"Grok Build did not finish within {int(timeout // 60)} minutes and was stopped. Nothing was retried."
        ) from None


async def generate(
    grok: str,
    prompt: str,
    reference_paths: list[str],
    run_dir: str,
    *,
    timeout: float = TIMEOUT_SECONDS,
    is_interrupted=lambda: False,
    poll_interval: float = 0.25,
) -> GrokResult:
    instruction_path = os.path.join(run_dir, "instruction.txt")
    with open(instruction_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(build_instruction(prompt, reference_paths))
    session_id = str(uuid.uuid4())
    command = build_command(grok, instruction_path, run_dir, session_id)

    started = time.time()
    clock = time.monotonic()
    events = GrokEvents()
    exit_code = await _run(command, run_dir, events, timeout, is_interrupted, poll_interval)

    images = pick_images(events, started, reference_paths, session_id)
    if not images:
        raise GrokError(explain_failure(events, exit_code))
    return GrokResult(images, session_id, time.monotonic() - clock, events.cost())


async def generate_video(
    grok: str,
    request: grok_video.VideoRequest,
    run_dir: str,
    *,
    timeout: float = VIDEO_TIMEOUT_SECONDS,
    is_interrupted=lambda: False,
    poll_interval: float = 0.25,
) -> GrokVideoResult:
    """One video. `request` names the image files (already in `run_dir`) and the settings."""
    try:
        request.validate()
    except grok_video.VideoRequestError as exc:
        raise GrokError(f"{exc} Nothing was sent.") from None
    instruction_path = os.path.join(run_dir, "instruction.txt")
    with open(instruction_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(grok_video.build_video_instruction(request))
    session_id = str(uuid.uuid4())
    turns = VIDEO_WITH_FIRST_FRAME_MAX_TURNS if request.make_first_frame else VIDEO_MAX_TURNS
    command = build_command(grok, instruction_path, run_dir, session_id, tools=request.tools, max_turns=turns)

    started = time.time()
    clock = time.monotonic()
    events = GrokEvents()
    exit_code = await _run(command, run_dir, events, timeout, is_interrupted, poll_interval)

    still = None
    if request.make_first_frame:
        stills = [i for i in pick_images(events, started, request.image_paths, session_id)
                  if i.tool in ("image_gen", "unknown")]
        still = stills[0] if stills else None
    videos = pick_videos(events, started, request.image_paths, session_id)
    if not videos:
        raise GrokError(explain_failure(events, exit_code, "video", still.path if still else None))
    return GrokVideoResult(videos[0], still, [v.path for v in videos[1:]], session_id,
                           time.monotonic() - clock, events.cost())
