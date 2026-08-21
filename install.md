# Installation

## Requirements

- Python 3.10 or newer
- A desktop environment supported by Qt 6
- Internet access only when using Magic Build
- A Gemini or OpenAI API key for AI generation

WebM decoding is supplied by `imageio-ffmpeg`; a separate system FFmpeg installation is normally unnecessary.

`QtWidgets` is supplied by the `PySide6-Essentials` wheel, which is installed directly by this project.

## Linux

```bash
git clone https://github.com/YOUR-ACCOUNT/ltx-prompt-director.git
cd ltx-prompt-director
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m ltx_prompt_director
```

If Qt reports a missing XCB library on Ubuntu/Debian:

```bash
sudo apt install libxcb-cursor0 libxkbcommon-x11-0
```

If Qt reports `libEGL.so.1` is missing:

```bash
sudo apt install libegl1
```

## Windows

```powershell
git clone https://github.com/YOUR-ACCOUNT/ltx-prompt-director.git
cd ltx-prompt-director
py -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m ltx_prompt_director
```

## macOS

```bash
git clone https://github.com/YOUR-ACCOUNT/ltx-prompt-director.git
cd ltx-prompt-director
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m ltx_prompt_director
```

## Install as a command

```bash
python -m pip install .
ltx-prompt-director
```

On Linux, the first launch automatically installs:

- `~/.local/share/applications/ltx-prompt-director.desktop`
- `~/.local/share/icons/hicolor/256x256/apps/ltx-prompt-director.png`

No root access is required. To install or refresh it manually:

```bash
ltx-prompt-director-install-desktop
```

To remove the launcher and icon:

```bash
ltx-prompt-director-uninstall-desktop
```

## Build distributable Python packages

```bash
python -m pip install build
python -m build
```

The wheel and source archive will be written to `dist/`.

## Repair an incomplete PySide6 installation

If startup reports `No module named 'PySide6.QtWidgets'`, activate the same virtual environment used to run the application and reinstall Qt Essentials:

```bash
python -m pip uninstall -y PySide6 PySide6-Addons PySide6-Essentials shiboken6
python -m pip install --upgrade --force-reinstall -r requirements.txt
python -c "from PySide6.QtWidgets import QApplication; print('QtWidgets OK')"
```

Also make sure the repository does not contain a local file or directory named `PySide6`, which would shadow the installed package.
