# Aurora 1.13.0a138 saved-project migration and retained local models

Legacy MiniMax prose is parsed once on initial project open. Project version 11 and promptTemplateVersion 1 mark the migration. All official fields, inline shot markers, Video/Picture/Subject/Audio definitions, workspace aliases and inactive render-group contexts are covered. Original prose and existing template edits are preserved; saved detailed-description bodies populate Unified sections independently of timeline prompts. There are no AI requests or forced regeneration.

The render queue owns one resident inference subprocess. Compatible jobs reuse weights across idle periods, pause and cloud jobs. Changing model files, loading settings, LoRAs or interpreter replaces the process. Cancellation, fatal errors and shutdown release only that owned process. A warm-model out-of-memory error retries once from a clean process. Job callbacks, preview hooks and voice conditioning are cleared between requests; output and request transport remain isolated per job.

The a137 dialogue import repair is retained. It was installed before resuming these changes.

Focused verification: 14 backend/queue/migration checks passed, using real subprocess transport and small stand-ins for model weights. The private legacy-project check verified all six fields, four saved shot bodies and Video 1 in the native editor, with unchanged timeline source prompts. Clean-wheel validation repeats the focused checks and isolated audio-worker import. No model downloads, paid calls or heavy sampling are part of this patch verification.
