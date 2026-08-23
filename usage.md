# Usage

## 1. Add and arrange media

Select **Add Media** and choose up to 16 PNG, JPEG, WebP, GIF, or WebM files, or drag supported files directly onto the timeline. The timeline gains a bright border while it is an active drop target. On KDE Plasma the application invokes `kdialog` directly, ensuring the standard KDE picker and its preview support are used. GNOME uses `zenity`; Windows and macOS use their system dialogs. The last folder is remembered. Drag timeline cards horizontally to reorder them.

For WebM input, the application captures the first frame of the clip's final second. This becomes the visible timeline thumbnail and the only frame sent to AI. The complete WebM remains attached to the segment for project and LTX Director exports.

Each WebM initially occupies one second. Image segments initially occupy five seconds when timeline space permits. The total sequence limit is 60 seconds.

## 2. Edit a segment

- Select a timeline card to edit its prompt.
- Change duration with the card control in 0.5-second increments.
- Drag the highlighted handle on the card's right edge to resize it directly. The cursor changes to a horizontal-resize indicator over the handle.
- The rest of the tile uses an open-hand cursor and can be dragged to move the segment.
- Right-click a card to replace its media, instantly export the complete source media, mark it as a start/end frame, or delete it. **Export image** or **Export video** copies only that segment's underlying media file directly to the folder configured in Application Settings—no prompts, metadata, or dialog. It initially uses the operating system's Downloads folder and creates collision-safe `<project name> - Segment 01.ext` filenames.
- Replacing media preserves the segment prompt, timing, role, and position.

Use the **Scale** slider to zoom the timeline horizontally, or select **Auto fit** to fit the complete sequence into the available width. Drag the dotted grip centered along the timeline's bottom edge downward to enlarge segment previews; previews use a centered cover crop beneath their separate header row, filling the expanded tile without empty padding above or below the image. Timeline height is automatically capped against the current window and desktop work area, preventing the lower edge of the application from being pushed beyond the screen.

Mouse-wheel scrolling across the timeline uses short eased horizontal movement instead of jumping by whole tiles. High-resolution touchpad gestures remain direct and track the gesture pixel-for-pixel.

During genuine timeline rebuilds, an animated loading indicator remains visible until every segment preview and control is ready. Timeline zoom, vertical preview resizing, and duration resizing update the existing cards in place without reloading them or displaying the loader.

## 3. Configure Magic Build

Open **AI Settings**, select Gemini or OpenAI, choose the Gemini model when applicable, and enter the corresponding API key.

Use the **Text scale** slider in the main toolbar for immediate live adjustment, or the matching **UI text scale (DPI)** slider in Application Settings. Both controls resize text throughout the main window from 75% to 200%, stay synchronized, and restore the selected scale on future launches.

The Magic Build overlay, its custom animation, and the timeline loading animation scale with the same DPI setting.

Enable persistent storage only on a trusted computer. Keys are stored with Qt's local application settings and are never written to an exported file.

Magic Build options:

- **SFX** adds synchronized Foley, impact, material, ambience, and transformation sound directions.
- **Spoken Dialog** allows supported spoken-dialog direction without inventing exact dialogue wording.
- **HDR** places `(4K, HDR, Realistic)` at the beginning of the global prompt.
- **Reduce Music** instructs the model to place a setting-specific `[SOUND]: Ambient …` line after the quality header. Supplying environmental ambience helps discourage unwanted generated music.

Enter optional Director's Intent, then select **Magic Build**. The empty field includes a narrative example and reminds you that it can request a total sequence length, such as `Total sequence length: 20 seconds.` A blocking animated overlay appears inside the application, prevents conflicting edits while the operation runs, and reports automatic retry attempts without creating another operating-system window. The default timeout is 400 seconds with two additional retries; both values are configurable in Application Settings. The result contains exactly one prompt for every timeline segment and one global prompt. Recommended durations ease smoothly into their new timeline widths instead of snapping instantly.

## 4. Project library

Select **Projects** in the toolbar to open the left project-library panel. The panel provides a searchable gallery with a square thumbnail and short description for every saved project. Drag the dotted grip on the panel's right edge to change its width. The **Icons** menu at the top selects Small, Medium, or Large cards and remembers that choice; cards wrap from left to right whenever the panel is wide enough for additional columns.

- **Save Current** adds the working timeline to the library. The first save asks for a project name and description; later saves update it directly.
- **Open** or double-clicking a project restores its complete embedded workspace.
- **Edit** changes a project's name, description, or collection.
- **Delete** permanently removes the selected project from the local library after confirmation.
- Search matches words in both project names and descriptions.
- Choose **Title A–Z**, **Title Z–A**, or **Custom** sorting. Dragging a project or collection tile automatically switches to Custom mode. Reordering follows the tile under the cursor in both rows and columns and retains edge auto-scrolling.

Several library projects can remain open as live in-memory workspaces. Clicking another project preserves the current project's latest segments, prompts, output dimensions, options, and timeline view without writing them to disk. A yellow dot in the project's upper-left corner marks unsaved changes. **Save Current** writes only the active workspace and clears its yellow dot; other unsaved workspaces remain intact for the lifetime of the application.

Assigning a collection groups related projects into a folder-like tile. Its cover is a 2×2 grid made from the four most recently saved member thumbnails. Open the collection to see its projects; select **↑ UP** to return to the top-level library. Collected projects appear only inside their collection.

The project library uses smooth per-pixel scrolling. Mouse-wheel movement is eased in short increments instead of jumping an entire project tile, while high-resolution touchpads retain direct pixel scrolling.

Switching projects preserves the library's current scroll position, so selecting a project cannot pull the viewport away from an in-progress drag. The **Save Current** control and project context menus provide distinct hover feedback before activation.

Library projects are stored in the operating system's per-user application-data directory. They use the same self-contained media format as Project Export, while lightweight metadata keeps the visual gallery responsive.

## 5. LTX Director files

Set the desired output width and height in the timeline header. Values are normalized to 32-pixel increments. **LTX Director Export** writes them to `settings.custom_width` and `settings.custom_height` along with timing in 24 FPS frames, prompts, roles, thumbnails, global prompt, and video metadata. Complete WebM content is embedded in `videoB64` so the portable file retains the source video.

**Open** restores supported image and WebM segments from an LTXDirector JSON file. Audio, motion, LoRA, and retake tracks are intentionally ignored.

All LTX Director and native project import/export actions use the same operating-system picker integration as Add Media: KDialog on KDE Plasma, Zenity on GNOME, and native dialogs on Windows and macOS.

## 6. Native project files

**Project Export** creates a `.LTXD` file, preserving the complete editable workspace:

- Segment order, roles, prompts, and durations
- Image and WebM media
- WebM preview and trim metadata
- Director's Intent
- Global prompt
- SFX and Spoken Dialog settings
- HDR and Reduce Music settings
- Output width and height
- Timeline scale and preview-panel height

**Import** restores that workspace. API keys are deliberately excluded. Older `*.ltxproject.json` project files are accepted for backward compatibility.

Export dialogs default to the active project name, producing `<project name>.json` or `<project name>.LTXD` as appropriate.

## Troubleshooting

- If a WebM cannot be decoded, verify that the file itself plays normally and update `imageio-ffmpeg`.
- If an AI request fails, verify the selected provider, model, API key, account quota, and network access.
- If a project file is very large, this is expected when full WebM files are embedded.
