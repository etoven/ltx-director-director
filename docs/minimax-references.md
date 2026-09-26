# MiniMax References — 1.12.57

The References button now uses a compact production brief adapted from the
[PromptSama MiniMax H3 guide](https://www.promptsama.ai/models/minimax-h3.html#overview).
References receive explicit roles, followed by scene direction, timed action and sound.
The previous reference generator, refiner and master instructions are preserved verbatim
in [the inactive 1.12.56 archive](archive/minimax_reference_1_12_56.py.txt).

## Detected workflow

Detection is ordinary client code and requires no model call.

| Inputs | Workflow |
| --- | --- |
| Text timeline only | T2V |
| One timeline image | I2V |
| Two timeline images, start then end roles | FL2V |
| Other multiple-image timelines | Multiple keyframes |
| Timeline video(s), no images | V2V |
| Images and video, or any untimed reference slot | R2V |

V2V describes the available source type. Director's Intent determines whether the source
is being edited, continued or used for performance guidance; media presence alone never
authorizes an identity swap or a scene replacement.

Start/end roles are respected in References mode: a start image belongs to the start of
its segment, an end image to its end. Hover over the detected workflow to see image labels
and checkpoint times. This is distinct from the existing Frames mode cue convention.
Video source ranges respect the timeline trim. Text-only segments supply action and duration.

## Two reference images

Drop a local still image into either slot, use Browse, or paste image pixels or a local file
from the clipboard. Choose Identity, Wardrobe, Setting, Visual style, Object / prop, or
Composition. Notes can specify the subject and exactly which attributes to use.

Each image is embedded at full resolution in the project. Its role and notes survive project
switching and .LTXD export/import. Image analysis uses a copy capped at 1024 pixels on its longest
edge. Clearing a slot removes only that reference. An untimed reference does not create a
timeline segment, move any checkpoint, or extend the duration.

Images are numbered independently from videos: timeline images first, then occupied reference
slots. The slot heading shows its generated ImageN label. The workflow tooltip lists the full
asset mapping. No AudioN reference is fabricated from video audio.

Generation and refinement both receive these images. Editing them marks the project dirty,
invalidates the source cache, and prevents an older running request from overwriting the editor.
Generation remains a manual button action. A timeline item is needed to define duration; use a
text segment for generation driven entirely by the two untimed images.

## Verification

Tests cover classification, reference numbering, start/end timing, drag/drop, original-image
persistence, session isolation, old projects, both provider payloads, reference-aware refinement,
and rejection of a response generated from stale references. Model responses in automated tests
are mocked; output quality still requires a real generation.
