# Aurora a105: wizard control sizing and spacing

The setup wizard uses the supplied reference proportions for panel margins, typography, sidebar rows, form spacing and button heights. Engine numeric controls use explicit minus/value/plus steppers while retaining their original ranges and keyboard editing. Automatic/manual cards and the Getting Started cards have consistent padding and vertical alignment. Other Settings controls and existing setup choices remain available.

Roboto Condensed is bundled with its OFL license from the official Google Fonts repository, so the wizard does not depend on a matching system font. Original catalog artwork and private design references remain unchanged.

Validation: native wizard and local-generation UI checks cover stepper bounds, setup callbacks, navigation and existing choices. Actual native captures were compared with the supplied references and included in the illustrated help. The installed wheel is checked separately before publication. This build does not run GPU inference.
