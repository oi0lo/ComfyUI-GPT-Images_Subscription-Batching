"""Subscription mode: one image through the Codex CLI and the user's ChatGPT sign-in.

Mirrors the command proven in the live test (Codex CLI 0.163.0-alpha.11.1):

    codex exec --json --ephemeral --ignore-user-config --enable image_generation
        --skip-git-repo-check --sandbox workspace-write -c model_reasoning_effort="low"
        --cd <run dir> --output-schema <schema> --output-last-message <result> - [--image <ref>]...

with the instruction on stdin. In that test the exec events carried no image
item; the saved path came from the agent's structured answer. So the image is
taken from Codex's own folder for this run, $CODEX_HOME/generated_images/<thread_id>/,
and the agent's answer is only a cross-check.
"""

from __future__ import annotations

import asyncio
import glob
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field

TIMEOUT_SECONDS = 15 * 60
MAX_REFERENCES = 5  # Codex's built-in image tool limit in the tested build; version dependent
MAX_REFERENCE_BYTES = 50_000_000  # Codex's bundled image-tool notes: input images "must be under 50MB"
STRIPPED_ENV = ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL")
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["success", "failed"]},
        "image_path": {"type": "string"},
        "details": {"type": "string"},
    },
    "required": ["status", "image_path", "details"],
    "additionalProperties": False,
}

RULES = (
    "Use the built-in tool only. Do not browse, inspect the project, delegate, use API scripts, "
    "or make additional generation attempts. If unavailable or blocked, report failure without a "
    "fallback. Return the absolute path of the actual generated image in the required JSON response; "
    "do not copy or re-encode it. Success requires a saved image. Keep details brief."
)


class CodexError(RuntimeError):
    pass


class CodexInterrupted(Exception):
    pass


# ------------------------------------------------------------------ discovery

def child_env() -> dict:
    """Environment for Codex: never hand it an API key, so it cannot bill the API account."""
    env = dict(os.environ)
    for name in STRIPPED_ENV:
        env.pop(name, None)
    return env


def find_codex() -> str:
    """Newest app-managed codex.exe first (its folder name changes on updates), then PATH."""
    candidates: list[str] = []
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            found = glob.glob(os.path.join(local, "OpenAI", "Codex", "bin", "*", "codex.exe"))
            found.sort(key=os.path.getmtime, reverse=True)
            candidates.extend(found)
    on_path = shutil.which("codex")
    if on_path and (sys.platform != "win32" or on_path.lower().endswith(".exe")):
        candidates.append(on_path)
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    raise CodexError(
        "Codex CLI not found. Subscription mode needs the Codex CLI installed and signed in "
        "with ChatGPT (it comes with the ChatGPT desktop app, or install it separately)."
    )


_login_ok: set[tuple[str, float]] = set()


def forget_login() -> None:
    _login_ok.clear()


