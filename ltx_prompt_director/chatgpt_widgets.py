"""Settings tab for ChatGPT plan sign-in and live model selection."""
from __future__ import annotations

import threading
import webbrowser

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QVBoxLayout, QWidget,
)

from .chatgpt_auth import ChatGPTAuthError, USAGE_URL


class _Signals(QObject):
    done = Signal(str, object)
    failed = Signal(str, str)


class ChatGPTSettingsPanel(QWidget):
    def __init__(self, auth, settings, parent=None):
        super().__init__(parent)
        self.auth = auth
        self.settings = settings
        self.signals = _Signals(self)
        self.signals.done.connect(self._finished)
        self.signals.failed.connect(self._failed)
        root = QVBoxLayout(self)
        root.addWidget(QLabel(
            "Use your ChatGPT Plus/Pro plan for eligible prompt generation. "
            "Authorization opens in your browser; no API key is required."
        ))
        form = QFormLayout()
        root.addLayout(form)
        self.account = QComboBox()
        self.account.currentIndexChanged.connect(self._select_account)
        self.connect = QPushButton("Continue with ChatGPT")
        self.connect.clicked.connect(lambda: self._start("signin", self.auth.sign_in,
                                                         self.account.currentData()))
        self.add = QPushButton("Add account")
        self.add.clicked.connect(lambda: self._start("signin", self.auth.sign_in, None))
        self.logout = QPushButton("Sign out")
        self.logout.clicked.connect(lambda: self._start("logout", self.auth.sign_out,
                                                       self.account.currentData()))
        account_row = QWidget()
        row = QHBoxLayout(account_row)
        row.setContentsMargins(0, 0, 0, 0)
        for item in (self.account, self.connect, self.add, self.logout):
            row.addWidget(item, 1 if item is self.account else 0)
        form.addRow("ChatGPT account", account_row)
        self.model = QComboBox()
        self.model.currentIndexChanged.connect(self._save_model)
        self.refresh = QPushButton("Refresh models")
        self.refresh.clicked.connect(self._refresh_models)
        model_row = QWidget()
        row = QHBoxLayout(model_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.model, 1)
        row.addWidget(self.refresh)
        form.addRow("Available model", model_row)
        self.status = QLabel()
        self.status.setWordWrap(True)
        form.addRow("Connection", self.status)
        manage = QPushButton("Manage ChatGPT plan usage")
        manage.clicked.connect(lambda: webbrowser.open(USAGE_URL))
        form.addRow("", manage)
        root.addStretch(1)
        self._populate_accounts()
        if self.auth.active_profile() and self.auth.active_profile()["connected"]:
            self._refresh_models()

    def _populate_accounts(self):
        self.account.blockSignals(True)
        self.account.clear()
        profiles = self.auth.profiles()
        if profiles:
            for profile in profiles:
                self.account.addItem(profile["email"], profile["client_id"])
            active = self.auth.active_profile()
            index = self.account.findData(active["client_id"] if active else "")
            if index >= 0:
                self.account.setCurrentIndex(index)
        else:
            self.account.addItem("Not connected", None)
        self.account.blockSignals(False)
        current = self.auth.active_profile()
        self.logout.setEnabled(bool(current and current["connected"]))
        self.status.setText(
            f"Connected: {current['email']} (ChatGPT plan usage enabled)"
            if current and current["connected"] and current["enabled"] else
            "Not connected. Sign in and grant ChatGPT plan usage to use this provider."
        )

    def _select_account(self, _index):
        client_id = self.account.currentData()
        if client_id:
            try:
                self.auth.select(client_id)
            except ChatGPTAuthError as error:
                self.status.setText(str(error))
                return
            self._refresh_models()

    def _save_model(self, _index):
        slug = self.model.currentData()
        if slug:
            self.settings.setValue("chatgpt_model", slug)

    def _start(self, kind, operation, *args):
        for control in (self.connect, self.add, self.logout, self.refresh, self.account):
            control.setEnabled(False)
        self.status.setText(
            "Waiting for ChatGPT browser sign-in…" if kind == "signin" else
            "Loading available models…" if kind == "models" else "Signing out…"
        )

        def execute():
            try:
                result = operation(*args)
                self.signals.done.emit(kind, result)
            except Exception as error:
                self.signals.failed.emit(kind, str(error))
        threading.Thread(target=execute, daemon=True).start()

    def _finished(self, kind, result):
        for control in (self.connect, self.add, self.logout, self.refresh, self.account):
            control.setEnabled(True)
        self._populate_accounts()
        if kind == "signin":
            self._refresh_models()
        elif kind == "models":
            previous = str(self.settings.value("chatgpt_model", ""))
            self.model.blockSignals(True)
            self.model.clear()
            for slug, label in result:
                self.model.addItem(label, slug)
            index = self.model.findData(previous)
            if index >= 0:
                self.model.setCurrentIndex(index)
            self.model.blockSignals(False)
            if self.model.count():
                self._save_model(self.model.currentIndex())
                self.status.setText("Connected. Model list refreshed from your ChatGPT account.")
            else:
                self.settings.remove("chatgpt_model")
                self.status.setText("This account has no listed models for ChatGPT plan usage.")
        elif kind == "logout":
            self.model.clear()
            self.settings.remove("chatgpt_model")
            if result is False:
                self.status.setText(
                    "Local sign-out completed, but remote revocation was not confirmed. "
                    "You can disconnect LTX Director in ChatGPT Settings."
                )

    def _failed(self, _kind, message):
        for control in (self.connect, self.add, self.logout, self.refresh, self.account):
            control.setEnabled(True)
        self._populate_accounts()
        self.status.setText(message)
        QMessageBox.warning(self, "ChatGPT plan connection", message)

    def _refresh_models(self):
        client_id = self.account.currentData()
        if not client_id:
            self.status.setText("Sign in with ChatGPT before refreshing models.")
            return
        self.auth.select(client_id)
        self._start("models", self.auth.list_models)
