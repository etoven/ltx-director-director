# Video extension review build

This local feature work is based on version 1.13.0a124 on `codex/conditioning-audio`. The user approved the final UI after reviewing the fitted mix box. The approved feature was published as experimental 1.13.0a125 after build, installed-wheel and public-artifact verification. The follow-up scrubbing release is 1.13.0a126. Existing changes to the experimental wheel workflow and migration documentation are preserved.

- Timeline groups persist per-segment membership and per-group timing/blend options. Each join has one shared mix box centered on the two connected cards, fitted inside the media preview area with two-pixel vertical margins. Mode and side controls fit inside that same area. Dragging either edge changes length symmetrically; no extra slider or visible numeric length control is used. The former group bar is hidden. The mix box does not add prompt segments or timeline duration.
- Generation snapshots the project, production prompt, conditioning and references. Dependencies stop subsequent work on failure/cancellation. Each generation has its own cancellation event and can be queued again.
- The marked duration is independent of H3's padded frame grid. Assembly trims blocks to marked frame lengths. It streams the existing sequence editor's exact blend math over the outgoing/incoming windows and crossfades corresponding audio. Source-side and destination-side joins replace frames inside the selected block, preserving the sum of marked lengths. Hard cuts concatenate complete blocks.
- Completed video blocks are cached by group inputs, media identities and incoming video content. A later group edit reuses the unchanged prefix; a changed predecessor invalidates dependent blocks only when its rendered content changes. Blend settings do not invalidate block videos. Caches persist in project/session metadata and require existing unchanged output files.
- Preview caches and generation counters are scoped to project/workflow, including unsaved project identities. Progress is retained when another project is selected. Static latents use a QLabel inside a persistent black video surface; video latents and final movies use the existing player.
- Combined Shot Sequence projects embed complete source `.LTXD` archives. Portable load and resave preserve those bundles, including nested combinations, prompts, workflow definitions and reference media. Dirty cached source edits are snapshotted without overwriting the originals. Both grouping and Collection previews preserve source bundles.
- Cleanup requires a completed final file and no active work in the selected group. Only explicitly marked intermediate video paths are deleted after confirmation. Export copies atomically off the UI thread; drag supplies a local file URL.

## Verification

Run `python -m compileall -q ltx_prompt_director`, `python scripts/test_video_extensions.py`, and focused pytest checks for `test_video_extensions.py`, `test_collection_combine.py`, `test_render_job_files.py`, `test_project_archive.py`, `test_project_duplicate.py`, `test_local_conditioning.py`, and `test_render_queue.py`, and `test_render_cache.py` using isolated XDG directories. Native GUI regressions run in separate bounded processes.

`python scripts/capture_extension_review.py` captures the current native UI with authored demonstration data and no provider/model calls. Screenshot videos/jobs are explicitly demonstrations, not generated H3 outputs.

A real GPU H3 continuation render is not part of the CPU/native verification. Frame conditioning reuses the existing Ref2VA VAE encoding and time-zero reference position support; end-to-end model continuity still needs a model-equipped run.

## Requested stage after the first publication

Add timeline scrubbing after this video build has been reviewed and published. Use rendered sequence video with audio when available. Before a sequence is rendered, scrubbing should show the text, image, or video conditioning segment under the scrub position. The phase was implemented after the a125 publication, as recorded below.

## Approved a125 release checks

The fitted one-box mix UI was approved by the user before release preparation. Source checks pass (43 focused tests; six optional tests skipped). Twelve original isolated native regressions passed, followed by the edge-fitting and original-block reuse regressions. The rebuilt installed wheel matches all 472 packaged source/assets and passes 20 extension/media/cache/portable-project regressions, including four isolated UI checks. The existing application smoke check and six installed conditioning-audio regressions also passed.

Candidate wheel SHA-256: `f97839cf3ec70756a26acaccf7b0937df6919adf05f4e9ff57872575550a501a`. Publication and verification of the actual public artifact will be recorded after the experimental workflow finishes.

The initial publication run caught a desktop-picker routing regression in the new job export action. The action now uses the shared native desktop picker; all six focused picker/file-action checks pass, and the rebuilt installed package passes its file-action checks. The approved layout is unchanged.

