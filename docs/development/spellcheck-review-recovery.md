# Spell checking after prompt review

The formatting cleanup used after accepting or rejecting a diff must remove review colors and strikethrough without disabling spelling. The spell checker now belongs to its editor rather than a document that can be replaced. Restoring streaming, paragraph or local segment review explicitly reconnects it to the active document and rehighlights the text. The plain-text cleanup performs the same refresh under the existing signal blocker, preserving its no-extra-edit-notification behavior and its clean insertion format. The dictionary and session cache stay on the same checker.

Spelling and directive spans also use Qt's UTF-16 positions. A deterministic regression reproduced a misplaced underline at index 10 instead of 11 after a leading non-BMP camera emoji. The correction covers misspellings and directive pills while retaining existing timecode highlighting.

The focused regressions check actual QTextLayout underline ranges, document replacement, typing after cleanup, approval and rejection, streaming restoration, and the absence of leaked diff backgrounds or strikethrough. The native local-review regression also retains an inline note while checking spelling after either approval or rejection. A wrapper in the existing release-core diff suite runs the focused spelling checks in a bounded Qt subprocess.

Validation: the release-core suite passed 241 tests and 12 subtests. All five focused spelling cases passed. Fourteen isolated local segment-review groups passed, including both new approval/rejection cases with a native note. All ten existing background/spelling/inline-cue/close checks passed. The window-chrome proof separately passed its control, geometry and toolbar-overlap checks. No GPU generation was run.

The user approved the panel and window-chrome proof. This patch is included with the 1.13.0a120 integrated header, panel rails and render queue release.
