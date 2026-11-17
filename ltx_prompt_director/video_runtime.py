"""Conservative Qt playback defaults; inference GPU configuration is independent."""
import os
import sys


def configure_playback(environ=None,platform=None):
    environ=os.environ if environ is None else environ
    platform=sys.platform if platform is None else platform
    if platform.startswith('linux'):
        # A comma is Qt FFmpeg's documented empty hardware-backend list.
        # Set before Qt Multimedia initializes; retain explicit user overrides.
        environ.setdefault('QT_FFMPEG_DECODING_HW_DEVICE_TYPES',',')
        environ.setdefault('QT_DISABLE_HW_TEXTURES_CONVERSION','1')
