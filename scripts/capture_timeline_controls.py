"""Capture the timeline navigation with local demo media and isolated settings."""
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

with tempfile.TemporaryDirectory(prefix="director-timeline-docs-") as folder:
    for kind in ("CONFIG", "DATA", "CACHE"):
        os.environ[f"XDG_{kind}_HOME"] = str(Path(folder) / kind.lower())
    from PySide6.QtCore import QPoint, QRect
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication
    from ltx_prompt_director import ui
    from ltx_prompt_director.models import Segment
    from ltx_prompt_director.workspaces import WorkspaceStore

    app = QApplication([])
    app.setOrganizationName("PromptDirectorDocs")
    app.setApplicationName("TimelineControls")
    app.setStyle("Fusion")
    app.setFont(QFont("DejaVu Sans", 10))
    store = WorkspaceStore(Path(folder) / "workspaces")
    with patch.object(ui, "WorkspaceStore", return_value=store), patch.object(ui.MainWindow, "restore_startup_workspace"):
        window = ui.MainWindow()
    for dock in window.findChildren(ui.QDockWidget):
        dock.hide()
    source = str(ROOT / "ltx_prompt_director/assets/splash-aurora-background.png")
    window.set_project_type("minimax_official_skill_reference")
    window.segments = [
        Segment("Opening", source, source, "image", "start", "Establish the aurora-lit mountains.", 3),
        Segment("Approach", "", "", "text", "text", "Follow the traveler toward the overlook.", 6),
        Segment("Horizon", source, source, "image", "end", "Hold on the quiet horizon.", 3),
    ]
    window.refresh_timeline(1)
    window.split_timeline_segment(window.segments[1].id, 3)
    window.resize(1300, 850)
    window.show()
    for _ in range(5):
        app.processEvents()
    window.autofit_timeline()
    app.processEvents()
    top = window.timeline_shell.mapTo(window, QPoint(0, 0))
    bottom = window.conditioning_guide.mapTo(window, QPoint(window.conditioning_guide.width(), window.conditioning_guide.height()))
    target = ROOT / "docs/images/timeline-navigation.png"
    assert window.grab(QRect(top, bottom)).save(str(target))
    print(target, flush=True)
    window.project_dirty = False
    window._close_saves_queued = True
    window.close()
    # Bound native multimedia cleanup just as in the GUI regression runner.
    sys.stdout.flush()
    os._exit(0)