## a125 publication

Published 1.13.0a125 on `experimental`; source correction `f2a2696`, wheel commit `a8fca8a`. [Release CI](https://github.com/etoven/ltx-director-director/actions/runs/40116678691) passed. The public raw wheel matches the committed wheel byte-for-byte, matches all 472 source/assets, and passes all 20 installed extension checks. Public SHA-256: `a7ed2058ccd8c7d40507a948bbcb21871f67f9cbca52e3c0d35be0d01b7ad9fe`.

## Post-publication scrubbing phase

Implemented after the a125 public artifact was verified. The existing timeline ruler now scrubs the preview. Available final footage or rendered blocks audition native video/audio, preserving paused/playing intent. Without rendered footage at that time, the preview displays the source text, image or trimmed video condition. Decode and archive work runs in the media pool, requests are coalesced, and scope/serial checks discard obsolete project results. No extra timeline or mix-length slider was added. The approved mix layout is unchanged.

The a126 candidate matches all 475 packaged source/assets. Installed baseline smoke and six conditioning-audio regressions pass. The focused installed checks cover native ruler scrubbing, stale project/prompt/cancelled work, continuous drag throttling, constrained long-text previews, and a real rendered MP4 with an audio stream. Cleanup additionally refuses intermediate deletion while any queued project still depends on those files.

## a126 publication

Published 1.13.0a126 on `experimental`; source `66041dd`, wheel commit `0925d36`. [Release CI](https://github.com/etoven/ltx-director-director/actions/runs/40119893834) passed 243 core tests plus 98 isolated Qt editor tests. The downloaded public wheel matches the committed artifact byte-for-byte and all 475 packaged source/assets. All 29 installed extension/scrubbing regressions, the baseline app smoke check, and six installed conditioning-audio regressions pass against that public wheel. Public SHA-256: `2200e171d8804868327f711776f241739685739825d4944b46e95729953a3128`. A new GPU H3 inference run was not performed.


## a127 local review candidate

This corrects the merge model to editable prompt groups using the existing bottom controls, with independent production contexts, workflow types and references. The user approved the Group 2 layout and then explicitly clarified 10 px top/bottom clearance for the whole mix. The working capture measures that inset against visible media bounds. No new group tabs or settings panels are added.

Audio-panel seeking/playback now follows the condition/rendered preview in prompt workflows, and the Shot Sequence audio Play control starts its assembled preview. Audio seeks wait for media loading and do not repeatedly reset the decoder on buffered notifications. Source video analysis uploads and storyboard samples use the actual trim, while local conditioning enforces trim-out and source timestamp origins. Source files are unchanged.

The local 1.13.0a127 wheel matches all 476 packaged source/assets and passes 41 installed feature checks, the baseline app smoke check, and six installed conditioning-audio regressions. Source media/local-runtime checks passed 32 tests with 11 optional ML tests skipped. Real Qt audio buffer and rendered playback checks were included. No new GPU inference was performed.

Candidate SHA-256: `1aa1882fc46f705bd36518f7ecc2c2deddf15997837a162c6a6b744d0752caf6`. At this checkpoint it was a local review build, before publication authorization; it was not installed into the user's running application.


## a127 publication

Published 1.13.0a127 to `experimental` after the user's explicit publication request. Source correction `02441bf`, wheel commit `e172b92`. [Release CI](https://github.com/etoven/ltx-director-director/actions/runs/40204964765) passed 243 core tests and all 98 isolated editor tests.

The initial CI run caught saved references being included for conditioning-only workflows. Video request snapshots now apply each group's engine eligibility as well as its reference settings. The existing exclusion test and a mixed-group request regression pass.

The downloaded public wheel matches the committed artifact byte-for-byte and all 476 packaged source/assets. All 41 installed feature checks pass against the public artifact, including real Qt audio playback, trimmed media, shared-control restoration, exact blend geometry and reuse of the original completed block after merging. Candidate baseline app smoke and all six conditioning-audio regressions also passed. Public SHA-256: `3819b4b6475c9723a4b0592ba38af696f9e683c79f401acae7489b16aeb81050`. No new GPU inference was run.
