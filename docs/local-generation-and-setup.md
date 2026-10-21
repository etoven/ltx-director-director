# Make images and video locally

![The Director setup guide](images/setup-wizard.png)

Open **Help → Setup wizard** for a guided start, or **Settings → Local models** to change an existing setup. A new installation opens the guide automatically. Writing, images and video have separate sources: you can use Gemini to refine prompts while Flux makes artwork and H3 renders video on your computer.

## Find settings

Settings uses one searchable sidebar for **Application**, **Local models**, **ChatGPT plan**, **Workspaces**, and **Storage and backups**. Search by a setting name, such as **seed**, **LoRA**, or **backup**, to open its section. Clearing search restores the full sidebar and keeps your edits.

Local models expands into **Engine**, **Video · H3**, **Images · Flux**, and **Writing · local**. Hover the readiness icon for details: a green file section means its files are configured; a green Engine icon follows an explicit successful **Check engine** or **Prepare local engine** operation. Opening Settings does not start a generation or engine check. Long pages scroll inside the content card, while **Save** and **Cancel** stay below it. Support & Donate will become available when its link is provided.

![Settings sidebar and native engine controls](images/settings-local-engine.png)

## Choose a writing companion

**Gemini** is the recommended starting point when its free text tier suits your account and region. Open [Google AI Studio](https://aistudio.google.com/api-keys), sign in, choose or create a project and create an API key. Paste it into the wizard, choose an available text model and review [current model limits and pricing](https://ai.google.dev/gemini-api/docs/pricing).

**ChatGPT account** uses the existing browser sign-in controls. Continue with ChatGPT, grant eligible plan usage and select an available model. An account must be eligible for this integration, and its plan limits apply. See the [official sign-in guide](https://developers.openai.com/siwc/quickstart).

**OpenAI API** uses a key from your [OpenAI project](https://platform.openai.com/api-keys). Configure API billing and choose a model your account can access. API usage is separate from ChatGPT subscription usage. It is a useful option for detailed reasoning and structured edits; compare actual results on your own prompts.

**Local instruction model** uses a complete Transformers model folder with configuration, tokenizer and safetensors weights. Choose a folder you already have or download a Qwen instruction model. The file scan suggests a model size from detected VRAM; you can choose a different size or repository. Local writing has no API token charges, and its speed, memory requirements, context window and license depend on the selected model. The local writer uses authored prompt and timeline text; it does not analyze video or pretend to see images. Image-model Qwen encoders are kept separate from the writing-model chooser.

Use **Remember API keys on this device** only when you want keys stored in the app settings. With this option off, API keys last for the current session. Do not paste keys into prompts or projects. ChatGPT account credentials use the existing account connection controls.

## Choose an image source

The wizard defaults to **Gemini Flash Lite Image**. This is an image-generation model, separate from Gemini's similarly named text model. Gemini image API output requires a paid tier; a free text allowance does not supply free image output. Choose another image model or **Local · Flux 2 Klein** if that fits your workflow.

The default image source is used for segment artwork, image edits and **Add your own effect → Generate thumbnail**. The supplied Effects Library keeps its original catalog illustrations. Selecting a specific source never silently switches to a different billed provider.

## Prepare local models

The wizard’s sidebar expands **Local model setup** into **Engine**, **Video · H3**, **Images · Flux**, and **Writing · local**. Click a substep to open its controls. Amber icons indicate pending configuration or an unverified engine; a green Engine check means its actual dependency, import and execution checks succeeded. The file sections use green checks once their required files are configured. Hover an icon for details.

![Local engine setup with automatic and manual options](images/local-model-setup.png)

Choose **Automatically configure all local engines** to scan standard model folders, preserve files you already selected, prepare or repair Director’s private environment, and get configuration files for the selected or discovered video and image models. Model weights still use the separate **Download model set** action. Expand **Optional Config** for the Hugging Face token and verbose diagnostics. **Cancel preparation** stops the current setup operation. You can instead use every manual control below.

1. Choose **Prepare local engine**. Director creates a private inference environment under its application data folder. The first installation downloads several GB of Python libraries and CUDA PyTorch. Its dependencies and download cache stay in the managed `local-generation/engine` venv; no separate package installation is required. It does not alter your running ComfyUI instance or require a ComfyUI server. Use **Check engine** to verify actual library imports, dependency consistency and CUDA execution. If preparation was interrupted, run **Prepare local engine** again.
2. Use **Find my model files** to scan your chosen model folder. The initial folder suggestion is `~/Programs/ComfyUI/models`, but any folder is allowed. Scanning reads filenames in a background worker and leaves model files in place.
3. For images, choose **Klein Base 4B** or **9B**. The scan suggests 4B on smaller GPUs and 9B when at least 20 GB VRAM is detected. This is a starting recommendation, rather than a guarantee: system RAM, canvas size and other running apps matter too. Pair 4B with its Qwen 4B encoder, or 9B with its Qwen 8B encoder, and select the Flux 2 VAE.
4. For video, select a **pruned H3** checkpoint, a supported H3 text encoder, video VAE and audio VAE. Choose Ref2VA for reference media, or FL2VA for text and first/last frames. The native backend accepts INT8 ConvRot and the 14 GB NVFP4/AWQ encoder. NVFP4 weights remain packed on disk and are expanded per layer for computation; older NVIDIA GPUs do not need NVFP4 tensor cores. LoRAs are optional and selected separately in Settings.
5. Select **Image models** or **Video models**, then **Get configuration files**. This downloads tokenizer, processor and model configuration files needed alongside your weights. It does not download the model weights.
6. If weights are missing, choose **Download model set**. Review the approximate size before downloading. Matching files already selected in the dialog are reused. Gated repositories may require accepting the model's terms on Hugging Face and entering a token in the dialog; that token is kept only for that dialog.
7. Save Settings or finish the wizard. Its final page lists incomplete setup so you can return later. You can also choose **Set up later** and keep using the editor.

FP8 image checkpoints are expanded into a derived BF16 compatibility cache one tensor at a time. Sequential offload limits GPU residency during image inference. Original model files are untouched. This cache needs extra disk space and can make the first run longer. The local video engine uses disk offload to limit host memory pressure. Other apps keep their processes and models; free GPU memory before requesting a local render. Quantized native H3 uses an NVIDIA GPU; CPU mode is available for images and writing.

## Generate a video

![Local controls in Video Preview](images/local-video-controls.png)

Choose **Settings → Local models → Video** to set the canvas, length, sampling steps, sampler and seed. The initial settings are **16:9 at 0.5 MP (960 × 544), 15 seconds, 20 steps**. H3 aligns 15 seconds to **362 frames at 24 fps**, producing about 15.08 seconds. Choose **RES multistep with a simple schedule** or the native Euler sampler. No LoRA is selected automatically. Canvas, duration, adapters and references affect memory use and render time.

In Settings, choose **Random for each render** for a fresh variation each time, or turn it off and enter a static seed, including a full 64-bit seed from a workflow. The generation output lists the actual seed used. Seeds make native runs repeatable with the same inputs and settings; different engines and devices can produce different results.

![Local video Settings page with canvas, duration, sampler and a static seed](images/local-video-settings.png)

Click **Video Preview → Generate locally** to render the current **unified production output** with the files in the **Reference media panel**. Edit the prompt in its unified editor and manage references in their existing panel. Reference videos include their paired audio when available; the audio-only option uses just the extracted sound.

**Verbose output** in the generation panel streams loading messages, sampling details and error tracebacks. Use **Copy output** to share a failure report. Credentials are redacted, and raw job requests and diagnostics are not saved to disk.

The default **Live noisy latent** preview updates as sampling advances, using an animated view of every frame from the actual temporal latents with approximate colors. It does not load the VAE or add model passes during sampling. In **Settings → Local models → Video**, choose **Denoised estimate** to decode a full-clip clean estimate from the existing model prediction every **5 steps** by default; its interval is adjustable. This optional preview pauses sampling briefly for VAE decoding. The full video and audio are decoded at the final stage. The **H3 sampling network · layer** messages report the required transformer layers within each sampling step; they do not indicate preview decoding. The pill at the bottom right shows sampling percentage and an estimated remaining time once measured steps are available. Loading and preparation show a stage message until an estimate is meaningful. Drafts loop through the full video duration and can be noisy; the finished video contains the final decoded frames and audio. You can pause or resume the draft in the player.

**Cancel generation** stops that Director worker. A completed video appears in the preview and is attached to the current saved project. For an unsaved project, the generated file remains in the local generation folder and can be exported through the player. Switching projects while a job runs prevents its result from replacing the new project's preview; its output remains available in the generation folder.

## Generate artwork and effect thumbnails

Choose **Local · Flux 2 Klein** as the default image source. Use a segment's **Generate Artwork** or **Render requested artwork** action. The **Image generation** panel shows draft images, sampling percentage and estimated time. Final artwork follows the existing segment review workflow; source media is retained.

In **Effects Library → Add your own**, write the effect instruction and choose **Generate thumbnail**. The dialog uses the same image source and shows local draft previews and progress. Save to keep the custom effect. Cancel leaves the effect entry unchanged.

## Get help as you work

**Help → Interactive illustrated manual** opens the complete offline guide in your browser. Search the manual, jump between chapters or take the short guided tour. The wizard’s **Getting started** page offers three paths: **Interactive Quick Start** highlights the actual workspace controls; **Interactive Tutorial** opens illustrated walkthroughs; **Go to Workspace** saves your choices and returns to editing. **Browse Features** opens the full manual. The guided tour leaves your projects, media and authored text in place and never starts generation.

![Getting started cards and onboarding progress](images/onboarding-getting-started.png)

The thank-you page has a five-slide showcase with arrow buttons and direct slide selectors. **Review source choices and pending setup** shows your configuration summary. **Support & Donate** remains a placeholder until the donation link is available.

![The thank-you showcase](images/onboarding-thanks.png)

## Use LoRAs for local generation

![Multiple LoRAs in native generation Settings](images/local-loras.png)

Open **Settings → Local models → Video · H3** or **Images · Flux**. Each page has its own **LoRAs** list. Click **+** to add an adapter, then use **…** to choose its `.safetensors` file. Repeat **+** to stack more adapters. Choose files trained for the selected model and size: H3 video adapters belong in Video, and Flux Klein adapters belong in Images.

Enter the adapter's **trigger words** and set its **Weight** from **0.00 to 1.00**. A weight of 1 applies full strength; lower weights reduce its effect. Turn off the row's checkbox to retain it without using it, or choose **×** to remove it. Zero weight also disables the adapter and its trigger words. Save Settings to use the list; Cancel leaves the saved configuration intact.

Each local render adds the enabled adapter's trigger words to its generation prompt. Image adapters also apply to locally generated tile artwork. Your authored prompt remains unchanged, and image and video triggers stay separate. Duplicate trigger phrases already present in the prompt are not appended again.

Adapter files remain in their selected locations, and Director applies their contributions without changing the base checkpoint. Standard LoRA matrices are supported; DoRA adapters are not supported. Incompatible targets or dimensions appear in verbose output. The setup wizard keeps these advanced settings intact but does not show LoRA controls.
