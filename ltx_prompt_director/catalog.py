"""Application-wide media catalog with native file URL interchange."""
from __future__ import annotations

import json
import shutil
from collections import deque
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QEvent, Qt, QSize, QMimeData, QUrl, Signal, QObject, QRunnable, QThreadPool, QStandardPaths
from PySide6.QtGui import QDrag, QIcon, QDesktopServices, QPixmap, QPainter, QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
    QTreeWidget, QTreeWidgetItem, QListWidget, QListWidgetItem, QAbstractItemView,
    QSplitter, QMenu, QInputDialog, QFileDialog, QDialog, QFormLayout, QTextEdit,
    QDialogButtonBox, QMessageBox, QStyle, QLabel, QSizePolicy)
from .media import prepare_media, TIMELINE_VIDEO_SUFFIXES
from .media_labels import add_thumbnail_labels

MEDIA_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'} | TIMELINE_VIDEO_SUFFIXES
ROLE = Qt.ItemDataRole.UserRole


class CatalogStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)) / 'media-catalog.json'
        self.media_root = self.path.parent / 'media'
        self.entries = []
        self.folders = []
        if self.path.exists():
            value = json.loads(self.path.read_text(encoding='utf-8'))
            self.entries = value.get('entries', [])
            self.folders = value.get('folders', [])

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'schema_version': 1, 'entries': self.entries, 'folders': self.folders}, indent=2), encoding='utf-8')
        temporary.replace(self.path)

    def add(self, paths, folder=''):
        added = []
        for path in paths:
            source = Path(path).resolve()
            if not source.is_file() or source.suffix.lower() not in MEDIA_SUFFIXES:
                continue
            entry = next((e for e in self.entries if e['path'] == str(source)
                          or e.get('source_path') == str(source)), None)
            entry_id = entry['id'] if entry else uuid4().hex
            current = Path(entry['path']) if entry else None
            # Dragging an owned file between catalog folders reuses its copy.
            if current and current.is_file() and current.is_relative_to(self.media_root.resolve()):
                destination = current
            else:
                destination = self.media_root.resolve() / entry_id / source.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_name(destination.name + '.' + uuid4().hex + '.tmp')
                try:
                    shutil.copy2(source, temporary)
                    temporary.replace(destination)
                finally:
                    temporary.unlink(missing_ok=True)
            if entry:
                entry.update(path=str(destination), folder=folder)
                entry.setdefault('source_path', str(source))
            else:
                entry = {'id': entry_id, 'path': str(destination), 'source_path': str(source),
                         'name': source.name, 'folder': folder, 'tags': [], 'description': ''}
                self.entries.append(entry)
            added.append(entry)
        self.save()
        return added

    def move(self, paths, folder):
        resolved = {str(Path(p).resolve()) for p in paths}
        for entry in self.entries:
            if entry['path'] in resolved or entry.get('source_path') in resolved:
                entry['folder'] = folder
        self.save()


class ImportSignals(QObject):
    ready = Signal(str)


class ImportJob(QRunnable):
    def __init__(self, store, paths, folder):
        super().__init__()
        self.store, self.paths, self.folder = store, paths, folder
        self.signals = ImportSignals()

    def run(self):
        error = ''
        try:
            import_catalog_paths(self.store, self.paths, self.folder)
        except Exception as failure:
            error = str(failure)
            try:
                self.store.save()
            except OSError:
                pass
        self.signals.ready.emit(error)


def import_catalog_paths(store, paths, folder):
    expanded = []
    for path in paths:
        source = Path(path)
        if source.is_dir():
            prefix = '/'.join(filter(None, [folder, source.name]))
            if prefix not in store.folders:
                store.folders.append(prefix)
            # Snapshot before copying: a source folder can contain the app's
            # working directory, whose managed copies must not recurse back in.
            files = [file for file in source.rglob('*')
                     if file.is_file() and file.suffix.lower() in MEDIA_SUFFIXES
                     and not file.resolve().is_relative_to(store.media_root.resolve())]
            for file in files:
                if file.is_file():
                    relative = file.parent.relative_to(source).as_posix()
                    destination = prefix if relative == '.' else prefix + '/' + relative
                    parts = destination.split('/')
                    for index in range(1, len(parts) + 1):
                        name = '/'.join(parts[:index])
                        if name not in store.folders:
                            store.folders.append(name)
                    store.add([str(file)], destination)
        else:
            expanded.append(path)
    store.add(expanded, folder)


