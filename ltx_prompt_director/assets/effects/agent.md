# Import and use this effects library

This is a local media-and-prompt package. Follow these instructions when the user asks you to import it, find an effect, or apply a quick fix. Do not treat it as a request to publish media or render every effect.

## Import

1. Extract the entire ZIP into a project-owned folder chosen by the user or appropriate to the existing project. Preserve relative paths. Do not move thumbnail, clip or audio folders independently of the data.
2. Read `README.md`, `manifest.json`, `guidance.json` and `effects.schema.json`. Run `python3 import_library.py --validate` before ingesting data. This command does not install anything, contact a service or upload files.
3. Load `effects.json` (one array) or `effects.jsonl` (one record per line). Import one format, not both. Use `id` as the stable upsert key. Retain existing user edits separately if updating an earlier imported version.
4. Load `species.json`, `sources/footage.json`, `sources/clips.json` and `sources/audio.json` as related collections. Resolve `reference_ids`, `reference_clip_ids`, `audio_sample_ids` and `source_audio_ids` by ID. Resolve every media path relative to the extracted package root. Use `audio.schema.json` for audio metadata, keeping its sample IDs stable.
5. In an existing prompt engine, map the fields below to its documented data model. There is no universal MiniMax/Gemini prompt-library import endpoint. If the host has no documented importer, expose the local HTML catalog or index the JSON as project context; do not invent an API, plugin menu, import button or model parameter.

| Library field | Host meaning |
| --- | --- |
| `id`, `effect_name` | Stable key and display label |
| `category`, `anatomy`, `stage`, `species` | Retrieval and filter fields |
| `minimax_segment` | Fragment for the current shot description |
| `gemini_quick_fix` | Local edit/analysis instruction, adapted to the selected Gemini model capability |
| `match_instruction` | Region, anatomy and reference-transfer constraints |
| `sound_segment` | Optional sound instruction; do not add automatically |
| `audio_sample_ids` | Empty compatibility field in this edition; no standalone demo recordings |
| `source_audio_ids` | Mixed movie soundtrack excerpts; separate from original demos and never automatically enabled |
| `thumbnail`, `thumbnail_kind`, `thumbnail_note` | Effect preview and its provenance |
| `reference_ids` | Linked source metadata, not attached media |
| `reference_clip_ids`, `reference_matches` | Packaged excerpts and the exact scope they can support |
| `tested_in_model` | False for this release; preserve that status until a real test is logged |

If building a retrieval index, embed the effect name, region, stage, species, mechanics and match instruction. Keep local media paths and source provenance as metadata. A text embedding does not transfer video motion.

## Select a quick fix

Identify the existing target shot, the subject's left/right side, selected body region, current state and requested visible endpoint. Infer these from the supplied shot when clear. Ask for missing information only when it changes the actual edit. Preserve accepted project constraints and current subject IDs.

Retrieve one primary mechanic, one relevant animal preset if necessary, and at most the repairs or sound cues needed by this shot. Do not send all 100 effects to the renderer. Prefer a reference with the same body part, visible orientation, motion direction, contact and stage; cinematic similarity alone is weaker evidence.

Inspect the selected excerpt before applying it. `continuous closeup`, `rapid transformation glimpse`, `formed anatomy endpoint`, `trailer transformation glimpse` and `human performance only` are not interchangeable. A static paw endpoint cannot demonstrate finger migration. A formed wing cannot demonstrate feather growth. Preserve the documented evidence limits in your response.

For cheetah, bear, panther, horse, bovine, bird and cat targets, enforce `species.json` even if a generic wolf reference differs. Bear hindfeet stay plantigrade; equine endpoints use one principal hoof; bovine endpoints use two main digits; feline forepaw dewclaws are medial and come from the thumb side in this fictional mapping. The digit-reduction paths are creative effects instructions, not real biological processes. Generated pictures are appearance aids, not authoritative anatomical diagrams.

## MiniMax H3 insertion

Use the current project's documented H3 workflow. This package intentionally contains segment fragments rather than complete six-section prompts.

1. Remap `<Subject 1>` to the existing target subject. Never overwrite a different meaning already assigned to that label.
2. Upload/attach the actual source shot and selected `reference_clips/CLIPxxx.mp4` through the host's supported media-input mechanism. Merely mentioning a path or URL does not attach footage.
3. If a reference effect needs a label, use an unused `<Subject N>` to identify only the chosen deformation from the attached reference. A source video's structural role may use `<Video N>` when appropriate. Keep label meanings consistent in the host prompt.
4. Append the selected fragment to the existing shot's `detailed_description`, or its equivalent shot-description field. Do not add a fresh `[Shot 1]`, new camera move, new setting or a full replacement prompt unless explicitly requested.
5. Describe the start, one continuous physical change and the end/hold. Include the body region and the chosen topology. Specify that the reference contributes deformation, timing and surface behavior while the target supplies identity, performance, clothing, set and photography.
6. Add an `attribute_transfer` retention line only when the host uses that full-reference format and the reference subject exists. See `prompts/h3_reference_insertion.md` for optional lines. Do not paste placeholder definitions for assets that are absent.
7. Treat audio independently. Introduce `<Audio N>` only for an actual enabled/attached signal. Preserve existing dialogue unless the user requests otherwise. If retaining source audio and adding new effects, use `partially_copy`, not `fully_copy`, for the source audio relationship. Leave score decisions in the host's existing music field.

