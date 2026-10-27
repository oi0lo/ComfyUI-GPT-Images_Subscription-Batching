#!/usr/bin/env python3
"""Stand-in for Grok Build (`grok`) in tests. Behaviour is chosen with FAKE_GROK_MODE
(or the file named in FAKE_GROK_MODE_FILE).

Its `streaming-json` lines copy the shapes in Grok Build's source
(crates/codegen/xai-grok-pager/src/headless/reducer/acp.rs, synced 2026-09-29).
"""

import json
import os
import sys
import time

mode = os.environ.get("FAKE_GROK_MODE", "success")
mode_file = os.environ.get("FAKE_GROK_MODE_FILE")
if mode_file and os.path.exists(mode_file):
    with open(mode_file, encoding="utf-8") as handle:
        mode = handle.read().strip() or mode
args = sys.argv[1:]


def arg(name):
    return args[args.index(name) + 1] if name in args else None


def emit(event):
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()



def keep_references(paths, label):
    """Test runs set FAKE_REF_COPY_DIR to see exactly which files the service received."""
    folder = os.environ.get("FAKE_REF_COPY_DIR")
    if not folder:
        return
    import shutil
    os.makedirs(folder, exist_ok=True)
    for number, path in enumerate(paths, start=1):
        shutil.copyfile(path, os.path.join(folder, f"{label}_{number}_{os.path.basename(path)}"))


instruction = ""
if arg("--prompt-file"):
    with open(arg("--prompt-file"), encoding="utf-8") as handle:
        instruction = handle.read()
log_path = os.environ.get("FAKE_GROK_LOG")
if log_path:
    with open(log_path, "w", encoding="utf-8") as handle:
        json.dump({"args": args, "instruction": instruction, "cwd": os.getcwd(),
                   "has_api_key": "XAI_API_KEY" in os.environ,
                   "disable_api_key_auth": os.environ.get("GROK_DISABLE_API_KEY_AUTH")}, handle)

if "--effort" in args or mode == "setup_error":  # the user's Grok Build, 2026-10-27
    emit({"type": "error", "message": "Couldn't apply reasoning effort: Invalid params: \"unknown model id\""})
    sys.exit(1)
if mode == "not_signed_in":
    sys.stderr.write("Not signed in. To authenticate without a browser, run:\n  grok login --device-code\n\n"
                     "Alternatively, set the XAI_API_KEY environment variable or run `grok login` on a machine "
                     "with a browser.\n")
    sys.exit(1)
if mode == "bad_flag":
    sys.stderr.write("error: unexpected argument '--verbatim' found\n")
    sys.exit(2)
if mode == "hang":
    time.sleep(600)

session = arg("-s") or "no-session"
home = os.environ.get("GROK_HOME") or os.path.join(os.path.expanduser("~"), ".grok")
images_dir = os.path.join(home, "sessions", (arg("--cwd") or "x").strip("/").replace("/", "-"), session, "images")
prompt = instruction.split("IMAGE PROMPT:\n", 1)[1].rstrip("\n") if "IMAGE PROMPT:\n" in instruction else ""
references = [line for line in instruction.splitlines() if line and os.path.isfile(line)]
tool = "image_edit" if references and mode != "ignore_refs" else "image_gen"
keep_references(references, "grok")


def save(n):
    from PIL import Image
    os.makedirs(images_dir, exist_ok=True)
    path = os.path.join(images_dir, f"{n}.jpg")
    Image.new("RGB", (32, 18), (200, 30 * n, 40)).save(path, "JPEG")
    return path


def call(n, prompt_text, output, status="completed"):
    raw_input = {"prompt": prompt_text, "aspect_ratio": "16:9"}
    if tool == "image_edit":
        raw_input["image"] = references
    emit({"type": "tool_call", "toolCallId": f"call-{n}", "title": tool, "kind": "other", "status": "pending",
          "toolName": tool, "rawInput": raw_input, "content": [], "locations": []})
    emit({"type": "tool_call_update", "toolCallId": f"call-{n}", "status": status,
          "content": [{"type": "content", "content": {"type": "text", "text": json.dumps(output)}}],
          "rawOutput": output, "locations": []})


def image_output(path):
    return {"type": "ImageEdit" if tool == "image_edit" else "ImageGen", "path": path,
            "filename": os.path.basename(path), "session_folder": "images"}


emit({"type": "thought", "data": "Calling the image tool once."})
if mode in ("success", "rewrite", "ignore_refs"):
    sent = ("REWRITTEN BY AGENT: " + prompt) if mode == "rewrite" else prompt
    call(1, sent, image_output(save(1)))
elif mode == "two":
    call(1, prompt, image_output(save(1)))
    call(2, prompt + " (second)", image_output(save(2)))
elif mode == "upsell":
    call(1, prompt, {"type": "Text", "text": "Image generation is a SuperGrok feature and isn't available on the "
                     "free or X Basic tier. Let the user know they can unlock image and video generation by "
                     "upgrading to SuperGrok: https://grok.com/supergrok?referrer=grok-build. Do not retry this tool."})
elif mode == "http403":
    call(1, prompt, {"error": "tool_execution_failed", "message": "Image generation failed with HTTP 403 Forbidden: "
                     "credits are exhausted or a Grok subscription is required."}, status="failed")
elif mode == "lie":
    call(1, prompt, image_output(references[0] if references else "/nonexistent.jpg"))
elif mode == "stale":
    call(1, prompt, image_output(os.environ["FAKE_GROK_STALE"]))
elif mode == "no_path":
    save(1)
    call(1, prompt, {"type": "ImageGen"})
elif mode == "no_tool":
    pass
emit({"type": "text", "data": "Done." if mode not in ("no_tool",) else "I would rather describe a cat."})
emit({"type": "usage", "usage": {"input_tokens": 1000, "output_tokens": 20}})
emit({"type": "end", "stopReason": "end_turn", "sessionId": session, "requestId": "req-1",
      "total_cost_usd": 0.0139, "num_turns": 2})
