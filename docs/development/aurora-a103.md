# Aurora a103: live latent previews

Local H3 defaults to a lightweight noisy-latent RGB channel visualization, updated at the initial latent and after each sampling step. It transfers only a single small latent frame to CPU, with approximate colors, and uses the existing video surface. It neither loads the VAE nor adds denoiser passes during sampling. Settings retains periodic denoised previews, defaulting to every five steps. These use a clean estimate from the already-computed flow velocity for the opening chunk. They skip the last sampling step because the pipeline performs the full video/audio decode immediately afterward. Image-preview intervals remain independent.

Verbose H3 messages now say sampling network/layer so the necessary 50-layer forward pass is distinguishable from optional preview VAE decoding and final video/audio decoding. Live image frames do not become saved project video paths; workspace guards apply to both kinds of preview.

Validation covers the default/periodic schedule, real tensor-to-PNG conversion, absence of VAE loads in noisy mode, clean-estimate arithmetic, native live video frames, settings round trips, stale workspace suppression, source regression checks and the installed published wheel. Full H3 sampling is unchanged from a102's target-size first-step validation.
