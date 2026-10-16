# Start directing with Prompt Director.

## Your desktop workspace for AI video planning

Install Prompt Director, open a project and start arranging your sequence. Timeline editing and project organization work locally. Connect Gemini, OpenAI API, or your eligible ChatGPT plan when you want help generating or refining prompts.

![The Prompt Director startup screen](docs/images/aurora-startup.png)

## Install the current experimental build

You need **Python 3.10 or newer** and a desktop environment supported by Qt 6. A virtual environment keeps Director’s dependencies separate from other Python applications.

### Linux and macOS

```bash
python3 -m venv ~/.venvs/ltx-director
source ~/.venvs/ltx-director/bin/activate
python -m pip install --upgrade pip
python -m pip install --upgrade 'https://raw.githubusercontent.com/etoven/ltx-director-director/experimental/dist/ltx_prompt_director-1.13.0a54-py3-none-any.whl'
prompt-director
```

### Windows PowerShell

```powershell
py -m venv "$env:USERPROFILE\ltx-director-venv"
& "$env:USERPROFILE\ltx-director-venv\Scripts\Activate.ps1"
python -m pip install --upgrade pip
python -m pip install --upgrade 'https://raw.githubusercontent.com/etoven/ltx-director-director/experimental/dist/ltx_prompt_director-1.13.0a54-py3-none-any.whl'
prompt-director
```

On first launch, Prompt Director migrates preferences, custom workspaces, projects and managed media from the previous LTX Director folders. Existing files in the new folders take priority, and the original folders are retained.

The package installs Qt components, image handling and the bundled FFmpeg helper. Review-video playback also depends on codec availability on the system.

## Connect your prompt-writing provider

Open **Settings → Application** to choose **Gemini**, **OpenAI**, or **chatgpt**. Gemini and OpenAI API use their existing API keys. For ChatGPT-plan authentication, open the new **ChatGPT plan** tab, select **Continue with ChatGPT**, approve plan usage in your browser, then choose a model from your account-specific model catalog. Return to the Application tab to select the **chatgpt** provider. No API key is required for the ChatGPT-plan route. Set the request timeout, additional retry attempts and cooldown to fit your provider.

![Application Settings with provider selection, request controls, display scale and folders](docs/images/application-settings.png)

API keys can be retained for the session or stored through the app’s persistent-key option. Use persistent storage on a computer you trust. Keys are excluded from portable project exports. AI requests send the selected project context to the provider; local editing and project storage do not require an AI call.

## Set up your working space

Set a default download folder for exports and a ComfyUI working directory for LTX JSON handoff. Customize status tags and other labels. Adjust text scale from 75% to 200%, either here or from the main toolbar. Open **Workspaces** when you want to customize prompt layouts and master instructions.

On Linux, the first launch installs an application-menu shortcut for the current installation. Refresh it manually with:

```bash
prompt-director-install-desktop
```

Remove that shortcut with `prompt-director-uninstall-desktop`. Keep the virtual environment in place while its launcher is in use.

## Run from source

```bash
git clone --branch experimental https://github.com/etoven/ltx-director-director.git
cd ltx-director-director
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install .
prompt-director
```

On Windows, use `py -m venv .venv` and activate `.venv\Scripts\Activate.ps1`. Use the `main` branch for the stable line and `experimental` for Aurora development.

## When a desktop dependency needs attention

On Ubuntu or Debian, an error naming a missing XCB component can be resolved with `sudo apt install libxcb-cursor0 libxkbcommon-x11-0`. If the named missing library is `libEGL.so.1`, install `libegl1`.

For inline spelling suggestions, install an Enchant provider and a dictionary for your language. The editors remain usable without a matching dictionary.

If startup reports missing PySide6 modules, activate the environment used to run Director and reinstall the wheel with `python -m pip install --force-reinstall` followed by the wheel URL above. For a provider error, check the selected model, API key, quota and connection in Settings.

**Ready for the first sequence? [Open the complete feature and usage guide →](usage.md)**

## ChatGPT plan OAuth

The ChatGPT-plan provider is for eligible Plus/Pro accounts on locally installed open-source clients. Requests are made to the official Responses API with `store=false` and `stream=true`, using a model listed for your authenticated account. The app stores its account tokens in `~/.config/PromptDirector/chatgpt-oauth.json` (owner-only permissions on Unix), separate from project exports and QSettings API keys. You can switch accounts, refresh the live model list, sign out, and manage plan usage in ChatGPT Settings. API-key providers are unchanged. The app's workspace prompts, selected timeline references and images are sent to the selected provider when generating AI content; the ChatGPT sign-in itself does not grant access to your conversation history.

ChatGPT-plan usage is subject to your account limits and selected-model eligibility. If authorization or permissions expire, reconnect from the ChatGPT plan tab. Documentation: https://developers.openai.com/siwc/token-sharing-open-source/sign-in

## Live generation streaming

Every prompt-generation and refinement action now displays the in-progress model output. Magic Build and segment/global refinements use a two-column generation overlay; MiniMax/Generic unified prompts display a collapsible Live AI Activity panel immediately above the editable prompt. The left column shows only provider-published reasoning **summaries** when the model provides them; the right column streams generated prompt text. Both columns have an Auto-scroll control. On retries the stream resets to avoid mixing separate attempts. The finished prompt is applied to the editable project only once the provider response passes its normal validation.

Gemini is streamed through `streamGenerateContent` with `includeThoughts` when accepted. OpenAI API and ChatGPT OAuth use Responses streaming and request `reasoning.summary=auto` when supported. Non-thinking models still stream generated text; unsupported summary options automatically fall back to text-only streaming. Private reasoning and cryptographic thought signatures are never displayed.
