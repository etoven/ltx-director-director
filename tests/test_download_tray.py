import time
from PySide6.QtCore import QSettings, QEvent, Qt, QPoint
from PySide6.QtGui import QImage, QColor
from PySide6.QtWidgets import QApplication, QWidget, QPushButton, QHBoxLayout
from PySide6.QtTest import QTest
from ltx_prompt_director.downloads import DownloadTray


def test_download_tray_remains_below_and_right_aligned_when_window_moves(tmp_path):
    app = QApplication.instance() or QApplication([])
    owner = QWidget(); owner.resize(650, 250)
    layout = QHBoxLayout(owner); layout.setAlignment(Qt.AlignmentFlag.AlignTop)
    layout.addStretch(); owner.downloads_button = QPushButton('Downloads', owner); layout.addWidget(owner.downloads_button)
    tray = DownloadTray(QSettings(str(tmp_path / 'anchor.ini'), QSettings.Format.IniFormat), owner)
    owner.show(); tray.show()
    try:
        for x,y,width in ((20,30,650),(90,70,650),(90,70,740),(-100,60,600)):
            owner.move(x,y); owner.resize(width,250)
            QTest.qWait(30); app.processEvents()
            edge = owner.downloads_button.mapToGlobal(QPoint(owner.downloads_button.width(), owner.downloads_button.height()))
            assert tray.x() + tray.width() == edge.x()
            assert tray.y() == edge.y() + 6
            assert tray.isVisible()
        tray.hide(); owner.move(50,50); tray.move(0,0); tray.show()
        QTest.qWait(30)
        edge = owner.downloads_button.mapToGlobal(QPoint(owner.downloads_button.width(), owner.downloads_button.height()))
        assert tray.x() + tray.width() == edge.x() and tray.y() == edge.y() + 6
    finally:
        tray.close(); owner.close()


def test_download_focus_sweep_and_thumbnails(tmp_path):
    app = QApplication.instance() or QApplication([])
    owner = QWidget()
    owner.downloads_button = QPushButton('Downloads', owner)
    other = QPushButton('Elsewhere', owner)
    other.move(0, 60)
    owner.resize(500, 200)
    settings = QSettings(str(tmp_path / 'settings.ini'), QSettings.Format.IniFormat)
    tray = DownloadTray(settings, owner)
    image = QImage(80, 60, QImage.Format.Format_RGB32)
    image.fill(QColor('red'))
    path = tmp_path / 'image.png'
    image.save(str(path))
    tray.add(path)
    owner.show()
    tray.show()
    try:
        app.processEvents()
        assert not tray.sweep_button.icon().isNull()
        app.sendEvent(owner, QEvent(QEvent.Type.WindowDeactivate))
        assert tray.isVisible()
        QTest.mouseClick(tray.list.viewport(), Qt.MouseButton.LeftButton)
        assert tray.isVisible()
        deadline = time.monotonic() + 5
        while tray.pending and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.01)
        assert not tray.pending and tray.previews[str(path)]
        preview = tray.list.item(0).icon().pixmap(56, 42).toImage()
        assert preview.pixelColor(preview.width() // 2, preview.height() // 2).red() > 200
        tray.sweep_button.click()
        assert not tray.history and tray.list.count() == 0
        assert settings.value('download_history') == '[]'
        assert path.exists()
        assert tray.isVisible()
        QTest.mouseClick(other, Qt.MouseButton.LeftButton)
        assert not tray.isVisible()
    finally:
        tray.close()
        owner.close()