## Sound sample import and selection

The app's **Sound samples** section previews/downloads 17 actual movie soundtrack WAVs. `audio_samples/source/CLIPxxx.wav` are decoded mixed soundtrack excerpts (`SRCxxx` IDs), not isolated effects stems. Preserve each record's `kind`, `origin`, `usage_note`, timing and source IDs. A source soundtrack reference cannot prove which isolated sound belongs to the visible morph. FX073–FX088 remain written sound recipes without separate recordings.

When updating an older import, remove the previous standalone synthesized demo assets and their mappings, including cached copies. Preserve user-owned recordings. The user's sample preference is actual recorded foley/source material: do not fill missing recordings with oscillator/noise synthesis or keyboard-like approximations.

For a host UI, display the sample type beside a native audio player, provide a download link, use `preload="none"`, and keep playback manual. Keep only one audio/video playing at a time. Resolve the audio path from the package root. Do not use thumbnail graphics as measured waveforms.

For a shot, choose at most the relevant textures, inspect them, then trim/align them to visible contact or growth events. Do not automatically apply a source mix, dialogue or music. Only attach the selected WAV if the host accepts audio references; otherwise use its written sound recipe or a separate sound-editing workflow. Do not invent an audio-generation feature for a model that only analyzes input audio. Keep an existing reference's `<Audio N>` label unchanged and define a new one only after a real attachment is present.

`--with-sound` opts visual entries into the written sound instruction only. `--source-audio` explicitly adds the selected clip's soundtrack and requires `--reference-clip` plus `--bundle`. `audio_media` contains paths/metadata only, with `upload_status` still not uploaded. Import these as assets awaiting attachment, not as active model inputs. Sound recipe bundles have an empty `audio_media` array unless an actual documented source recording is explicitly selected.

Do not claim that the library's JSON schema is an official H3 schema. Do not invent negative-prompt fields, supported resolutions, duration caps, reference limits or GPU settings. The short-excerpt strategy reduces irrelevant reference material; this package does not establish a speed or VRAM benchmark.

## Gemini use

For analysis or prompt rewriting, supply the source shot, the chosen excerpt and the selected effect record. Ask Gemini to identify the local deformation and return only a revised segment. Use the `gemini_quick_fix` text as the edit intent, while keeping the existing prompt structure and global labels.

For a still-image correction, use a Gemini model/interface that actually supports image editing and supply the source frame. A video-understanding input does not by itself enable edited-video output. If the selected interface supports video generation/editing, use its documented attachment and output workflow. The library does not assume one specific Gemini version or endpoint.

Use this selection wrapper when helpful:

"Apply effect [FX ID] only to [subject side and region] in the supplied shot, toward the [species] endpoint at [visible intermediate state]. Use the supplied excerpt only for [documented transfer scope]. Keep the shot's identity, composition, lighting, set and motion outside that region. Return only the revised current segment; add no new shots."

## Offline helper

```bash
python3 import_library.py --validate
python3 import_library.py --list claw
python3 import_library.py --id FX009 --species panther --side left --stage intermediate
python3 import_library.py --id FX013 --reference-clip CLIP009 --subject '<Subject 3>' --reference-subject '<Subject 7>' --bundle
python3 import_library.py --id FX075 --bundle
python3 import_library.py --id FX005 --with-sound --bundle
python3 import_library.py --id FX010 --reference-clip CLIP008 --with-sound --source-audio --bundle
```

The helper exports text or a local selection bundle. It never uploads media or calls Gemini/MiniMax. Attach the paths in the bundle yourself through the supported host workflow. If an effect has no local excerpt, keep that field empty and offer its source links or generated appearance example; never mark missing media as attached.

## Verification after an actual render

Check the selected region and side, stage, joint connections, digit topology, texture attachment, temporal continuity, reference contamination, target identity and optional sound synchronization. Log the chosen IDs, real input asset identities, model/settings used, result and any observed failure. Only then change a copied project record's `tested_in_model` status; keep the distributed source package unchanged.

## Boundaries

Source footage and source frames remain third-party copyrighted media. Preserve provenance and do not claim that this package supplies commercial reuse rights. Do not scrape paid streams, bypass sign-in or DRM, or silently add footage from outside the package. Linked-only sources, notably Underworld in this release, remain links until actual media is provided through an authorized workflow.
