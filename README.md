# Comfy-CLI-Subscription_Image-Video (version 6.1.0)

Three ComfyUI nodes in one package (before version 6.1 the package was called `comfyui-gpt-images`):

| Node | What it does | Who pays | Outputs |
| --- | --- | --- | --- |
| **GPT Images: Subscription / Batching gpt-image-2.5 sunburst** | **Subscription** mode: one image through the Codex CLI and your ChatGPT sign-in. **Sunburst Batch** mode: one `gpt-image-2.5-sunburst` job on the OpenAI Batch API. | Your Codex allowance, or your OpenAI API balance at Batch prices | `image`, or `batch_id` and `batch_url` |
| **Grok Images: Subscription** | **Image** mode: one image through Grok Build and your Grok sign-in, made by Grok Imagine. **Video: frames** and **Video: references** modes (new): one video from Grok's video tool. | Your Grok plan. Never an xAI API key. | `image` and `imagine_prompt` (Image mode); `video`, `first_frame` and `imagine_prompt` (Video modes) |
| **Antigravity Nano Banana: Subscription** (renamed from Antigravity Images) | One image through the Antigravity CLI and your Google sign-in. | Your Google plan's Antigravity quota. Never a Gemini API key. | `image`: every image Antigravity saved in the run |

Only the node (and mode) you run is used. None of them uses ComfyUI credits or OpenRouter. ComfyUI's own "Grok Image" partner nodes are different nodes: they use Comfy credits.

Pure Python: the standard library plus what ComfyUI already ships (PyTorch, NumPy, Pillow, PyAV). No pip installs, no JavaScript, no frontend extension.

## What's new in version 6.1

**The package has a new name: `Comfy-CLI-Subscription_Image-Video`.** The features outgrew "GPT Images", so the folder, the zip and the menu group now carry the new name. Nothing else changed: the three nodes behave exactly as in version 6.0.0.

- **One menu group.** All three nodes sit directly in the add-node menu group **Comfy-CLI-Subscription_Image-Video**, with no sub-groups. They were in three groups before (**GPT Images**, **Grok Images**, **Antigravity Nano Banana**). Searching still finds them by the same words.
- **The badge** on each node shows the folder name, but ComfyUI's frontend cuts a leading "Comfy-" off it, so the badge reads `CLI-Subscription_Image-Video` (seen in frontend 1.57.4). The menu group keeps the full name.
- **Node ids did not change** (`GPTImagesSubscriptionSunburstBatch`, `GrokImagesSubscription`, `AntigravityNanoBananaSubscription`), so workflows saved with version 6.0.0 open and run as they are.
- **Your stored OpenAI API key is still found.** It stays in Windows Credential Manager under the same entry, so you do not enter it again.

### Upgrading to 6.1

1. Close ComfyUI.
2. **Delete the old folder** `custom_nodes\comfyui-gpt-images`. Do this before adding the new one: both folders define the same nodes (same ids), so keeping both makes them clash.
3. Unzip the new folder `Comfy-CLI-Subscription_Image-Video` into `custom_nodes` (see Install), then start ComfyUI.

## What's new in version 6

**Grok Images makes videos.** A new **mode** dropdown sits above the prompt:

- **Image**: what version 5 did. One image from the prompt and up to 5 reference images.
- **Video: frames**: a video from the prompt and a first, a middle and/or a last frame. Three sockets, `first_frame`, `middle_frame` and `last_frame`, are all visible at once; connect any of them, in any combination. The first frame is the exact first image of the video, the last frame the exact last image, and the middle frame appears halfway through the video (at seconds / 2; Grok snaps it to a 1/3-second grid).
- **Video: references**: a video from the prompt and up to 14 reference images (Grok's limit). The image sockets grow as before: each new connection opens the next socket. Write "image 1", "image 2" and so on in the prompt where a reference should appear; the node turns them into Grok's own tags (`<IMAGE_0>`, `<IMAGE_1>`, ...).

Both video modes show these dropdowns, with the choices Grok Build's video tool accepts (from its public source, synced 2026-09-29):

- **aspect_ratio**: 1:1, 16:9 (default), 9:16, 4:3, 3:4, 3:2, 2:3.
- **seconds**: 1 to 15 (default 6).
- **resolution**: 480p or 720p (default 720p).
- **voice_1, voice_2, voice_3**: `none` (default) or one of Grok's 28 preset voices (the names in xAI's text-to-speech documentation, read 2026-10-28). Write "voice 1" in the prompt where that voice should speak; the node turns it into Grok's tag (`<AUDIO_0>`, ...).

