"""Qt playback preferences; inference GPU configuration is independent."""
import os

HARDWARE_PLAYBACK_KEY = "playback/hardware_acceleration"


def hardware_playback_enabled(settings=None):
    if settings is None:
        from PySide6.QtCore import QSettings
        # Explicit identity is available before QApplication is constructed.
        settings = QSettings("PromptDirector", "Prompt Director")
    return settings.value(HARDWARE_PLAYBACK_KEY, False, bool)


def configure_playback(environ=None, platform=None, enabled=None):
    environ = os.environ if environ is None else environ
    if enabled is None:
        try:
            enabled = hardware_playback_enabled()
        except ModuleNotFoundError:
            enabled = False  # Startup reports missing Qt dependencies itself.
    if not enabled:
        # A comma is Qt FFmpeg's documented empty hardware-backend list.
        # Set before Qt Multimedia initializes; retain explicit user overrides.
        environ.setdefault("QT_FFMPEG_DECODING_HW_DEVICE_TYPES", ",")
        environ.setdefault("QT_DISABLE_HW_TEXTURES_CONVERSION", "1")
