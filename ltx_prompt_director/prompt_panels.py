"""Independent dockable prompt documents, retaining existing AI action routes."""
from PySide6.QtCore import Qt, QTimer, QEvent
from PySide6.QtWidgets import (QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QStackedWidget, QApplication, QToolButton, QMenu, QFrame, QLabel,
    QDialog, QDialogButtonBox, QPlainTextEdit)
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
        self.refinement_toasts = {}
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
            generate = QPushButton('Generate')
            generate.clicked.connect(lambda _, k=scope: self.run_panel(k, False))
            if scope == 'unified':
                refine = QToolButton()
                refine.setText('Refine')
                refine.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
                menu = QMenu(refine)
                menu.addAction('Refine with intent…', self.refine_with_intent)
                refine.setMenu(menu)
            else:
                refine = QPushButton('Refine')
            for button in (quick, generate, refine):
                button.setObjectName('promptAction'); bar.addWidget(button)
            refine.clicked.connect(lambda _, k=scope: self.run_panel(k, True))
            copy = QToolButton(); copy.setText('Copy'); bar.addWidget(copy)
            copy.clicked.connect(lambda _, k=scope: QApplication.clipboard().setText(self.panel_text(k)))
            layout.addLayout(bar)
            if scope == 'unified':
                self.reference_flag = QFrame(); self.reference_flag.setObjectName('minimaxMessagePanel')
                self.reference_flag.setProperty('level', 'warning')
                flag_layout = QHBoxLayout(self.reference_flag); flag_layout.setContentsMargins(8, 5, 8, 5)
                self.reference_flag_message = QLabel('References changed. Reassess the prompts to apply them.'); self.reference_flag_message.setWordWrap(True)
                self.reference_flag_message.setMinimumWidth(0)
                self.reference_flag_message.setObjectName('minimaxMessageBanner')
                flag_layout.addWidget(self.reference_flag_message, 1)
                self.reference_flag_button = QPushButton('Reassess')
                self.reference_flag_button.clicked.connect(self.structured.reassess_references)
                flag_layout.addWidget(self.reference_flag_button)
                layout.addWidget(self.reference_flag); self.reference_flag.hide()
            toast = QFrame(); toast.setObjectName('minimaxMessagePanel')
            toast_layout = QHBoxLayout(toast); toast_layout.setContentsMargins(8, 5, 8, 5)
            message = QLabel(); message.setObjectName('minimaxMessageBanner')
            message.setTextFormat(Qt.TextFormat.PlainText); message.setWordWrap(True); message.setMinimumWidth(0)
            retry = QPushButton('Retry')
            retry.clicked.connect(lambda _, button=retry: self.retry_refinement(button))
            toast_layout.addWidget(message, 1); toast_layout.addWidget(retry)
            timer = QTimer(toast); timer.setSingleShot(True); timer.timeout.connect(toast.hide)
            self.refinement_toasts[scope] = toast, message, retry, timer
            layout.addWidget(toast); toast.hide()
            if scope == 'unified':
                layout.addWidget(owner.minimax_panel.message_panel)
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

    def refine_with_intent(self):
        dialog = QDialog(self.docks['unified'])
        dialog.setWindowTitle('Refine with intent')
        dialog.resize(480, 260)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel('What would you like to change in the unified prompt?'))
        editor = QPlainTextEdit()
        editor.setAccessibleName('Refinement instructions')
        editor.setPlaceholderText('Describe the changes you want the model to make…')
        layout.addWidget(editor)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        submit = buttons.button(QDialogButtonBox.StandardButton.Ok)
        submit.setText('Refine')
        submit.setEnabled(False)
        editor.textChanged.connect(lambda: submit.setEnabled(bool(editor.toPlainText().strip())))
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        editor.setFocus()
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.run_panel('unified', True, editor.toPlainText().strip())

    def clear_refinement_feedback(self):
        for toast, _, retry, timer in self.refinement_toasts.values():
            timer.stop()
            retry.setProperty('refinementRetry', None)
            toast.hide()
        self.owner.minimax_panel.clear_message()

    def show_refinement_result(self, scope, text, retry_action=None):
        toast, message, retry, timer = self.refinement_toasts[scope]
        timer.stop()
        message.setText(text)
        toast.setProperty('level', 'error' if retry_action else 'success')
        toast.style().unpolish(toast); toast.style().polish(toast)
        retry.setVisible(retry_action is not None)
        retry.setProperty('refinementRetry', retry_action)
        toast.show()
        if retry_action is None:
            timer.start(5000)

    def retry_refinement(self, button):
        action = button.property('refinementRetry')
        if action and not self.owner.ai_busy:
            action()

    def run_panel(self, scope, refine, intent=''):
        self.select(scope)
        if scope == 'unified' and refine and self.owner.unified_workspace:
            if intent:
                self.owner.refine_minimax_prompt(intent)
            else:
                self.owner.refine_minimax_prompt()
            return
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
            self.refinement_toasts[scope][2].setEnabled(enabled)
        if hasattr(self, 'structured'):
            self.structured.schedule()
        self.update_reference_flag()

    def update_reference_flag(self):
        if not hasattr(self, 'reference_flag'):return
        state = self.structured.state()
        pending = state.get('pending_reference_definitions', [])
        needed = state.get('references_need_reassessment', bool(state.get('reference_changes')))
        running = self.structured.definition_running
        error = state.get('reference_definition_error', '')
        self.reference_flag.setVisible(self.owner.unified_workspace and not self.owner.prompt_only_workspace and bool(needed or pending or running or error))
        text = ('Generating reference definitions… Prompts will need reassessment.' if running else
                'Definitions need attention. Reassess to retry and update the prompts.' if error else
                'Reference definitions queued. Reassess to update the prompts.' if pending else
                'References changed. Reassess the prompts to apply them.')
        self.reference_flag_message.setText(text); self.reference_flag_message.setToolTip(error or text)
        self.reference_flag_button.setEnabled(not self.owner.ai_busy and not running)

    def sync_workspace(self):
        owner = self.owner
        availability = {scope: (owner.workspace_engine != 'shot_sequence'
                        and (not owner.prompt_only_workspace or scope == 'unified')
                        and (scope != 'unified' or owner.unified_workspace)) for scope in self.docks}
        for panel in (owner.director_panel, owner.segment_panel, owner.unified_panel, owner.prompt_splitter):
            panel.hide()
        owner.prompt_scope.hide(); owner.intent_prompt_splitter.hide(); self.hide()
        if self.panel_workspace != owner.project_type or availability != self.panel_available:
            for scope, dock in self.docks.items():
                if self.panel_available.get(scope):
                    self.panel_visibility[scope] = not dock.isHidden()
                allowed = availability[scope]
                dock.setVisible(allowed and self.panel_visibility.get(scope, True))
                dock.toggleViewAction().setVisible(allowed)
                dock.toggleViewAction().setEnabled(allowed)
                self.panel_available[scope] = allowed
            self.panel_workspace = owner.project_type
            self.clear_refinement_feedback()
        # Native saved layouts can contain panels from a different workspace.
        # Reapply availability after restoration without reopening closed docks.
        for scope, dock in self.docks.items():
            if not availability[scope]:
                dock.hide()
            dock.toggleViewAction().setVisible(availability[scope])
            dock.toggleViewAction().setEnabled(availability[scope])
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
