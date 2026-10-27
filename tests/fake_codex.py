#!/usr/bin/env python3
"""Stand-in for the Codex CLI in tests. Behaviour is chosen with FAKE_CODEX_MODE.

Event lines copy the format of the real successful run (Codex CLI 0.163.0-alpha.11.1).
"""

import json
import os
import sys
import time
import uuid

mode = os.environ.get("FAKE_CODEX_MODE", "success")
mode_file = os.environ.get("FAKE_CODEX_MODE_FILE")
if mode_file and os.path.exists(mode_file):
    with open(mode_file, encoding="utf-8") as handle:
        mode = handle.read().strip() or mode
log_path = os.environ.get("FAKE_CODEX_LOG")
args = sys.argv[1:]


def emit(event):
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


def write_png(path):
    # Minimal valid 2x1 RGB PNG.
    import struct
    import zlib

    raw = b"\x00" + b"\xff\x00\x00" + b"\x00\xff\x00"
    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 1, 8, 2, 0, 0, 0))
    data += chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    with open(path, "wb") as handle:
        handle.write(data)



def keep_references(paths, label):
    """Test runs set FAKE_REF_COPY_DIR to see exactly which files the service received."""
    folder = os.environ.get("FAKE_REF_COPY_DIR")
    if not folder:
        return
    import shutil
    os.makedirs(folder, exist_ok=True)
    for number, path in enumerate(paths, start=1):
        shutil.copyfile(path, os.path.join(folder, f"{label}_{number}_{os.path.basename(path)}"))


if args[:2] == ["login", "status"]:
    if mode == "login_none":
        sys.stderr.write("Not logged in\n")
        sys.exit(1)
    if mode == "login_apikey":
        sys.stderr.write("Logged in using an API key - sk-****\n")
        sys.exit(0)
    sys.stderr.write("Logged in using ChatGPT\n")
    sys.exit(0)

if args[:1] != ["exec"]:
    sys.exit(2)

stdin_text = sys.stdin.read()
if log_path:
    with open(log_path, "w", encoding="utf-8") as handle:
        json.dump({"args": args, "stdin": stdin_text, "has_api_key": "OPENAI_API_KEY" in os.environ,
                   "has_codex_key": "CODEX_API_KEY" in os.environ, "cwd": os.getcwd()}, handle)

keep_references([args[i + 1] for i, a in enumerate(args) if a == "--image"], "codex")
result_path = args[args.index("--output-last-message") + 1]
thread_id = str(uuid.uuid4())
home = os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
folder = os.path.join(home, "generated_images", thread_id)
emit({"type": "thread.started", "thread_id": thread_id})
emit({"type": "turn.started"})

if mode == "hang":
    time.sleep(3600)

report = None
if mode == "success":
    os.makedirs(folder, exist_ok=True)
    image = os.path.join(folder, f"exec-{uuid.uuid4()}.png")
    write_png(image)
    report = {"status": "success", "image_path": image, "details": "Generated and saved exactly one image."}
elif mode == "success_wrong_path":
    os.makedirs(folder, exist_ok=True)
    write_png(os.path.join(folder, f"exec-{uuid.uuid4()}.png"))
    report = {"status": "success", "image_path": os.path.join(home, "nowhere.png"), "details": "ok"}
elif mode == "blocked":
    report = {"status": "failed", "image_path": "", "details": "moderation_blocked: category sexual"}
elif mode == "lie_reference":
    images = [args[i + 1] for i, a in enumerate(args) if a == "--image"]
    report = {"status": "success", "image_path": images[0], "details": "ok"}
elif mode == "stale":
    report = {"status": "success", "image_path": os.environ["FAKE_CODEX_STALE"], "details": "ok"}
elif mode == "crash":
    sys.stderr.write("fatal: something broke\n")
    sys.exit(3)

if report is not None:
    text = json.dumps(report)
    emit({"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": text}})
    with open(result_path, "w", encoding="utf-8") as handle:
        handle.write(text)
emit({"type": "turn.completed", "usage": {"input_tokens": 29728, "cached_input_tokens": 14464, "output_tokens": 258}})
sys.exit(0)
