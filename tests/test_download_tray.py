import time
from PySide6.QtCore import QSettings, QEvent, Qt
from PySide6.QtGui import QImage, QColor
from PySide6.QtWidgets import QApplication, QWidget, QPushButton
from PySide6.QtTest import QTest
from ltx_prompt_director.downloads import DownloadTray


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
