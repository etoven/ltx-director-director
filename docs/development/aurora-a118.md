# Aurora a118: Flux clean-image previews

Flux 4B and 9B share the Klein image-generation loop. Its preview callback previously decoded the post-step noisy sampling state. It now decodes the clean estimate `x0 = x_sigma - sigma * velocity`, using the existing guided prediction and the actual scheduler sigma for the completed step. The estimate is calculated in float32 before VAE normalization, unpatchifying and conversion to the VAE's execution device and dtype.

Preview capture wraps the existing Euler step without changing its inputs, output or number of calls. The saved preview interval remains five steps by default. The final step uses the pipeline's normal finished-image decode, avoiding a duplicate preview decode immediately before the result. This shared path covers segment artwork, image refinement and locally generated Effects thumbnails, including mixed-precision 9B loading. No new dependency or preview model is required.

The image panel and offline guide identify intermediate images as clean draft estimates. MiniMax's user-verified preview and the a117 header correction remain unchanged.

Validation: all seven private-runtime CPU preview checks passed. These cover high-sigma clean recovery on the actual shifted Euler schedule, batch-normalization/unpatchifying and BF16 VAE inputs, requested preview cadence, and tiny real Klein pipelines with guidance, reference images and distilled sampling. Preview-enabled runs produced identical final pixels and transformer forward counts to the baseline. The three H3 preview regressions remained passing. The native 4B/9B canvas regression and clean-installed app smoke check passed; all 449 packaged source/asset files matched the checkout. No GPU render or full-size checkpoint sampling was performed for this release.
