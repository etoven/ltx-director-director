# Aurora 1.13.0a139 mixed reference definitions

Unified definitions now use the same media inventory and labels as provider requests: timeline images/videos, imported images/videos/audio, paired soundtracks, audio-only video, and custom subjects. Migrated Video/Picture definitions bind to their source instead of being duplicated. Reference normalization retains generated descriptions across save/load.

Reference Media has per-reference Generate/Regenerate, batch Generate definitions and explicit Remove controls. The floating add menu imports each supported media type through the existing background import path. Unified sections and subject definitions have removal controls. Removing a timeline media definition preserves its source timeline item; removing a timed section removes that timeline item.

Generation uses an explicit reference-only response schema and validates exact requested IDs. Media preparation runs in the existing AI worker and only prepares requested media. Applying results changes definitions alone and rejects changed media, projects, render groups and edited definitions. Provider audio limitations are retained; local writing models use supplied text context. No forced reevaluation or timeline generation occurs.

Verification uses two focused checks: reference-contract validation (including wrong-ID rejection and migrated Video binding), and one native add/generate/save/reopen/remove flow for all three media types, paired audio, batch updates, stale results and preserved timeline/other fields. Provider output is a fixture; no live provider or neural inference claim is made. The installed wheel repeats the same checks and is compared with source and the public artifact.
