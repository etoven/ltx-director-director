# Aurora 1.13.0a142 Global guidance and Summary

The structured editor no longer overwrites generated Summary/scene prose when Global instruction sources change. Initial template documents are parsed into their actual fields before legacy splitting, preserving section boundaries. The AI variable schema describes each field's section purpose, explicitly identifying Summary as finished narrative prose rather than raw Global instructions.

Template output retains its exact layout whitespace through AI response extraction and Refine cleanup. Completed global annotations are removed inside variable bodies before rendering. Previously, generic cleanup trimmed the document and collapsed blank lines, causing the structured parser to treat surrounding headers as Summary text. The native fixture uses the actual variable renderer and response extractor to cover that path.

Unified Generate explicitly prioritizes the current visible Global guidance over older segment wording and production notes. Refine includes changed Global sources as current private edit requests, overriding the earlier continuity-only treatment. Successful production results record which Global input was applied; unchanged guidance is not replayed over subsequent manual edits. Input placeholders omitted by custom templates still receive global_direction context. No semantic content gate is added.

The focused native wiring scenario types new Global guidance, clicks Generate/Refine, inspects the actual worker arguments, supplies production-output fixtures, and verifies the returned Summary/shot fields. It also covers unchanged-note consumption and both Global panel action routes. The installed wheel runs that scenario; no paid model calls or content-acceptance checks are involved.
