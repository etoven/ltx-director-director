# Preserve live preview through group navigation · Aurora a164

Selecting a group previously called the normal attachment display path, which closed the latent RGB player and loaded that group's attached video. A passive project-preview refresh could also replay the cached sampling event instead of retaining its current stream.

Group attachment display now defers while the current project is rendering. Passive refreshes retain an existing preview for the same render target. Completion and failure release the guard through the existing queue state; cancellation now explicitly restores the selected group's attachment after worker acknowledgement. Controls remain hidden during sampling.

A bounded native regression exercises real group/blank-space clicks while a queue fixture holds sampling between events. It verifies that the same RGB mapping and source generation remain active, playback advances, prompts remain unchanged, and generation is not cancelled. The same scenario checks Complete, Failed and Cancelled terminal states and attachment restoration. The fixture replaces inference, so it does not claim live model quality.

This release changes no sizing, layout or styling. Further sizing corrections remain deferred.
