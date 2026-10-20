# Aurora a108: native generation LoRAs

Settings → Local models has separate H3 video and Flux Klein image LoRA stacks. Users add rows with +, choose safetensors, enter trigger words and set each weight from 0 to 1. Rows can be disabled or removed. Zero strength disables both adapter and trigger. Saved configurations survive the setup wizard; onboarding has no adapter controls. Canceling Settings leaves the saved profile unchanged.

The native workers apply adapters and append active trigger phrases to their own generation inputs, preserving the authored project prompt. Local tile artwork uses the same image worker. H3 uses DiffSynth's low-rank hot-load path. Flux uses the pinned Diffusers format converters and registered residual modules before Accelerate configures offload. Quantized base weights remain immutable, with explicit target/dimension checks rather than silently skipping incompatible layers. LoRA filenames, weights and layer counts appear in verbose output. Standard LoRA is supported; DoRA is rejected with an actionable error.

The helper ships in the app wheel and is copied into the isolated inference worker alongside the other local modules. It uses libraries already installed by Prepare local engine into the private venv; no GUI-side ML dependency or new external runtime is needed.

Validation: profile/trigger/preflight tests, small CPU numerical adapter stacking and alpha-scaling checks, packed Flux weight preservation, repeated Accelerate offload forwards, native H3 hot-loading, Settings persistence/cancel and wizard preservation. The actual Settings capture illustrates the shipped offline manual. GPU sampling remains deferred at the user's request while their render occupies the device. Release CI and installed-wheel checks are recorded after completion.

Completed local verification: all six isolated native local-generation UI groups passed, as did the initial four CPU adapter checks in the app's private inference venv. The a108 wheel matches the application source and assets, passes ZIP integrity checking, and its installed copy passed Settings persistence, trigger augmentation, wizard preservation and existing native video request checks. The final expanded CPU run includes training-metadata coverage. No GPU sampling was attempted.

Final expanded CPU verification: 5/5 passed against the final source in the app's private inference venv, including training alpha/rsLoRA metadata and rejecting base checkpoints before reading weights. No additional inference dependencies were installed or required.
