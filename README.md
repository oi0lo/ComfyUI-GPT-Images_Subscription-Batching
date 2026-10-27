# ComfyUI GPT Images (version 5.0.1)

Three ComfyUI nodes in one package:

| Node | What it does | Who pays | Outputs |
| --- | --- | --- | --- |
| **GPT Images: Subscription / Batching gpt-image-2.5 sunburst** | **Subscription** mode: one image through the Codex CLI and your ChatGPT sign-in. **Sunburst Batch** mode: one `gpt-image-2.5-sunburst` job on the OpenAI Batch API. | Your Codex allowance, or your OpenAI API balance at Batch prices | `image`, or `batch_id` and `batch_url` |
| **Grok Images: Subscription** (new) | One image through Grok Build and your Grok sign-in, made by Grok Imagine. | Your Grok plan. Never an xAI API key. | `image`, and `imagine_prompt`: the exact prompt Grok's agent sent to Imagine |
| **Antigravity Images: Subscription** (new) | One image through the Antigravity CLI and your Google sign-in. | Your Google plan's Antigravity quota. Never a Gemini API key. | `image`: every image Antigravity saved in the run |

Only the node (and mode) you run is used. None of them uses ComfyUI credits or OpenRouter. ComfyUI's own "Grok Image" partner nodes are different nodes: they use Comfy credits.

Pure Python: the standard library plus what ComfyUI already ships (PyTorch, NumPy, Pillow). No pip installs, no JavaScript, no frontend extension.

## What's new in version 5.0.1

- **Grok Images no longer passes `--effort low` to Grok Build.** On your machine that option stopped the run before Grok's agent started. Grok Build said: Couldn't apply reasoning effort: Invalid params: "unknown model id". Without it Grok Build skips that step, as in your first Grok test. The agent now works at Grok Build's default effort.
- **Clearer errors.** When Grok Build stops before its agent runs, the error says so instead of "did not call the image tool". When a Google server fails (for example 503 UNAVAILABLE, "Deadline expired"), the Antigravity error says it is a server-side error, that nothing was retried, and where Antigravity's files for that run are. The ComfyUI log also gets Antigravity's full answer for a failed run.

## What's new in version 5

- **Grok Images: Subscription** and **Antigravity Images: Subscription**, two new nodes: a prompt and up to 5 reference images in, the images out.
- **References with transparency** (RGBA, for example from **Join Image with Alpha**) now work in all three nodes and both GPT modes. They are sent as PNG exactly as they reach the node, including the colours stored under transparent pixels. Version 4 stopped with "expected RGB images (3 channels)".
- If references are too large as PNG and one of them has transparency, the node stops and asks you to reduce or flatten that image yourself. It never removes transparency on its own. (Without transparency, the JPEG fallback works as before.)
- Every subscription run (Codex, Grok, Antigravity) works in its own new folder in your temporary folder, never in Windows' folder (System32) or in ComfyUI's folder. The folder is deleted when the run ends.
- If Codex, Grok Build or Antigravity leaves a background helper running after it finishes, the node no longer waits for that helper, and still reads the full answer.
- The GPT node's controls are unchanged.

### Upgrading

Replace the whole folder and restart ComfyUI. Workflows saved with version 4 open as they are.

**Before opening Batch workflows saved with version 3,** turn on ComfyUI's experimental setting **Restore widget values by name** (Settings, Comfy, Workflow; or search the Settings for it). With it on, those workflows open with their prompt and settings, and the transparent switch off. Without it, ComfyUI restores values by position: the prompt text lands in the transparent switch and the prompt box is empty. The node refuses to run either way until you fix it. Workflows saved with version 2 in Batch mode open with an empty prompt unless that setting is on. Workflows saved with version 1 need their resolution picked again.

## Requirements

- ComfyUI with the V3 node features DynamicCombo and Autogrow. Tested with ComfyUI **v0.42.4** and its frontend **1.55.18**, in both the classic and the newer node design.
- GPT Images, Subscription mode: the **Codex CLI**, signed in with ChatGPT, with access to GPT-6 Luna. On Windows the ChatGPT desktop app installs it. Built against `codex-cli 0.163.0-alpha.11.1`.
- GPT Images, Sunburst Batch mode: an **OpenAI API key** from your Platform account. A restricted key needs access to Batches and Files.
- Grok Images: **Grok Build** (`grok`), signed in with a Grok account whose plan includes image generation. Built against Grok Build's public source (synced 2026-09-29) and your test run.
- Antigravity Images: the **Antigravity CLI** (`agy`), signed in with your Google account, with the agent model `gemini-3.8-flash-low` (the one your tests used).

