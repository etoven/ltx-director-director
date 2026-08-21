import sys


def migrate_settings() -> None:
    """Preserve saved keys and preferences across the application rename."""
    from PySide6.QtCore import QSettings

    current = QSettings()
    if current.allKeys():
        return
    legacy = QSettings("LTXPromptDirector", "LTX Prompt Director")
    for key in legacy.allKeys():
        current.setValue(key, legacy.value(key))
    current.sync()


def main() -> int:
    try:
        from PySide6.QtWidgets import QApplication
        from .ui import MainWindow
    except ModuleNotFoundError as error:
        if error.name and error.name.startswith("PySide6"):
            print(
                "Qt for Python is incomplete in this environment.\n"
                "Activate the project's virtual environment, then run:\n\n"
                "  python -m pip install --upgrade --force-reinstall PySide6-Essentials\n",
                file=sys.stderr,
            )
            return 2
        raise
    if sys.platform.startswith("linux"):
        try:
            from .desktop import install_desktop_entry
            install_desktop_entry()
        except OSError as error:
            print(f"Desktop entry installation skipped: {error}", file=sys.stderr)
    app = QApplication(sys.argv)
    app.setApplicationName("LTX Director - Director")
    app.setOrganizationName("LTXDirectorDirector")
    migrate_settings()
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
