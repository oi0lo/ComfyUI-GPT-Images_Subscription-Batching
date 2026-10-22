# ComfyUI GPT Images: Subscription + Sunburst Batching

A ComfyUI custom node with two independent ways to generate images through OpenAI:

| Mode | Authentication and usage | What the node returns |
| --- | --- | --- |
| **Subscription** | Codex CLI, signed in with a ChatGPT account. Subject to the account's Codex allowances, limits, and applicable charges. | An `IMAGE` output when generation finishes. |
| **Sunburst Batch** | An OpenAI **Platform API key**. Uses the Batch API and your API account's billing. | A `batch_id` and `batch_url` immediately after the job is submitted. **No image is downloaded.** |

The node is named **GPT Images: Subscription / Batching gpt-image-2.5 sunburst** in the **GPT Images** category. Only the selected mode runs.

> **Early release (v1.0.0):** This project relies on the Codex CLI's image-generation functionality and on specific OpenAI API models and parameters. Those interfaces, account entitlements, and availability may change. This is an independent community project, not an official OpenAI or ComfyUI integration.

## Features

- One node with a mode-dependent interface.
- Optional reference images, with automatically growing input sockets (`image_1`, `image_2`, etc.).
- **Subscription:** one image-generation task via the Codex CLI and an existing ChatGPT sign-in. The resulting image is loaded into ComfyUI.
- **Sunburst Batch:** uploads a single JSONL request to `/v1/files`, creates a Batch job for `/v1/responses`, and returns its ID and dashboard link. Uses `gpt-6-luna` with one `image_generation` tool call targeting `gpt-image-2.5-sunburst`.
- Sunburst aspect-ratio, resolution, and quality presets, including portrait sizes.
- Reference images are encoded as lossless PNG where possible, with JPEG quality 95 fallback when required by size limits.
- No frontend JavaScript or separate Python package installation. Uses Python's standard library and packages that ship with ComfyUI (PyTorch, NumPy, Pillow).

## Requirements

- A compatible **ComfyUI** build with V3 nodes, `DynamicCombo` and `Autogrow`. The original implementation was exercised with ComfyUI **v0.42.4** and frontend **1.55.18**; older builds may not work.
- **Subscription mode:** Codex CLI installed, its `image_generation` feature available, and signed in through **ChatGPT** rather than an API key. Developed against `codex-cli 0.163.0-alpha.11.1`. Feature and usage availability depend on the installed version and account.
- **Sunburst Batch mode:** an OpenAI Platform API key with access to the required models and Batch API. API billing is **separate** from ChatGPT subscriptions.

## Installation

Clone this repository directly into ComfyUI's `custom_nodes` directory:

```bash
cd /path/to/ComfyUI/custom_nodes
git clone https://github.com/oi0lo/ComfyUI-GPT-Images_Subscription-Batching.git comfyui-gpt-images
```

Alternatively, download the repository ZIP and extract **its contents** into `ComfyUI/custom_nodes/comfyui-gpt-images/`. There should be an `__init__.py` directly inside that directory, not another nested project directory.

Restart ComfyUI. Search for **GPT Images**, **sunburst**, or **codex** to add the node. There is no `requirements.txt` to install because the node uses ComfyUI's existing dependencies.

### Configure Subscription mode

