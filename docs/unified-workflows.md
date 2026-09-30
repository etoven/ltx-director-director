# One creative workspace. Several ways to direct.

## Choose the prompt that fits the production

Director keeps the visual timeline and writing space together whether you are developing a continuous production brief or a collection of segment prompts. Choose the approach in **Project type**, then use the same timeline, intent and refinement tools to work through the sequence.

![The continuous MiniMax production prompt and its linked timeline](images/ltx-director-director-overview.png)

| Workspace | Best suited to | What you develop |
| --- | --- | --- |
| MiniMax | Opening/closing frames, source clips or attribute references | A production brief with explicit asset roles |
| LTX Video | Individual motion beats with shared continuity | Segment prompts plus a global prompt |
| Generic | A custom prompting process | Unified or segmented prompts using your templates |

## MiniMax: direct the whole sequence

Select **MiniMax**, add your checkpoints and describe the movement between them in **Director’s Intent**. **Generate Prompt** develops a brief that can include frame or reference use, continuity, scene, timed action, sound and avoid instructions.

The **[TIMED ACTION]** section presents each range beside its editable action. The timecode column is protected during ordinary editing. Select a timeline card to focus the corresponding action; edit the action’s words or change the card’s duration to develop the same connected plan.

Choose **MiniMax** when the role of each source matters: an opening image, a destination frame, source footage or an untimed style or identity guide. Add up to two untimed images in **Reference images** and specify the attributes to draw from each one.

![MiniMax References with an untimed atmosphere image and an editable production brief](images/minimax-references.png)

**Refine Prompt** works on the complete edited brief, your inline instructions and the available references. You can also write or paste your own prompt and refine that draft. Use **Copy** to take the visible brief into your video workflow.

LTX and MiniMax retain their prompt drafts in the project. Custom workspace definitions can provide additional prompting approaches.

## LTX: give every moment its own direction

Select **LTX Video**, arrange the visual and text beats, set your creative direction and choose **Magic Build**. Director develops one prompt per segment and a global prompt for shared scene continuity.

![LTX segment editing with Magic Build, output dimensions and focused refinement](images/ltx-workspace.png)

Select a card to edit that segment’s prompt. Use the prompt-scope selector to open **Global** in the same editor and describe the setting, lighting, quality or continuity that should apply throughout.

**Refine Prompt** improves the selected beat while considering neighboring frames and prompts. It may adjust that beat’s duration when the action or dialogue needs more room. **Refine Timing** adjusts the selected duration while preserving its wording. Other segment durations remain fixed during these focused refinements, and the selected beat can grow beyond the requested sequence target when necessary.

Set the output width and height above the timeline. Director normalizes them to 32-pixel increments for export. When the plan is ready, **Export** creates LTX Director JSON for your ComfyUI workflow.

## Keep the creative throughline

**Director’s Intent**, total length and prompt options remain available across modes. Collapsing the intent panel preserves your choices. Generation and refinement happen when you request them; preparing media or switching workspaces does not submit an AI request.

You can keep writing in the MiniMax editor while a request runs. If you change the project, prompt or references in a way that makes that response obsolete, Director protects the current draft from being overwritten by the outdated result.

Projects retain their workspace and saved drafts. **Save to Library** commits the current work; **Export Project** makes a portable copy. For a personalized layout and prompting style, create a [custom workspace](workspace-definitions-and-exports.md).

**Continue:** [Refinement and audio](refinement-and-audio.md) · [Reference images](minimax-references.md) · [Complete feature guide](../usage.md)
