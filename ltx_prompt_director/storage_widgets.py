"""Cache usage and complete setup backup controls for Application Settings."""
from pathlib import Path
from PySide6.QtCore import Qt,QTimer,QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QGroupBox,QLabel,QPushButton,QFileDialog,QMessageBox
from .storage_management import (setup_roots,cache_roots,disk_usage,clear_cache,
                                 settings_snapshot,export_setup,stage_import,stage_reset)


def size_text(value):
    for unit in ('B','KiB','MiB','GiB','TiB'):
        if value<1024 or unit=='TiB':return f'{value:.1f} {unit}'
        value/=1024


class StorageSettingsPanel(QWidget):
    def __init__(self,owner,parent=None):
        super().__init__(parent);self.owner=owner;self.busy=False
        layout=QVBoxLayout(self)
        group=QGroupBox('Cache');box=QVBoxLayout(group)
        self.usage=QLabel('Calculating cache usage…');self.usage.setObjectName('cacheDiskUsage')
        self.usage.setStyleSheet('font-size: 16px; padding: 12px; border: 1px solid #50606b; border-radius: 5px;')
        self.usage.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.addWidget(self.usage)
        self.location=QLabel(str(cache_roots()[0]));self.location.setWordWrap(True);box.addWidget(self.location)
        row=QHBoxLayout()
        self.refresh_button=QPushButton('Refresh usage');self.refresh_button.clicked.connect(self.refresh_usage)
        self.clear_button=QPushButton('Clear unused cache');self.clear_button.clicked.connect(self.clear_unused)
        open_button=QPushButton('Open cache folder');open_button.clicked.connect(lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(cache_roots()[0]))))
        for button in (self.refresh_button,self.clear_button,open_button):row.addWidget(button)
        box.addLayout(row);box.addWidget(QLabel('Projects and managed media are kept. Files used by open projects remain available.'))
        layout.addWidget(group)
        backup=QGroupBox('Complete settings and project backup');content=QVBoxLayout(backup)
        info=QLabel('Export the complete configuration and project-data folders as a ZIP, including saved preferences, workspaces, projects, catalog data and managed media. Cache files are excluded. Imports apply on the next launch.')
        info.setWordWrap(True);content.addWidget(info)
        row=QHBoxLayout()
        self.export_button=QPushButton('Export complete setup…');self.export_button.clicked.connect(self.export_all)
        self.import_button=QPushButton('Import complete setup…');self.import_button.clicked.connect(self.import_all)
        row.addWidget(self.export_button);row.addWidget(self.import_button);content.addLayout(row)
        self.reset_button=QPushButton('Reset settings…');self.reset_button.clicked.connect(self.reset_settings)
        content.addWidget(self.reset_button);layout.addWidget(backup)
        self.message=QLabel('');self.message.setWordWrap(True);layout.addWidget(self.message);layout.addStretch()
        self._timer=QTimer(self);self._timer.setSingleShot(True);self._timer.setInterval(80)
        self.refresh_usage()

    def set_busy(self,busy):
        self.busy=busy
        for button in (self.refresh_button,self.clear_button,self.export_button,self.import_button,self.reset_button):button.setEnabled(not busy)

    def error(self,message):
        self.set_busy(False);self.message.setText(message)
        QMessageBox.warning(self,'Storage and backups',message)

    def refresh_usage(self):
        if self.busy:return
        self.set_busy(True)
        def ready(value):
            self.set_busy(False)
            self.usage.setText(f"Cache: {size_text(value['bytes'])} · {value['files']} files\nFree disk space: {size_text(value['free'])}")
        self.owner.start_disk_job(disk_usage,(cache_roots(),),ready,self.error)

    def clear_unused(self):
        if self.busy:return
        self.set_busy(True);self.message.setText('Clearing unused cache…')
        panel=self.owner.shot_sequence_panel;panel.deactivate()
        def wait():
            if panel._worker_running or self.owner._pending_disk_jobs:
                self._timer.singleShot(80,wait);return
            protected=[]
            states=[self.owner.capture_workspace_state()]+[session.get('state',{}) for session in self.owner.project_sessions.values()]
            def protect(value):
                if hasattr(value,'media_path'):protected.extend((value.media_path,value.preview_path))
                elif isinstance(value,dict):
                    for item in value.values():protect(item)
                elif isinstance(value,(list,tuple)):
                    for item in value:protect(item)
                elif isinstance(value,str) and Path(value).is_absolute():protected.append(value)
            for state in states:protect(state)
            def ready(value):
                from .shot_preview_cache import PreviewCache
                panel.cache=PreviewCache()
                panel.refresh();panel._build_timer.stop()
                self.set_busy(False);self.message.setText(f'Cleared {size_text(value)} of unused cache. Open-project files were kept.')
                self.refresh_usage()
            self.owner.start_disk_job(clear_cache,(cache_roots(),protected),ready,self.error,serialized=True)
        wait()

    def export_all(self):
        if self.busy:return
        path,_=QFileDialog.getSaveFileName(self,'Export complete Prompt Director setup','Prompt-Director-Setup.zip','ZIP backup (*.zip)')
        if not path:return
        if not path.lower().endswith('.zip'):path+='.zip'
        self.set_busy(True);self.message.setText('Saving open projects and preparing the ZIP backup…')
        closing_flag=self.owner._close_saves_queued
        self.owner.queue_closing_projects();self.owner._close_saves_queued=closing_flag
        def wait():
            if self.owner._pending_disk_jobs:
                self._timer.singleShot(80,wait);return
            if self.owner.project_dirty or any(session.get('dirty') for session in self.owner.project_sessions.values()):
                self.error('An open project could not be saved. Resolve the save error before exporting a complete backup.');return
            values=settings_snapshot(self.owner.settings)
            def ready(_):self.set_busy(False);self.message.setText(f'Complete setup exported: {path}')
            self.owner.start_disk_job(export_setup,(path,setup_roots(),values),ready,self.error,serialized=True)
        wait()

    def import_all(self):
        if self.busy:return
        path,_=QFileDialog.getOpenFileName(self,'Import complete Prompt Director setup','','ZIP backup (*.zip)')
        if not path:return
        self.set_busy(True);self.message.setText('Checking and preparing the backup…')
        def ready(_):
            self.set_busy(False);self.message.setText('Backup prepared. Close and reopen Prompt Director to restore its saved settings and project files.')
        self.owner.start_disk_job(stage_import,(path,setup_roots()['data']),ready,self.error,serialized=True)

    def reset_settings(self):
        if self.busy:return
        answer=QMessageBox.question(self,'Reset Prompt Director settings',
            'Reset all application preferences, stored API keys and workspace definitions to their defaults on the next launch?\n\nSaved projects, collections and managed media will be kept.',
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)
        if answer!=QMessageBox.StandardButton.Yes:return
        self.set_busy(True)
        def ready(_):self.set_busy(False);self.message.setText('Reset prepared. Close and reopen Prompt Director to apply the default settings.')
        self.owner.start_disk_job(stage_reset,(setup_roots()['data'],),ready,self.error,serialized=True)