## Install

1. Unzip so that this folder sits at `custom_nodes\comfyui-gpt-images` inside your ComfyUI folder.
   For ComfyUI Desktop that is inside the base folder chosen at install, for example
   `D:\Comfy\Local\ComfyUI\custom_nodes\comfyui-gpt-images`.
2. Restart ComfyUI. The nodes are in the categories **GPT Images**, **Grok Images** and **Antigravity Images**. Searching "sunburst", "codex", "grok" or "antigravity" also finds them.

## Setup

### GPT Images: API key (Sunburst Batch mode only)

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

### GPT Images: Codex sign-in (Subscription mode only)

Run `codex login` once in a terminal and sign in with ChatGPT; `codex login status` should then say `Logged in using ChatGPT`. The node checks this the first time Subscription mode runs in a ComfyUI session and refuses any other kind of login (for example an API key), so Subscription mode cannot bill your API account. It also never passes `OPENAI_API_KEY` to Codex.

### Grok Images: Grok Build sign-in

1. Install Grok Build. In PowerShell: `irm https://x.ai/cli/install.ps1 | iex`. This puts `grok.exe` in `%USERPROFILE%\.grok\bin`.
2. Run `grok login` once in a terminal and sign in with your Grok account.
3. The node uses `%USERPROFILE%\.grok\bin\grok.exe` (or `bin\grok.exe` inside `GROK_HOME`, if you set that variable), otherwise `grok.exe` on PATH.

Sign-in only: the node removes `XAI_API_KEY` from Grok's environment and sets `GROK_DISABLE_API_KEY_AUTH=1`, Grok Build's own switch that turns off API-key login. So a run cannot bill an xAI API account.

Image generation needs a Grok plan that includes it. On the Free and X Basic plans Grok answers with its SuperGrok upgrade message, which the node shows as an error. An HTTP 403 from xAI (as in your test) means xAI refused the request for that account; the error quotes xAI's reason, which in your test was "credits are exhausted or a Grok subscription is required".

### Antigravity Images: Google sign-in

1. Install the Antigravity CLI. In PowerShell: `irm https://antigravity.google/cli/install.ps1 | iex`. This puts `agy.exe` in `%LOCALAPPDATA%\agy\bin`.
2. Open a normal PowerShell (not as administrator), run `agy` once and sign in with your Google account. An administrator PowerShell starts in `C:\Windows\System32`, which is why Antigravity asked for access to System32 in your test. The node itself never runs there.
3. Your choices in Antigravity's settings stay yours: "Use G1 Credits" (`useG1Credits`) decides whether paid AI credits may be spent once your plan's quota is used up, and "Enable Telemetry" is in `/config`.

The node refuses Gemini API-key mode: if `%USERPROFILE%\.gemini\antigravity-cli\settings.json` contains `"modelProvider": "gemini"`, it stops with an error instead of running. It also removes `GEMINI_API_KEY`, `AGY_ADC_AUTH` and `GOOGLE_GEMINI_BASE_URL` from Antigravity's environment (`AGY_ADC_AUTH=true` would switch to Google Cloud credentials billed to a Cloud project).

## Wiring

1. Add the node.
2. Optional references: connect **Load Image** (or any IMAGE output) to `image_1`. A new socket appears each time you connect one. For an image with transparency, connect Load Image's IMAGE and MASK to **Join Image with Alpha** (`image` and `alpha`) and its output to `image_1`.
3. Connect `image` to **Preview Image** or **Save Image**. For the GPT node in Batch mode, connect `batch_url` (or `batch_id`) to **Preview as Text** instead. For Grok, you can connect `imagine_prompt` to **Preview as Text**.
4. Write the prompt, set the run count, and press Run.

## Controls and outputs

### GPT Images: Subscription / Batching gpt-image-2.5 sunburst

- **mode**: Subscription or Sunburst Batch. Sunburst Batch shows more settings:
  - **aspect_ratio**: `1:1 Square` (default), `16:9 Landscape`, `9:16 Portrait`, `4:5 Portrait`, `5:4 Landscape`, `3:2 Landscape`, `2:3 Portrait`.
  - **resolution**: the sizes for that ratio, each with its megapixels and a classification label; the default is the largest. Labels such as "Recommended" and "Documented example" are this project's classification. OpenAI's docs list "common sizes" and call outputs above 3,686,400 pixels (2560 x 1440) experimental; every "Experimental" label here follows that rule.
  - **quality**: auto, low, medium, high, xhigh, max. Default max.
  - **transparent_background**: off (default) for an opaque background, on for a transparent one. The output is always PNG. OpenAI's API reference lists both backgrounds for `gpt-image-2.5-sunburst`.
