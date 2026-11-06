# Aurora 1.13.0a135 startup repair

The user's saved maximized geometry and dock layout reproduced a native Qt 6.12 segmentation fault on Wayland at the first window show. Workspace JSON loaded correctly. Removing either geometry or layout in a private diagnostic copy avoided the crash, while keeping both reproduced it.

Restoring geometry after the embedded toolbar, window chrome and panel rails have finished construction fixes the copied-profile startup. The change preserves the saved dock layout, window geometry, custom workspace files and project data. Native XCB checks alone did not expose this Wayland startup failure.

The existing saved-layout regression now also saves a maximized window and checks successful startup and retention of the saved geometry without resizing the new window first. Validation additionally uses a private copy of the user's settings and workspaces on the actual Wayland backend, with credentials excluded and project loading suppressed. No private profile content is committed or published.

The clean-installed a135 wheel also starts successfully on Wayland with the copied user profile and splash screen; the identical profile crashed with a134. The installed dialogue adapter checks still pass.
