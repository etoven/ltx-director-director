# Aurora a172: render queue preparation crash

The preview client's fatal trace ended at `QueueJobDelegate.paint`, on `self.pill.grab()`, following backing-store and multiple-active-painter errors. The other thread in `continuation_reference` was still preparing the attached clip; it was not the crashing thread. Casting and model inference were not responsible for this trace.

The queue delegate now paints its progress pill directly on its existing painter instead of grabbing and resizing a hidden child widget during a list paint. It retains the preview pill's colors, border, radius, font size, percentage, ETA and Pending/Preparing text. No render inputs, conditioning, selected modes, attachments, preview transport or model code change.

Focused native checks cover Preparing, Pending, progress and cancellation tiles for both MiniMax video and Flux images, with widget grab/render forbidden inside the delegate. The existing queue-mode/ETA and remote-preview/canvas completion/cancellation scenarios are included. No extended model render is required for this paint-only fix.
