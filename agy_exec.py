"""Antigravity Nano Banana: images through the Antigravity CLI (`agy`) and the user's Google sign-in.

The command is the one proven in the user's tests on 2026-10-27 (text only, and one
reference that kept the same person):

    agy -p <request> --model gemini-3.8-flash-low --output-format json
        --json-schema <schema file> --print-timeout 15m        (in this run's own temp folder)

Antigravity hands image requests to its built-in image-generator subagent, which
writes its own prompt and checks results "with up to three attempts" (Google's
changelog, 1.2.16). In the tests the result was saved as
~/.gemini/antigravity-cli/brain/<conversation_id>/<image name>_<number>.jpg, while the
subagent kept a byte-identical copy of the reference in its own folder's
.tempmediaStorage. So results are taken only from the top level of this run's
conversation folder (plus paths the agent reports, checked the same way), and
never a file identical to a reference.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from collections import deque
from dataclasses import dataclass, field

try:
    from . import agent_process
except ImportError:  # loaded on its own (tests)
    import agent_process

log = logging.getLogger("Comfy-CLI-Subscription_Image-Video")

AGENT_MODEL = "gemini-3.8-flash-low"  # the agent model of the user's successful tests
TIMEOUT_SECONDS = 15 * 60
PRINT_TIMEOUT = "15m"
MAX_REFERENCES = 5  # same limit as the other subscription nodes (user's choice)
MAX_REFERENCE_BYTES = 50_000_000
MAX_COMMAND_CHARS = 30_000  # Windows command lines end at 32,767 characters
# GEMINI_API_KEY alone has no effect (Google docs), but never hand any of these over:
# AGY_ADC_AUTH=true would switch to Google Cloud credentials billed to a Cloud project.
STRIPPED_ENV = ("GEMINI_API_KEY", "AGY_ADC_AUTH", "GOOGLE_GEMINI_BASE_URL")
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")

RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "generated": {"type": "boolean"},
        "image_paths": {"type": "array", "items": {"type": "string"}},
        "error": {"type": "string"},
    },
    "required": ["generated", "image_paths", "error"],
    "additionalProperties": False,
}


class AgyError(RuntimeError):
    pass


class AgyInterrupted(Exception):
    pass


# ------------------------------------------------------------------ discovery

def gemini_home() -> str:
    return os.path.join(os.path.expanduser("~"), ".gemini")


def brain_dir() -> str:
    return os.path.join(gemini_home(), "antigravity-cli", "brain")


def settings_path() -> str:
    return os.path.join(gemini_home(), "antigravity-cli", "settings.json")


def child_env() -> dict:
    env = dict(os.environ)
    for name in STRIPPED_ENV:
        env.pop(name, None)
    return env


def find_agy() -> str:
    """The installer's copy (%LOCALAPPDATA%\\agy\\bin\\agy.exe) first, then PATH."""
    candidates = []
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        candidates.append(os.path.join(os.environ["LOCALAPPDATA"], "agy", "bin", "agy.exe"))
    on_path = shutil.which("agy")
    if on_path and (sys.platform != "win32" or on_path.lower().endswith(".exe")):
        candidates.append(on_path)
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    raise AgyError(
        "Antigravity CLI (agy) not found. Install it (PowerShell: irm https://antigravity.google/cli/install.ps1 "
        "| iex), then run agy once in a normal PowerShell and sign in with your Google account."
    )


def check_sign_in_mode(path: str | None = None) -> None:
    """Refuse Gemini API-key mode instead of switching silently (modelProvider "gemini")."""
    path = path or settings_path()
    if not os.path.isfile(path):
        return
    try:
        with open(path, "rb") as handle:
            text = handle.read(1_000_000).decode("utf-8", "replace")
    except OSError:
        return
    provider = None
    try:
        settings = json.loads(text)
        if isinstance(settings, dict):
            provider = settings.get("modelProvider")
    except ValueError:
        match = re.search(r'"modelProvider"\s*:\s*"([^"]*)"', text)
        provider = match.group(1) if match else None
    if provider == "gemini":
        raise AgyError(
            "Antigravity is set to Gemini API-key mode (\"modelProvider\": \"gemini\" in "
            f"{path}), which uses an API key instead of your Google sign-in. Switch it back to Google "
            "account sign-in to use this node."
        )


# ------------------------------------------------------------------ one run

