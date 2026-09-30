"""Aurora splash artwork, brand marks and live startup status."""
from __future__ import annotations

from importlib.resources import files

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPixmap, QPen
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QSplashScreen

from . import __version__

WIDTH, HEIGHT = 980, 558
BRANDS = (('ltx', 'LTX VIDEO'), ('minimax', 'MiniMax H3'),
          ('gemini', 'Gemini'), ('openai', 'OpenAI'))


def splash_pixmap() -> QPixmap:
    assets = files('ltx_prompt_director').joinpath('assets')
    backdrop = QPixmap(str(assets.joinpath('splash-aurora-background.png')))
    canvas = QPixmap(WIDTH, HEIGHT)
    canvas.fill(QColor('#07141f'))
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.drawPixmap(0, 0, WIDTH, HEIGHT, backdrop)
    shade = QLinearGradient(0, 0, 690, 0)
    shade.setColorAt(0, QColor(3, 14, 24, 224))
    shade.setColorAt(.52, QColor(3, 15, 24, 147))
    shade.setColorAt(1, QColor(3, 15, 24, 0))
    painter.fillRect(0, 0, 730, HEIGHT, shade)
    painter.fillRect(0, 390, WIDTH, 168, QColor(3, 13, 23, 181))
    painter.setPen(QPen(QColor('#9bc4cb'), 1))
    painter.drawRect(1, 1, WIDTH - 2, HEIGHT - 2)
    painter.setFont(QFont('Sans Serif', 10, QFont.Weight.DemiBold))
    painter.setPen(QColor('#a7d5db'))
    painter.drawText(52, 57, 'TOVENSOLUTIONS   /   CREATIVE TOOLS')
    painter.setPen(QColor('#f0f6f5'))
    painter.setFont(QFont('Sans Serif', 37, QFont.Weight.Bold))
    painter.drawText(47, 175, 'LTX DIRECTOR')
    painter.setFont(QFont('Sans Serif', 24, QFont.Weight.Light))
    painter.setPen(QColor('#b9dadd'))
    painter.drawText(51, 212, 'D I R E C T O R')
    painter.setPen(QPen(QColor('#e8bd85'), 2))
    painter.drawLine(53, 229, 307, 229)
    painter.setPen(QColor('#ffd69d'))
    painter.setFont(QFont('Sans Serif', 12, QFont.Weight.Bold))
    painter.drawText(52, 263, '13  /  AURORA')
    painter.setPen(QColor('#c6d9dd'))
    painter.setFont(QFont('Sans Serif', 10))
    painter.drawText(52, 286, 'Shape the moment. Direct the sequence.')
    painter.setPen(QColor('#b6d4d7'))
    painter.setFont(QFont('Sans Serif', 9, QFont.Weight.DemiBold))
    painter.drawText(52, 410, 'POWERED BY')
    for index, (key, name) in enumerate(BRANDS):
        left = 51 + index * 224
        painter.setPen(QPen(QColor('#78a5ad'), 1))
        painter.setBrush(QColor(8, 29, 40, 207))
        painter.drawRoundedRect(left, 421, 210, 57, 6, 6)
        if key == 'ltx':
            # LTX Video's wordmark remains a wordmark where no official glyph ships.
            painter.setPen(QColor('#e9eaf0'))
            painter.setFont(QFont('Sans Serif', 16, QFont.Weight.Black))
            painter.drawText(left + 13, 457, 'LTX')
            name = 'VIDEO'
            label_x = left + 83
        else:
            renderer = QSvgRenderer(assets.joinpath('brand-' + key + '.svg').read_bytes())
            painter.save()
            if key == 'openai':
                painter.setClipRect(left + 12, 430, 35, 35)
            renderer.render(painter, QRectF(left + 11, 430, 39, 39))
            painter.restore()
            label_x = left + 57
        painter.setPen(QColor('#eef5f5'))
        painter.setFont(QFont('Sans Serif', 11, QFont.Weight.DemiBold))
        painter.drawText(label_x, 456, name)
    painter.setPen(QColor('#a9bcc4'))
    painter.setFont(QFont('Sans Serif', 8))
    painter.drawText(52, 501, 'Third-party marks belong to their respective owners; no endorsement implied.')
    painter.drawText(52, 520, '© 2026 tovensolutions. All rights reserved.')
    painter.setPen(QColor('#e8d2af'))
    painter.drawText(885, 520, 'v' + __version__)
    painter.end()
    return canvas


class StartupSplash(QSplashScreen):
    def __init__(self):
        super().__init__(splash_pixmap())
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.set_status('Starting application…')

    def set_status(self, status: str):
        self.showMessage(status, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
                         QColor('#c8e2e7'))

    def drawContents(self, painter):
        painter.fillRect(2, 534, self.width() - 4, 22, QColor('#071521'))
        painter.setPen(QColor('#8fc9d0'))
        painter.setFont(QFont('Sans Serif', 9))
        painter.drawText(52, 550, self.message())
