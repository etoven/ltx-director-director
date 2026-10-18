# See every beat. Shape every transition.

## Build the sequence in pictures and words

Director’s timeline gives your prompt a visual structure. Each card has a duration, a place in the story and a prompt you can edit. Mix reference images, source clips and text to describe the sequence at the level of detail you have today.

![A visual opening, a written walking beat and a finishing image on the timeline](images/timeline-and-magic-build.png)

## Bring in the right material

| Segment | Use it for | Add it with |
| --- | --- | --- |
| Image | Identity, composition, an opening state or a target pose | Add media or timeline drag-and-drop; PNG, JPEG, WebP and GIF |
| Video | Source footage or movement context | Add media or timeline drag-and-drop; MP4 and WebM |
| Text | An action, pause or transition without its own reference image | Add text |

Drop media near the position where you want it inserted. The timeline shows when it is accepting a drop. Project archives and Director JSON dropped into supported project or timeline areas open or import their workspaces.

Videos use a preview frame from the clip’s final second for the timeline and AI image context. The complete source clip remains available for project storage and export. A GIF is handled as image media; use MP4 or WebM for a video segment.

## Make timing tangible

Drag a card to reorder the sequence. Select it and enter a precise value in **Duration** to edit its length. The sequence summary shows the total; each card shows its starting time. Image and video cards also show source resolution.

Drag the dotted divider **between two cards** to transfer time from one to the
other: moving it right lengthens the left segment and shortens the right, and
moving it left does the reverse. Their combined duration and every later start
time stay fixed. The drag snaps to frames and stops at one frame per segment or
the available video source limits. The final card's right grip changes the
overall length; the Duration field still edits a single card.

Use the **Re-time all** clock button beneath Rewrite Timeline in the right rail
to distribute time using dialogue delivery, action, camera movement, frame roles
and shot pacing. Set the sequence-length box below the timeline to the desired
total first; **Auto** (0) keeps the current total. The proposal moves the tiles
and offers approve/revert controls. Approving or rejecting any timing change
resolves the complete timing pass. Segment prompts, visual directions, global
guidance and the unified prompt remain unchanged. Video source limits and the
workspace's duration limit still apply.

![Timeline timing controls and timing-only review](images/timeline-timing.png)

Right-click a card and open **Split segment**. **At clicked position** divides
the card where you clicked. **2-way split** and **3-way split** create equal,
frame-aligned parts with these choices:

| Choice | Result |
| --- | --- |
| Keep prompt | Each part keeps the original prompt |
| Divide prompt with AI | The model distributes the prompt's action beats chronologically across the parts |
| Hard cuts + close-ups with AI | The model divides the action into shots, establishing it first and cutting to close-ups in later parts |

Every split preserves total duration and original media. Video parts use
consecutive source intervals. AI splits use the configured prompt provider and
offer one review for the entire split: approve any part to keep all parts, or
deny any part to restore the original segment. Other segments and the combined
prompt stay as authored. Each part needs at least one frame. Pending timeline
reviews and the workspace's **Maximum shots** setting control availability.

![The segment split menu with two-way and three-way options](images/segment-split-menu.png)

![Prompt-preserving, AI division and hard-cut options](images/segment-split-options.png)

Use **Zoom** beside the timeline ruler to inspect a passage. The
**Fit timeline** icon at the right edge brings the whole sequence into view; moving
Zoom all the way left also fits the sequence. Drag the dotted lower grip to
make the visual previews taller. Smooth horizontal scrolling helps you move
through longer plans.

The conditioning tiles beneath the timeline show larger previews, frame roles
and timing values on separate lines. Scroll horizontally to see additional
references. Drag the dotted handle beneath the tiles down to enlarge them, or
up to hide the strip completely. Double-click that handle to hide or reopen
the strip. Its size and collapsed state are remembered.

![Timeline navigation and readable conditioning tiles with their own resize handle](images/timeline-navigation.png)

There is no fixed segment-count or total-length cap in the editor. The renderer you use still determines which output lengths it can produce.

## Anchor the opening and the destination

**Start frame** tells a compatible workflow to begin a segment from its supplied image. **End frame** describes the image it should reach by that segment’s end. These controls are available in LTX and MiniMax.

Click a card to edit its segment prompt. In MiniMax, this automatically switches
the upper editor to **Segment**, including when you click the selected card
again. Choose **Global** to return to whole-sequence direction. MiniMax keeps an
independently editable unified production prompt below it. Refining a segment
can update its duration and the timeline; typing in the unified box preserves
segment timing.

![Shared segment editing with the full production prompt below](images/shared-segment-editors.png)

## Update the image without rebuilding the beat

Use **Replace media**, or hold **Ctrl** while dropping media onto a card, to retain its duration, prompt, role and identity while changing the source. This is useful when a revised pose or a cleaner frame is ready.

An image card’s context menu includes **Copy image** and **Paste image to replace**, using the original-resolution source. For a text card, **Convert to image segment** lets you add an image while keeping the written beat. **Export image** and **Export video** place the original media in the project’s download folder.

Choose **Convert to text segment** on an image or video card to keep its prompt,
visual direction and duration as a text beat. Its original file remains intact;
the text segment carries no media or source trims.

When opening older projects, Director checks the stored media type and
normalizes media names and cache extensions. Duplicated attachments and media
exports use safe filenames with the actual file extension, preserving the
original source bytes.

Delete a card with its close control, the toolbar’s **Delete selected** action or the context menu. Revisit the surrounding action after a large sequence change so the written transitions still describe the plan you intend.

## Build the plan from a timed brief

In a unified workspace, paste a brief whose timed-action lines use 24 fps SMPTE ranges:

```text
00:00:00:00 - 00:00:03:00: Establish the traveler beneath the aurora.
00:00:03:00 - 00:00:08:00: Follow the traveler toward the overlook.
00:00:08:00 - 00:00:12:00: Settle into a quiet horizon hold.
```

Director matches the actions to existing segments in order, preserves their media, resizes their durations and creates text segments for extra valid actions at the end. A whole-brief paste replaces the brief; a timed-action-only paste retains the surrounding sections.

Start at zero and keep ranges continuous, ascending and nonempty. The final two digits are frames from 00 to 23. If a range is missing or invalid, unmatched cards turn gray while their media remains intact. Correct the timed block or refine the prompt to reconnect the plan.

**Continue:** [Prompt workflows](unified-workflows.md) · [Exports](workspace-definitions-and-exports.md) · [Complete feature guide](../usage.md)
