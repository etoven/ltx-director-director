# Aurora a177: Linux playback hardening

The supplied fault-handler trace ends in native code below Qt's main event loop. The Intel iHD/VAAPI initialization failure immediately preceding it makes hardware video handling a suspect, but the Python trace does not identify the native faulting function or prove a driver root cause.

Linux startup now applies Qt's documented software-decoding default (`QT_FFMPEG_DECODING_HW_DEVICE_TYPES=,`) and disables GPU texture conversion before Qt Multimedia initializes. Explicit environment overrides remain authoritative. These settings affect Qt playback, not Torch/CUDA model generation. Reference: https://doc.qt.io/qt-6/advanced-ffmpeg-configuration.html#configure-hardware-acceleration-in-backends

Video-frame capture is an explicitly queued Qt slot. Decoder callbacks cannot replace a live latent preview or a static generation image. Static preview images update the owned image directly instead of injecting synthetic frames into a hidden native video sink. A concrete layout teardown error observed during the short check is also guarded: Qt layout virtuals tolerate cleared Python item storage and empty layouts have a valid minimum size.

AGENTS.md now explicitly forbids extended testing without an explicit user request. Verification was restricted to playback configuration, static/live frame isolation, one short video-source transition, and the layout callback guard. No broad suite, soak test or model render ran. This release mitigates the suspect native playback path; the exact cause of the reported native segmentation fault remains unconfirmed. Installed-wheel checks are limited to artifact integrity, with broader CI reported separately.