- **prompt**: sent exactly as written. In Batch mode it goes to Luna, and OpenAI's docs say the mainline model revises it before Sunburst sees it. The node's instruction asks Luna to pass it on as written; the `revised_prompt` field in the batch output file shows what Sunburst actually got.
- **image_1, image_2, ...**: optional references, used in socket order. Every image inside a connected batch counts as one reference.
- Outputs: **image** (Subscription mode), **batch_id** and **batch_url** (Batch mode). The outputs a mode does not produce are skipped, so anything connected to them simply does not run.

### Grok Images: Subscription

- **prompt**: sent as written to Grok's agent, which passes it to Grok Imagine. The agent may reword it. There are no size or aspect ratio controls; ask for an aspect ratio in the prompt.
- **image_1 to image_5**: optional references, at most 5 in total, used in socket order.
- Outputs: **image**, every image Grok made in the run (normally one); **imagine_prompt**, for each image the exact prompt Grok's agent sent to Imagine, read from the image tool call itself.

### Antigravity Images: Subscription

- **prompt**: sent as written to Antigravity's agent, which hands it to its image generator. The image generator writes its own final prompt, and Antigravity does not report it reliably, so this node has no prompt output. Ask for an aspect ratio in the prompt.
- **image_1 to image_5**: optional references, at most 5 in total, used in socket order.
- Output: **image**, every new image Antigravity saved in the run, oldest first. One image is asked for, but its image generator may make up to three attempts; each saved attempt comes out.

The Grok and Antigravity outputs are lists: each image goes to the next node on its own, so images of different sizes are fine. Preview Image shows all of them; Save Image saves each one.

### Every Run is a new job

Each queued run makes a new generation (Subscription nodes) or a new paid batch job (Batch), even with unchanged inputs. Use the run count for several images. To work on other parts of the workflow without generating, mute or bypass the node; nodes after it then get no image.

## What happens in each node

**GPT Images, Subscription.** The node finds Codex (the newest copy installed by the ChatGPT app, otherwise `codex` on PATH), checks the sign-in, writes the references into its own run folder, and runs one Codex task with GPT-6 Luna at low reasoning. It takes the image from Codex's folder for that task (`.codex\generated_images\<task id>` in your user folder), checks that the file is new and not one of the inputs, and loads it. If the image is blocked, the error names it as a safety block and quotes Codex's exact reason; the same prompt can pass on another try.

**GPT Images, Sunburst Batch.** The node gets the API key (asking for it if needed), encodes the references, builds one JSONL line, checks its exact size, uploads it and creates the batch. It does not poll or download results: get the output file from the batch page (`batch_url`). Errors quote OpenAI's own answer. If the upload fails, no batch was created and queueing again cannot make a duplicate. If OpenAI answers with a server error, or the connection drops, after the batch request was sent, a batch may exist: check your batches page before queueing again.

**Grok Images.** The node finds Grok Build, writes the references into its run folder, and runs Grok Build once without a window: a new session, only its two image tools (`image_gen` without references, `image_edit` with them), tool calls approved automatically, at most 4 turns, Grok Build's default effort. The instruction asks for exactly one tool call with your prompt unchanged. The node takes each image's path and prompt from the tool call itself, not from what the agent says afterwards, and loads the images. The log shows each image's size, the aspect ratio, and the cost Grok Build reports for the run. The images stay where Grok saved them (`.grok\sessions\...\images` in your user folder).

What Grok's image tools do with your files, from Grok Build's source: images come out at Grok's "1k" resolution as JPEG. A reference file of at most 400 KB is used as it is. A larger one is shrunk to at most 768 pixels on its long side and re-encoded to fit in 400 KB, as PNG or JPEG, whichever is smaller. For photos that is usually JPEG, which has no transparency: transparent areas then show the colours stored under them. The node sends your image in its original form; this step happens inside Grok. Grok refuses references over 12,000,000 pixels (unless the file is at most 400 KB), so the node stops before running and names them; reduce them yourself. With one reference, the output keeps that reference's aspect ratio; otherwise the agent's choice applies, or `auto`. If Grok's agent makes an image with `image_gen` although you connected references, that image ignores them: the node still outputs it and says so in the log.

