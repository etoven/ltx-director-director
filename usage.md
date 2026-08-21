# Usage

## 1. Add and arrange media

Select **Add Media** and choose up to 16 PNG, JPEG, WebP, GIF, or WebM files. On KDE Plasma the application invokes `kdialog` directly, ensuring the standard KDE picker and its preview support are used. GNOME uses `zenity`; Windows and macOS use their system dialogs. The last folder is remembered. Drag timeline cards horizontally to reorder them.

For WebM input, the application captures the first frame of the clip's final second. This becomes the visible timeline thumbnail and the only frame sent to AI. The complete WebM remains attached to the segment for project and LTX Director exports.

Each WebM initially occupies one second. Image segments initially occupy five seconds when timeline space permits. The total sequence limit is 60 seconds.

## 2. Edit a segment

- Select a timeline card to edit its prompt.
- Change duration with the card control in 0.5-second increments.
- Drag the highlighted handle on the card's right edge to resize it directly. The cursor changes to a horizontal-resize indicator over the handle.
- The rest of the tile uses an open-hand cursor and can be dragged to move the segment.
- Right-click a card to replace its media, mark it as a start/end frame, or delete it.
- Replacing media preserves the segment prompt, timing, role, and position.

## 3. Configure Magic Build

Open **AI Settings**, select Gemini or OpenAI, choose the Gemini model when applicable, and enter the corresponding API key.

Enable persistent storage only on a trusted computer. Keys are stored with Qt's local application settings and are never written to an exported file.

Magic Build options:

- **SFX** adds synchronized Foley, impact, material, ambience, and transformation sound directions.
- **Vocals** adds supported breathing, exertion, cries, or dialogue direction without inventing dialogue wording.

Enter optional Director's Intent, then select **Magic Build**. The result contains exactly one prompt for every timeline segment and one global prompt.

## 4. Project library

Select **Projects** in the toolbar to open the left project-library panel. The panel provides a searchable gallery with a large square thumbnail and short description for every saved project.

- **Save Current** adds the working timeline to the library. The first save asks for a project name and description; later saves update it directly.
- **Open** or double-clicking a project restores its complete embedded workspace.
- **Delete** permanently removes the selected project from the local library after confirmation.
- Search matches words in both project names and descriptions.

Library projects are stored in the operating system's per-user application-data directory. They use the same self-contained media format as Project Export, while lightweight metadata keeps the visual gallery responsive.

## 5. LTX Director files

**LTX Director Export** creates a timeline JSON containing timing in 24 FPS frames, prompts, roles, thumbnails, global prompt, and video metadata. Complete WebM content is embedded in `videoB64` so the portable file retains the source video.

**LTX Director Import** restores supported image and WebM segments. Audio, motion, LoRA, and retake tracks are intentionally ignored.

All LTX Director and native project import/export actions use the same operating-system picker integration as Add Media: KDialog on KDE Plasma, Zenity on GNOME, and native dialogs on Windows and macOS.

## 6. Native project files

**Project Export** creates `*.ltxproject.json`, preserving the complete editable workspace:

- Segment order, roles, prompts, and durations
- Image and WebM media
- WebM preview and trim metadata
- Director's Intent
- Global prompt
- SFX and Vocals settings

**Project Import** restores that workspace. API keys are deliberately excluded.

## Troubleshooting

- If a WebM cannot be decoded, verify that the file itself plays normally and update `imageio-ffmpeg`.
- If an AI request fails, verify the selected provider, model, API key, account quota, and network access.
- If a project file is very large, this is expected when full WebM files are embedded.
