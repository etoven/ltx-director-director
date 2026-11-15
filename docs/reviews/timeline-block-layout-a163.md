# Timeline block layout · Aurora a163

The timeline dock previously used ignored size policies and a no-constraint root layout. Qt could shrink the containing panel below its children's fixed heights, making dialogue overlap the visual track.

The root now propagates its layout minimum, uses normal horizontal sizing and a minimum vertical policy, with a 480 px width floor. Visual, dialogue, stack and workspace layouts propagate their content minima as separate blocks. Existing compact margins, gutter widths, resize handles and horizontal scrolling are retained.

One bounded native resize scenario shrinks the main window and timeline dock, then checks minimum track height, non-overlapping sibling geometry and the root's minimum dimensions. The change does not alter grouped rendering or playback logic.
