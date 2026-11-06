# Feature list  
\*\*Do not burn tokens on pointless tests and checks. Just get the build out there with minimal functionality checking.  

## Segment / Global Prompts / Unified Prompt Box  
1. Need to make all the different prompt types floating panels. Remove the selector. Just make them dockable panels.  
2. Restore real time editing section panel >> Unfied output. (See notes about updated template changes first however)  

## Audio Generation  
3. New panel (not workflow) to generate dialog using a provided audio sample and indexTTS 2.5.  
  4. Provide a checkbox in the panel to do a hidden ai refinement in the background before sampling.  
  5. Add a export button (using the download manager)  
  6. Output audio player in the panel  
4. New panel (not workflow) to generate folly using latest stable audio  
  4. Provide a checkbox in the panel to do a hidden ai refinement in the background before sampling  
  5. Add a export button (using the download manager)  
  6. Output audio player in the panel.  
5. New panel (not workflow) to generate MiniMax H3 music  
  7. Provide a checkbox in the panel to do a hidden ai refinement in the background before sampling  see AI skill here. https://github.com/MiniMax-AI/MiniMax-Music3/tree/main/skills
  8. Provide comfyUI workflow export button in the topbar in the proper related section.  
  9. Add a export button (using the download manager)  
  10. Output audio player in the panel.  
6. Add all new model setups to the on boarding wizard and relevant settings pages.  

## Template Changes  
7. Workspaces should provide a template approach that conforms to model standards.  
  11. Include a template editor in the workflow configuration panel.  
    12. Workspace templates should be a sub section under “Workspaces”  
    13. Use the subsection styling from the onboarding wizzard for this section.  
    14. You type in the box and drag in “blocks” in the proper sections from the panel to the left.  
  12. The unified prompt box should conform all output  
 (ai and user generated to the template layout).  
    4. This will be the bases for offline generation modes:  
      5. Creating a new segment will add a new section  
      6. Moving a segment will move the section in the unified prompt box.  
      7. Adding a reference will add a new section.  
      8. Typing in a text prompt (assuming you have the segment prompt panel open). Will update the proper unified prompt section in real time.  
  13. Derive layouts for existing default templates.  
  14. The new base template should be easy for humans to read with proper spacing applied.  
  15. AI output from henceforth now me schema driven template variables to be filled into the proper sections of the unified output panel. The output schema will be explicitly passed to the model.  
  16. Editing will now be restricted to defined section parts. Headers will not be derived from the templates. With light shading to define editable parts.  
  17. include floating (add part) button that scrolls with you along the left side that adds a blank timeline entry (Minimax H3 or LTX) or subject definition.  
 Hitting “add” in the case of segments will also create the new segment.  
    5. Editing the the section timecode will also slide the segment panel or resize it in real time as well as update the preview if you type or edit text.  

## Other new features and bug fixes  
1. Default model to use for effects or option (any model)  
  2. For the current bundled library or if importing a model if the config doesn’t have that section then assume user picks  
2. Quick model switching in the top bar the button that shows the current model should just be a drop-down.  
  2. Also the editor hangs when switching models via the settings page.  
  3. The drop down shouldn’t do that. Its okay if the settings page creates a hang however.  
