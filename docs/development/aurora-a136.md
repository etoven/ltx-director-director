# Aurora 1.13.0a136 unused centre space

When all open panels are docked at the sides, the zero-height centre anchor no longer reserves a middle column. It collapses to zero width and stays hidden. Top/bottom panel layouts retain their centre-column sizing. Dock visibility and location changes update this automatically.

One focused Wayland visual check used a private copy of the saved profile with only side docks open. The centre anchor measured zero width and the side panels filled the available area; startup remained healthy. The a135 startup repair is retained.
