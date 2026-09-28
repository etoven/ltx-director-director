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

### MiniMax Frames brief (1.13.0a3)

Frames generation now uses the same production-brief layout as References, with `[FRAME USE]`, `[CONTINUITY]`, `[SCENE]`, `[TIMED ACTION]`, `[SOUND]` and `[AVOID]` as applicable. `[TIMED ACTION]` uses the exact timeline intervals in `00:00:000 - 00:03:000: ...` form. A start image anchors the beginning of its interval and an end image must be reached at its interval end. The images are timed conditioning checkpoints, while untimed images in References mode are guides for selected attributes. Generation and refinement return the authored brief directly without inserting extra bridge slots or changing user-edited wording.

### MiniMax Frames references and line layout (1.13.0a4)

Both MiniMax modes now expose the same two untimed reference-image drop targets. In Frames mode, those images keep their selected attribute roles and notes; they never become timeline checkpoints or add duration. The Frames prompt asks for a blank line between bracketed sections and one complete `MM:SS:mmm - MM:SS:mmm:` range per line inside `[TIMED ACTION]`, following the example production brief. Manual refinement receives the same two reference images while respecting edits already made in the shared prompt editor.

### Responsive projects and inline spelling (1.13.0a5)

Media imports, portable project saves and loads, metadata ZIP updates, and LTX JSON exports now run in Qt background workers. AI requests already run in separate workers. New project selections invalidate unfinished older loads, and closing waits for pending disk work. The existing prompt, intent, refinement, and description text boxes mark misspellings inline and offer right-click suggestions using the installed Enchant system dictionary. On Linux install an Enchant provider and a dictionary for your locale if one is not already available; without a matching dictionary the text boxes remain editable without spell marks.

MiniMax Frames and References prompts now request 24 fps, non-drop-frame SMPTE `HH:MM:SS:FF` timecode, with `FF` as a frame number from 00 to 23. Each timed range occupies one line and sections are separated by a blank line. Frame boundaries from the app's second-based timeline are rounded to the nearest frame, which can differ by up to half a frame from a millisecond timestamp.

### Linked MiniMax cues and automatic projects (1.13.0a6)

MiniMax Frames and References request ordered `timed_actions` alongside the remaining brief in JSON. The client assembles SMPTE cue boundaries from timeline segment lengths. Compact per-segment action controls edit the same `[TIMED ACTION]` section as the shared production editor; retiming and rearranging update cue boundaries locally and show a yellow refine prompt action for continuity review. Ctrl+drop media onto an existing timeline tile replaces that segment while retaining its duration, prompt, role and ID. Projects save in the background after edits and when switching or closing; the tile action row and unsaved badges have been removed. Project operations remain in the tile context menu. Spelling suggestions appear directly at the top of the editor context menu.

### Save queue correction (1.13.0a7)

Edits made during a pending archive write enqueue a fresh snapshot when the project changes, so a fast switch or close still persists the latest version.

### Inline timed actions (1.13.0a8)

Removed the duplicate MiniMax timed-action controls and their scroll area. Timed cues live only in the shared production QTextEdit, wrap at word boundaries, and receive a subtle line tint for orientation. Editing a cue updates the linked timeline prompt; timeline edits retime the cues while retaining the prose. Retiming preserves the editor cursor and scroll position.

### Native MiniMax cue cells, deliberate saves, and close progress (1.13.0a9)

MiniMax timed actions now live in wrapping, editable Qt table cells at their actual positions in the shared prompt editor. Each cell stores its timeline segment ID in its document format; cue text is also persisted by ID in the project archive. Surrounding prose edits cannot shift these associations. Projects keep edits in memory across switches and write archives only from the top toolbar's **Save to Library** command or when the app closes. **Export Project** still writes a portable copy on request. On close, the main window hides, a modal project-save progress dialog remains visible, and the window is disposed of after background writes finish. The spelling menu's suggestion actions use Qt QAction objects, restoring right-click replacements at the top level.
