# ComfyUI GPT Images (version 3)

One ComfyUI node, **GPT Images: Subscription / Batching gpt-image-2.5 sunburst**, with two modes:

| Mode | What it does | Who pays | The node finishes when |
| --- | --- | --- | --- |
| **Subscription** | Generates one image through the Codex CLI with your ChatGPT sign-in (agent model GPT-6 Luna, low reasoning) and returns it as an IMAGE. | Your Codex allowance (ChatGPT subscription) | the image is loaded |
| **Sunburst Batch** | Packs prompt, settings and references into one OpenAI Batch request (Luna on `/v1/responses`, with `gpt-image-2.5-sunburst` as the image tool) and creates the batch job. | Your OpenAI API balance, at Batch prices | OpenAI confirms the batch with an id |

Only the selected mode runs. Neither mode uses ComfyUI credits or OpenRouter.

Pure Python: the standard library plus what ComfyUI already ships (PyTorch, NumPy, Pillow). No pip installs, no JavaScript, no frontend extension.

## What's new in version 3

- **No API key notice in the node.** In ComfyUI's newer node design the notice box showed only its name and looked like a place to type the key. The key window itself says where the key is kept.

**Upgrading from version 2:** replace the whole folder and restart ComfyUI. Workflows saved with version 2 in Sunburst Batch mode open with an **empty prompt**: ComfyUI restores a node's values by position, and the removed box sat just before the prompt. Their other settings carry over. Copy those prompts before upgrading, or paste them again afterwards. A run with an empty prompt stops before anything is sent. Subscription-mode workflows are not affected.

**Upgrading from version 1:** workflows saved with version 1 keep their old resolution value, which no longer exists; pick the resolution again (ComfyUI marks it as "Value not in list").

Version 2 added the Windows key window (paste the key once; it is checked with OpenAI and stored in Windows Credential Manager), the seven-ratio size list with 1:1 Square as the default, GPT-6 Luna at low reasoning for Subscription mode, and Codex's exact reason when an image is blocked.

## Requirements

- ComfyUI with the V3 node features DynamicCombo and Autogrow. Tested with ComfyUI **v0.42.4** and its frontend **1.55.18**, in both the classic and the newer node design.
- Subscription mode: the **Codex CLI**, signed in with ChatGPT, with access to GPT-6 Luna. On Windows the ChatGPT desktop app installs it. Built against `codex-cli 0.163.0-alpha.11.1`.
- Sunburst Batch mode: an **OpenAI API key** from your Platform account. A restricted key needs access to Batches and Files.

## Install

Clone the repository into ComfyUI's `custom_nodes` directory:

```bash
cd /path/to/ComfyUI/custom_nodes
git clone https://github.com/oi0lo/ComfyUI-GPT-Images_Subscription-Batching.git comfyui-gpt-images
```

Alternatively, install from a downloaded ZIP:

1. Unzip so that this folder sits at `custom_nodes\comfyui-gpt-images` inside your ComfyUI folder.
   For ComfyUI Desktop that is inside the base folder chosen at install, for example
   `D:\Comfy\Local\ComfyUI\custom_nodes\comfyui-gpt-images`.
2. Restart ComfyUI. The node is in the **GPT Images** category; searching "sunburst" or "codex" also finds it.

## API key (Sunburst Batch mode only)

**On Windows, nothing to set up in advance.** The first Batch run without a key opens Windows' credential window ("ComfyUI GPT Images: OpenAI API key"):

1. Paste the key into the **Password** field and press OK. The user name field is only a label.
2. The node checks the key with one free request to OpenAI (it lists your batches). If OpenAI refuses it, the window opens again; if your network is down, nothing is saved.
3. An accepted key is stored in Windows Credential Manager as `ComfyUI.GPTImages.OPENAI_API_KEY`, and the same run continues.

Cancel ends the run without sending anything. If nothing is entered within 10 minutes, the window closes and the run ends. If OpenAI later rejects the stored key (expired or revoked), the window opens again so you can paste a new one; the refused upload created nothing, so the new upload cannot make a duplicate.

What the key window says is exact: the key is checked once with OpenAI (free) and stored by Windows Credential Manager for your Windows user; it is never saved in the node, the workflow, or ComfyUI's files and logs. The node reads it only to send requests to OpenAI (the key check and the batch upload). Programs running as your Windows user can still read Credential Manager entries.

**Other ways**, still supported:

- The included helper, from a terminal with ComfyUI's own Python (the error message prints this exact command for your install):
  ```
  "D:\Comfy\Local\ComfyUI\.venv\Scripts\python.exe" "D:\Comfy\Local\ComfyUI\custom_nodes\comfyui-gpt-images\setup_credentials.py" set
  ```
  `status` shows which key source the node will use (never the key itself); `delete` removes the stored key. You can also remove it in Windows' Credential Manager under Windows Credentials.
- The `OPENAI_API_KEY` environment variable. It takes priority over the stored key. Set it for your Windows user, then fully quit and reopen ComfyUI. If OpenAI rejects a key from the variable, the node tells you to fix or remove the variable; it does not open the window. Every program you run can see this variable, and OpenAI's standard libraries use it automatically.

On Linux and macOS there is no key window: use the environment variable.

