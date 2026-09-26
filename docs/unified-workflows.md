# Unified project workflows — 1.13.0a1

Experimental branch: `experimental/unified-project-workflows`. Stable `main` remains
on 1.12.57. This experiment brings all MiniMax controls into the main timeline window.

## One editor

| Project type | Shared editor | Generate | Refine |
| --- | --- | --- | --- |
| LTX Video | Selected segment; changes when a timeline segment is clicked | All segment prompts and the global prompt | Selected segment |
| MiniMax · Frames | Full continuous production prompt | Frame-timeline generation | Full edited prompt plus private notes |
| MiniMax · References | Full production brief | Locally detected T2V/I2V/FL2V/keyframes/V2V/R2V generation | Full edited brief, references, and private notes |

LTX's Global prompt is available from the selector beside the editor heading. It uses the
same text box. Switching to Global does not overwrite any segment prompt. MiniMax keeps
its production prompt visible when selecting, retiming, or reordering timeline items.
The LTX segment prompts remain in project storage and continue to provide timeline context
to MiniMax generation.

Generate and Refine are manual actions. Changing project type or dropping a reference
never calls an AI provider. Frame and Reference drafts, including private refinement notes,
are stored independently. The existing LTX prompts and global context survive mode changes.

## Controls

- One Generate action routes to the selected project type. One Refine action targets the
  active segment or the full MiniMax prompt. Copy copies exactly the visible editor text.
- Direction & audio opens the shared Director's Intent and generation options. It starts
  expanded for LTX and collapsed for MiniMax to give the production prompt more room.
- MiniMax hides LTX JSON export, HDR prefixing, requested total length, output dimensions,
  per-segment AI retiming, and Gemini image-prompt copy. MiniMax timing comes from the timeline.
- Start/end roles are available for LTX and MiniMax References visual segments. Frames mode
  continues to use segment-start checkpoints and does not expose unused end-frame controls.
- Language and accent appear when Spoken Dialog is enabled.
- The two untimed reference slots live in a dock inside the main window, available only in
  MiniMax References. Reopen it with Reference images. It cannot become a floating window.
- Refinement notes expand above the same production editor. They are not copied as output.
- The extra MiniMax window, duplicate pacing strip, parallel generation buttons, and the
  always-visible second global prompt box have been removed.

## Project storage and running requests

Project format 8 records `projectType` and both MiniMax drafts in `minimaxH3.drafts`.
The active draft is also written to the existing `minimaxH3.prompt` fields. Existing project
files remain readable: an older saved MiniMax prompt selects its recorded MiniMax mode;
a project without one opens in LTX mode. Switching between open project sessions restores
each project's type and drafts. LTX imports start with fresh MiniMax state.

Save Project exports the portable `.LTXD` file.
Manual MiniMax edits also save when the main application closes for an existing library
project. A new unsaved project still needs Save Project.

AI results and errors from a previous project or workflow are discarded. Reference or
prompt edits during a MiniMax request also reject an outdated response. The editor stays
editable during MiniMax generation. No live provider request was made for automated testing.

## Install this experimental wheel

```bash
python3 -m pip install --upgrade 'https://raw.githubusercontent.com/etoven/ltx-director-director/experimental/unified-project-workflows/dist/ltx_prompt_director-1.13.0a1-py3-none-any.whl'
```

To return to the stable build, install its wheel explicitly:

```bash
python3 -m pip install --force-reinstall --no-deps 'https://raw.githubusercontent.com/etoven/ltx-director-director/main/dist/ltx_prompt_director-1.12.57-py3-none-any.whl'
```