def build_request(prompt: str, image_name: str, reference_paths: list[str]) -> str:
    lines = [f"Generate exactly one image with the built-in image-generator subagent. Use ImageName {image_name}."]
    if reference_paths:
        lines.append(
            "Give the image generator the reference images below as reference images, by their absolute "
            "paths, in this order. Do not replace them with a text description. Pass the image prompt below unchanged."
        )
        lines.append("")
        lines += [f"Reference image {i}: {path}" for i, path in enumerate(reference_paths, start=1)]
    else:
        lines.append("Pass the image prompt below unchanged. There are no reference images.")
    lines += [
        "",
        "Use the native subscription image tool only. Do not use a browser, an external service, a paid API key, "
        "shell commands, or code to make a substitute image. Do not make another generation attempt if the tool "
        "fails. Do not inspect unrelated files or change settings.",
        "",
        "When finished, return the absolute path of every image that was generated in image_paths and set "
        "generated to true. If generation fails, set generated to false, image_paths to an empty list, and error "
        "to the actual reason. Never report a nonexistent image.",
        "",
        "IMAGE PROMPT:",
        prompt,
    ]
    return "\n".join(lines)


def build_command(agy: str, request: str, schema_path: str) -> list[str]:
    return [
        agy, "-p", request, "--model", AGENT_MODEL, "--output-format", "json",
        "--json-schema", schema_path, "--print-timeout", PRINT_TIMEOUT,
    ]


@dataclass
class AgyOutput:
    stdout: bytearray = field(default_factory=bytearray)
    stderr_tail: deque = field(default_factory=lambda: deque(maxlen=40))
    agy_errors: list = field(default_factory=list)

    def feed_stdout(self, stream) -> None:
        # read1 hands over what has arrived; read(n) would wait for n bytes or the end of the
        # pipe, which a helper left running by agy could keep open.
        read = getattr(stream, "read1", stream.read)
        for chunk in iter(lambda: read(65536), b""):
            if len(self.stdout) < 5_000_000:
                self.stdout.extend(chunk)

    def feed_stderr(self, stream) -> None:
        for raw in stream:
            text = raw.decode("utf-8", "replace").rstrip()
            if not text:
                continue
            self.stderr_tail.append(text[:800])
            if text.startswith("AGY_ERROR:"):
                self.agy_errors.append(text[len("AGY_ERROR:"):].strip()[:1500])

    def answer(self) -> dict | None:
        text = bytes(self.stdout).decode("utf-8", "replace").strip()
        if not text:
            return None
        try:
            value = json.loads(text)
        except ValueError:
            start, end = text.find("{"), text.rfind("}")
            if start < 0 or end <= start:
                return None
            try:
                value = json.loads(text[start:end + 1])
            except ValueError:
                return None
        return value if isinstance(value, dict) else None


def structured(answer: dict | None) -> dict:
    if not answer:
        return {}
    value = answer.get("structured_output")
    if isinstance(value, dict):
        return value
    response = answer.get("response")
    if isinstance(response, str):
        start, end = response.rfind("{\""), response.rfind("}")
        if 0 <= start < end:
            try:
                parsed = json.loads(response[start:end + 1])
                return parsed if isinstance(parsed, dict) else {}
            except ValueError:
                return {}
    return {}


