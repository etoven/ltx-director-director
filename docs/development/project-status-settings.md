# Project status settings and card colors

The project context menu now has a single status submenu populated from Application Settings, with color dots and the current status checked. Collections use Group status and apply the selected status to each member. The former hard-coded appearance menu and its separate setter are removed.

Card painting and collection covers resolve colors through the saved status definitions. Legacy color overrides no longer mask a status; assigning a status also removes that obsolete override from the affected metadata. Status colors remain visible on archived cards, with Archive still identified separately. Settings color edits immediately affect existing cards without rewriting their project archives.

The browser Status and Tags menus share the existing persisted status/tag filter controls. The separate static color filter is removed. Status filtering uses names, so distinct statuses can share a color; configured tags stay in Tags, with Archive under More filters. Existing saved metadata, statuses, tags and archive flags are retained.

Validation covers custom names and colors, one context-menu status entry, selected status checks, project and collection assignment, obsolete color overrides, painted card pixels, settings recoloring, archive coloring and independent browser filters. Native illustrations show authored demonstration data; no model or provider calls are required.

Source validation for a122 passed: 243 release-core tests and 12 subtests, 13 isolated native chrome/dock/status/queue checks, seven panel checks and two project-properties/cache checks. Native captures confirm one context-menu Status entry and settings-defined Status and Tags filter lists. All 462 packaged source/assets match the wheel and the fresh installation target.

The same a122 build also repairs the timing overview: existing text-segment records are no longer discarded because they lack media labels. They use the existing tile layout and their original timeline ranges. Prompt edits update text previews in place without rebuilding media thumbnails. Native conditioning records, frame checkpoints, video trims and generation inputs are unchanged. A regression through Add Text, prompt editing, duration changes and deletion failed before the repair and passed afterward.

The repaired overview passed the existing shot-range, checkpoint/source-trim and resize/collapse checks. The final wheel was installed into a separate fresh target and passed custom-status assignment, group assignment, browser filters and the text-segment overview regression. All 462 source/assets matched the rebuilt wheel and that installation.
