"""Shared thumbnail tag pills for projects and catalog media."""
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPixmap, QPainter, QColor, QPen

def add_thumbnail_labels(pixmap: QPixmap, labels: list[tuple[str, str]]) -> QPixmap:
    result = pixmap.copy()
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    font = painter.font()
    font.setBold(True)
    font.setPixelSize(max(9, round(result.height() * .075)))
    painter.setFont(font)
    metrics = painter.fontMetrics()
    margin = max(4, round(result.width() * .03))
    pad_x = max(6, round(result.width() * .035))
    height = metrics.height() + max(4, round(result.height() * .025))
    bottom = result.height() - margin
    for label, color in reversed(labels):
        display = metrics.elidedText(label, Qt.TextElideMode.ElideRight, max(30, result.width() - margin * 2 - pad_x * 2))
        width = min(result.width() - margin * 2, metrics.horizontalAdvance(display) + pad_x * 2)
        rect = QRectF(result.width() - margin - width, bottom - height, width, height)
        background = QColor(color)
        background.setAlpha(235)
        painter.setPen(QPen(background.lighter(145), 1))
        painter.setBrush(background)
        painter.drawRoundedRect(rect, height / 2, height / 2)
        painter.setPen(QColor("#111416") if background.lightness() > 150 else QColor("#ffffff"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, display)
        bottom -= height + max(3, round(result.height() * .02))
    painter.end()
    return result

