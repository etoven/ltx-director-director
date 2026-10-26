# Aurora 1.13.0a129 — current Unified Prompt refinement

Refining an edited Unified Prompt previously included older segment drafts in
both media metadata and generation instructions. Those instructions marked the
old wording authoritative, requested new dialogue when enabled, and allowed
previously supplied speech when disabled. Saved custom templates could also
omit the current sound options.

Unified refinement now sends conditioning media and timing without older
segment/image prompt prose. It uses the refinement template directly, gives the
current editor and explicit edits priority, and always includes current sound
controls. Dialog off excludes speech and voice directions; explicit MiniMax
speech tags in a returned result fail validation and follow the normal retry
path. Visible written text and permitted wordless sounds remain valid.

Worker inputs are independent snapshots. A completed refinement cannot replace
a newer editor draft or newer sound controls. Project storage, segment wording,
reference files, generation behavior and the accepted layout are unchanged.

## Verification

- The new stale-wording request regression fails against the public a128 wheel
  in all six combinations of MiniMax Frames, MiniMax References and Generic,
  with stock and custom templates.
- Current core checks: 21 tests and 24 subtests passed, including response
  validation, detail preservation, visible quoted text, and dialogue on/off.
- Native editor regression passed for both MiniMax modes and a custom Generic
  workspace after deleting speech, turning Dialog off and deleting its reference.
  It verifies current immutable worker inputs and application of the response.
- Native completion checks preserve newer editor text and newer sound controls;
  existing reference-change and edited-prompt tests also passed.
- Tests capture actual provider request arguments and simulate provider responses;
  no paid model calls or GPU renders were used.
- A broader run passed 36 tests and 31 subtests but failed the existing
  `IntentGenericTests.test_intent_splitter_stays_collapsed_across_all_modes_and_refreshes`.
  That same assertion fails in a separate process using the untouched public a128
  package and fresh XDG directories. This patch does not change that panel.

Source commit: `f1906017bba2ed8df1d9530b5837366bfc86fefd`.
Fixture-isolation follow-up: `502e91316f5b4ab2372bfe500cacf75b233a2869`.
The initial CI run passed the new refinement checks but exposed a fixture that
saved its custom Generic workspace, changing the menu order in a later test.
That fixture is now local to its window and does not write a workspace file.
Release CI: <https://github.com/etoven/ltx-director-director/actions/runs/40246629163>.

CI passed 243 core tests and 101 isolated Qt editor regressions and published
wheel commit `05e33de`. The downloaded public wheel matches the committed wheel
byte for byte and all 476 packaged source/assets match this checkout.

Public wheel SHA256:
`02c1d7751ef2f6d4084bd5cd069fc00a334176f7ae632997c07dbbc9276dd6a5`.

The clean-install `scripts/verify_extension_wheel.py --refinement` run passed
all 25 tests and 27 subtests. It verifies
package origin outside the checkout, current-refinement contracts and native
editor request/completion behavior. Evidence is in
`/tmp/director-a129-public-refinement.log`; CI evidence is in
`/tmp/director-a129-ci-success.log` on the development host.
