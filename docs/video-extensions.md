# Continue a MiniMax video

Use **V＋** on the timeline rail to add a continuation. Each continuation group is one video block. Select a segment in a group and use the existing length control to set that block's duration, from 4 to 15 seconds for the local H3 engine. **Auto** uses the group's segment lengths.

![Two connected blocks with one shared mix box](images/video-extensions-timeline.png)

One mix box connects each pair of blocks. Drag either edge to change its length; both edges move equally so the box stays centered on the join, inside the media preview area with two pixels of vertical margin; its mode and side controls stay inside that same box. Choose **Hard cut**, **Linear blend**, **Filmic crossfade**, or **Perceptual crossfade**. The icon and curve show the selected type. Source-side or destination-side overlap controls determine which block contains the composited frames. Visual segments show edge images; text sequences show the curve alone. After rendering, visual segments can show the rendered block's edge frames.

Blends are included in the marked durations. Two 15-second blocks make 30 seconds; blocks marked 10, 5, and 5 seconds make 20 seconds. Changing a blend does not rewrite the production prompt.

Choose the **Ref2VA** checkpoint in local model settings. **Generate locally** renders the first block, then conditions each continuation at time zero using the preceding block's trailing second. The final assembly uses the chosen blends and the sequence editor's blend algorithms. Completed blocks are reused while their group, production settings, references and incoming video stay unchanged. Editing a later group keeps earlier completed blocks; changing a blend only rebuilds the final assembly. Block videos remain available until you clean them up.

## Review and organize renders

![Grouped jobs with file actions and cleanup](images/video-extensions-queue.png)

Each generation expands into its block jobs and final assembled video. Click its group heading to collapse or expand it. Pause the queue to hold waiting jobs. Cancelling a generation stops its remaining blocks and assembly.

The folder icon at a tile's top right reveals its output file. The export icon beneath it saves a copy. Drag a completed tile to copy its output into a file manager or another application. **Clean up block videos** removes the selected generation's intermediate videos after its final video exists; the final video is kept. Deleting block videos removes their reusable outputs, so the next build must render those blocks again.

Click **Generate locally** again to queue another independent generation of the current project. The button shows the next generation number.

![A live latent preview with playback controls hidden](images/video-preview-rendering.png)

While the selected project is rendering, playback, seek, add-video, export, and fullscreen controls are hidden. Other projects retain their controls. Returning to a project restores its most recent latent preview or final video. The video surface and idle preview background are black.

## Combine projects from a collection

Inside a collection, Ctrl-click projects, then right-click and choose **Group selected projects into Shot Sequence**. Every selected project needs a saved rendered video. The new sequence follows the displayed sort order. **Collection previews** in an existing Shot Sequence follows the collection's current sort order as well.

The combined project embeds editable source projects, preserving their segment prompts, unified prompts, workflow types, timeline images, reference-workflow images, and other archived source media. Choose **Source project…** on a shot to reopen its source. Moving or resaving the combined `.LTXD` retains those embedded sources; the original projects remain independent.

## Scrub the timeline

Drag across the existing time ruler to preview the sequence without changing the selected segment or its prompt. Available rendered footage auditions its video and audio. If the sequence has no final render at that position, an available rendered block can be previewed; otherwise the panel follows the text, image or trimmed video conditioning segment under the scrub position.

![Scrubbing to an unrendered conditioning segment](images/timeline-scrub.png)

![The source text shown in the preview panel](images/timeline-scrub-preview.png)

Scrubbing a rendered video uses brief audio samples and preserves whether playback was paused or playing. Conditioning-video previews seek to the source trim without audio. Switching projects discards obsolete preview work. Long source text stays within the preview panel and can be scrolled.
