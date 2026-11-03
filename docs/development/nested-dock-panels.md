# Nested dock panels and dark desktop styling

Based on Aurora `1.13.0a131`. The dock layout uses Qt's existing dock groups,
with visible top tabs, rather than a second set of panel widgets. The icon rails
remain available when expanded. Collapsing a column records its open tabs and
each group's active member, so reopening it after a restart restores the native
groups. Existing saved dock positions continue to load by their object names.

Visual references: Adobe's [panel grouping](https://helpx.adobe.com/photoshop/desktop/get-started/learn-the-basics/manipulate-panel-groups.html),
[collapse-to-icons interaction](https://helpx.adobe.com/photoshop/desktop/get-started/learn-the-basics/collapse-expand-icons.html),
and [Spectrum color hierarchy](https://spectrum.adobe.com/page/color-system/).
The dock chrome uses neutral layers, full tab names with overflow scrolling,
and a restrained blue focus accent. Existing project status colors retain their
meaning.

Capture the actual interface with `python scripts/capture_panel_docs.py
--dock-groups`. The script uses isolated settings and an authored demonstration
project. See the [expanded](../images/dock-tabs-expanded.png) and
[collapsed](../images/dock-tabs-collapsed.png) states.

Validation: all 17 isolated workspace chrome checks passed. New native tests
exercise grouping through the panel menu, switching tabs, closing one member,
restoring the column and its width, scaling text, and reopening two stacked
groups with their selected tabs after restart. Both new tests passed again after
leaving tab visibility to Qt's dock layout. The bundled illustrated manual was
rebuilt from the updated guide.
