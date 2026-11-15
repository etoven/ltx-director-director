# Local latent playback

Acceptance: animate the whole current latent estimate continuously between sampling steps; deliver RGB samples directly to Qt without temporary video encoding or VAE decoding; retain hidden player controls during sampling; leave final decoded video/audio and other generation features unchanged.

The worker publishes the RGB projection as a compact, atomically replaced sample bank. The GUI maps it read-only and interpolates temporal samples at 24 fps into the preview image surface. A monotonic clock loops the clip independently of inference, retaining the playhead across sampling updates. Switching preview sources closes the mapping and timer. The projection and sampler prediction remain unchanged.

Native checks caught segmentation faults in Qt's QVideoFrame.toImage() readback with both QImage-created and explicitly CPU-backed frames. The latent stream now paints its raw QImages directly onto the existing preview image surface, avoiding QMediaPlayer and QVideoFrame conversion entirely. Failed checks are retained in this chat's tool output. Offscreen checks alone did not reveal this.

No full model render was started for this change: an existing user render was already using the GPU. That running process retains its old code until Director is restarted.