class PreviewSignals(QObject):
    ready = Signal(str, str)


class PreviewJob(QRunnable):
    def __init__(self, entry_id, path):
        super().__init__()
        self.entry_id, self.path = entry_id, path
        self.signals = PreviewSignals()

    def run(self):
        try:
            _, preview, _, _ = prepare_media(self.path)
        except Exception:
            preview = ''
        self.signals.ready.emit(self.entry_id, preview)


def local_paths(mime):
    return [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]


class CatalogTiles(QListWidget):
    dropped = Signal(list)
    rename_requested = Signal()
    remove_requested = Signal()
    def __init__(self):
        super().__init__()
        self.setObjectName('catalogTiles')
        self.setMouseTracking(True)
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Static)
        self.setIconSize(QSize(156, 112))
        self.setGridSize(QSize(184, 160))
        self.setWordWrap(True)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSpacing(8)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_F2:
            self.rename_requested.emit()
            event.accept()
        elif event.key() == Qt.Key.Key_Delete:
            self.remove_requested.emit()
            event.accept()
        else:
            super().keyPressEvent(event)

    def startDrag(self, actions):
        paths = [item.data(ROLE)['path'] for item in self.selectedItems() if Path(item.data(ROLE)['path']).is_file()]
        if paths:
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(path) for path in paths])
            drag = QDrag(self)
            drag.setMimeData(mime)
            drag.exec(Qt.DropAction.CopyAction)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        self.dropped.emit(local_paths(event.mimeData()))
        event.acceptProposedAction()


class FolderTree(QTreeWidget):
    dropped = Signal(list, str)
    def __init__(self):
        super().__init__()
        self.setHeaderHidden(True)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        if item:
            self.dropped.emit(local_paths(event.mimeData()), item.data(0, ROLE) or '')
            event.acceptProposedAction()


