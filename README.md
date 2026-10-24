# ComfyUI GPT Images (version 4)

One ComfyUI node, **GPT Images: Subscription / Batching gpt-image-2.5 sunburst**, with two modes:

| Mode | What it does | Who pays | The node finishes when |
| --- | --- | --- | --- |
| **Subscription** | Generates one image through the Codex CLI with your ChatGPT sign-in (agent model GPT-6 Luna, low reasoning) and returns it as an IMAGE. | Your Codex allowance (ChatGPT subscription) | the image is loaded |
| **Sunburst Batch** | Packs prompt, settings and references into one OpenAI Batch request (Luna on `/v1/responses`, with `gpt-image-2.5-sunburst` as the image tool) and creates the batch job. | Your OpenAI API balance, at Batch prices | OpenAI confirms the batch with an id |

Only the selected mode runs. Neither mode uses ComfyUI credits or OpenRouter.

Pure Python: the standard library plus what ComfyUI already ships (PyTorch, NumPy, Pillow). No pip installs, no JavaScript, no frontend extension.

## What's new in version 4

- **New instruction to Luna in Batch mode**, the same with and without reference images: "Use the configured image_generation tool. Follow the user prompt. Pass the user prompt to the tool as written. The attached images are references, in the order supplied; if any." The version 3 instruction began "Generate exactly one final image", which Luna turned into "Create a single ... portrait ... Avoid collage or multiple views" for a multi-view reference sheet. Whether Luna follows "as written" shows in the `revised_prompt` field of the batch output file.
- **transparent_background switch** in Batch mode. Off (default) asks for an opaque background, on for a transparent one. Version 3 always sent `auto`.
- **Error messages quote OpenAI's own answer**, then say whether a batch exists. A failed upload means no batch was created. A server error while creating the batch means one may exist, so check your batches page before queueing again.
- Version 3 removed the API key notice from the node; version 2 added the Windows key window, the seven-ratio size list, GPT-6 Luna at low reasoning for Subscription mode, and Codex's exact reason when an image is blocked.

### Upgrading

Replace the whole folder and restart ComfyUI.

**Before opening Batch workflows saved with version 3,** turn on ComfyUI's experimental setting **Restore widget values by name** (Settings, Comfy, Workflow; or search the Settings for it). With it on, those workflows open with their prompt and settings, and the new switch off. Without it, ComfyUI restores values by position: the prompt text lands in the new switch (shown as "transparent") and the prompt box is empty. The node refuses to run either way until you fix it: an empty prompt stops the run, and so does leftover text in the switch, because ComfyUI would otherwise read any text there as "on". Set the switch and paste the prompt, then run. Subscription-mode workflows are not affected.

Workflows saved with version 2 in Batch mode open with an empty prompt unless that setting is on. Workflows saved with version 1 need their resolution picked again.

## Requirements

- ComfyUI with the V3 node features DynamicCombo and Autogrow. Tested with ComfyUI **v0.42.4** and its frontend **1.55.18**, in both the classic and the newer node design.
- Subscription mode: the **Codex CLI**, signed in with ChatGPT, with access to GPT-6 Luna. On Windows the ChatGPT desktop app installs it. Built against `codex-cli 0.163.0-alpha.11.1`.
- Sunburst Batch mode: an **OpenAI API key** from your Platform account. A restricted key needs access to Batches and Files.

## Install

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
  - **transparent_background**: off (default) for an opaque background, on for a transparent one. The output is always PNG. OpenAI's API reference lists both backgrounds for `gpt-image-2.5-sunburst`.
- **prompt**: sent exactly as written. In Batch mode it goes to Luna, and OpenAI's docs say the mainline model revises it before Sunburst sees it. The node's instruction asks Luna to pass it on as written; the `revised_prompt` field in the batch output file shows what Sunburst actually got.
- **image_1, image_2, ...**: optional references, used in socket order. Every image inside a connected batch counts as one reference.
- Outputs: **image** (Subscription mode), **batch_id** and **batch_url** (Batch mode). The outputs a mode does not produce are skipped, so anything connected to them simply does not run.

### Every Run is a new job

Each queued run makes a new generation (Subscription) or a new paid batch job (Batch), even with unchanged inputs. Use the run count for several images. To work on other parts of the workflow without generating, mute or bypass this node; nodes after it then get no image.

## What happens in each mode

**Subscription.** The node finds Codex (the newest copy installed by the ChatGPT app, otherwise `codex` on PATH), checks the sign-in, writes the references as lossless PNGs into its own temporary folder, and runs one Codex task with GPT-6 Luna at low reasoning. It takes the image from Codex's folder for that task (`.codex\generated_images\<task id>` in your user folder), checks that the file is new and not one of the inputs, and loads it. The temporary folder is then deleted; the generated image stays where Codex saved it. If the image is blocked, the error names it as a safety block and quotes Codex's exact reason; the same prompt can pass on another try. The node gives up after 15 minutes, and Stop in ComfyUI ends the Codex process. Nothing is ever retried automatically.

**Sunburst Batch.** The node gets the API key (asking for it if needed), encodes the references, builds one JSONL line, checks its exact size, uploads it and creates the batch. It does not poll or download results: get the output file from the batch page (`batch_url`). Errors quote OpenAI's own answer. If the upload fails, no batch was created and queueing again cannot make a duplicate. If OpenAI answers with a server error, or the connection drops, after the batch request was sent, a batch may exist: check your batches page before queueing again. Nothing is retried automatically.

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

Tested on Linux: the offline tests, and the node loaded and run inside ComfyUI v0.42.4 with its real frontend in both node designs, against stand-ins for Codex, OpenAI, the key window and Credential Manager (both modes, references, the JPEG fallback, Stop, sign-in and safety errors, stale settings, OpenAI errors on upload and on batch creation, the transparent switch, old workflows with and without Restore widget values by name, and every key case: no key, wrong key, rejected stored key, rejected variable, Cancel).

Run for real on Windows: Subscription generations with and without references, also with GPT-6 Luna; the key window and saving the key to Credential Manager; Batch submissions with references, with their results. Not yet run for real: the version 4 instruction and the transparent switch.