1. Install the [Codex CLI](https://developers.openai.com/codex/cli/) and verify that the `codex` executable is available, or that its app-managed Windows executable can be found.
2. Run `codex login` and choose **ChatGPT sign-in**.
3. Verify with `codex login status`. The node expects the message `Logged in using ChatGPT`.
4. Select **Subscription**, enter a prompt, and connect the `image` output to **Preview Image** or **Save Image**.

The node removes `OPENAI_API_KEY`, `CODEX_API_KEY`, and `OPENAI_BASE_URL` from the spawned Codex process environment and checks its ChatGPT login. This is intended to avoid switching to API-key billing; it is **not** a guarantee about all possible Codex configurations or future versions. Codex usage counts against the applicable plan limits or credits. Subscription mode does not let you select the image model, resolution, or quality from node controls. Describe the desired format in the prompt.

### Configure Sunburst Batch mode

The key is read from `OPENAI_API_KEY`, or on Windows from an optional Windows Credential Manager entry. **Do not put an API key in the prompt, workflow, README, or repository.**

**Option A: Windows Credential Manager**

Open PowerShell and run the included helper using ComfyUI's Python (adjust these example paths):

```powershell
& "D:\Comfy\Local\ComfyUI\.venv\Scripts\python.exe" "D:\Comfy\Local\ComfyUI\custom_nodes\comfyui-gpt-images\setup_credentials.py" set
```

It prompts for the key with hidden input and stores it for your Windows account under `ComfyUI.GPTImages.OPENAI_API_KEY`. Substitute `status` to inspect which key source is selected, or `delete` to remove the saved entry. For portable ComfyUI, substitute its `python_embeded\python.exe` for the virtual-environment Python path.

**Option B: Environment variable (Windows, macOS, Linux)**

Set `OPENAI_API_KEY` in the environment **that launches ComfyUI**, then restart ComfyUI. For example, in a bash shell:

```bash
export OPENAI_API_KEY="your-api-key"
```

The environment variable takes priority over Windows Credential Manager. A wrongly configured environment key does not fall back to the stored key. Environment variables may be accessible to other processes launched under the same user account.

## Use the node

1. Add **GPT Images: Subscription / Batching gpt-image-2.5 sunburst** to your graph.
2. Optionally connect one or more `IMAGE` outputs to `image_1`, `image_2`, and subsequent inputs as they appear. All images within a connected image batch count as individual references, in socket order.
3. Enter a prompt and choose **Subscription** or **Sunburst Batch**.
4. For **Subscription**, connect `image` to **Preview Image** or **Save Image**.
5. For **Sunburst Batch**, choose `aspect_ratio`, `resolution`, and `quality`, then connect `batch_url` or `batch_id` to a compatible string display node, such as **Preview as Text**, to inspect the submitted job.
6. Queue the workflow. Each new queued run can create another image or **another billable Batch job**, even if the inputs are unchanged. Avoid accidental repeated submissions.

### Outputs

| Output | Subscription | Sunburst Batch |
| --- | --- | --- |
| `image` | Finished `IMAGE` tensor | Blocked / unavailable |
| `batch_id` | Blocked / unavailable | OpenAI batch ID |
| `batch_url` | Blocked / unavailable | Link to the batch dashboard |

Batch mode is asynchronous: this node **does not** wait for completion, retrieve the output JSONL, or decode the image. Open `batch_url` and retrieve the output file through the OpenAI Platform after processing finishes. The URL points to the account's dashboard, so you may need to sign in. See the [Batch API guide](https://developers.openai.com/api/docs/guides/batch).

### Sunburst controls

- **Aspect ratio:** `1:1`, `5:4`, `4:3`, `3:2`, `16:10`, `16:9`, `2:1`, `21:9`, and `3:1`, with landscape and portrait orientations where applicable.
- **Resolution:** four preset tiers per ratio. The maximum preset is **3840 × 2160** for 16:9, **2880 × 2880** for square, with other maxima depending on aspect ratio. Larger sizes are marked experimental where appropriate.
- **Quality:** `auto`, `low`, `medium`, `high`, `xhigh`, `max` (default: `max`).
- **Prompt:** sent as written in the Batch request to the Responses model. The Responses image tool may revise it before generation; check `revised_prompt` in the output when present.

Sunburst requests use `output_format="png"`, `background="auto"`, and `moderation="low"` in the image-generation tool configuration; these are fixed in v1, not adjustable from the node interface.

## Limits, behavior, and privacy

- **Subscription:** up to **5** individual reference images in the tested Codex build, each encoded image **under 50 MB**. This limit is version-dependent. Execution times out after **15 minutes**, and interrupting ComfyUI stops the spawned Codex process.
- **Batch:** one JSONL request per run, up to **200 MB**, including base64-encoded references. The code checks this before upload. Failed requests are not automatically retried; if creation fails after transmission, check the dashboard before queuing again to avoid duplicates.
- **Image encoding:** RGB images are transferred from ComfyUI, encoded first as PNG, and re-encoded as JPEG quality 95 if necessary to fit the applicable limit. The node does not edit your existing source image files.
- **Data disclosure:** both modes transmit prompts and any connected reference images to OpenAI services. Subscription mode also invokes a local Codex agent process and stores generated images in Codex's own image directory. Do not use sensitive prompts or images unless that data transfer is acceptable to you.
- **Authentication:** the node never places the Batch API key in a ComfyUI node input or saved workflow. The optional Windows Credential Manager entry is accessible to software running as the same Windows user.
- **Costs:** OpenAI API charges can apply when a Batch job is submitted. ChatGPT subscription pricing and Codex usage limits are separate, and using Codex may also consume credits depending on your plan. See [Codex plan usage](https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan) and [Batch API documentation](https://developers.openai.com/api/docs/guides/batch).

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Node missing in ComfyUI | Check that `__init__.py` is at the top of the custom-node folder; update ComfyUI to a build supporting V3 nodes, `DynamicCombo`, and `Autogrow`; restart. |
| `Codex CLI not found` | Install Codex CLI, verify `codex --version` (or app-managed Windows Codex), and restart ComfyUI. |
| Codex sign-in rejected | Run `codex login status`. Sign in through ChatGPT, not an API key. |
| Subscription ends with no image | Inspect ComfyUI logs, Codex access and limits, moderation messages, and the installed Codex version. |
| Batch says no key found | Set `OPENAI_API_KEY` in ComfyUI's environment or use the Windows setup helper; fully restart if changing environment variables. |
| Batch HTTP 401, 403, or 429 | Check your API key, project permissions, model access, quota, and rate limits. |
| Batch job exists but no image is returned | Expected: Batch mode only submits the request. Check the dashboard for output/error files. |
| References are too large | Resize images upstream or use fewer references. The JPEG fallback is automatic. |

## Tests and compatibility

The repository includes offline `unittest` tests with local stand-ins for Codex and HTTP, so they **do not** issue paid requests:

```bash
python -m unittest discover -s tests -v
```

**Release review:** 56 offline tests passed on Linux. Tests using POSIX shell stand-ins are skipped on Windows. The original project's README reports a ComfyUI v0.42.4 integration run on Linux against mock services, but the following paths have **not** been independently verified for this publication: actual Windows Credential Manager and Codex execution, reference-image generation against live Codex, and creation of a real OpenAI Batch job from the node. Treat this as an initial community release, not production-validated integration software.

## Versioning and community distribution

This repository is installable by cloning it into `custom_nodes`. **Publishing to GitHub does not automatically list it in ComfyUI Manager or the ComfyUI Registry.** A future Registry listing needs a registered publisher account, its actual `PublisherId`, and a separate publish step. No registry publisher ID or key is bundled in this repository.

## License and acknowledgements

Licensed under the **MIT License**, see [LICENSE](LICENSE). This license covers this project's source code, not OpenAI models, APIs, services, or trademarks. OpenAI, ChatGPT, Codex, GPT Images, and ComfyUI are names of their respective owners. This project is independent and unaffiliated with OpenAI and Comfy-Org.
