# Aurora 1.13.0a137 dialogue worker import repair

The audio worker receives only backend modules. The IndexTTS control helper now lives in local_dialogue_controls.py so it travels with those modules. dialogue_controls.py remains a compatible import for the UI. The next audio job picks up the repaired worker files without requiring the running application to restart.

Validation is one direct import through the isolated worker transport, repeated from the clean-installed wheel. No model weights or generation calls are needed for this missing-module fix.
