"""IndexTTS 2.5 controls, independent of the model runtime and Qt."""
import math

EMOTIONS = ('happy', 'angry', 'sad', 'afraid', 'disgusted', 'melancholic', 'surprised', 'calm')


def inference_options(request):
    speed = float(request.get('speed', 1.0))
    strength = float(request.get('emotion_strength', .75))
    emotion = request.get('emotion') or ''
    if not math.isfinite(speed) or not .5 <= speed <= 2:
        raise ValueError('Dialogue speed must be between 0.5× and 2×.')
    if not math.isfinite(strength) or not 0 <= strength <= 1:
        raise ValueError('Emotion strength must be between 0 and 1.')
    if emotion and emotion not in EMOTIONS:
        raise ValueError('Choose a supported dialogue emotion.')
    # IndexTTS controls duration, so faster speech uses a smaller factor.
    options = dict(duration_factor=1.0 / speed)
    if emotion:
        options.update(emo_vector=[.8 if value == emotion else 0.0 for value in EMOTIONS], emo_alpha=strength)
    return options
