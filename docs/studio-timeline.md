# Studio timeline

Visual media and text prompts share one track. Double-click a dialogue cue to edit the quoted words while preserving the surrounding production prompt. The native prompt panels remain available through Panels.

![Experimental studio timeline with timed dialogue and three real audio lanes](images/studio-v3-experimental.png)

Play and pause use the existing sequence audio/video transport. The previous and next controls seek to clip boundaries. Snap toggles an eight-pixel magnet for ruler seeks, visual trim handles and audio moves/trims; editing remains frame-aligned. Audio lane M and S controls mute or solo monitoring. All, Dialog, SFX and Music filter the displayed lanes. Properties opens the existing start, end, source-in and track fields.

The right rail adds media or text, rewrites from the combined prompt, re-times the complete sequence and adds a MiniMax continuation. These use the existing experimental controllers and provider/model settings.

![Three native 15-second groups with one-second blends](images/studio-v3-groups.png)

Groups retain their own settings, source prompts and render caches. The brackets show each block's duration. The blend panel shows the crossing curves, duration, blend mode and overlap side.

![Blend controls between adjacent groups](images/group-blend-controls.png)

Each continuation receives the preceding video's trailing clip as a video conditioning segment at time zero, with its length set by the blend duration. This render input is prepared automatically. FL2VA uses its video conditioning lane. Hard cuts start without the inherited clip.

In Render Queue, drag waiting jobs or waiting generation groups to change their order. Dependent blocks stay after their source. Use the × on a job to cancel and remove it with dependent work. Sweep clears completed, cancelled and failed entries. Output files remain available on disk. Queue updates retain your scroll position and selection.

The local video preview keeps the last valid frame visible while the next preview loads and between render stages.
