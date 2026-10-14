import sys


def migrate_settings() -> None:
    from .config_migration import migrate_settings as migrate
    migrate()


def main() -> int:
    try:
        from PySide6.QtWidgets import QApplication
        from .ui import MainWindow
        from .splash import StartupSplash
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
        except (OSError,ValueError) as error:
            print(f"Desktop entry installation skipped: {error}", file=sys.stderr)
    app = QApplication(sys.argv)
    app.setApplicationName("Prompt Director")
    app.setApplicationDisplayName("Prompt Director")
    app.setDesktopFileName("ltx-director-director")
    app.setOrganizationName("PromptDirector")
    splash = StartupSplash()
    app._director_splash = splash
    splash.show()
    app.processEvents()
    try:
        from .config_migration import migrate_configuration
        def migration_progress(message):
            splash.set_status(message);app.processEvents()
        migrate_configuration(migration_progress)
        from .storage_management import apply_pending_setup
        splash.set_status("Checking pending settings restores…")
        app.processEvents()
        apply_pending_setup()
    except (OSError,ValueError) as error:
        from PySide6.QtWidgets import QMessageBox
        splash.hide()
        QMessageBox.critical(None,'Configuration migration failed',
                             f'Your previous files are unchanged. Migration can be retried on the next launch.\n\n{error}')
        return 2
    splash.set_status('Loading workspace and project tools…')
    app.processEvents()
    window = MainWindow()
    splash.set_status('Restoring editor layout…')
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
