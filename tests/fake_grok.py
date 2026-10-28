#!/usr/bin/env python3
"""Stand-in for Grok Build (`grok`) in tests. Behaviour is chosen with FAKE_GROK_MODE
(or the file named in FAKE_GROK_MODE_FILE).

Its `streaming-json` lines copy the shapes in Grok Build's source
(crates/codegen/xai-grok-pager/src/headless/reducer/acp.rs, synced 2026-09-29).

When `--tools` contains reference_to_video, it plays the agent for a video instruction: it reads
the arguments the instruction names (see grok_video.build_video_instruction) and calls the tools
with them, like the user's own test showed Grok's agent doing (2026-10-28).
"""

import json
import os
import re
import sys
import time

mode = os.environ.get("FAKE_GROK_MODE", "success")
mode_file = os.environ.get("FAKE_GROK_MODE_FILE")
if mode_file and os.path.exists(mode_file):
    with open(mode_file, encoding="utf-8") as handle:
        mode = handle.read().strip() or mode
args = sys.argv[1:]

ZDR_MESSAGE = ("Video generation tools are unavailable under zero data retention (ZDR). To enable, either turn off "
               "/privacy mode to disable ZDR or supply a user-hosted storage bucket (see "
               "https://docs.x.ai/build/settings/zdr-video-storage).")
FAKE_MP4 = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41" + b"fake video bytes " * 8


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
tool_calls = []  # what the "agent" passed to each tool, for the tests


def write_log():
    if log_path:
        with open(log_path, "w", encoding="utf-8") as handle:
            json.dump({"args": args, "instruction": instruction, "cwd": os.getcwd(),
                       "has_api_key": "XAI_API_KEY" in os.environ,
                       "disable_api_key_auth": os.environ.get("GROK_DISABLE_API_KEY_AUTH"),
                       "tool_calls": tool_calls}, handle)


write_log()

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
session_dir = os.path.join(home, "sessions", (arg("--cwd") or "x").strip("/").replace("/", "-"), session)
images_dir = os.path.join(session_dir, "images")
videos_dir = os.path.join(session_dir, "videos")
tools = (arg("--tools") or "").split(",")


def save_image(n):
    from PIL import Image
    os.makedirs(images_dir, exist_ok=True)
    path = os.path.join(images_dir, f"{n}.jpg")
    Image.new("RGB", (32, 18), (200, 30 * n, 40)).save(path, "JPEG")
    return path


def save_video(n):
    os.makedirs(videos_dir, exist_ok=True)
    path = os.path.join(videos_dir, f"{n}.mp4")
    source = os.environ.get("FAKE_VIDEO_SOURCE")
    if source and mode != "bad_video":
        import shutil
        shutil.copyfile(source, path)
    else:
        with open(path, "wb") as handle:
            handle.write(FAKE_MP4)
    return path


def emit_call(call_id, tool, raw_input, output, status="completed"):
    tool_calls.append({"tool": tool, "input": raw_input})
    emit({"type": "tool_call", "toolCallId": call_id, "title": tool, "kind": "other", "status": "pending",
          "toolName": tool, "rawInput": raw_input, "content": [], "locations": []})
    emit({"type": "tool_call_update", "toolCallId": call_id, "status": status,
          "content": [{"type": "content", "content": {"type": "text", "text": json.dumps(output)}}],
          "rawOutput": output, "locations": []})


# ---------------------------------------------------------------------------- video runs

def parse_video_instruction(text):
    """The prompt and the named arguments of grok_video.build_video_instruction."""
    head, prompt = text.split("VIDEO PROMPT:\n", 1)
    values, current = {}, None
    for line in head.split("\n"):
        named = re.match(r"^   (\w+): (.*)$", line)
        if named:
            current = named.group(1)
            values[current] = [named.group(2)]
            continue
        more = re.match(r"^      (.+)$", line)
        if more and current:
            values[current].append(more.group(1))
            continue
        current = None
    return prompt.rstrip("\n"), values, head


