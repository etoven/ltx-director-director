# Aurora 1.13.0a134 dialogue controls

Follow-up after publishing a133: IndexTTS 2.5 now exposes its eight native emotion presets, emotion strength and 0.5x to 2x speaking speed. Match voice sample preserves the default emotion path. Speed is converted to the inverse native duration factor, so a higher speed value speaks faster. Changing either control invalidates pending output in the same way as editing the script.

The controls reach local inference through a validated data-only adapter. Eight focused checks cover UI input, defaults, invalid ranges and the real adapter call using a small WAV-producing inference stub; they do not load model weights or claim a new neural sampling run. The official API and emotion-vector order were checked against https://github.com/index-tts/index-tts/blob/main/indextts/infer_v2_5.py .

Also updates the rail restoration check for the actual timeline dock and corrects the Generic Prompt Refine template-page explanation. The a133 audit and native installed-wheel check remain in docs/development/aurora-a133.md.

The release audit also exposed native saved layouts reopening a Unified dock in LTX. Workspace availability is now reapplied after layout restoration, while preserving user-closed supported panels. Narrow sequence-option rows retain their preferred control widths and scroll rather than compressing their contents.

Verification: the full native feature-plan smoke passed again after these changes. The clean-installed a134 wheel passed all eight dialogue-control checks, and every packaged source/asset matched the intended checkout. Candidate wheel SHA-256: `df33ac585ab7051ebb3eedf4ad99ad1bc2182c783d9e7f42b3d752233a0abeb3`. The original a133 wheel was published first in commit `9c2f5a6` and its public bytes matched the clean-installed candidate.