def _norm(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def _digest(path: str) -> str | None:
    try:
        sha = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                sha.update(chunk)
        return sha.hexdigest()
    except OSError:
        return None


def pick_images(answer: dict | None, started: float, reference_paths: list[str]) -> list[str]:
    """Every new image this run saved, oldest first. Never a reference or a copy of one."""
    reference_names = {_norm(p) for p in reference_paths}
    reference_digests = {d for d in (_digest(p) for p in reference_paths) if d}
    home = _norm(gemini_home())

    def acceptable(path: str) -> bool:
        if not path or not os.path.isfile(path) or not path.lower().endswith(IMAGE_EXTENSIONS):
            return False
        if _norm(path) in reference_names or ".tempmediaStorage" in path.replace("\\", "/").split("/"):
            return False
        if os.path.getmtime(path) < started - 2:
            return False
        return _digest(path) not in reference_digests

    found: dict[str, str] = {}
    conversation = (answer or {}).get("conversation_id")
    if isinstance(conversation, str) and conversation and "/" not in conversation and "\\" not in conversation:
        folder = os.path.join(brain_dir(), conversation)
        if os.path.isdir(folder):
            for name in os.listdir(folder):  # top level only: subfolders hold inputs and internals
                path = os.path.join(folder, name)
                if acceptable(path):
                    found.setdefault(_norm(path), path)
    for path in structured(answer).get("image_paths") or []:
        if isinstance(path, str) and _norm(path).startswith(home + os.sep) and acceptable(path):
            found.setdefault(_norm(path), path)
    return sorted(found.values(), key=os.path.getmtime)


def explain_failure(answer: dict | None, output: AgyOutput, exit_code: int | None) -> str:
    result = structured(answer)
    said = [
        str((answer or {}).get("error") or ""),
        str(result.get("error") or ""),
        " | ".join(output.agy_errors),
    ]
    said = [s.strip() for s in said if s and s.strip()]
    tail = " / ".join(list(output.stderr_tail)[-4:])
    denied = (answer or {}).get("denied_actions")
    everything = " ".join(said + [tail, str((answer or {}).get("response") or "")]).lower()

    if "authentication required" in everything or "not signed in" in everything or "sign in" in everything:
        lead = ("Antigravity is not signed in. Open a normal (not administrator) PowerShell, run agy once and "
                "sign in with your Google account.")
    elif ("credits balance is too low" in everything or "quota" in everything
          or "resource_exhausted" in everything or re.search(r"\b429\b", everything)):
        lead = ("Your Antigravity quota appears to be used up (with AI credits off, runs wait for the next "
                "refresh).")
    elif "model" in everything and ("unknown" in everything or "not found" in everything or "not available" in everything):
        lead = f"Antigravity does not offer {AGENT_MODEL} for this account or version."
    elif re.search(r"\b50[34]\b|\bunavailable\b|deadline (?:expired|exceeded)|overloaded|internal server error",
                   everything):
        lead = ("A Google server " + ("did not finish the request in time" if "deadline" in everything
                                     else "answered with a server error")
                + " (a server-side error). Nothing was retried; the same run may work later.")
    elif answer is None:
        lead = "Antigravity printed no answer."
    elif str(answer.get("status")) not in ("SUCCESS", "None"):
        lead = f"Antigravity ended with status {answer.get('status')}."
    else:
        lead = "Antigravity finished without a new image."
    message = lead
    if denied:
        message += f" Antigravity refused these actions: {json.dumps(denied)[:400]}."
    reported = [p for p in (result.get("image_paths") or []) if isinstance(p, str)]
    if reported:
        message += (" None of the paths it reported is a new image saved by this run: "
                    f"{', '.join(reported)[:600]}.")
    if said:
        message += f" Antigravity said: {' | '.join(said)[:800]}"
    elif answer and answer.get("response") and not reported:
        message += f" Antigravity said: {str(answer['response']).strip()[:600]}"
    if exit_code not in (0, None):
        message += f" (exit code {exit_code})"
        if tail and not said:
            message += f" Last output: {tail[:600]}"
    conversation = (answer or {}).get("conversation_id")
    if isinstance(conversation, str) and conversation:
        message += f" Antigravity's files for this run: {os.path.join(brain_dir(), conversation)}"
    return message


@dataclass
class AgyResult:
    images: list
    conversation_id: str | None
    elapsed: float
    usage: dict | None


async def generate(
    agy: str,
    prompt: str,
    reference_paths: list[str],
    run_dir: str,
    *,
    timeout: float = TIMEOUT_SECONDS,
    is_interrupted=lambda: False,
    poll_interval: float = 0.25,
) -> AgyResult:
    check_sign_in_mode()
    schema_path = os.path.join(run_dir, "result.schema.json")
    with open(schema_path, "w", encoding="utf-8") as handle:
        json.dump(RESULT_SCHEMA, handle)
    image_name = f"comfyui_{uuid.uuid4().hex[:12]}"
    command = build_command(agy, build_request(prompt, image_name, reference_paths), schema_path)
    if len(subprocess.list2cmdline(command)) > MAX_COMMAND_CHARS:
        raise AgyError(
            "The prompt is too long for Antigravity's command line (Windows allows about 32,000 characters, "
            "including the node's own instructions). Shorten the prompt."
        )

    started = time.time()
    clock = time.monotonic()
    output = AgyOutput()
    try:
        exit_code = await agent_process.run(
            command,
            cwd=run_dir,
            env=child_env(),
            stdin_data=None,
            on_stdout=output.feed_stdout,
            on_stderr=output.feed_stderr,
            timeout=timeout,
            is_interrupted=is_interrupted,
            poll_interval=poll_interval,
        )
    except OSError as exc:
        raise AgyError(f"Could not start Antigravity: {exc}") from None
    except agent_process.ProcessInterrupted:
        raise AgyInterrupted() from None
    except agent_process.ProcessTimedOut:
        raise AgyError(
            f"Antigravity did not finish within {int(timeout // 60)} minutes and was stopped. Nothing was "
            "retried. An image may still appear later in Antigravity's brain folder."
        ) from None

    answer = output.answer()
    images = pick_images(answer, started, reference_paths)
    if not images:
        raw = bytes(output.stdout).decode("utf-8", "replace").strip()
        log.warning("[Antigravity Nano Banana] Antigravity's full answer (%d s, exit code %s): %s",
                    int(time.monotonic() - clock), exit_code, raw[:3000] or "(none)")
        raise AgyError(explain_failure(answer, output, exit_code))
    usage = (answer or {}).get("usage")
    return AgyResult(images, (answer or {}).get("conversation_id"), time.monotonic() - clock,
                     usage if isinstance(usage, dict) else None)
