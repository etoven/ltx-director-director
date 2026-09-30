# Help build a better directing workspace

Director brings visual planning, prompt writing and production handoff together. Contributions should make that creative workflow clearer, more capable or easier to use.

## Choose the development line

The repository has two active branches: **main** for the stable line and **experimental** for Aurora development. Start a feature branch from the line your change targets, using its current published build as the baseline.

Use Python 3.10 or newer and a virtual environment. Install the project with `python -m pip install .`. For release packaging, install `build` and run `python -m build`.

## Keep the user’s work at the center

Preserve project media, prompt edits and timeline associations. Keep expensive media and disk work off the UI thread. Handle stale asynchronous results so they cannot replace a newer project or draft. Keep API keys out of logs and exports.

Describe changes in terms of the resulting user behavior. Keep the user documentation focused on capabilities and usage; place implementation rationale and validation in the pull request.

## Verify the change

Run the checks appropriate to the work:

```bash
python -m compileall -q ltx_prompt_director
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests
```

Use fresh XDG config, data and cache directories for isolated Linux UI tests. Qt multimedia may need normal local IPC to initialize. Review media imports, prompt editing, native projects and Director JSON handoff when your change affects those paths.

## Keep the product guide current

Update the illustrated guide that describes the changed capability. Capture the current native UI with demonstration data, keep API keys and personal project information out of screenshots, and check every image and link before publishing.

`scripts/capture_docs.py` reproduces the Aurora demonstration screenshots without provider calls. Run it with Qt available and isolated application directories. The demo prompts are locally authored examples, and the review clip is sample media rather than an AI-generated output claim.

Include the user-visible result and relevant validation in the pull request description. For a package release, verify the installed wheel as well as the source checkout.
