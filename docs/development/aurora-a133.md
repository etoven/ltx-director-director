# Aurora 1.13.0a133 feature-plan implementation

Implements `docs/feature planning/feature planning.md`, including the clarified per-effect AI model preference and subsequent UI corrections.

- Independent prompt docks replace the visible scope selector. LTX exposes Global and Segment; Unified is limited to supported workspaces.
- One complete template document controls literal headings, spacing, field order and subject/segment repeat blocks. It is edited in one code-kit canvas under Workspaces, with native pill images, interlocking containers, nested drop sockets, movable children and local output preview; the production editor follows that document. JSON-only schemas derive their named variables from the template.
- Local section synchronization covers typing, reordering, references, add-part actions and timing. Clicking a segment, including a repeated click on the current segment, reveals its Unified section without moving keyboard focus.
- Factory defaults are limited to LTX Video, the two official MiniMax skills and Generic Prompt Refine. Migration removes only unchanged redundant defaults; customized variants remain.
- The timeline is a native dock with a zero-height central layout anchor. Its legacy empty splitters are detached from the visible layout, and its options row scrolls at narrow widths. The ruler spacer and navigation controls have bounded height. The central timeline no longer stretches its ruler to consume the space released by the docked editors.
- Dialogue, Foley and music panels use cancellable render jobs, independent runtime configuration, player controls and background exports through Recent exports. Music includes the official progressive caption-rewriter resources and editable ComfyUI export.
- Audio environments/model downloads are available in Settings and onboarding. The toolbar writes the next writing-model choice without opening Settings. Effects can override that choice per refinement.

Focused verification: `scripts/check_feature_plan.py` exercises native widgets, a custom document order, section/timing edits, project persistence, both AI output contracts, model selection, effect override, audio fixture playback/export and stale-result rejection. Captures use isolated XDG directories under Xvfb. The fixture is recorded test data, not a model-generation claim.

The selected effects/provider/local-runtime tests passed (199 passed, 5 skipped). No production provider request, multi-GB model installation or neural audio sample is part of this check. Actual sampling remains dependent on the user's selected inference environments and weights.

Existing local changes to dock chrome, its tests and the experimental CI workflow were preserved. Release requested on experimental. Native feature smoke passed, including the clean-install release check. Heavy audio inference and paid provider calls are not included in verification. The existing workflow draft remains local because the token lacks workflow scope.

## Feature-plan audit

| Plan requirement | Delivered behavior |
| --- | --- |
| Separate prompt panels and live synchronization | Native Global, Segment and model-supported Unified docks, independent controls, live source-to-section editing. |
| Dialogue | IndexTTS 2.5 sample-based panel, optional background refinement, playback and download-manager export. |
| Foley | Stable Audio 3 panel with optional refinement, playback and download-manager export. |
| Music | MiniMax Music 3 local panel, official progressive refinement skill, playback, audio export and topbar editable ComfyUI export. |
| Audio setup | All three engines have Settings and onboarding configuration, background environment preparation and weight downloads. |
| Workspace template editor | One output document in the approved graphical code-kit design, draggable pill images, nested containers and compatible sockets. |
| Template-conforming output | Model-derived defaults, explicit AI variable schemas, fixed literal headers and shaded editable fields. |
| Offline editing | Add/reorder segments, add references/subjects, live prompt editing, editable time boundaries and timeline preview updates. |
| Add-part and navigation | Floating gutter actions add segments/subjects; LTX adds segments in its Segment dock. Segment clicks reveal the matching Unified section, including repeat clicks. |
| Effects and model selection | Optional per-effect writing-model preference with current-model fallback; immediate toolbar dropdown without opening Settings. |
| Later corrections | Bounded timeline ruler sizing; LTX has no Unified editor; four factory workspaces only, preserving customized legacy definitions. |

The implementation audit found no unimplemented feature-plan item. Native verification covers UI and transport contracts, fixture playback/export and packaging. Neural audio sampling, model downloads and live provider output are not end-to-end verified in this release check.

Follow-up audit fixed chip format inheritance on adjacent typed text, stable repeated template saves, reserved/duplicate named fields and exact chip hit testing. Native checks cover four serialization roundtrips and three real workspace saves, along with floating/redocking the Timeline. User testing confirmed generation works; the release checks still do not claim independent live inference verification.
