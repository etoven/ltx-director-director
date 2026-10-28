# Aurora 1.13.0a130 — prompt selection and ComfyUI output names

Timeline clicks now retain the user's chosen prompt scope. Selecting another
card or clicking the selected card again refreshes segment data and timecode
navigation without selecting a different prompt box. Selecting Segment in the
existing prompt selector still exposes the selected card's wording. Render
groups continue to use their existing shared controls.

The ComfyUI export captures the current project name with its other background
job inputs and sets `VHS_VideoCombine.widgets_values.filename_prefix` to that
name. It uses the same filename-safe project naming convention as the existing
export path, preserving spaces. Renaming the exported JSON does not rename the
rendered video prefix. Other graph settings and media handling are unchanged.

Verification before publication:

- Native repeated-click regression passed in Global, Unified and Segment scopes,
  with prompt documents and segment data preserved.
- All 11 existing ComfyUI graph/media export checks passed, including the
  actual project-name prefix in a workflow exported under a different JSON name.
- The native toolbar-to-background-export regression also passed independently.
- Historical segment-editing test helpers now choose Segment explicitly.
- Changed runtime modules passed Python compilation.
- No provider inference or GPU render was used.

Source commit: `4e131b3e6e3098c4ebeed775e9091b07bfed1197`.
Release CI: <https://github.com/etoven/ltx-director-director/actions/runs/40728480580>.

CI passed 243 core tests and 102 isolated Qt editor regressions and published
wheel commit `cab58a4`. The downloaded public wheel matches the committed artifact
byte for byte.

Public wheel SHA256:
`0c046d8df4677f69038373be1ca41e11fac062e83c9d47e1442e9da59b3c4a19`.

Clean-install verification runs outside the checkout with isolated XDG paths and
asserts the package import comes from the installed public wheel. All 476
packaged source/assets matched the checkout, and all 14 graph, prompt-selection,
toolbar-export and shared-group checks passed.

Evidence on the development host:
`/tmp/director-a130-public-scope-export.log` and
`/tmp/director-a130-ci-success.log`.
