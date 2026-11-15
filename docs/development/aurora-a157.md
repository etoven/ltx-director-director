# Aurora 1.13.0a157

Based directly on experimental 3d60e6a (1.13.0a155), not the stable branch. This candidate adds the v3 studio presentation around existing experimental SegmentCard, audio transport, render groups, render queue and prompt controllers. No main-branch controller replacement is included.

The native acceptance scenario exercises circular transport, snap on/off, three 15-second blocks, one-second blend transitions and project save/reopen without changing segment IDs, prompts or group membership. The capture script also verifies the nine-segment three-group round-trip.

Focused existing checks: 61 passed and eight assertions failed in the initial 69-case run. The audio trim regression introduced by snapping was corrected. The remaining failure assertions were reproduced on the untouched a155 baseline: legacy prompt-format, context-menu and render-cache expectations, plus a hidden-dock fixture. Baseline/candidate comparisons ran in isolated Qt subprocesses. The new studio scenario passes; broader existing CI is reported separately.

Publication uses one frozen wheel, checked against package source and clean-installed before push.