**Antigravity Images.** The node finds the Antigravity CLI, refuses API-key mode, writes the references into its run folder, and runs one request in that folder: `agy -p <request> --model gemini-3.8-flash-low --output-format json --json-schema <file> --print-timeout 15m`. The request asks for exactly one image from Antigravity's built-in image generator, with your prompt unchanged and the references given by their paths. The node then takes every new image saved at the top level of this run's conversation folder (`.gemini\antigravity-cli\brain\<conversation id>` in your user folder), plus image paths the agent reports inside `.gemini`. It never returns a reference or an identical copy of one: Antigravity keeps a copy of each reference in a `.tempmediaStorage` folder. The images stay in Antigravity's folder. In your test, one reference gave one 1024 x 1024 JPEG that kept the same person.

**All subscription runs** give up after 15 minutes. Stop in ComfyUI ends the program (and anything it started). Nothing is ever retried automatically. The run folder (with the reference copies) is deleted when the run ends.

## Limits and size checks

- **Batch file: at most 200 MB.** That is OpenAI's documented Batch input limit; the documented per-request limit (512 MB) is higher. References are encoded as lossless PNG first. If the request would exceed 200 MB, all references are encoded again as JPEG (quality 95, full colour resolution) and checked again; if it is still too large, the node stops with an error. Roughly 6 to 8 PNG references at 4K fit, depending on the image.
- **Subscription nodes (GPT Subscription, Grok, Antigravity): at most 5 references, each under 50 MB** (Codex's own limit; the same ceiling is used for Grok and Antigravity). The same PNG-then-JPEG rule applies.
- **Grok Images:** references over 12,000,000 pixels whose file is over 400 KB are refused before running, because Grok's image tool refuses them (for example 4096 x 4096; 4000 x 3000 still fits).
- **Transparency:** references with 4 channels (RGBA) are sent as RGBA PNG. JPEG cannot keep transparency, so if the JPEG fallback would be needed and any reference has transparency, the node stops before sending anything and names the references. Reduce their size, or flatten them before the node, then run again.
- References must have 3 channels (RGB, as Load Image gives) or 4 (RGBA).
- Files on disk are never changed. The nodes work only on the pixels ComfyUI passes in and write only into their own run folder.
- The subscription nodes have no size, quality or moderation settings.

## Billing and privacy

- Your prompt and references go to the company behind each node: OpenAI (GPT Images), xAI (Grok Images), Google (Antigravity Images). Their terms decide how they are used.
- Grok Build reports a cost figure for each run, which the node logs. It is Grok Build's own figure; how xAI counts a run against your plan is up to xAI.
- Antigravity's answer includes the agent's token usage, which the node logs.

## Tests

Offline tests use stand-ins for Codex, Grok Build, Antigravity, OpenAI and the key window, and never generate anything. Run them with ComfyUI's Python from the `tests` folder:

```
cd "D:\Comfy\Local\ComfyUI\custom_nodes\comfyui-gpt-images\tests"
"D:\Comfy\Local\ComfyUI\.venv\Scripts\python.exe" -m unittest
```

The Codex, Grok and Antigravity tests use POSIX shell scripts as stand-ins and are skipped on Windows.

## What has and has not been tested

Tested on Linux: the offline tests, and all three nodes loaded and run inside ComfyUI v0.42.4 with its real frontend in both node designs, against stand-ins for Codex, Grok Build, Antigravity, OpenAI, the key window and Credential Manager. For the new nodes: references with transparency reaching each service pixel for pixel, the image and prompt outputs, several images from one run, Grok's upgrade message and 403 error, not signed in, unknown commands, quota and refused actions, API-key mode refused, API keys kept away from both programs, Stop, the reference limit, the run folder (and the refusal when the temporary folder lies inside ComfyUI's folder), and saving and reopening workflows. Version 4 workflows and every earlier test still pass.

Run for real on Windows: Subscription generations with and without references, also with GPT-6 Luna; the key window and saving the key to Credential Manager; Batch submissions with references, with their results. Grok Build's sign-in and a run limited to its image tools, which xAI refused with HTTP 403 for the account. Antigravity runs from test scripts: one without references and one with a reference. Version 5.0.0 of both new nodes in ComfyUI 0.43.0 (frontend 1.57.4), once each with one reference (2026-10-27): Grok Build accepted every option of the node's command and opened the session, then stopped at `--effort` (removed in 5.0.1); Antigravity ran for 221 seconds and ended with a 503 from a Google server, with no image.

Not yet seen for real: an image from either new node; a Grok image from an account whose plan includes image generation; Grok's agent working through the node's instruction; Antigravity with two or more references; how each service treats transparency in references; Stop, a missing install and quota errors with the real programs. Also not yet confirmed: whether the version 4 Batch instruction fixes the single-face result.
