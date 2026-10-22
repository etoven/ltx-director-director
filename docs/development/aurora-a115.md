# Aurora a115: inline project and collection renaming

Project Browser now supports inline name editing through F2, the toolbar Rename button and a right-click Rename action displaying its shortcut. Grid and list layouts place the editor over the card title. Enter saves, Escape cancels, and starting rename cancels any pending click-to-open timer. The existing metadata path preserves descriptions, tags, status, collection membership and active-project title updates. Collection renaming retains its existing name-collision guard. Media Catalog rename behavior is unchanged.

Inline rename validation covers F2 and context-menu entry, Enter/Escape, grid/list placement, collection membership and selection, active-project title updates and preservation of existing metadata. The release CI includes this as a new isolated editor group (98 total).

The a114 header scope combo and confirmed status-menu wording are retained. Local validation passed nine focused panel/editor checks. The installed-wheel checks verify the menu callbacks, all three header scopes and inline rename; publication CI repeats the release core suite and all 98 isolated editor groups.
