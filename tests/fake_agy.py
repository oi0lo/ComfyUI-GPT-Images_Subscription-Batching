#!/usr/bin/env python3
"""Stand-in for the Antigravity CLI (`agy`) in tests. Behaviour is chosen with FAKE_AGY_MODE
(or the file named in FAKE_AGY_MODE_FILE).

Output and folders copy the user's real test on 2026-10-27: one JSON answer on stdout,
the result in ~/.gemini/antigravity-cli/brain/<conversation>/<ImageName>_<ms>.jpg, and the
subagent's byte-identical copy of the reference in brain/<other>/.tempmediaStorage/.
"""

import json
import os
import re
import shutil
import sys
import time
import uuid

mode = os.environ.get("FAKE_AGY_MODE", "success")
mode_file = os.environ.get("FAKE_AGY_MODE_FILE")
if mode_file and os.path.exists(mode_file):
    with open(mode_file, encoding="utf-8") as handle:
        mode = handle.read().strip() or mode
args = sys.argv[1:]


def arg(name):
    return args[args.index(name) + 1] if name in args else None



def keep_references(paths, label):
    """Test runs set FAKE_REF_COPY_DIR to see exactly which files the service received."""
    folder = os.environ.get("FAKE_REF_COPY_DIR")
    if not folder:
        return
    import shutil
    os.makedirs(folder, exist_ok=True)
    for number, path in enumerate(paths, start=1):
        shutil.copyfile(path, os.path.join(folder, f"{label}_{number}_{os.path.basename(path)}"))


request = arg("-p") or ""
log_path = os.environ.get("FAKE_AGY_LOG")
if log_path:
    with open(log_path, "w", encoding="utf-8") as handle:
        json.dump({"args": args, "request": request, "cwd": os.getcwd(),
                   "env": {k: os.environ.get(k) for k in ("GEMINI_API_KEY", "AGY_ADC_AUTH", "GOOGLE_GEMINI_BASE_URL")}},
                  handle)

if mode == "hang":
    time.sleep(600)
if mode == "auth":
    sys.stderr.write("error: authentication required: run agy interactively to sign in\n")
    print(json.dumps({"conversation_id": "", "status": "ERROR", "error": "authentication required"}))
    sys.exit(1)
if mode == "quota":
    sys.stderr.write('AGY_ERROR: {"status":"RESOURCE_EXHAUSTED","code":429,"retryable":false,'
                     '"message":"Your AI credits balance is too low to continue."}\n')
    print(json.dumps({"conversation_id": str(uuid.uuid4()), "status": "ERROR",
                      "error": "Your AI credits balance is too low to continue."}))
    sys.exit(3)
if mode == "bad_model":
    print(json.dumps({"conversation_id": "", "status": "ERROR", "error": "unknown --model gemini-3.8-flash-low"}))
    sys.exit(1)

brain = os.path.join(os.path.expanduser("~"), ".gemini", "antigravity-cli", "brain")
conversation = str(uuid.uuid4())
folder = os.path.join(brain, conversation)
os.makedirs(folder, exist_ok=True)
name = (re.search(r"Use ImageName (\S+?)\.?\n", request + "\n") or re.search(r"ImageName (\S+)", request)).group(1).rstrip(".")
references = re.findall(r"^Reference image \d+: (.+)$", request, flags=re.M)
keep_references(references, "agy")

if references:  # the image subagent keeps a copy of each reference in its own folder
    media = os.path.join(brain, str(uuid.uuid4()), ".tempmediaStorage")
    os.makedirs(media, exist_ok=True)
    for ref in references:
        shutil.copyfile(ref, os.path.join(media, f"media_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}.png"))


def save(index):
    from PIL import Image
    path = os.path.join(folder, f"{name}_{int(time.time() * 1000) + index}.jpg")
    Image.new("RGB", (40, 40), (10 * index, 120, 200)).save(path, "JPEG")
    return path


paths, generated, error, denied = [], True, "", None
if mode == "success":
    paths = [save(0)]
elif mode == "three":
    paths = [save(i) for i in range(3)]
elif mode == "unreported":  # saved but the agent lists nothing
    save(0)
elif mode == "lie":  # reports a reference (and an outside file) but saves nothing
    paths = references + ["/etc/hostname"]
elif mode == "copy_in_folder":  # a byte-identical reference copy at the top level is not a result
    if references:
        shutil.copyfile(references[0], os.path.join(folder, "copy.png"))
    paths = [os.path.join(folder, "copy.png")]
elif mode == "unavailable":  # the user's run on 2026-10-27, after 221 s
    generated = False
    error = '503 Service Unavailable ("Deadline expired before operation could complete.", status: UNAVAILABLE)'
elif mode == "denied":
    generated, error, denied = False, "read_file was denied", ["read_file"]

result = {"error": error, "generated": generated, "image_paths": paths}
answer = {"conversation_id": conversation, "status": "SUCCESS",
          "response": "I have dispatched the task.\n" + json.dumps(result) + "\n",
          "duration_seconds": 1.2, "num_turns": 1, "structured_output": result,
          "usage": {"input_tokens": 33184, "output_tokens": 529, "total_tokens": 33713}}
if denied:
    answer["denied_actions"] = denied
print(json.dumps(answer))
