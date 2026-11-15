"""One integer frame grid for MiniMax conditioning and sequence joins."""
import math

FPS = 24
DEFAULT_FRAMES = 22
DEFAULT_SECONDS = DEFAULT_FRAMES / FPS


def blend_frames(seconds, maximum=None, cut=False):
    if cut:return 0
    requested = float(seconds) * FPS
    ceiling = math.floor(float(maximum) * FPS + 1e-6) if maximum is not None else 1440
    if ceiling < 5:return 0
    top = (ceiling - 5) // 17
    candidates = {max(0, min(top, math.floor((requested - 5) / 17))),
                  max(0, min(top, math.ceil((requested - 5) / 17)))}
    return min((17 * k + 5 for k in candidates), key=lambda n: (round(abs(n - requested), 8), n))


def blend_seconds(seconds, maximum=None, cut=False):
    return blend_frames(seconds, maximum, cut) / FPS
