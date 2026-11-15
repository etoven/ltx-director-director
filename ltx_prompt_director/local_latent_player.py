"""Clocked RGB latent samples delivered directly to the preview's image surface."""
import bisect
import mmap
import time

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QImage, QPainter, QPixmap


class LatentFramePlayer(QObject):
    frame_presented = Signal(QImage)
    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self.data = None
        self.playing = False
        self.position = 0
        self.timer = QTimer(self)
        self.timer.setInterval(1000 // 24)
        self.timer.timeout.connect(self.tick)

    @property
    def active(self):
        return self.data is not None

    def close(self):
        self.timer.stop()
        self.playing = False
        if self.data is not None:
            self.data.close()
            self.data = None

    def load(self, event, autoplay):
        width, height, count = (int(event[key]) for key in ('width', 'height', 'latent_frames'))
        fps, frames = int(event['fps']), int(event['frames'])
        if min(width, height, count, fps, frames) <= 0:
            raise ValueError('Invalid latent preview dimensions.')
        with open(event['path'], 'rb') as source:
            if source.seek(0, 2) != width * height * count * 3:
                raise ValueError('Incomplete latent preview frames.')
            data = mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ)
        # Keep the playhead and the user's pause across sampling updates.
        position = self.position if self.active else 0
        self.close()
        self.data = data
        self.width, self.height, self.count = width, height, count
        self.fps = fps
        self.duration = round(frames * 1000 / fps)
        self.times = [0]
        for index in range(count - 1):
            self.times.append(self.times[-1] + (1 if index % 5 == 0 else 4))
        self.panel.duration_changed(self.duration)
        self.seek(position)
        self.set_playing(autoplay)

    def set_playing(self, playing):
        self.playing = playing
        self.origin = time.monotonic() - self.position / 1000
        if playing:
            self.timer.start()
        else:
            self.timer.stop()
        text = '❚❚ Pause' if playing else '▶ Play'
        self.panel.play.setText(text)
        self.panel.fullscreen_window.play.setText(text)

    def tick(self):
        self.position = int((time.monotonic() - self.origin) * 1000) % self.duration
        self.present()

    def seek(self, position):
        self.position = max(0, min(int(position), self.duration - 1))
        self.origin = time.monotonic() - self.position / 1000
        self.present()

    def present(self):
        frame = self.position * self.fps / 1000
        low = min(self.count - 1, max(0, bisect.bisect_right(self.times, frame) - 1))
        high = min(low + 1, self.count - 1)
        size = self.width * self.height * 3

        def sample(index):
            # Copy one small sample so a subsequent sampling update can
            # replace and unmap the bank without invalidating the image.
            pixels = self.data[index * size:(index + 1) * size]
            return QImage(pixels, self.width, self.height, self.width * 3,
                          QImage.Format.Format_RGB888).copy()

        image = sample(low)
        if high != low:
            painter = QPainter(image)
            painter.setOpacity((frame - self.times[low]) / (self.times[high] - self.times[low]))
            painter.drawImage(0, 0, sample(high))
            painter.end()
        # Render the raw image directly. No media decoder, graphics-context
        # readback, or QMediaPlayer source transition participates in playback.
        self.panel.current_frame = image.copy()
        self.panel._latent_pixmap = QPixmap.fromImage(image)
        self.panel.fit_latent_image()
        self.panel.position_changed(self.position)
        self.frame_presented.emit(image)
