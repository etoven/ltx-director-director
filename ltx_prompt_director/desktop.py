from __future__ import annotations

import shutil
import subprocess
import sys
from importlib.resources import files
from pathlib import Path


DESKTOP_NAME = "ltx-director-director.desktop"
ICON_NAME = "ltx-director-director.png"
LEGACY_DESKTOP_NAME = "ltx-prompt-director.desktop"
LEGACY_ICON_NAME = "ltx-prompt-director.png"


def desktop_path() -> Path:
    return Path.home() / ".local" / "share" / "applications" / DESKTOP_NAME


def icon_path() -> Path:
    return Path.home() / ".local" / "share" / "icons" / "hicolor" / "256x256" / "apps" / ICON_NAME


def legacy_paths() -> tuple[Path, Path]:
    return (
        Path.home() / ".local" / "share" / "applications" / LEGACY_DESKTOP_NAME,
        Path.home() / ".local" / "share" / "icons" / "hicolor" / "256x256" / "apps" / LEGACY_ICON_NAME,
    )


def desktop_contents() -> str:
    python = str(Path(sys.executable).resolve()).replace('"', '\\"')
    return f"""[Desktop Entry]
Type=Application
Version=1.0
Name=LTX Director - Director
Comment=Build LTX Video 2.3 timeline and global prompts
Exec=\"{python}\" -m ltx_prompt_director
Icon=ltx-director-director
Terminal=false
Categories=AudioVideo;Video;Graphics;
Keywords=LTX;video;prompt;timeline;Gemini;OpenAI;
StartupNotify=true
StartupWMClass=LTX Director - Director
"""


def install_desktop_entry(force: bool = False) -> Path | None:
    if not sys.platform.startswith("linux"):
        return None
    target = desktop_path()
    contents = desktop_contents()
    for legacy in legacy_paths():
        if legacy.exists():
            legacy.unlink()
    if force or not target.exists() or not icon_path().exists() or target.read_text(encoding="utf-8", errors="ignore") != contents:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")
        target.chmod(0o755)
        icon = icon_path()
        icon.parent.mkdir(parents=True, exist_ok=True)
        icon.write_bytes(files("ltx_prompt_director").joinpath("assets/icon.png").read_bytes())
        updater = shutil.which("update-desktop-database")
        if updater:
            subprocess.run([updater, str(target.parent)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    return target


def uninstall_desktop_entry() -> bool:
    target = desktop_path()
    icon = icon_path()
    if icon.exists():
        icon.unlink()
    removed = False
    if target.exists():
        target.unlink()
        removed = True
    for legacy in legacy_paths():
        if legacy.exists():
            legacy.unlink()
            removed = True
    return removed


def install_main() -> int:
    target = install_desktop_entry(force=True)
    if target:
        print(f"Installed desktop entry: {target}")
        return 0
    print("Desktop entries are supported only on Linux.", file=sys.stderr)
    return 1


def uninstall_main() -> int:
    if uninstall_desktop_entry():
        print(f"Removed desktop entry: {desktop_path()}")
    else:
        print("No LTX Director - Director desktop entry was installed.")
    return 0