def check_login(codex: str, run=subprocess.run) -> None:
    """Confirm a ChatGPT sign-in without any model call. Cached for this ComfyUI session."""
    key = (codex, os.path.getmtime(codex))
    if key in _login_ok:
        return
    try:
        done = run(
            [codex, "login", "status"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=60,
            env=child_env(),
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CodexError(f"Could not run Codex to check its sign-in: {exc}") from None
    text = (done.stdout or b"").decode("utf-8", "replace") + (done.stderr or b"").decode("utf-8", "replace")
    if "Logged in using ChatGPT" in text:
        _login_ok.add(key)
        return
    if "Logged in using" in text:
        raise CodexError(
            "Codex is signed in with something other than ChatGPT (for example an API key, which bills "
            "your API account). For Subscription mode, run 'codex login' in a terminal and sign in with ChatGPT."
        )
    if "Not logged in" in text:
        raise CodexError("Codex is not signed in. Run 'codex login' in a terminal and sign in with ChatGPT.")
    raise CodexError(f"Could not confirm the Codex sign-in. Codex said: {text.strip()[-300:]}")


# ------------------------------------------------------------------ one run

def build_instruction(prompt: str, reference_paths: list[str]) -> str:
    lines = ["Generate exactly one image using the built-in image-generation tool and the existing ChatGPT login."]
    if reference_paths:
        lines.append(
            "Use the attached images as input images for this one generation, in the order attached, "
            "and pass them to the image tool as referenced images. Their file paths, in order:"
        )
        lines.extend(reference_paths)
    lines += ["Prompt:", '"""', prompt, '"""', RULES]
    return "\n".join(lines) + "\n"


def build_command(codex: str, run_dir: str, schema_path: str, result_path: str, reference_paths: list[str]) -> list[str]:
    command = [
        codex, "exec", "--json", "--ephemeral", "--ignore-user-config",
        "--enable", "image_generation", "--skip-git-repo-check",
        "--sandbox", "workspace-write", "-c", 'model_reasoning_effort="low"',
        "--cd", run_dir, "--output-schema", schema_path, "--output-last-message", result_path, "-",
    ]
    for path in reference_paths:
        command += ["--image", path]
    return command


def codex_home() -> str:
    return os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")


@dataclass
class EventLog:
    thread_id: str | None = None
    usage: dict | None = None
    turn_failed: str | None = None
    errors: deque = field(default_factory=lambda: deque(maxlen=6))
    last_agent_message: str | None = None
    stderr_tail: deque = field(default_factory=lambda: deque(maxlen=40))

    def feed_stdout(self, stream) -> None:
        for raw in stream:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
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
        if kind == "thread.started" and isinstance(event.get("thread_id"), str):
            self.thread_id = event["thread_id"]
        elif kind == "turn.completed":
            self.usage = event.get("usage")
        elif kind == "turn.failed":
            error = event.get("error") or {}
            self.turn_failed = str(error.get("message") if isinstance(error, dict) else error)[:1000]
        elif kind == "error":
            self.errors.append(str(event.get("message", ""))[:500])
        elif kind == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "agent_message":
                self.last_agent_message = str(item.get("text", ""))[:2000]
            elif item.get("type") == "error":
                self.errors.append(str(item.get("message", ""))[:500])

    def problems(self) -> str:
        parts = []
        if self.turn_failed:
            parts.append(self.turn_failed)
        parts.extend(e for e in self.errors if e)
        return " | ".join(parts)


@dataclass
class CodexResult:
    image_path: str
    thread_id: str | None
    elapsed: float
    usage: dict | None


def _kill(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(process.pid)],
                capture_output=True, timeout=15, creationflags=_NO_WINDOW,
            )
        else:
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)
    except Exception:
        pass
    if process.poll() is None:
        process.kill()
        process.wait(timeout=10)


def _read_report(path: str) -> dict | None:
    try:
        with open(path, "rb") as handle:
            data = handle.read(1_000_000)
        report = json.loads(data.decode("utf-8"))
        return report if isinstance(report, dict) else None
    except (OSError, ValueError):
        return None


