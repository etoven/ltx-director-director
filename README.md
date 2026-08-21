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

## Quick start

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

See [install.md](install.md) for platform setup and [usage.md](usage.md) for the complete workflow.

## Privacy

Media processing happens locally. Magic Build sends compressed 384-pixel reference frames and prompt instructions directly to the selected AI provider. Full WebM files are never sent to the AI. API keys are never included in project or LTX exports.

## License

MIT