def run_video():
    prompt, values, head = parse_video_instruction(instruction)
    sent_prompt = ("REWRITTEN BY AGENT: " + prompt) if mode == "rewrite" else prompt
    aspect = values.get("aspect_ratio", ['"16:9"'])[0].strip('"')
    still = None
    inputs = []
    emit({"type": "thought", "data": "Planning the video."})
    if "Call image_gen with" in head:
        if mode == "still_fails":
            emit_call("call-1", "image_gen", {"prompt": prompt, "aspect_ratio": aspect},
                      {"error": "tool_execution_failed",
                       "message": "Image generation failed with HTTP 403 Forbidden: credits are exhausted."},
                      status="failed")
            return
        still = save_image(1)
        emit_call("call-1", "image_gen", {"prompt": prompt, "aspect_ratio": aspect},
                  {"type": "ImageGen", "path": still, "filename": "1.jpg", "session_folder": "images"})
        if mode == "still_only":
            return

    raw = {"prompt": sent_prompt}
    first = values.get("first_frame", [None])[0]
    if first:
        raw["first_frame"] = still if first.startswith("the absolute path") else first
        inputs.append(("first_frame", raw["first_frame"]))
    if "keyframes" in values:
        match = re.match(r"a list with one entry: image (.+), timestamp_s ([0-9.]+)$", values["keyframes"][0])
        raw["keyframes"] = [{"image": match.group(1), "timestamp_s": float(match.group(2))}]
        inputs.append(("middle_frame", match.group(1)))
    if "last_frame" in values:
        raw["last_frame"] = values["last_frame"][0]
        inputs.append(("last_frame", raw["last_frame"]))
    if "images" in values:
        raw["images"] = values["images"][1:]
        inputs += [(f"reference_{n}", path) for n, path in enumerate(raw["images"], start=1)]
    if "voices" in values:
        raw["voices"] = json.loads(values["voices"][0])
    raw["aspect_ratio"] = aspect
    raw["duration"] = int(values["duration"][0])
    raw["resolution_name"] = values["resolution_name"][0].strip('"')
    for label, path in inputs:
        keep_references([path], "grok_video_" + label)

    if mode == "zdr":
        emit_call("call-2", "reference_to_video", raw, {"error": "tool_execution_failed", "message": ZDR_MESSAGE},
                  status="failed")
    elif mode == "upsell":
        emit_call("call-2", "reference_to_video", raw, {
            "type": "Text", "text": "Video generation is a SuperGrok feature and isn't available on the free or X "
            "Basic tier. Let the user know they can unlock image and video generation by upgrading to SuperGrok: "
            "https://grok.com/supergrok?referrer=grok-build. Do not retry this tool."})
    elif mode == "video_http403":
        emit_call("call-2", "reference_to_video", raw, {
            "error": "tool_execution_failed",
            "message": "Video generation failed with HTTP 403 Forbidden: a subscription is required."}, status="failed")
    elif mode == "video_429":
        emit_call("call-2", "reference_to_video", raw, {
            "error": "tool_execution_failed",
            "message": "Video generation failed with HTTP 429 Too Many Requests: Too many requests for team"},
            status="failed")
    elif mode == "video_timeout":
        emit_call("call-2", "reference_to_video", raw, {
            "error": "tool_execution_failed",
            "message": "Video generation did not complete within 300s (request_id=req-abc)"}, status="failed")
    elif mode == "bad_voice":
        emit_call("call-2", "reference_to_video", raw, {
            "error": "tool_execution_failed",
            "message": "Video generation failed with HTTP 400 Bad Request: unknown voice. Available voices: ara, eve"},
            status="failed")
    elif mode == "uploaded":
        emit_call("call-2", "reference_to_video", raw, {
            "type": "ReferenceToVideo", "path": "", "filename": "", "session_folder": "",
            "uploaded_url": "https://bucket.example/grok-videos/abc.mp4"})
    elif mode == "no_path":
        save_video(1)
        emit_call("call-2", "reference_to_video", raw, {"type": "ReferenceToVideo"})
    elif mode == "stale":
        emit_call("call-2", "reference_to_video", raw, {
            "type": "ReferenceToVideo", "path": os.environ["FAKE_GROK_STALE"], "filename": "old.mp4",
            "session_folder": "videos"})
    elif mode == "lie":  # reports one of the input images as the video
        emit_call("call-2", "reference_to_video", raw, {
            "type": "ReferenceToVideo", "path": inputs[0][1] if inputs else "/nonexistent.mp4",
            "filename": "x", "session_folder": "videos"})
    else:
        path = save_video(1)
        emit_call("call-2", "reference_to_video", raw, {
            "type": "ReferenceToVideo", "path": path, "filename": "1.mp4", "session_folder": "videos"})
        if mode == "two_videos":
            second = save_video(2)
            emit_call("call-3", "reference_to_video", dict(raw, prompt=sent_prompt + " (second)"), {
                "type": "ReferenceToVideo", "path": second, "filename": "2.mp4", "session_folder": "videos"})