def _norm(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def pick_image(report, events: EventLog, started: float, reference_paths: list[str], home: str) -> str | None:
    """Choose the generated image for this run, or None. Never an input or an older file."""
    references = {_norm(p) for p in reference_paths}

    def acceptable(path: str) -> bool:
        if not path or not os.path.isfile(path):
            return False
        if _norm(path) in references:
            return False
        if not path.lower().endswith(IMAGE_EXTENSIONS):
            return False
        return os.path.getmtime(path) >= started - 2

    folder = os.path.join(home, "generated_images", events.thread_id) if events.thread_id else None
    in_folder = []
    if folder and os.path.isdir(folder):
        in_folder = sorted(
            (os.path.join(folder, name) for name in os.listdir(folder)),
            key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0,
            reverse=True,
        )
        in_folder = [p for p in in_folder if acceptable(p)]

    reported = None
    if report and report.get("status") == "success" and isinstance(report.get("image_path"), str):
        reported = report["image_path"].strip()

    if reported and _norm(reported) in {_norm(p) for p in in_folder}:
        return reported
    if in_folder:
        return in_folder[0]  # newest image Codex saved for this run
    if reported and acceptable(reported):
        return reported  # layout differs from the tested build, but the file is new and not an input
    return None


def explain_failure(report, events: EventLog, exit_code: int | None) -> str:
    details = ""
    if report:
        details = str(report.get("details", "")).strip()
    if not details and events.last_agent_message:
        details = events.last_agent_message.strip()
    problems = events.problems()
    combined = f"{details} {problems}".lower()
    if "moderation" in combined:
        lead = "OpenAI's moderation blocked this image. Subscription mode has no moderation setting."
    elif "usage limit" in combined or "rate limit" in combined or re.search(r"\b429\b", combined):
        lead = "Your Codex usage limit appears to be reached."
    elif "unauthorized" in combined or "not logged in" in combined or re.search(r"\b401\b", combined):
        forget_login()
        lead = "Codex reported a sign-in problem. Run 'codex login' in a terminal and sign in with ChatGPT."
    else:
        lead = "Codex finished without a new image."
    message = lead
    if details:
        message += f" Codex said: {details[:600]}"
    if problems:
        message += f" Errors: {problems[:600]}"
    if exit_code not in (0, None):
        message += f" (exit code {exit_code})"
        if events.stderr_tail and not problems:
            message += f" Last output: {' / '.join(list(events.stderr_tail)[-3:])[:600]}"
    return message


async def generate(
    codex: str,
    prompt: str,
    reference_paths: list[str],
    run_dir: str,
    *,
    timeout: float = TIMEOUT_SECONDS,
    is_interrupted=lambda: False,
    poll_interval: float = 0.25,
) -> CodexResult:
    schema_path = os.path.join(run_dir, "result.schema.json")
    result_path = os.path.join(run_dir, "result.json")
    with open(schema_path, "w", encoding="utf-8") as handle:
        json.dump(RESULT_SCHEMA, handle)
    command = build_command(codex, run_dir, schema_path, result_path, reference_paths)
    instruction = build_instruction(prompt, reference_paths).encode("utf-8")

    started = time.time()
    clock = time.monotonic()
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=run_dir,
            env=child_env(),
            creationflags=_NO_WINDOW,
            start_new_session=(sys.platform != "win32"),
        )
    except OSError as exc:
        raise CodexError(f"Could not start Codex: {exc}") from None

    events = EventLog()

    def write_stdin():
        try:
            process.stdin.write(instruction)
            process.stdin.close()
        except OSError:
            pass

    threads = [
        threading.Thread(target=write_stdin, daemon=True),
        threading.Thread(target=events.feed_stdout, args=(process.stdout,), daemon=True),
        threading.Thread(target=events.feed_stderr, args=(process.stderr,), daemon=True),
    ]
    for thread in threads:
        thread.start()

    try:
        while process.poll() is None:
            if is_interrupted():
                _kill(process)
                raise CodexInterrupted()
            if time.monotonic() - clock > timeout:
                _kill(process)
                last = events.problems()
                raise CodexError(
                    f"Codex did not finish within {int(timeout // 60)} minutes and was stopped. Nothing was "
                    "retried. The image may still appear later in your .codex/generated_images folder."
                    + (f" Last Codex message: {last[:400]}" if last else "")
                )
            await asyncio.sleep(poll_interval)
    finally:
        for thread in threads:
            thread.join(timeout=5)
        for pipe in (process.stdin, process.stdout, process.stderr):
            try:
                pipe.close()
            except OSError:
                pass

    report = _read_report(result_path)
    image = pick_image(report, events, started, reference_paths, codex_home())
    if image is None:
        raise CodexError(explain_failure(report, events, process.returncode))
    return CodexResult(image, events.thread_id, time.monotonic() - clock, events.usage)
