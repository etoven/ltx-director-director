# Aurora a178: reconnect restored remote jobs by GUID

Queue restoration no longer depends on a separate remote receipt for a saved network job. The saved queue request UUID can recover monitoring directly. Existing receipts also repair a snapshot missing its request UUID. Receipts for known queue jobs remain eligible even when project metadata is temporarily unavailable.

The client probes discovered hosts for ownership of that request GUID instead of requiring exactly one matching display name or cached feature advertisement. A successful probe refreshes the target and resumes the same host mailbox, progress and live frames. Pending jobs that were never submitted stay paused. Cancellation verifies GUID ownership before issuing Cancel.

Reconnection bypasses local input preparation and upload: the running host already owns its conditioning, resolved seed and model. It cannot submit a replacement job when the GUID is absent. Existing job GUIDs and the detached-jobs-v1 protocol remain unchanged; the host can continue its current render without restarting for this client update. Video and Flux reconnect share this path.

Focused verification: one native WebSocket workflow closes and reopens the client, restores from its queue snapshot with the side receipt removed, and uses a renamed host alongside another instance with the same name. Missing local continuation media does not block reconnect. Live latent preview resumes and the result attaches; exactly one host render ran, and unrelated pending work stays paused. No GPU inference or extended tests ran. The wheel is frozen once, checked against source and installation, then published unchanged; broader CI is reported separately.
