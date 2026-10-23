# Timing overview preview sizing

Long wrapped text made QLabel request its full paragraph height. The horizontal overview layout propagated that height to its scroll-area content, while vertical scrolling was intentionally hidden. This pushed both text and media tile footers below the viewport. Short demonstration prompts did not expose the problem.

Text and media preview labels now share a fitted sizing base. Their size hints and height-for-width contribution leave space allocation to the existing tile layout, which keeps the title and timing footer visible. Text remains wrapped and read-only; media retains its aspect ratio. The constraint remains valid after prompt edits, font/style changes and panel resizing, including resizing back down after expansion.

The native regression uses a video preview and three long text prompts, checks compact and expanded panel heights, narrow and wide windows, 100/150/200 percent text sizes, and another edit after resizing. It verifies the scroll content fits the viewport and every timing footer remains inside it. The original sizing failure reproduced as a 5158-pixel content widget inside an 82-pixel viewport.

a123 validation passed: 243 core tests and 12 subtests, including all 15 isolated native checks. A fresh wheel installation passed the long-prompt sizing regression, text-segment updates, resize/collapse/restore and shot-range footers. All 462 packaged source/assets matched the source checkout and installation. A native capture with long prompts confirms visible footers and aspect-correct media.