## Codex sign-in (Subscription mode only)

Run `codex login` once in a terminal and sign in with ChatGPT; `codex login status` should then say `Logged in using ChatGPT`. The node checks this the first time Subscription mode runs in a ComfyUI session and refuses any other kind of login (for example an API key), so Subscription mode cannot bill your API account. It also never passes `OPENAI_API_KEY` to Codex.

## Wiring

1. Add **GPT Images: Subscription / Batching gpt-image-2.5 sunburst**.
2. Optional references: connect **Load Image** (or any IMAGE output) to `image_1`. A new socket appears each time you connect one.
3. Subscription mode: connect `image` to **Preview Image** or **Save Image**.
4. Batch mode: to see the id and link, connect `batch_url` (or `batch_id`) to **Preview as Text**.
5. Set `mode`, write the prompt, set the run count, and press Run.

## Controls and outputs

- **mode**: Subscription or Sunburst Batch. Sunburst Batch shows more settings:
  - **aspect_ratio**: `1:1 Square` (default), `16:9 Landscape`, `9:16 Portrait`, `4:5 Portrait`, `5:4 Landscape`, `3:2 Landscape`, `2:3 Portrait`.
  - **resolution**: the sizes for that ratio, each with its megapixels and a classification label; the default is the largest. Labels such as "Recommended" and "Documented example" are this project's classification. OpenAI's docs list "common sizes" and call outputs above 3,686,400 pixels (2560 x 1440) experimental; every "Experimental" label here follows that rule.
  - **quality**: auto, low, medium, high, xhigh, max. Default max.
- **prompt**: sent exactly as written. In Batch mode it goes to Luna, and OpenAI's docs say Luna rewrites it before Sunburst sees it; the `revised_prompt` field in the batch output file shows what Sunburst actually got.
- **image_1, image_2, ...**: optional references, used in socket order. Every image inside a connected batch counts as one reference.
- Outputs: **image** (Subscription mode), **batch_id** and **batch_url** (Batch mode). The outputs a mode does not produce are skipped, so anything connected to them simply does not run.

### Every Run is a new job

Each queued run makes a new generation (Subscription) or a new paid batch job (Batch), even with unchanged inputs. Use the run count for several images. To work on other parts of the workflow without generating, mute or bypass this node; nodes after it then get no image.

## What happens in each mode

**Subscription.** The node finds Codex (the newest copy installed by the ChatGPT app, otherwise `codex` on PATH), checks the sign-in, writes the references as lossless PNGs into its own temporary folder, and runs one Codex task with GPT-6 Luna at low reasoning. It takes the image from Codex's folder for that task (`.codex\generated_images\<task id>` in your user folder), checks that the file is new and not one of the inputs, and loads it. The temporary folder is then deleted; the generated image stays where Codex saved it. If the image is blocked, the error names it as a safety block and quotes Codex's exact reason; the same prompt can pass on another try. The node gives up after 15 minutes, and Stop in ComfyUI ends the Codex process. Nothing is ever retried automatically.

**Sunburst Batch.** The node gets the API key (asking for it if needed), encodes the references, builds one JSONL line, checks its exact size, uploads it and creates the batch. It does not poll or download results: get the output file from the batch page (`batch_url`). If the network fails after the batch request was sent, the error says the outcome is unknown; check your batches page before queueing again.

## Limits and size checks

- **Batch file: at most 200 MB.** That is OpenAI's documented Batch input limit; the documented per-request limit (512 MB) is higher. References are encoded as lossless PNG first. If the request would exceed 200 MB, all references are encoded again as JPEG (quality 95, full colour resolution) and checked again; if it is still too large, the node stops with an error. Roughly 6 to 8 PNG references at 4K fit, depending on the image.
- **Subscription: at most 5 references, each under 50 MB** (the limit in Codex's own image-tool notes). The same PNG-then-JPEG rule applies.
- References must be RGB images with 3 channels, as Load Image produces.
- Files on disk are never changed. The node works only on the pixels ComfyUI passes in and writes only into its own temporary folder.
- Subscription mode has no size, quality or moderation settings. Ask for an aspect ratio in the prompt if you need one.

## Tests

Offline tests use stand-ins for Codex, OpenAI and the key window, and never generate anything. Run them with ComfyUI's Python from the `tests` folder:

```
cd "D:\Comfy\Local\ComfyUI\custom_nodes\comfyui-gpt-images\tests"
"D:\Comfy\Local\ComfyUI\.venv\Scripts\python.exe" -m unittest
```

The Codex tests use a POSIX shell script as the stand-in and are skipped on Windows.

## What has and has not been tested

Tested on Linux: the offline tests, and the node loaded and run inside ComfyUI v0.42.4 with its real frontend in both node designs, against stand-ins for Codex, OpenAI, the key window and Credential Manager (both modes, references, the JPEG fallback, Stop, sign-in and safety errors, stale settings, old workflows, and every key case: no key, wrong key, rejected stored key, rejected variable, Cancel).

Run for real on Windows: Subscription generations through the node, with and without references (version 1); the key window, saving the key to Credential Manager, and a real Batch submission with two references (version 2). Not yet run for real: Subscription mode with GPT-6 Luna.
