"""Grok Images: one image through Grok Build (`grok`) and the user's Grok sign-in.

The command follows the user's live test (2026-10-24: headless run, tool limit and
sign-in worked; xAI then refused the image for account reasons) and Grok Build's
public source (github.com/xai-org/grok-build, synced 2026-09-29):

    grok --no-auto-update --prompt-file <instruction> --verbatim --cwd <run dir>
         -s <new uuid> --tools image_gen,image_edit --always-approve
         --output-format streaming-json --max-turns 4

No --effort: on the user's machine (2026-10-27) it stopped the run before the agent
started ("Couldn't apply reasoning effort: Invalid params: "unknown model id""). In
headless.rs, --effort without -m sends a model change for the session's current model,
which Grok's agent did not recognise; without -m and --effort that step is skipped.

`streaming-json` prints one JSON object per line (headless/reducer/acp.rs):
    {"type":"tool_call","toolCallId":..,"toolName":"image_gen"|"image_edit","rawInput":{"prompt":..,..}}
    {"type":"tool_call_update","toolCallId":..,"status":"completed"|"failed","rawOutput":..}
        rawOutput: {"type":"ImageGen"|"ImageEdit","path":<absolute path>,..}  (image saved)
                   {"type":"Text","text":..}            (for example the SuperGrok upgrade message)
                   {"error":"tool_execution_failed","message":<the tool's exact error>}
    {"type":"text","data":..}  {"type":"error","message":..}  {"type":"max_turns_reached"}
    {"type":"end","stopReason":..,"sessionId":..,..usage and cost..}
The image path comes from the tool's own result and the prompt from the tool call's
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
    from . import agent_process
except ImportError:  # loaded on its own (tests)
    import agent_process

TIMEOUT_SECONDS = 15 * 60
MAX_REFERENCES = 5  # Imagine edits take "up to five source images" (xAI docs)
MAX_REFERENCE_BYTES = 50_000_000  # same per-file ceiling as Subscription mode
IMAGE_TOOLS = ("image_gen", "image_edit")
MAX_TURNS = 4  # one tool call and the closing reply, with room to spare
STRIPPED_ENV = ("XAI_API_KEY",)
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")
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


def build_command(grok: str, instruction_path: str, run_dir: str, session_id: str) -> list[str]:
    return [
        grok, "--no-auto-update",
        "--prompt-file", instruction_path, "--verbatim",
        "--cwd", run_dir, "-s", session_id,
        "--tools", ",".join(IMAGE_TOOLS), "--always-approve",
        "--output-format", "streaming-json",
        "--max-turns", str(MAX_TURNS),
    ]


@dataclass
class ImageCall:
    tool: str
    prompt: str | None = None
    aspect_ratio: str | None = None
    status: str | None = None
    path: str | None = None
    error: str | None = None
    text: str | None = None


@dataclass
class GrokEvents:
    calls: dict = field(default_factory=dict)  # toolCallId -> ImageCall, in arrival order
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
            if name in IMAGE_TOOLS:
                call = self.calls.setdefault(call_id, ImageCall(tool=name))
                raw = event.get("rawInput")
                if isinstance(raw, dict):
                    if isinstance(raw.get("prompt"), str):
                        call.prompt = raw["prompt"]
                    if isinstance(raw.get("aspect_ratio"), str):
                        call.aspect_ratio = raw["aspect_ratio"]
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
                if output.get("type") in ("ImageGen", "ImageEdit") and isinstance(output.get("path"), str):
                    call.path = output["path"]
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
class GrokResult:
    images: list
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
        if call.path and acceptable(call.path) and _norm(call.path) not in seen:
            seen.add(_norm(call.path))
            images.append(GrokImage(call.path, call.prompt or "", call.aspect_ratio, call.tool))
    if images:
        return images
    # The stream did not name a file: look in this session's own images folder
    # (<GROK_HOME>/sessions/<folder for the run dir>/<session id>/images).
    sessions = os.path.join(grok_home(), "sessions")
    if os.path.isdir(sessions):
        for cwd_folder in os.listdir(sessions):
            folder = os.path.join(sessions, cwd_folder, session_id, "images")
            if not os.path.isdir(folder):
                continue
            for name in sorted(os.listdir(folder), key=lambda n: os.path.getmtime(os.path.join(folder, n))):
                path = os.path.join(folder, name)
                if acceptable(path) and _norm(path) not in seen:
                    seen.add(_norm(path))
                    prompts = [c.prompt for c in events.calls.values() if c.prompt]
                    images.append(GrokImage(path, prompts[0] if len(prompts) == 1 else "", None, "unknown"))
    return images


def explain_failure(events: GrokEvents, exit_code: int | None) -> str:
    output = " / ".join(list(events.stderr_tail)[-4:])
    everything = " ".join([output, " ".join(events.errors)] + [c.error or "" for c in events.calls.values()]).lower()
    upgrade = next((c.text for c in events.calls.values() if c.text and "supergrok" in c.text.lower()), None)
    tool_error = next((c.error for c in events.calls.values() if c.error), None)

    if "not signed in" in everything or "no cached credentials" in everything or "grok login" in everything:
        return ("Grok Build is not signed in. Run 'grok login' once in a terminal and sign in with your Grok "
                "account." + (f" Grok said: {output[:400]}" if output else ""))
    if upgrade:
        return ("Your Grok plan does not include image generation (the Free and X Basic plans do not). "
                f"Grok said: {upgrade[:600]}")
    if tool_error:
        message = f"Grok's image tool failed: {tool_error[:800]}"
        if re.search(r"\b403\b", tool_error):
            message += (" (xAI refused the request for this account. Check that your Grok plan includes image "
                        "generation and has usage left.)")
        return message
    if re.search(r"unexpected argument|unrecognized|invalid value", everything):
        return ("Grok Build did not accept the command this node uses; it may be a different version than "
                f"the one the node was built against. Grok said: {output[:600]}")
    parts = []
    if not events.calls and events.errors:
        parts.append("Grok Build stopped with an error before its agent called the image tool.")
    elif not events.calls:
        parts.append("Grok's agent did not call the image tool.")
    else:
        parts.append("Grok finished without a new image.")
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
    return " ".join(parts)


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
    try:
        exit_code = await agent_process.run(
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

    images = pick_images(events, started, reference_paths, session_id)
    if not images:
        raise GrokError(explain_failure(events, exit_code))
    return GrokResult(images, session_id, time.monotonic() - clock, events.cost())
