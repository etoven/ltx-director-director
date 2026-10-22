# Aurora a114: project menu wording and prompt header

The existing Card color context submenu is now labeled Status. Its presets use the confirmed labels Ready, In progress, Needs review, Completed, Failed and Draft in place of Green, Blue, Amber, Purple, Red and Gray. The color values, callbacks, saved-status actions, filters and panel layout are unchanged.

A native menu check verifies every label and its original color callback; the illustrated guide describes the current menu. Packaging and publication repeat the existing release checks. The private style guide records the confirmed mapping and remains Git-ignored.

The scope picker is now a full-width combo dropdown in the prompt panel header. Its displayed title is Segment Prompt, Global Prompt or Unified Prompt, matching the active document. Generate, Refine and Quick Fix retain their compact action rail. Scope keys, document mappings, draft preservation, timing and generation callbacks are unchanged. Focused panel tests cover header placement and all three titles at normal, narrow and scaled layouts.

Local validation passed nine focused panel/editor checks, including the full-width combo and all three scope titles at normal, narrow and 120% text scale. The native menu check confirms the seven menu entries and every original color callback.

Project Browser now supports inline name editing through F2, the toolbar Rename button and a right-click Rename action displaying its shortcut. Grid and list layouts place the editor over the card title. Enter saves, Escape cancels, and starting rename cancels any pending click-to-open timer. The existing metadata path preserves descriptions, tags, status, collection membership and active-project title updates. Collection renaming retains its existing name-collision guard. Media Catalog rename behavior is unchanged.

Inline rename validation covers F2 and context-menu entry, Enter/Escape, grid/list placement, collection membership and selection, active-project title updates and preservation of existing metadata. The release CI includes this as a new isolated editor group (98 total).
