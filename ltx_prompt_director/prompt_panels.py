"""Independent dockable prompt documents, retaining existing AI action routes."""
from PySide6.QtCore import Qt, QTimer, QEvent
from PySide6.QtWidgets import (QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QStackedWidget, QApplication, QToolButton, QMenu)
from .panel_design import PromptSurface
from .structured_prompt_panel import StructuredPromptPanel


class DockPromptSurface(PromptSurface):
    def __init__(self, owner):
        super().__init__(owner)
        self.scope.hide()
        self.docks = {}; self.actions = {}; self.panel_workspace = None
        self.panel_available = {}; self.panel_visibility = {'global': True, 'segment': True, 'unified': True}
        for editor in (owner.intent, owner.intent_segment_prompt, owner.segment_prompt,
                       owner.unified_prompt, owner.global_prompt):
            self.editors.removeWidget(editor); editor.setParent(owner); editor.hide()
        self.structured = StructuredPromptPanel(owner)
        self.segment_stack = QStackedWidget()
        self.segment_stack.addWidget(owner.segment_prompt); self.segment_stack.addWidget(owner.intent_segment_prompt)
        self.unified_stack = QStackedWidget()
        self.unified_stack.addWidget(self.structured); self.unified_stack.addWidget(owner.unified_prompt)
        for scope, title, widget in [('global', 'Global Prompt', owner.intent),
                                      ('segment', 'Segment Prompt', self.segment_stack),
                                      ('unified', 'Unified Prompt', self.unified_stack)]:
            dock = QDockWidget(title, owner); dock.setObjectName(scope + 'PromptDock')
            dock.setMinimumWidth(250); dock.setProperty('adobePanel', True)
            panel = QWidget(); layout = QVBoxLayout(panel); layout.setContentsMargins(7, 5, 7, 5)
            bar = QHBoxLayout(); bar.addStretch()
            quick = QPushButton('Quick Fix'); quick_menu = QMenu(quick); quick.setMenu(quick_menu)
            quick_menu.aboutToShow.connect(lambda k=scope, m=quick_menu: self.populate_panel_quick(k, m))
            generate, refine = QPushButton('Generate'), QPushButton('Refine')
            for button in (quick, generate, refine):
                button.setObjectName('promptAction'); bar.addWidget(button)
            generate.clicked.connect(lambda _, k=scope: self.run_panel(k, False))
            refine.clicked.connect(lambda _, k=scope: self.run_panel(k, True))
            copy = QToolButton(); copy.setText('Copy'); bar.addWidget(copy)
            copy.clicked.connect(lambda _, k=scope: QApplication.clipboard().setText(self.panel_text(k)))
            layout.addLayout(bar)
            marquee = {'global': owner.director_ai_marquee, 'segment': owner.segment_ai_marquee,
                       'unified': owner.minimax_panel.ai_marquee}[scope]
            layout.addWidget(marquee)
            if scope == 'segment':
                editor_row=QHBoxLayout();self.segment_add=QPushButton('+');self.segment_add.setFixedWidth(30)
                self.segment_add.setToolTip('Add a blank timeline entry');self.segment_add.clicked.connect(self.structured.add_segment)
                editor_row.addWidget(self.segment_add,0,Qt.AlignmentFlag.AlignTop);editor_row.addWidget(widget,1);layout.addLayout(editor_row,1)
            else:
                layout.addWidget(widget, 1)
            dock.setWidget(panel); self.docks[scope] = dock
            self.actions[scope] = (quick, generate, refine)
            owner.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)
            dock.visibilityChanged.connect(owner.save_window_panel_state)
            dock.topLevelChanged.connect(owner.save_window_panel_state)
        owner.splitDockWidget(self.docks['global'], self.docks['segment'], Qt.Orientation.Horizontal)
        owner.splitDockWidget(self.docks['segment'], self.docks['unified'], Qt.Orientation.Horizontal)
        owner.global_prompt_dock = self.docks['global']; owner.segment_prompt_dock = self.docks['segment']
        owner.unified_prompt_dock = self.docks['unified']
        self.hide()
        for editor in (owner.intent, owner.segment_prompt, owner.intent_segment_prompt, owner.unified_prompt):
            editor.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.FocusIn and hasattr(self, 'docks'):
            scope = ('global' if watched is self.owner.intent else
                     'unified' if watched is self.owner.unified_prompt else 'segment')
            self.select(scope)
        return super().eventFilter(watched, event)

    def panel_text(self, scope):
        return (self.owner.intent.toPlainText() if scope == 'global' else
                self.owner.unified_prompt.toPlainText() if scope == 'unified' else
                self.owner.current_segment().prompt if self.owner.current_segment() else '')

    def run_panel(self, scope, refine):
        self.select(scope)
        if scope == 'unified' and not self.owner.unified_workspace:
            self.run_shared_production(refine)
        else:
            self.run(refine)

    def populate_panel_quick(self, scope, menu):
        self.select(scope)
        if scope == 'global':
            self.quick_menu = menu; self.populate_quick_fixes()
        else:
            self.owner.populate_quick_fix_menu(menu, scope)

    def select(self, scope):
        if scope == 'unified' and not self.owner.unified_workspace:
            return
        index = self.scope.findData(scope)
        if index < 0:
            return
        self.scope.blockSignals(True); self.scope.setCurrentIndex(index); self.scope.blockSignals(False)
        if scope in ('global', 'segment'):
            self.owner.prompt_scope.setCurrentIndex(self.owner.prompt_scope.findData(scope))
        self.refresh_editor()

    def scope_changed(self, *_):
        if hasattr(self, 'docks'):
            self.select(self.scope.currentData())

    def refresh_editor(self):
        if not hasattr(self, 'docks'):
            return
        owner = self.owner
        owner.intent.show()
        self.segment_add.setVisible(not owner.unified_workspace and not owner.prompt_only_workspace)
        self.segment_stack.setCurrentWidget(owner.intent_segment_prompt if owner.unified_workspace else owner.segment_prompt)
        self.unified_stack.setCurrentWidget(owner.unified_prompt if owner.prompt_only_workspace else self.structured)
        self.refresh_actions(); self.structured.schedule()

    def refresh_counter(self):
        self.refresh_actions()

    def refresh_actions(self):
        if not hasattr(self, 'actions'):
            return
        owner = self.owner; enabled = not owner.ai_busy
        for scope, (quick, generate, refine) in self.actions.items():
            generate.setVisible(not owner.prompt_only_workspace)
            generate.setEnabled(enabled and bool(owner.segments))
            refine.setEnabled(enabled and bool(self.panel_text(scope).strip()) and (bool(owner.segments) or owner.prompt_only_workspace))
            quick.setEnabled(refine.isEnabled())
        if hasattr(self, 'structured'):
            self.structured.schedule()

    def sync_workspace(self):
        owner = self.owner
        for panel in (owner.director_panel, owner.segment_panel, owner.unified_panel, owner.prompt_splitter):
            panel.hide()
        owner.prompt_scope.hide(); owner.intent_prompt_splitter.hide(); self.hide()
        if self.panel_workspace != owner.project_type:
            for scope, dock in self.docks.items():
                if self.panel_available.get(scope):
                    self.panel_visibility[scope] = not dock.isHidden()
                allowed = (owner.workspace_engine != 'shot_sequence'
                           and (not owner.prompt_only_workspace or scope == 'unified')
                           and (scope != 'unified' or owner.unified_workspace))
                dock.setVisible(allowed and self.panel_visibility.get(scope, True))
                dock.toggleViewAction().setVisible(allowed)
                dock.toggleViewAction().setEnabled(allowed)
                self.panel_available[scope] = allowed
            self.panel_workspace = owner.project_type
        for index in range(self.scope.count()):
            key = self.scope.itemData(index)
            allowed = self.panel_available.get(key, False)
            self.scope.model().item(index).setEnabled(allowed)
            self.scope.view().setRowHidden(index, not allowed)
        if not self.panel_available.get(self.scope.currentData()):
            self.select('unified' if owner.prompt_only_workspace else 'segment')
        self.refresh_editor()

    def set_collapsed(self, collapsed):
        if hasattr(self, 'docks'):
            self.docks['unified'].setVisible(not collapsed and self.owner.unified_workspace and self.owner.workspace_engine != 'shot_sequence')

    def schedule_sync(self):
        self.structured.schedule()

    def global_quick_fix(self, preset):
        previous = getattr(self.owner, '_effect_ai_choice', None)
        self.owner._effect_ai_choice = preset.get('refinement_model')
        try:
            super().global_quick_fix(preset)
        finally:
            self.owner._effect_ai_choice = previous
