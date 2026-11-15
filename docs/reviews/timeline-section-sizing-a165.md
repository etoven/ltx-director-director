# Adjacent Timeline section sizing · Aurora a165

Visual & Prompts, Dialogue, optional Audio and Time/conditioning now share a vertical splitter inside one framed content column. The right action gutter spans that frame, including the unchanged footer controls and bottom cache/scrollbar. Existing segment cards, group brackets, blend controls and conditioning widgets are reused.

The first divider trades height between Visual and Dialogue. With Audio hidden, the next visible divider sits between Dialogue and I2V/conditioning. Native splitter minimums prevent collapse or overlap. The old standalone grips are retained only as hidden compatibility objects, so they cannot appear together at the bottom.

Fixed heights that fought these dividers are released for the section bodies. The Timeline dock can shrink down to the sum of its visible section minima; contents remain top-aligned. Named section heights persist in application settings, and the optional Audio section retains its preference when hidden. Shot Sequence keeps its existing separate audio layout and shared horizontal scrollbar.

A native scenario checks both divider directions, unchanged outer-frame height during internal dragging, correct divider placement, fixed right controls, shrinking the dock, workspace visibility and reopening saved preferences. The live-preview selection regression also remains applicable. Its timing assertion now checks the unchanged playback clock origin instead of assuming the looped playhead cannot wrap around during native UI event processing.

MiniMax render jobs additionally capture their assigned video mode at enqueue time. Waiting and rendering cards, tooltips and selected details display FL2VA or Ref2VA before the model name; non-MiniMax jobs retain their prior label. The per-job value comes from the resolved group profile, not the currently selected workspace. A small native queue-card scenario verifies different modes on the active and waiting jobs.

Queue cards reuse Video Preview’s ProgressPill. Only running jobs show measured percentage/ETA; waiting jobs remain Pending. Inferred durations are confined to the aggregate video guide. It sums the active job’s own remaining ETA and estimates missing durations from completed, non-reused renders of the same source/model, preferring the same batch. With no history, the active ETA is the fallback. The native queue scenario checks 48 minutes times three, the mean of 40/60-minute completions, a lone running job’s eight-minute ETA, mode labels, pending pills and cancellation.