**With no image and no voice, the prompt makes the first frame.** The node first asks Grok Imagine for a picture from your prompt (at the chosen aspect ratio), then makes the video with that picture as the exact first frame, as in your PowerShell test. The picture comes out of the new **first_frame** output. If you choose a voice but connect no image, the video is made from the prompt and the voice alone: no first frame is made and `first_frame` gives nothing.

**Outputs of the Grok node:** `image` (Image mode), `imagine_prompt` (the exact prompt Grok's agent sent to Imagine, or to the video tool in a Video mode), `video` (a VIDEO; connect it to **Save Video**), and `first_frame` (only when the node made it from your prompt).

**No video or audio files as references.** Grok Build's video tool takes images and its preset voices only, so the node has no video or audio inputs.

**Video needs Grok Build's privacy mode off** (in Grok Build, type `/privacy` and set "Coding data, retention, and training" to Opt in) and a Grok plan that includes video generation. If privacy mode is on, the node stops with that instruction.

**Antigravity Images is now Antigravity Nano Banana: Subscription.** Same node, new name: display name, category (**Antigravity Nano Banana**), log tag and run-folder prefix. Its internal id changed too (`AntigravityNanoBananaSubscription`), so a workflow that holds the old node shows it as a missing node: add the new node and connect the same prompt and images.

### Upgrading from version 5 or older

Replace the whole folder and restart ComfyUI. Workflows saved with version 5 or 4 open as they are, with two exceptions:

- **A Grok node saved with version 5.** The new `mode` dropdown is the first control, and ComfyUI restores saved values by position, so the old prompt text lands in the mode dropdown and the prompt box is empty. The image links come back. Running it stops with "The mode dropdown holds text that is not one of its choices..." and nothing is sent. Fix: turn on ComfyUI's experimental setting **Restore widget values by name** (Settings, Comfy, Workflow) before opening the workflow, or choose **Image** in the dropdown and write the prompt again (read the old text from the dropdown first; it is also in the workflow file).
- **An Antigravity Images node** is a missing node (see above).

**Before opening Batch workflows saved with version 3,** turn on ComfyUI's experimental setting **Restore widget values by name** (Settings, Comfy, Workflow; or search the Settings for it). With it on, those workflows open with their prompt and settings, and the transparent switch off. Without it, ComfyUI restores values by position: the prompt text lands in the transparent switch and the prompt box is empty. The node refuses to run either way until you fix it. Workflows saved with version 2 in Batch mode open with an empty prompt unless that setting is on. Workflows saved with version 1 need their resolution picked again.

## Earlier versions

**5.0.1.** Grok Images no longer passes `--effort low` to Grok Build (on your machine it stopped the run before Grok's agent started: "Couldn't apply reasoning effort: Invalid params: "unknown model id""). Clearer errors when Grok Build stops before its agent runs and when a Google server fails (503 UNAVAILABLE, "Deadline expired"): the error says it is server-side, that nothing was retried, and where Antigravity's files for that run are.

**5.** The Grok and Antigravity nodes. References with transparency (RGBA, for example from **Join Image with Alpha**) work in all nodes and both GPT modes; they are sent as PNG exactly as they reach the node. If references are too large as PNG and one has transparency, the node stops and asks you to reduce or flatten that image yourself; it never removes transparency on its own. Every subscription run works in its own new folder in your temporary folder, never in Windows' folder (System32) or in ComfyUI's folder, and deletes it when the run ends. If Codex, Grok Build or Antigravity leaves a background helper running after it finishes, the node no longer waits for that helper.

## Requirements

- ComfyUI with the V3 node features DynamicCombo and Autogrow. Tested with ComfyUI **v0.42.4** (frontends **1.55.18** and **1.57.4**) and ComfyUI **v0.43.0** (frontend **1.57.4**), in both the classic and the newer node design.
- GPT Images, Subscription mode: the **Codex CLI**, signed in with ChatGPT, with access to GPT-6 Luna. On Windows the ChatGPT desktop app installs it. Built against `codex-cli 0.163.0-alpha.11.1`.
- GPT Images, Sunburst Batch mode: an **OpenAI API key** from your Platform account. A restricted key needs access to Batches and Files.
- Grok Images: **Grok Build** (`grok`), signed in with a Grok account whose plan includes image generation (and video generation, for the Video modes). Built against Grok Build's public source (synced 2026-09-29) and your test runs.
- Antigravity Nano Banana: the **Antigravity CLI** (`agy`), signed in with your Google account, with the agent model `gemini-3.8-flash-low` (the one your tests used).

## Install

1. Unzip so that this folder sits at `custom_nodes\Comfy-CLI-Subscription_Image-Video` inside your ComfyUI folder.
   For ComfyUI Desktop that is inside the base folder chosen at install, for example
   `D:\Comfy\Local\ComfyUI\custom_nodes\Comfy-CLI-Subscription_Image-Video`.
2. Restart ComfyUI. The three nodes are in the menu group **Comfy-CLI-Subscription_Image-Video**. Searching "sunburst", "codex", "grok", "grok video", "nano banana" or "antigravity" also finds them.

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
  "D:\Comfy\Local\ComfyUI\.venv\Scripts\python.exe" "D:\Comfy\Local\ComfyUI\custom_nodes\Comfy-CLI-Subscription_Image-Video\setup_credentials.py" set
  ```
  `status` shows which key source the node will use (never the key itself); `delete` removes the stored key. You can also remove it in Windows' Credential Manager under Windows Credentials.
- The `OPENAI_API_KEY` environment variable. It takes priority over the stored key. Set it for your Windows user, then fully quit and reopen ComfyUI. If OpenAI rejects a key from the variable, the node tells you to fix or remove the variable; it does not open the window. Every program you run can see this variable, and OpenAI's standard libraries use it automatically.

On Linux and macOS there is no key window: use the environment variable.

### GPT Images: Codex sign-in (Subscription mode only)

Run `codex login` once in a terminal and sign in with ChatGPT; `codex login status` should then say `Logged in using ChatGPT`. The node checks this the first time Subscription mode runs in a ComfyUI session and refuses any other kind of login (for example an API key), so Subscription mode cannot bill your API account. It also never passes `OPENAI_API_KEY` to Codex.

### Grok Images: Grok Build sign-in

1. Install Grok Build. In PowerShell: `irm https://x.ai/cli/install.ps1 | iex`. This puts `grok.exe` in `%USERPROFILE%\.grok\bin`.
2. Run `grok login` once in a terminal and sign in with your Grok account.
3. For the Video modes: in Grok Build, type `/privacy` and set "Coding data, retention, and training" to Opt in. With privacy mode (zero data retention) on, Grok Build refuses to make videos. Its other way out, a user-hosted storage bucket (docs.x.ai/build/settings/zdr-video-storage), is not supported by this node: the video would go to your bucket, not to this computer, and the node says so if that happens.
4. The node uses `%USERPROFILE%\.grok\bin\grok.exe` (or `bin\grok.exe` inside `GROK_HOME`, if you set that variable), otherwise `grok.exe` on PATH.

Sign-in only: the node removes `XAI_API_KEY` from Grok's environment and sets `GROK_DISABLE_API_KEY_AUTH=1`, Grok Build's own switch that turns off API-key login. So a run cannot bill an xAI API account.

Image generation needs a Grok plan that includes it, and video generation a plan that includes that. On the Free and X Basic plans Grok answers with its SuperGrok upgrade message, which the node shows as an error. An HTTP 403 from xAI means xAI refused the request for that account; the error quotes xAI's reason (in your first test: "credits are exhausted or a Grok subscription is required"). After subscribing or changing plan, `grok logout` and `grok login` once fixed the node on your machine.

### Antigravity Nano Banana: Google sign-in

1. Install the Antigravity CLI. In PowerShell: `irm https://antigravity.google/cli/install.ps1 | iex`. This puts `agy.exe` in `%LOCALAPPDATA%\agy\bin`.
2. Open a normal PowerShell (not as administrator), run `agy` once and sign in with your Google account. An administrator PowerShell starts in `C:\Windows\System32`, which is why Antigravity asked for access to System32 in your test. The node itself never runs there.
3. Your choices in Antigravity's settings stay yours: "Use G1 Credits" (`useG1Credits`) decides whether paid AI credits may be spent once your plan's quota is used up, and "Enable Telemetry" is in `/config`.

The node refuses Gemini API-key mode: if `%USERPROFILE%\.gemini\antigravity-cli\settings.json` contains `"modelProvider": "gemini"`, it stops with an error instead of running. It also removes `GEMINI_API_KEY`, `AGY_ADC_AUTH` and `GOOGLE_GEMINI_BASE_URL` from Antigravity's environment (`AGY_ADC_AUTH=true` would switch to Google Cloud credentials billed to a Cloud project).

## Wiring

1. Add the node.
2. Optional references: connect **Load Image** (or any IMAGE output) to `image_1`. A new socket appears each time you connect one. For an image with transparency, connect Load Image's IMAGE and MASK to **Join Image with Alpha** (`image` and `alpha`) and its output to `image_1`.
3. Connect `image` to **Preview Image** or **Save Image**. For the GPT node in Batch mode, connect `batch_url` (or `batch_id`) to **Preview as Text** instead. For Grok, you can connect `imagine_prompt` to **Preview as Text**.
4. Write the prompt, set the run count, and press Run.

**Grok video, frames.** Set **mode** to **Video: frames**. Connect your images to `first_frame`, `middle_frame` and/or `last_frame` (one, two or all three). Pick aspect_ratio, seconds and resolution. Connect `video` to **Save Video**. The `image_1...` sockets stay empty in this mode.

**Grok video, references.** Set **mode** to **Video: references**. Connect your images to `image_1`, `image_2`, ... and write "image 1", "image 2" in the prompt where each should appear (for example: "image 1 walks into the room of image 2"). Choose voices if you want speech and write "voice 1" where the first voice should speak. Connect `video` to **Save Video**.

**Grok video from the prompt alone.** Set **mode** to **Video: frames** (or references), connect nothing, choose no voice, write the prompt. The node makes the first frame from the prompt, then the video. Connect `first_frame` to **Preview Image** or **Save Image** to keep the first frame.

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

- **mode** (above the prompt): **Image**, **Video: frames** or **Video: references**. The prompt box and the image sockets are shared by all modes: text you typed stays when you change the mode, and so do the links on the image sockets. The settings of each video mode (aspect ratio, seconds, resolution, voices) are remembered per mode while the workflow stays open; a saved workflow keeps the settings of the mode that is selected. Switching away from **Video: frames** removes its three frame sockets and their links; connect them again when you come back.
- **prompt**: sent as written to Grok's agent, which passes it to Grok Imagine (Image) or to Grok's video tool (Video). The agent may reword it; `imagine_prompt` shows what it sent. In **Image** mode there are no size or aspect ratio controls: ask for an aspect ratio in the prompt.
- **image_1 to image_14**: reference images, used in socket order; every image inside a connected batch counts as one. **Image** mode takes up to 5. **Video: references** takes up to 14. **Video: frames** does not use these sockets: if an image is connected to one, the node stops before running and says so.
- **first_frame, middle_frame, last_frame** (Video: frames only): one image each. A socket that receives a batch of several images is refused (pick one image first, for example with an image selector node).
- **aspect_ratio, seconds, resolution, voice_1, voice_2, voice_3** (both Video modes): see "What's new in version 6".
- Words in a Video prompt that the node converts, before anything is sent: "image 1", "image 2", ... (Video: references only; also "Image 2", "image2", "image_2", "image #2"; the number is the socket number, and a socket that carries a batch becomes one tag per image) and "voice 1", "voice 2", "voice 3" (the number of the voice dropdown; the tag numbers follow the voices you chose, in order). Grok's own tags written directly (`<IMAGE_0>`) are left alone. The log lists each change. The log warns when a connected image or a chosen voice is not mentioned in the prompt (Grok may then use a reference only loosely), and when the prompt names an image or voice that is not there (the text is left as written).
- Outputs: **image**, every image Grok made in an Image run (normally one); **imagine_prompt**, for each image the exact prompt Grok's agent sent to Imagine, and for a video the exact prompt it sent to the video tool, both read from the tool call itself; **video**, the finished video (Video modes); **first_frame**, the picture the node made from your prompt (Video modes, only when no image and no voice were given). The outputs a mode does not produce are skipped, so anything connected to them simply does not run. The node shows no video preview itself: connect `video` to **Save Video** or any node that takes a VIDEO.

### Antigravity Nano Banana: Subscription

- **prompt**: sent as written to Antigravity's agent, which hands it to its image generator. The image generator writes its own final prompt, and Antigravity does not report it reliably, so this node has no prompt output. Ask for an aspect ratio in the prompt.
- **image_1 to image_5**: optional references, at most 5 in total, used in socket order.
- Output: **image**, every new image Antigravity saved in the run, oldest first. One image is asked for, but its image generator may make up to three attempts; each saved attempt comes out.

The Grok image and Antigravity outputs are lists: each image goes to the next node on its own, so images of different sizes are fine. Preview Image shows all of them; Save Image saves each one.

### Every run is a new job

Each queued run makes a new generation (Subscription nodes) or a new paid batch job (Batch), even with unchanged inputs. Use the run count for several images or videos. To work on other parts of the workflow without generating, mute or bypass the node; nodes after it then get no image.

## What happens in each node

**GPT Images, Subscription.** The node finds Codex (the newest copy installed by the ChatGPT app, otherwise `codex` on PATH), checks the sign-in, writes the references into its own run folder, and runs one Codex task with GPT-6 Luna at low reasoning. It takes the image from Codex's folder for that task (`.codex\generated_images\<task id>` in your user folder), checks that the file is new and not one of the inputs, and loads it. If the image is blocked, the error names it as a safety block and quotes Codex's exact reason; the same prompt can pass on another try.

**GPT Images, Sunburst Batch.** The node gets the API key (asking for it if needed), encodes the references, builds one JSONL line, checks its exact size, uploads it and creates the batch. It does not poll or download results: get the output file from the batch page (`batch_url`). Errors quote OpenAI's own answer. If the upload fails, no batch was created and queueing again cannot make a duplicate. If OpenAI answers with a server error, or the connection drops, after the batch request was sent, a batch may exist: check your batches page before queueing again.

**Grok Images, Image.** The node finds Grok Build, writes the references into its run folder, and runs Grok Build once without a window: a new session, only its two image tools (`image_gen` without references, `image_edit` with them), tool calls approved automatically, at most 4 turns, Grok Build's default effort. The instruction asks for exactly one tool call with your prompt unchanged. The node takes each image's path and prompt from the tool call itself, not from what the agent says afterwards, and loads the images. The log shows each image's size, the aspect ratio, and the cost Grok Build reports for the run. The images stay where Grok saved them (`.grok\sessions\...\images` in your user folder).

What Grok's image tools do with your files, from Grok Build's source: images come out at Grok's "1k" resolution as JPEG. A reference file of at most 400 KB is used as it is. A larger one is shrunk to at most 768 pixels on its long side and re-encoded to fit in 400 KB, as PNG or JPEG, whichever is smaller. For photos that is usually JPEG, which has no transparency: transparent areas then show the colours stored under them. The node sends your image in its original form; this step happens inside Grok. Grok refuses references over 12,000,000 pixels (unless the file is at most 400 KB), so the node stops before running and names them; reduce them yourself. With one reference, the output keeps that reference's aspect ratio; otherwise the agent's choice applies, or `auto`. If Grok's agent makes an image with `image_gen` although you connected references, that image ignores them: the node still outputs it and says so in the log.

**Grok Images, Video.** The node finds Grok Build, writes the connected images into its run folder (PNG exactly as they reach the node, transparency kept), and runs Grok Build once without a window: a new session, only the video tool (`reference_to_video`), plus `image_gen` when the prompt has to make the first frame, tool calls approved automatically, at most 4 turns (6 when a first frame is made), Grok Build's default effort. The instruction names every argument of the video tool, so the agent only passes them on: the prompt unchanged, the aspect ratio, the duration, the resolution, and then the images (`first_frame`, `last_frame`, or for the middle frame a `keyframes` entry at seconds / 2, or `images` for references, in socket order) and the voices. The node takes the video's path and the arguments the agent really used from the tool call itself, loads the video into memory, and outputs it. The log shows the video's size, length and file size, the cost Grok Build reports, and warns when the agent changed the prompt or a setting before calling the tool. Grok keeps its own copy of every video (`.grok\sessions\<id>\videos` in your user folder); the node never changes or deletes it. Grok Build waits at most 300 seconds for a video; the node itself gives up after 25 minutes. If Grok makes more than one video in a run, the first one is output and the others are named in the log.

What the video tool does with your images is up to Grok: from its source, a first or last frame is pinned as the exact frame, the middle frame is a keyframe that Grok snaps to a 1/3-second grid, and the reference images are what the prompt's `<IMAGE_n>` tags point to. The node does not check the size or format of video reference images beforehand, because Grok Build's limits for them are not known to this project; Grok's own error is shown if it refuses one.

**Antigravity Nano Banana.** The node finds the Antigravity CLI, refuses API-key mode, writes the references into its run folder, and runs one request in that folder: `agy -p <request> --model gemini-3.8-flash-low --output-format json --json-schema <file> --print-timeout 15m`. The request asks for exactly one image from Antigravity's built-in image generator, with your prompt unchanged and the references given by their paths. The node then takes every new image saved at the top level of this run's conversation folder (`.gemini\antigravity-cli\brain\<conversation id>` in your user folder), plus image paths the agent reports inside `.gemini`. It never returns a reference or an identical copy of one: Antigravity keeps a copy of each reference in a `.tempmediaStorage` folder. The images stay in Antigravity's folder. In your test, one reference gave one 1024 x 1024 JPEG that kept the same person.

**All subscription runs** give up after 15 minutes (Grok video: 25 minutes). Stop in ComfyUI ends the program (and anything it started). Nothing is ever retried automatically. The run folder (`Comfy-CLI-Subscription_Image-Video-...`, `comfyui-grok-images-...` or `comfyui-nano-banana-...` in your temporary folder, with the reference copies) is deleted when the run ends.

## Limits and size checks

- **Batch file: at most 200 MB.** That is OpenAI's documented Batch input limit; the documented per-request limit (512 MB) is higher. References are encoded as lossless PNG first. If the request would exceed 200 MB, all references are encoded again as JPEG (quality 95, full colour resolution) and checked again; if it is still too large, the node stops with an error. Roughly 6 to 8 PNG references at 4K fit, depending on the image.
- **Subscription nodes (GPT Subscription, Grok, Antigravity Nano Banana): each reference under 50 MB** (Codex's own limit; the same ceiling is used for Grok and Antigravity). The same PNG-then-JPEG rule applies. At most 5 references for GPT Subscription, Grok Image mode and Antigravity Nano Banana; **at most 14 for Grok Video: references** and 3 for **Video: frames** (one per frame socket).
- **Grok video:** 1 to 15 seconds, 480p or 720p, the seven aspect ratios above, at most 3 voices. These are the limits of Grok Build's video tool; the dropdowns offer nothing else.
- **Grok Image mode:** references over 12,000,000 pixels whose file is over 400 KB are refused before running, because Grok's image tool refuses them (for example 4096 x 4096; 4000 x 3000 still fits).
- **Transparency:** references with 4 channels (RGBA) are sent as RGBA PNG. JPEG cannot keep transparency, so if the JPEG fallback would be needed and any reference has transparency, the node stops before sending anything and names the references. Reduce their size, or flatten them before the node, then run again.
- References must have 3 channels (RGB, as Load Image gives) or 4 (RGBA).
- Files on disk are never changed. The nodes work only on the pixels ComfyUI passes in and write only into their own run folder. Grok's and Antigravity's own folders keep what those programs saved.
- The subscription nodes have no quality or moderation settings.

## Billing and privacy

- Your prompt and references go to the company behind each node: OpenAI (GPT Images), xAI (Grok Images), Google (Antigravity Nano Banana). Their terms decide how they are used.
- Grok Build reports a cost figure for each run, which the node logs. It is Grok Build's own figure; how xAI counts a run against your plan is up to xAI. A video made from the prompt alone first generates a picture, so it uses an image generation as well as the video.
- Antigravity's answer includes the agent's token usage, which the node logs.
- Grok Build's video tool refuses to run while its privacy mode (zero data retention) is on, so video runs are done with that mode off. What xAI may do with the data in that case is set by xAI's terms; Grok Build's `/privacy` screen describes it.

## Tests

Offline tests use stand-ins for Codex, Grok Build, Antigravity, OpenAI and the key window, and never generate anything. Run them with ComfyUI's Python from the `tests` folder:

```
cd "D:\Comfy\Local\ComfyUI\custom_nodes\Comfy-CLI-Subscription_Image-Video\tests"
"D:\Comfy\Local\ComfyUI\.venv\Scripts\python.exe" -m unittest
```

The Codex, Grok and Antigravity tests use POSIX shell scripts as stand-ins and are skipped on Windows.

## What has and has not been tested

Tested on Linux, against stand-ins for Codex, Grok Build, Antigravity, OpenAI and the key window:

- The 174 offline tests, which include every Grok failure message: not signed in, the SuperGrok upgrade message, HTTP 403 and 429, privacy mode on, a video sent to a storage bucket, a 300-second timeout, an unknown voice, an unknown command, and a run that gives no file.
- All three nodes loaded in ComfyUI v0.42.4 (real frontends 1.55.18 and 1.57.4) and v0.43.0 (frontend 1.57.4), in both node designs: the mode dropdown and its three modes, the sockets and dropdowns of each, connecting images, saving and reopening a workflow in each mode, and switching modes.
- The Grok node run end to end in all three modes inside ComfyUI, against a stand-in for Grok Build that saves a real H.264 video: the video through **Save Video** and **Get Video Components**; the first frame made from the prompt; frames and references reaching Grok's tool pixel for pixel, with transparency kept; the instruction and the tool arguments for each combination (including the middle frame at half the length); "image 1" and "voice 1" turned into Grok's tags; the limits (14 references, 5 in Image mode, frames only on the frame sockets); the privacy-mode failure; Stop; no API key reaching Grok Build; the run folder; and the refusal to run when the temporary folder lies inside ComfyUI's folder.
- Antigravity Nano Banana end to end, the GPT node, and every earlier test.
- Workflows saved with version 5 and opened in version 6: the Grok node's old prompt lands in the mode dropdown unless "Restore widget values by name" is on (see Upgrading).

Run for real on Windows (by you): GPT Subscription generations with and without references; the key window and saving the key to Credential Manager; Batch submissions with references, with their results; Grok Images (version 5.0.1) generations after `grok logout` and `grok login`; Antigravity with one reference, which gave an image. Your PowerShell test of a video (2026-10-28): Grok's agent called `image_gen` and then `reference_to_video` with the arguments it was given, but no video came out of that test.

Not yet seen for real: a finished video from this node, in any of the Video modes; frames, references and voices reaching Grok's video tool through the node; how each service treats transparency in references; version 6 on Windows (all tests ran on Linux); Stop, a missing install and quota errors with the real programs. Also not yet confirmed: whether the version 4 Batch instruction fixes the single-face result.
