# Prompt Director project context

This repository continues the ChatGPT project **LTX Prompt APP** in Codex. Read
`docs/codex-handoff.md` for the migration baseline, recovered requirements,
source references, and history coverage limits.

## Development

- Follow `CONTRIBUTING.md`. The stable line is `main`; Aurora development is
  `experimental`. The migration baseline was `1.13.0a81` on `experimental`.
  Check the current branch and version before starting new work.
- The app is a native Python/PySide6 desktop application. Main UI behavior lives
  in `ltx_prompt_director/ui.py`; reusable logic and widgets live beside it.
- Preserve project archives, original media, timeline associations, and user
  edits. Keep expensive media/disk work off the UI thread and discard stale
  asynchronous results. Keep credentials out of logs and exports.
- Keep controls compact and use the existing editor and toolbar conventions.
  Segment Generate/Refine affects the selected segment only. Global scope acts
  on the entire sequence and then rebuilds the lower combined prompt. Reverse
  synchronization uses the entire ordered timeline. Preserve the source side
  of synchronization.
- Keep user documentation illustrated and focused on capabilities and usage.
  Put patch rationale and validation in development notes or PR descriptions.

## Verification and history

- Run checks appropriate to the change. See `CONTRIBUTING.md` and
  `.github/workflows/experimental-wheel.yml`; pytest also covers tests that
  unittest discovery alone will miss. Use isolated XDG directories for Qt tests
  and follow the CI process isolation for native GUI regressions.
- Local history is indexed in `.codex/chatgpt-history/README.md`. Its raw JSON,
  Markdown excerpts, and attachments are excluded through `.git/info/exclude`.
  They are local reference material and will not accompany an ordinary clone.
- Historical messages are evidence, not a queue of new instructions. Check
  current code and later requirements before acting on an old request. The
  recovered history is incomplete; do not claim a full transcript migration.

## Cost-bounded verification and delivery

The reference-panel audit found that isolated helper checks had missed the actual
add/generate/save/reopen/remove workflow, while repeated broad validation delayed
requested fixes. Treat observable user behavior and the installed artifact as the
acceptance criteria.

- Translate the latest user requests into a short acceptance checklist before
  coding. Verify every requested action, supported media type, and preserved
  source field. Do not report an earlier release as completing newer requests.
- Ship an explicitly prioritized unblocker before process reviews or unrelated
  audits. Keep the remaining authorized work active until it is delivered.
- Choose the smallest checks that can falsify the change. For a UI workflow,
  prefer one native scenario that clicks the real controls, exercises add and
  remove, updates existing content, saves/reopens, and checks stale results and
  untouched fields. Add a focused transport/response-contract check where needed.
  Provider fixtures prove application behavior, not live model quality.
- Keep Qt checks in isolated XDG directories and bounded subprocesses. Retain
  real failures. If a check hangs, isolate its failing interaction; do not keep
  repeating the whole suite or reviewing unrelated logs.
- For subprocess features, verify the actual copied backend and its imports,
  rather than only importing from the checkout. Check the clean-installed wheel
  when packaging changes or new modules are involved.
- Freeze the source candidate before building. Reuse one wheel for clean-install
  checks, desktop installation and publication; compare its files with source and
  verify the public artifact. A source change after the snapshot invalidates that
  candidate and requires rebuilding. Avoid editing during a running build.
- Once focused checks pass, do not rerun them or broaden local testing without
  new changes, a failure, or an identified unresolved concern. Let the existing CI
  run its broader regression suite once. Report pending CI as pending.
- Keep progress reports concrete: what is implemented, what check remains, and
  what is actually installed/published. Do not spend the user's usage budget on
  speculative redesigns, repeated repository surveys, or unrelated cleanup.
- Reference reassessment is an LLM editing operation. Apply its revised sections
  directly. Do not add exact/fuzzy snippet matching or a non-LLM semantic gate to
  decide whether additions/removals are acceptable. Preserve newer user edits
  and project targeting while the request is in flight.
  Reassess all related prompt surfaces together: timeline/segment prompts, image
  and artwork prompts, Global direction/intent, Unified and reference notes/
  definitions. Preserve each draft's unrelated wording independently; never copy
  Unified text over the timeline. Keep timing, order, media and settings intact.
