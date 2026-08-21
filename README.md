# LTX Prompt Director

A native Python desktop timeline and AI prompt builder for LTX Video 2.3. Arrange image and WebM reference segments, assign start/end-frame roles, refine timing, and generate one prompt per segment plus a global continuity prompt with Gemini or OpenAI.

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

Download the `.whl` file from the latest GitHub release, then install it with:

```bash
python3 -m pip install ./ltx_prompt_director-1.3.1-py3-none-any.whl
```

You can also install a locally built wheel from the repository:

```bash
python3 -m pip install ./dist/ltx_prompt_director-*.whl
```

Launch the installed application with:

```bash
ltx-prompt-director
```

Using a virtual environment is recommended if you do not want to install the package into your user Python environment.

## Linux desktop shortcut

After installing the wheel or package, add LTX Prompt Director to your desktop environment's application menu:

```bash
ltx-prompt-director-install-desktop
```

The shortcut is installed for the current user. You may need to reopen the application launcher before it appears. To remove it later:

```bash
ltx-prompt-director-uninstall-desktop
```

The app also attempts to install the per-user shortcut automatically on its first Linux launch.

See [install.md](install.md) for platform setup and [usage.md](usage.md) for the complete workflow.

## Privacy

Media processing happens locally. Magic Build sends compressed 384-pixel reference frames and prompt instructions directly to the selected AI provider. Full WebM files are never sent to the AI. API keys are never included in project or LTX exports.

## License

MIT
