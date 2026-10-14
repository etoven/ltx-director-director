# Finish the film, shot by shot

**Shot Sequence** brings completed renders together in one continuous preview, with the sound from your original movies. Every collection receives a dedicated Shot Sequence project tile. Open it to order shots, tune their joins, and bake a finished WebM.

![The native Shot Sequence workspace with ordered completed shots and a continuous RAM preview](images/shot-sequence.png)

## Bring in your completed shots

Open a collection and select **Shot Sequence**. Drop movie files onto the NLA timeline, use **Add shots**, or choose **Add collection previews…** to bring in the saved rendered videos from that collection. Director copies imports into managed storage; their originals and shot order travel inside saved `.LTXD` projects.

Drag linked video/audio strips to reorder them, use the left/right buttons, or select shots and press **Delete** to remove them from the sequence. Double-click a shot to inspect its source movie through the same player. Save the project to keep its order, blend settings and output options. Completed shots keep their full source lengths, independent of generation duration limits. **Restore full clips** re-reads the source movies if an older project contains shortened timings. The sequence workspace has no duration ceiling.

The NLA timeline places shots on alternating video tracks with linked audio tracks below. Strip widths follow actual movie duration; shaded regions show overlaps. Use **+**, **−** and **Fit** to inspect a long sequence, Ctrl-click to select multiple strips, and click the time ruler to seek the continuous RAM preview.

## Shape the join

The defaults are **perceptual_crossfade**, **1 second** of overlap, and **Source side**.

| Mode | Result |
| --- | --- |
| `cut` | Remove the selected side's overlap and join the remaining footage directly |
| `linear_blend` | Mix pixel values through the overlap |
| `filmic_crossfade` | Mix in gamma-corrected linear light for a brighter photographic fade |
| `perceptual_crossfade` | Mix in LAB color space for a perceptual color transition |

For blends, Source side fades the source tail toward the next shot's head. Destination side reverses that overlap weighting. For cuts, Source side trims the source tail; Destination side trims the next shot's head. An overlap longer than an available shot is reduced to fit. Set overlap to zero for a direct join without trimming.

The source movies' audio follows the same joins. Blends crossfade the two soundtracks, while cuts trim audio on the selected side. Silent movies contribute silence; mono and multichannel sources are normalized to stereo for the finished sequence.

## Watch continuously, in RAM

Click **Build RAM preview**. Director composes a lightweight preview in memory and plays it with source audio through the existing player. A progress bar follows source reading, audio preparation, encoding and WebM finishing. No preview movie is written to disk. Use Play/Pause, click the seek bar, or open fullscreen. Frame export and clipboard frame capture remain available through the player's context menu.

Changing the order or blend settings clears the previous preview so the next preview reflects the current sequence. Preview uses a reduced canvas for responsive playback; output dimensions remain independent.

## Bake the final movie

Choose output width and height, then click **Bake WebM**. The finished file includes video and source audio, encoded as VP9 and Opus. **CRF defaults to 15**; lower values retain more detail and usually produce larger files. Baking uses the same blend and overlap settings as the RAM preview at the selected output resolution.

Clips are normalized to 24 fps and fitted to the output canvas without stretching. Only overlap frame buffers and working audio are retained during composition; full-resolution movies do not need to be loaded as one giant image batch. Baking runs in the background with its own progress bar, and the finished file appears in **Recent exports**, where it can be opened, revealed or dragged into another application.
