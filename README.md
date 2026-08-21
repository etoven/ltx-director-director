# LTX Director - Director

A native Python desktop timeline and AI prompt builder for LTX Video 2.3. Arrange image and WebM reference segments, assign start/end-frame roles, refine timing, and generate one prompt per segment plus a global continuity prompt with Gemini or OpenAI.

![LTX Director - Director application overview](docs/images/ltx-director-director-overview.png)

## What it does

LTX Director - Director turns a folder of reference frames into a structured LTX Video 2.3 sequence:

1. Add images or WebM clips and arrange them directly on the visual timeline.
2. Mark each segment as a start frame or end frame, then drag its edge to set the duration.
3. Describe the overall scene in **Director's Intent** and optionally enable SFX or vocals.
4. Run **Magic Build** to refine timing and generate a focused prompt for every segment.
5. Review the shared global continuity prompt, then export to LTX Director or save the complete editable project.

![Timeline, frame roles, duration controls, and Magic Build](docs/images/timeline-and-magic-build.png)

*Duration-scaled segments make the full sequence readable at a glance. Frames can be reordered, resized, replaced, assigned a role, or deleted without leaving the timeline.*

![Generated segment and global prompts](docs/images/generated-prompts.png)

*Magic Build creates the selected segment's motion prompt and a global prompt that keeps subject identity, setting, lighting, camera, and style consistent across the sequence.*

## Highlights

- Native PySide6 interface for Linux, Windows, and macOS
- Web-app-matched dark editor layout with compact toolbar and stacked prompt panels
- Numbered timeline ruler with duration-proportional segment widths
- Drag-to-reorder horizontal timeline
- One-second minimum segment duration in 0.5-second increments
- Right-click replace, role assignment, and deletion
- Direct KDE `kdialog`, GNOME `zenity`, macOS, and Windows native media-picker integration
- Branded application icon and automatic per-user Linux desktop-menu installation
- WebM support with a preview captured at the start of the final second
- Only one optimized frame per WebM is sent to vision AI
- Gemini and OpenAI support with local key storage
- Independent SFX and Vocals Magic Build controls
- LTX Director-compatible JSON import/export
- Complete portable project import/export, including embedded media
- No server, database, account, or telemetry

## Quick start from source

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m ltx_prompt_director
```

Windows activation:

```powershell
.venv\Scripts\activate
```

## Install from a wheel

### One-line Linux install or update

This automatically finds the wheel attached to the latest GitHub release, installs or upgrades it, and creates the Linux desktop shortcut:

```bash
python3 -m pip install --upgrade "$(wget -qO- https://api.github.com/repos/etoven/ltx-director-director/releases/latest | sed -n 's/.*"browser_download_url": "\(.*\.whl\)".*/\1/p' | head -n 1)" && ltx-director-director-install-desktop
```

Or download the `.whl` file from the latest GitHub release, then install it with:

```bash
python3 -m pip install ./ltx_prompt_director-1.4.0-py3-none-any.whl
```

You can also install a locally built wheel from the repository:

```bash
python3 -m pip install ./dist/ltx_prompt_director-*.whl
```

Launch the installed application with:

```bash
ltx-director-director
```

Using a virtual environment is recommended if you do not want to install the package into your user Python environment.

## Linux desktop shortcut

After installing the wheel or package, add LTX Director - Director to your desktop environment's application menu:

```bash
ltx-director-director-install-desktop
```

The shortcut is installed for the current user. You may need to reopen the application launcher before it appears. To remove it later:

```bash
ltx-director-director-uninstall-desktop
```

The app also attempts to install the per-user shortcut automatically on its first Linux launch.

See [install.md](install.md) for platform setup and [usage.md](usage.md) for the complete workflow.

## Privacy

Media processing happens locally. Magic Build sends compressed 384-pixel reference frames and prompt instructions directly to the selected AI provider. Full WebM files are never sent to the AI. API keys are never included in project or LTX exports.

## License

MIT
