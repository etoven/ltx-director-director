# Aurora 1.13.0a131 — thumbnail image export

The project-details thumbnail strip now provides **Export Image** on every
image's context menu, including custom covers. Right-clicking does not replace
the selected project cover. The action exports the clicked item's stored image
bytes, preserving its format and uncropped dimensions.

Exports use the existing background disk queue, filename reservation, error
handling and Recent exports history/feedback. The destination is the download
folder shown in the dialog, with an empty field using the application default.
Editing another project's details does not redirect exports into the active
project's folder or change the active project's download setting.

The native regression passed with three different stored images (PNG, WebP and
JPEG), including a custom cover. It exercises right-button gestures and real
menu actions, executes the real asynchronous writes, checks the download tray's
history, verifies bytes/dimensions and checks both explicit and default folders.
The native popup loop is replaced only in the regression harness; production
uses the existing Qt menu behavior.

Source commit: `886d731e27e6a4986bfeb733d0ceaec42c4e4d9c`.
CI: <https://github.com/etoven/ltx-director-director/actions/runs/40779998022>.

CI passed 243 core tests and 103 isolated Qt editor regressions, then published
wheel commit `39dd3df`. The downloaded public wheel matches the committed wheel
byte for byte and all 476 packaged source/assets match this checkout.

SHA256:
`9b2f49e828ce49ce8be2b13b4edec5ab17a772f131e6f639f3e6ea7aa83a69f4`.

All three clean-install native checks passed: thumbnail context-menu exports,
download-panel behavior and the existing ComfyUI toolbar export. They ran with
isolated XDG paths, outside the checkout, and asserted the imported package was
the installed public wheel.

Evidence on the development host:
`/tmp/director-a131-public-thumbnails.log` and
`/tmp/director-a131-ci-success.log`.