if "reference_to_video" in tools:
    if mode != "no_tool":
        run_video()
    emit({"type": "text", "data": "I would rather describe a cat." if mode == "no_tool" else "Done."})
    emit({"type": "usage", "usage": {"input_tokens": 2000, "output_tokens": 60}})
    emit({"type": "end", "stopReason": "end_turn", "sessionId": session, "requestId": "req-1",
          "total_cost_usd": 0.31, "num_turns": 3})
    write_log()
    sys.exit(0)


# ---------------------------------------------------------------------------- image runs

prompt = instruction.split("IMAGE PROMPT:\n", 1)[1].rstrip("\n") if "IMAGE PROMPT:\n" in instruction else ""
references = [line for line in instruction.splitlines() if line and os.path.isfile(line)]
tool = "image_edit" if references and mode != "ignore_refs" else "image_gen"
keep_references(references, "grok")


def call(n, prompt_text, output, status="completed"):
    raw_input = {"prompt": prompt_text, "aspect_ratio": "16:9"}
    if tool == "image_edit":
        raw_input["image"] = references
    emit_call(f"call-{n}", tool, raw_input, output, status)


def image_output(path):
    return {"type": "ImageEdit" if tool == "image_edit" else "ImageGen", "path": path,
            "filename": os.path.basename(path), "session_folder": "images"}


emit({"type": "thought", "data": "Calling the image tool once."})
if mode in ("success", "rewrite", "ignore_refs"):
    sent = ("REWRITTEN BY AGENT: " + prompt) if mode == "rewrite" else prompt
    call(1, sent, image_output(save_image(1)))
elif mode == "two":
    call(1, prompt, image_output(save_image(1)))
    call(2, prompt + " (second)", image_output(save_image(2)))
elif mode == "upsell":
    call(1, prompt, {"type": "Text", "text": "Image generation is a SuperGrok feature and isn't available on the "
                     "free or X Basic tier. Let the user know they can unlock image and video generation by "
                     "upgrading to SuperGrok: https://grok.com/supergrok?referrer=grok-build. Do not retry this tool."})
elif mode == "http403":
    call(1, prompt, {"error": "tool_execution_failed", "message": "Image generation failed with HTTP 403 Forbidden: "
                     "credits are exhausted or a Grok subscription is required."}, status="failed")
elif mode == "http429":
    call(1, prompt, {"error": "tool_execution_failed", "message": "Image generation failed with HTTP 429 Too Many "
                     "Requests: Too many requests for team"}, status="failed")
elif mode == "lie":
    call(1, prompt, image_output(references[0] if references else "/nonexistent.jpg"))
elif mode == "stale":
    call(1, prompt, image_output(os.environ["FAKE_GROK_STALE"]))
elif mode == "no_path":
    save_image(1)
    call(1, prompt, {"type": "ImageGen"})
elif mode == "no_tool":
    pass
emit({"type": "text", "data": "Done." if mode not in ("no_tool",) else "I would rather describe a cat."})
emit({"type": "usage", "usage": {"input_tokens": 1000, "output_tokens": 20}})
emit({"type": "end", "stopReason": "end_turn", "sessionId": session, "requestId": "req-1",
      "total_cost_usd": 0.0139, "num_turns": 2})
write_log()
