# Conditioning audio UI proof

Feature branch: `codex/conditioning-audio`, based on the published
`1.13.0a123` source at `13d8630`. The user approved the native UI proof and
authorized experimental publication as `1.13.0a124` on October 24, 2026.

## Review the native interface

- [Main workspace](../images/audio-timeline-proof.png)
- [Scrubbing animation](../images/audio-timeline-scrub.gif)
- [LTX workspace](../images/audio-timeline-ltx-proof.png)
- [Prompt-only workspace](../images/audio-timeline-prompt-proof.png)
- [Shot Sequence workspace](../images/audio-timeline-shot-proof.png)

These are captures of the implemented PySide6 interface, using local synthetic
sample sound and demonstration imagery. The animation shows an actual native
ruler drag. Reproduce them with `python scripts/capture_audio_timeline.py` using
local multimedia IPC and isolated settings, as configured by the script.

The shared audio panel sits beneath the sequence timeline and stays available
in every stock workspace. Separate strips retain independent positions, source
trims and lanes, including gaps and overlaps. Import accepts multiple dropped
files. The waveform decoder copies original media and computes bounded peaks
in the media worker; obsolete project imports cannot replace newer audio.
Source duration and timeline edits use 24 fps frame boundaries. The panel starts
collapsed, opens on import, and preserves user resizing and full collapse/restore.

Native archives now use project version 10 and include original audio. LTX JSON
export/import carries audio start, length, source in and lane metadata. Export
copies originals to the ComfyUI input folder; its duration includes independent
audio extending beyond the visual plan. Muted or disabled independent audio is
excluded. Source-video audio export retains its existing behavior.

Independent audio audition uses native streaming players, simultaneous playback
for overlapping strips, silence across gaps, and short scrub samples. Software
transport and UI behavior were checked; subjective listening and device latency
still need an interactive review. Other renderer handoffs, including MiniMax,
and the completed-shot movie bake do not yet consume these independent tracks.

## Verification

`python scripts/test_conditioning_audio.py` passes all six new regressions in
separate Qt processes with temporary XDG directories. This covers source bounds,
waveform decoding, portable originals, LTX timing import/export, overlapping
audition and gaps, source replacement, actual strip/trim/ruler/drop gestures,
all stock workspace views, drag-to-zero collapse, restoration and stale results.
The runner is available for local and future CI verification.

Nine existing focused checks pass: the two timing-overview regressions, existing
audio-scrub intent and shot-timeline navigation, portable and legacy archive
round trips, native shot trim handles, split/trim/export, and automatic movie
preview caching/scrubbing. The cache check first exceeded its existing six-second
wait while checks ran concurrently; both the unchanged baseline and the feature
passed when run individually. Python compilation and `git diff --check` pass.

`test_internal_layout_and_native_prompt_drag_without_outer_scrollbar` fails at
its legacy MiniMax copy-button geometry assertion. It also fails against
unchanged baseline modules in `/tmp/director-audio-baseline`; the hidden legacy
button reports a 480 px height. That unrelated assertion was preserved.

Release verification: the rebuilt offline manual includes the illustrated audio
guide. All 60 local links in the changed guides resolve. The wheel passes ZIP
integrity and version checks, and all 465 packaged source/assets match the
checkout byte-for-byte. A clean installation of `1.13.0a124` passes the existing
application smoke check and all six isolated audio regressions from outside the
source checkout. The existing experimental CI runs the general regressions and
builds/publishes the wheel. The proposed additional source/installed audio CI
steps remain local because the current GitHub sign-in lacks workflow-update
permission. The published wheel is also verified directly after publication.

Subjective listening and independent audio integration into other renderer
handoffs remain outside this release's verified scope.
