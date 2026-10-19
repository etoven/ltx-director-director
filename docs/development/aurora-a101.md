# Aurora a101 development notes

The a100 Effects Library build was published and verified before work began on the second release. Its existing catalog illustrations remain the bundled tile artwork.

## Local generation

Native inference runs in a cancellable, app-managed Python subprocess with a JSON-lines progress protocol. ML imports remain outside the UI process. A process lock serializes GPU work; cancellation terminates only that worker's process group. Request files and transient stderr logs are removed on completion or failure. Generation outputs remain in the application data folder.

H3 uses DiffSynth-Studio's pruned INT8 ConvRot path, pinned to commit `681c456a3e8cf9e17cf2cb9a35297718ed4270ca`. No Lightning/Turbo LoRA is selected or downloaded. The discovered NVFP4/AWQ text encoder is explicitly identified as incompatible with this backend; setup offers the supported INT8 encoder. Video, audio and processor files stay separate. Reference video decoding is bounded to the selected trimmed interval, resampled to 24 fps and silent unless an audio-only reference is explicitly selected.

Flux Klein uses Diffusers' native single-file transformer conversion. Scaled FP8 inputs are expanded one tensor at a time into an atomic BF16 compatibility cache; originals are immutable. The corresponding Qwen encoder and Flux 2 VAE are loaded separately. Sequential CPU offload reduces GPU residency at the cost of speed. Draft previews decode actual intermediate latents; they are not quality or timing benchmarks.

The local writer uses a complete Transformers instruction-model folder and the existing operation-specific JSON response schemas. Encoder-only image checkpoints are not offered as chat models. The text path receives authored timeline context and does not pretend to analyze images or video.

Project identity and workspace revision guard preview updates and final attachments. A new local video is copied in the background to a unique managed file, retaining prior source renders. Unsaved generated videos can be exported before a project is saved.

## Onboarding and help

The six-page native wizard uses the existing ChatGPT account panel and separate text/image preferences. API keys remain session-only unless the user elects to remember them. Setup can be postponed. The offline manual packages 13 illustrated product chapters with search, a guided tour and an interactive scope example.

Header artwork uses the built-in ImageGen tool. Final assets are `ltx_prompt_director/assets/onboarding/{welcome,language,local,images,getting-started,thanks}.png`. All six outputs were visually inspected and copied into the repository. Original generated files remain in the tool's image directory.

Prompt set: each image uses elegant thin luminous cyan and pale ivory line art, restrained teal aurora ribbons and tiny constellations on a deep navy `#14232d` background, a wide 3:1 composition, centered objects with generous margins, and no text, logos, watermark, photographs or interface mockup.

- Welcome: a director's chair, tripod film camera and three storyboard frames joined by a flowing aurora ribbon.
- Writing: a fountain pen traces a story ribbon joining a cloud, a key and a conversation bubble.
- Local: a desktop workstation and graphics-card microchip connected to a film frame.
- Images: a paintbrush transforms a landscape outline into three luminous storyboard cards.
- Getting started: storyboard cards follow a timeline toward a film camera and a play symbol.
- Thanks: a director's chair, open clapperboard, glowing heart and distant star.

## Validation and limits

A CPU tensor test verified scaled FP8 conversion, scalar tensors, cache reuse and source-file preservation. The core prompt/project test group, isolated native UI checks, subprocess protocol/cancellation, reference trimming, model scanning and stale-result handling were exercised. The manual's search and tour were checked in the local browser, and all local manual links were verified. Native documentation screenshots use demonstration controls without provider calls.

Weighted GPU inference was not run: the user requested that the existing ComfyUI process keep running with its GPU allocation. No claim of model-render quality, speed, VRAM fit or complete checkpoint compatibility is made from the transport/UI tests. The compatible native H3 encoder and optional inference environment still need setup before a first render on this machine.

The exact prompts and saved asset filenames are recorded in [onboarding-artwork-prompts.json](onboarding-artwork-prompts.json).