class MediaCatalog(QWidget):
    add_to_timeline = Signal(list)
    view_requested = Signal(dict)
    def __init__(self, parent=None, store=None):
        super().__init__(parent)
        self.setObjectName("mediaCatalogPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setProperty("dropActive", False)
        self.store = store or CatalogStore()
        self.current_folder = None
        self.import_queue = deque()
        self.import_busy = False
        self.previews, self.pending = {}, set()
        layout = QVBoxLayout(self)
        tools = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search names, tags and descriptions…')
        tools.addWidget(self.search, 1)
        add = QPushButton('Import media')
        add.clicked.connect(self.import_media)
        tools.addWidget(add)
        folder = QPushButton('New folder')
        folder.clicked.connect(self.new_folder)
        tools.addWidget(folder)
        layout.addLayout(tools)
        split = QSplitter()
        self.folders = FolderTree()
        self.tiles = CatalogTiles()
        split.addWidget(self.folders)
        split.addWidget(self.tiles)
        split.setStretchFactor(1, 1)
        split.setSizes([160, 480])
        layout.addWidget(split, 1)
        self.import_status = QLabel()
        self.import_status.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.import_status.hide()
        layout.addWidget(self.import_status)
        self.description = QTextEdit()
        self.description.setReadOnly(True)
        self.description.setMaximumHeight(80)
        self.description.setPlaceholderText('Select a tile to see its description. Right-click to edit.')
        layout.addWidget(self.description)
        self.search.textChanged.connect(self.refresh_tiles)
        self.folders.currentItemChanged.connect(self.select_folder)
        self.folders.dropped.connect(self.drop_into_folder)
        self.tiles.dropped.connect(lambda paths: self.import_paths(paths, self.current_folder or ''))
        self.tiles.rename_requested.connect(self.rename_selected)
        self.tiles.remove_requested.connect(self.remove_selected)
        self.tiles.itemChanged.connect(self.save_inline_name)
        self.tiles.itemSelectionChanged.connect(self.show_description)
        self.tiles.itemDoubleClicked.connect(lambda item: self.view_requested.emit(item.data(ROLE)))
        self.tiles.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tiles.customContextMenuRequested.connect(self.context_menu)
        self.folders.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.folders.customContextMenuRequested.connect(self.folder_menu)
        self.refresh_folders()
        self.refresh_tiles()
        # Native drag events land on the child under the pointer, including
        # scroll-area viewports and text boxes. Route every surface through
        # the catalog rather than letting a child consume file URLs as text.
        self.setAcceptDrops(True)
        for widget in self.findChildren(QWidget):
            widget.setAcceptDrops(True)
            widget.installEventFilter(self)

    def accepts_files(self, mime):
        return any(Path(path).is_dir() or
                   (Path(path).is_file() and Path(path).suffix.lower() in MEDIA_SUFFIXES)
                   for path in local_paths(mime))

    def handle_file_event(self, watched, event):
        if not self.accepts_files(event.mimeData()):
            self.set_drop_active(False)
            event.ignore()
            return True
        self.set_drop_active(event.type() != QEvent.Type.Drop)
        if event.type() == QEvent.Type.Drop:
            folder = self.current_folder or ''
            if watched is self.folders.viewport():
                item = self.folders.itemAt(event.position().toPoint())
                if item:
                    folder = item.data(0, ROLE) or ''
            self.import_paths(local_paths(event.mimeData()), folder)
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        return True

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.DragLeave:
            self.set_drop_active(False)
        if event.type() in {QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop}:
            return self.handle_file_event(watched, event)
        return super().eventFilter(watched, event)

    def set_drop_active(self, active):
        self.setProperty('dropActive', bool(active))
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def dragLeaveEvent(self, event):
        self.set_drop_active(False)
        super().dragLeaveEvent(event)

    def dragEnterEvent(self, event):
        self.handle_file_event(self, event)

    def dragMoveEvent(self, event):
        self.handle_file_event(self, event)

    def dropEvent(self, event):
        self.handle_file_event(self, event)

    def refresh_folders(self):
        self.folders.blockSignals(True)
        self.folders.clear()
        items = {}
        for label, key in [('All media', None), ('Unfiled', '')]:
            item = QTreeWidgetItem([label])
            item.setData(0, ROLE, key)
            self.folders.addTopLevelItem(item)
        for folder in sorted(self.store.folders):
            parent = items.get(folder.rpartition('/')[0])
            item = QTreeWidgetItem([folder.rsplit('/', 1)[-1]])
            item.setData(0, ROLE, folder)
            if parent:
                parent.addChild(item)
            else:
                self.folders.addTopLevelItem(item)
            items[folder] = item
        self.folders.expandAll()
        chosen = items.get(self.current_folder)
        if chosen is None:
            chosen = self.folders.topLevelItem(1 if self.current_folder == '' else 0)
        self.folders.setCurrentItem(chosen)
        self.folders.blockSignals(False)

    def select_folder(self, item, previous):
        self.current_folder = item.data(0, ROLE) if item else None
        self.refresh_tiles()

    def refresh_tiles(self):
        self.tiles.clear()
        query = self.search.text().strip().casefold()
        for entry in self.store.entries:
            if self.current_folder is not None and entry['folder'] != self.current_folder:
                continue
            if query and query not in ' '.join([entry['name'], entry['description'], *entry['tags']]).casefold():
                continue
            icon = self.tile_icon(entry)
            missing = not Path(entry['path']).is_file()
            item = QListWidgetItem(icon, entry['name'])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            item.setData(ROLE, entry)
            item.setToolTip('\n'.join([entry['path'], ', '.join(entry['tags']), entry['description']]))
            self.tiles.addItem(item)
            if not missing and entry['id'] not in self.pending and entry['id'] not in self.previews:
                self.pending.add(entry['id'])
                job = PreviewJob(entry['id'], entry['path'])
                job.signals.ready.connect(self.preview_ready)
                QThreadPool.globalInstance().start(job)

    def tile_icon(self, entry):
        preview = self.previews.get(entry['id'])
        source = QPixmap(preview) if preview else self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon).pixmap(64, 64)
        canvas = QPixmap(156, 112)
        canvas.fill(QColor('#172127'))
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if not source.isNull():
            source = source.scaled(canvas.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            painter.drawPixmap((canvas.width() - source.width()) // 2, (canvas.height() - source.height()) // 2, source)
        painter.end()
        tags = entry.get('tags', [])
        shown = tags if len(tags) <= 4 else [*tags[:3], f'+{len(tags) - 3}']
        return QIcon(add_thumbnail_labels(canvas, [(tag, '#567a94') for tag in shown]))

    def preview_ready(self, entry_id, preview):
        self.pending.discard(entry_id)
        self.previews[entry_id] = preview
        for index in range(self.tiles.count()):
            item = self.tiles.item(index)
            if item.data(ROLE)['id'] == entry_id and preview:
                item.setIcon(self.tile_icon(item.data(ROLE)))

    def show_description(self):
        selected = self.tiles.selectedItems()
        self.description.setPlainText(selected[0].data(ROLE)['description'] if len(selected) == 1 else f'{len(selected)} items selected' if selected else '')

    def import_media(self):
        paths, _ = QFileDialog.getOpenFileNames(self, 'Import catalog media', '', 'Images and videos (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff *.mp4 *.webm)')
        if paths:
            self.import_paths(paths, self.current_folder or '')

    def import_paths(self, paths, folder):
        self.import_queue.append((list(paths), folder))
        self.start_next_import()

    def start_next_import(self):
        if self.import_busy or not self.import_queue:
            return
        paths, folder = self.import_queue.popleft()
        self.import_busy = True
        self.import_status.setText('Copying media into the working folder…')
        self.import_status.show()
        self.setEnabled(False)
        job = ImportJob(self.store, paths, folder)
        job.signals.ready.connect(self.import_finished)
        QThreadPool.globalInstance().start(job)

    def import_finished(self, error):
        self.import_busy = False
        self.setEnabled(True)
        self.import_status.setText('')
        self.import_status.hide()
        self.refresh_folders()
        self.refresh_tiles()
        if error:
            QMessageBox.warning(self, 'Media import failed', f'Could not copy media into the working folder:\n{error}')
        self.start_next_import()

    def drop_into_folder(self, paths, folder):
        self.import_paths(paths, folder)

    def new_folder(self):
        name, ok = QInputDialog.getText(self, 'New catalog folder', 'Folder name:')
        if ok and name.strip() and '/' not in name and name.strip() not in {'.', '..'}:
            folder = '/'.join(filter(None, [self.current_folder, name.strip()]))
            if folder not in self.store.folders:
                self.store.folders.append(folder)
                self.store.save()
                self.refresh_folders()

    def folder_menu(self, point):
        item = self.folders.itemAt(point)
        if not item or not item.data(0, ROLE):
            return
        folder = item.data(0, ROLE)
        menu = QMenu(self)
        rename = menu.addAction('Rename folder')
        remove = menu.addAction('Remove folder (keep media)')
        action = menu.exec(self.folders.mapToGlobal(point))
        if action == rename:
            name, ok = QInputDialog.getText(self, 'Rename folder', 'Folder name:', text=folder.rsplit('/', 1)[-1])
            if not ok or not name.strip() or '/' in name or name.strip() in {'.', '..'}:
                return
            destination = '/'.join(filter(None, [folder.rpartition('/')[0], name.strip()]))
            if destination in self.store.folders:
                return
            mapping = {f: destination + f[len(folder):] for f in self.store.folders if f == folder or f.startswith(folder + '/')}
            self.store.folders = [mapping.get(f, f) for f in self.store.folders]
            for entry in self.store.entries:
                entry['folder'] = mapping.get(entry['folder'], entry['folder'])
        elif action == remove:
            removed = {f for f in self.store.folders if f == folder or f.startswith(folder + '/')}
            self.store.folders = [f for f in self.store.folders if f not in removed]
            for entry in self.store.entries:
                if entry['folder'] in removed:
                    entry['folder'] = ''
        else:
            return
        self.current_folder = None
        self.store.save()
        self.refresh_folders()
        self.refresh_tiles()

    def context_menu(self, point):
        item = self.tiles.itemAt(point)
        if item and not item.isSelected():
            self.tiles.setCurrentItem(item)
        entries = [item.data(ROLE) for item in self.tiles.selectedItems()]
        if not entries:
            return
        menu = QMenu(self)
        menu.addAction('Add to timeline', lambda: self.add_to_timeline.emit([e['path'] for e in entries if Path(e['path']).is_file()]))
        if len(entries) == 1:
            menu.addAction('View media', lambda: self.view_requested.emit(entries[0]))
            menu.addAction('Rename (F2)', lambda: self.rename_entries(entries))
        menu.addAction('Edit details…', lambda: self.edit_details(entries))
        move = menu.addMenu('Move to folder')
        for folder in ['', *sorted(self.store.folders)]:
            move.addAction(folder or 'Unfiled', lambda checked=False, f=folder: self.move_entries(entries, f))
        menu.addAction('Open containing folder', lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(entries[0]['path']).parent))))
        menu.addAction('Remove from catalog (Delete)', lambda: self.remove_entries(entries))
        menu.exec(self.tiles.mapToGlobal(point))

    def move_entries(self, entries, folder):
        self.store.move([e['path'] for e in entries], folder)
        self.refresh_tiles()

    def rename_selected(self):
        self.rename_entries([item.data(ROLE) for item in self.tiles.selectedItems()])

    def rename_entries(self, entries):
        if len(entries) != 1:
            return
        item = next((self.tiles.item(i) for i in range(self.tiles.count())
                     if self.tiles.item(i).data(ROLE)['id'] == entries[0]['id']), None)
        if item:
            self.tiles.setCurrentItem(item)
            self.tiles.editItem(item)

    def save_inline_name(self, item):
        data = item.data(ROLE)
        if not isinstance(data, dict):
            return
        entry = next((entry for entry in self.store.entries if entry['id'] == data['id']), None)
        if not entry or item.text() == entry['name']:
            return
        name = item.text().strip()
        self.tiles.blockSignals(True)
        try:
            if name:
                entry['name'] = name
                self.store.save()
                item.setData(ROLE, entry)
            item.setText(entry['name'])
        finally:
            self.tiles.blockSignals(False)

    def remove_selected(self):
        self.remove_entries([item.data(ROLE) for item in self.tiles.selectedItems()])

    def remove_entries(self, entries):
        if not entries:
            return
        label = entries[0]['name'] if len(entries) == 1 else f'{len(entries)} selected items'
        answer = QMessageBox.question(self, 'Remove media from catalog',
                                      f'Remove {label} from the catalog?\nMedia files will remain in the working folder.',
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        ids = {e['id'] for e in entries}
        self.store.entries = [e for e in self.store.entries if e['id'] not in ids]
        self.store.save()
        self.refresh_tiles()

    def edit_details(self, entries):
        # Qt item data returns detached dictionaries; edits must target the
        # authoritative entries, resolved by their stable catalog IDs.
        ids = {entry['id'] for entry in entries}
        entries = [entry for entry in self.store.entries if entry['id'] in ids]
        if not entries:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('Edit media details' if len(entries) == 1 else 'Edit selected media')
        form = QFormLayout(dialog)
        name = QLineEdit(entries[0]['name'])
        if len(entries) == 1:
            form.addRow('Display name', name)
        tags = QLineEdit(', '.join(entries[0]['tags']) if len(entries) == 1 else '')
        tags.setPlaceholderText('Comma-separated tags; blank keeps current tags for multiple items')
        form.addRow('Tags', tags)
        description = QTextEdit()
        description.setPlainText(entries[0]['description'] if len(entries) == 1 else '')
        description.setPlaceholderText('Short description; blank keeps current descriptions for multiple items')
        form.addRow('Description', description)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            for entry in entries:
                if len(entries) == 1:
                    entry['name'] = name.text().strip() or Path(entry['path']).name
                if len(entries) == 1 or tags.text().strip():
                    entry['tags'] = list(dict.fromkeys(t.strip() for t in tags.text().split(',') if t.strip()))
                if len(entries) == 1 or description.toPlainText().strip():
                    entry['description'] = description.toPlainText().strip()
            self.store.save()
            self.refresh_tiles()
            for index in range(self.tiles.count()):
                item = self.tiles.item(index)
                if item.data(ROLE)['id'] in ids:
                    item.setSelected(True)
            self.show_description()
