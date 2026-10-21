"""Shared desktop-native file pickers, with KDE, Zenity and Qt fallbacks."""
import shutil
import subprocess
import sys
from PySide6.QtWidgets import QWidget, QFileDialog


def choose_media_files(parent: QWidget, multiple: bool, initial: str) -> list[str]:
    """Prefer the desktop's actual file picker, then fall back to QFileDialog."""
    media_filter = "Supported Media (*.png *.jpg *.jpeg *.webp *.gif *.webm *.mp4)"
    return choose_files(parent, "Add images or video" if multiple else "Replace media", initial, media_filter, multiple)


def choose_files(parent: QWidget, title: str, initial: str, name_filter: str = "All files (*)", multiple: bool = True) -> list[str]:
    media_filter = name_filter
    if sys.platform.startswith("linux") and shutil.which("kdialog"):
        command = ["kdialog", "--title", title]
        if multiple:
            command.extend(["--multiple", "--separate-output"])
        command.extend(["--getopenfilename", initial or ":ltxPromptDirectorMedia", media_filter])
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode == 0:
            return [line.strip() for line in result.stdout.splitlines() if line.strip()]
        return []
    if sys.platform.startswith("linux") and shutil.which("zenity"):
        command = ["zenity", "--file-selection", f"--title={title}", f"--filename={initial.rstrip('/')}/", f"--file-filter={name_filter}"]
        if multiple:
            command.extend(["--multiple", "--separator=\n"])
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode == 0:
            return [line.strip() for line in result.stdout.splitlines() if line.strip()]
        return []
    if multiple:
        files, _ = QFileDialog.getOpenFileNames(parent, title, initial, media_filter)
        return files
    file, _ = QFileDialog.getOpenFileName(parent, title, initial, media_filter)
    return [file] if file else []


def choose_document_open(parent: QWidget, title: str, initial: str = "", name_filter: str = "All files (*)") -> str:
    if sys.platform.startswith("linux") and shutil.which("kdialog"):
        result = subprocess.run(["kdialog", "--title", title, "--getopenfilename", initial, name_filter], capture_output=True, text=True, check=False)
        return result.stdout.strip() if result.returncode == 0 else ""
    if sys.platform.startswith("linux") and shutil.which("zenity"):
        result = subprocess.run(["zenity", "--file-selection", f"--title={title}", f"--filename={initial.rstrip('/')}/", f"--file-filter={name_filter}"], capture_output=True, text=True, check=False)
        return result.stdout.strip() if result.returncode == 0 else ""
    file, _ = QFileDialog.getOpenFileName(parent, title, initial, name_filter)
    return file


def choose_document_save(parent: QWidget, title: str, suggested: str = "", name_filter: str = "All files (*)") -> str:
    if sys.platform.startswith("linux") and shutil.which("kdialog"):
        result = subprocess.run(["kdialog", "--title", title, "--getsavefilename", suggested, name_filter], capture_output=True, text=True, check=False)
        return result.stdout.strip() if result.returncode == 0 else ""
    if sys.platform.startswith("linux") and shutil.which("zenity"):
        result = subprocess.run(["zenity", "--file-selection", "--save", "--confirm-overwrite", f"--title={title}", f"--filename={suggested}", f"--file-filter={name_filter}"], capture_output=True, text=True, check=False)
        return result.stdout.strip() if result.returncode == 0 else ""
    file, _ = QFileDialog.getSaveFileName(parent, title, suggested, name_filter)
    return file


def choose_directory(parent: QWidget, title: str, initial: str) -> str:
    if sys.platform.startswith("linux") and shutil.which("kdialog"):
        result = subprocess.run(["kdialog", "--title", title, "--getexistingdirectory", initial], capture_output=True, text=True, check=False)
        return result.stdout.strip() if result.returncode == 0 else ""
    if sys.platform.startswith("linux") and shutil.which("zenity"):
        result = subprocess.run(["zenity", "--file-selection", "--directory", f"--title={title}", f"--filename={initial.rstrip('/')}/"], capture_output=True, text=True, check=False)
        return result.stdout.strip() if result.returncode == 0 else ""
    return QFileDialog.getExistingDirectory(parent, title, initial)

