# Aurora a179: restore queues containing receipt-only remote jobs

An a178 receipt merge regression could abort loading the entire queue: after inserting a receipt-only job into the snapshot, the loader still accessed the previous `None` local row. This could hide pending and terminal entries together. The saved snapshot and receipts were not deliberately cleared; the startup error could be overwritten by the project-opening status message.

The loader now assigns the recovered row before backfilling its request GUID. Receipt recovery no longer requires project metadata to exist. Jobs remain discoverable even if their project cannot yet be opened. A bad job's input binding no longer hides unrelated queue tiles: that job shows a failure, other jobs restore, and structurally unreadable rows are retained in future snapshots. Restoration failures are also logged to the terminal.

Focused checks cover recovery from an existing snapshot plus an orphan remote receipt with absent project metadata, preservation of pending and failed tiles, and retention of unreadable row data. A single native WebSocket close/reopen scenario covers receipt-only recovery, resumption of the original GUID, live preview, output attachment, and unrelated pending work remaining paused. No GPU inference, extended tests or broad suite ran. The same frozen wheel is checked, installed and published; broader CI is reported separately.

The separately reported host segmentation fault remains unconfirmed: no new fault-handler stack has been captured. This release fixes queue loading; it does not claim to fix that native crash or subnet mDNS reliability.
