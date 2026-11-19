# Aurora a185: MiniMax terminal conditioning alignment

Frame counts already on the 17k+5 grid remain unchanged instead of gaining another 17-frame block. Outgoing overlap conditioning is placed relative to the actual generated frame count, not the shorter requested seconds. All timed image/video conditions entering the worker are positioned on the model temporal-token grid, bounded by the generated range. A terminal authored image targets the actual last frame. Outgoing AV conditions retain their shared aligned start and snapped overlap length. Worker normalization also covers restored and remote requests; reference-panel media stays untimed and unchanged.

Static source review identified these mismatches. No tests, GPU renders or test installation workflow were run, as explicitly requested. Package file parity is checked for delivery only. The reference-context sidebar feature is paused and excluded.
