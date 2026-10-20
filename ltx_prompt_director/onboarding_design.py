"""Native presentation and navigation for Director's illustrated setup guide."""
from pathlib import Path
from PySide6.QtCore import Qt, Signal, QEvent, QRect
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (QWidget, QFrame, QLabel, QPushButton, QVBoxLayout,
    QHBoxLayout, QProgressBar, QDialog, QRubberBand, QStackedWidget, QSizePolicy)

ASSETS = Path(__file__).parent / 'assets' / 'onboarding'
STYLE = '''
QDialog#onboardingWizard, QWidget#onboardingSurface { background:#171a25; color:#d8dbe4; }
QDialog#onboardingWizard QWidget { background:transparent; color:#d8dbe4; }
QDialog#onboardingWizard QLabel { color:#d8dbe4; font-size:14px; background:transparent; }
QDialog#onboardingWizard QLabel#muted { color:#a6adbc; }
QDialog#onboardingWizard QFrame#onboardingRail { background:#171924; border-right:1px solid #303441; }
QDialog#onboardingWizard QFrame#onboardingCard { background:#252934; border:1px solid #424754; border-radius:12px; }
QDialog#onboardingWizard QFrame#onboardingCard[recommended="true"] { background:#252b35; border:1px solid #259cb3; }
QDialog#onboardingWizard QLabel#cardTitle { font-size:20px; font-weight:600; }
QDialog#onboardingWizard QLabel#pageTitle { font-size:28px; font-weight:650; }
QDialog#onboardingWizard QPushButton { color:#dce0e7; background:#323641; border:1px solid #505562; border-radius:7px; padding:9px 14px; font-size:14px; }
QDialog#onboardingWizard QPushButton:hover { background:#3a424d; border-color:#3bafc4; }
QDialog#onboardingWizard QPushButton:disabled { color:#777f8c; background:#292d37; border-color:#383e49; }
QDialog#onboardingWizard QPushButton[primary="true"] { background:#00849b; border:1px solid #0897af; color:white; }
QDialog#onboardingWizard QPushButton[primary="true"]:hover { background:#009bb3; }
QDialog#onboardingWizard QPushButton#navRow { text-align:left; border:0; border-left:4px solid transparent; background:transparent; border-radius:7px; padding:14px 9px; font-size:17px; }
QDialog#onboardingWizard QPushButton#navRow[active="true"] { background:#153d4d; border-left-color:#00a4c2; color:#f1f6f7; }
QDialog#onboardingWizard QPushButton#navRow[substep="true"] { margin-left:18px; padding:7px 10px; border-radius:5px; font-size:16px; }
QDialog#onboardingWizard QPushButton#navRow:hover { background:#263544; }
QDialog#onboardingWizard QLineEdit, QDialog#onboardingWizard QComboBox, QDialog#onboardingWizard QSpinBox, QDialog#onboardingWizard QDoubleSpinBox, QDialog#onboardingWizard QAbstractSpinBox { color:#dce0e7; background:#343945; border:1px solid #505563; border-radius:6px; min-height:28px; padding:3px 7px; font-size:13px; }
QDialog#onboardingWizard QComboBox QAbstractItemView { background:#252934; color:#e0e5ec; selection-background-color:#145068; }
QDialog#onboardingWizard QCheckBox { color:#ced3dd; font-size:13px; spacing:8px; }
QDialog#onboardingWizard QCheckBox::indicator { width:16px; height:16px; border:1px solid #758292; border-radius:4px; background:#242a35; }
QDialog#onboardingWizard QCheckBox::indicator:checked { background:#17879b; border-color:#4dc0d4; image:url(__CHECK__); }
QDialog#onboardingWizard QScrollArea { border:0; background:transparent; }
QDialog#onboardingWizard QWidget#localSubsteps { border-left:2px solid #323d50; margin-left:12px; }
QWidget#onboardingPage, QWidget#qt_scrollarea_viewport { background:transparent; }
QDialog#onboardingWizard QTabWidget::pane { border:0; background:transparent; }
QDialog#onboardingWizard QProgressBar { background:#313441; border:0; border-radius:3px; max-height:6px; }
QDialog#onboardingWizard QProgressBar::chunk { background:#87929d; border-radius:3px; }
QDialog#onboardingWizard QPlainTextEdit { background:#191f29; color:#bccbd6; border:1px solid #414959; border-radius:5px; }
'''

STYLE = STYLE.replace('__CHECK__', str(ASSETS / 'check.svg').replace('\\','/'))


def label(text, name=None):
    item = QLabel(text); item.setWordWrap(True)
    if name: item.setObjectName(name)
    return item


def primary(button):
    button.setProperty('primary', True)
    return button


def card(title='', body='', recommended=False):
    frame = QFrame(); frame.setObjectName('onboardingCard'); frame.setProperty('recommended', recommended)
    layout = QVBoxLayout(frame); layout.setContentsMargins(20, 18, 20, 18); layout.setSpacing(12)
    if title: layout.addWidget(label(title, 'cardTitle'))
    if body: layout.addWidget(label(body, 'muted'))
    return frame, layout


class GridSurface(QWidget):
    def __init__(self):
        super().__init__(); self.setObjectName('onboardingSurface')

    def paintEvent(self, event):
        painter = QPainter(self); painter.fillRect(self.rect(), QColor('#171a25'))
        painter.setPen(QColor('#232733'))
        for x in range(0, self.width(), 145): painter.drawLine(x, 0, x, self.height())
        for y in range(0, self.height(), 145): painter.drawLine(0, y, self.width(), y)


class Artwork(QLabel):
    def __init__(self, path, height=150):
        super().__init__(); self.source = QPixmap(str(path)); self.setFixedHeight(height)
        self.setMinimumWidth(0); self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter); self.setStyleSheet('background:#10202b;border-radius:8px')

    def resizeEvent(self, event):
        super().resizeEvent(event); self.fit()

    def fit(self):
        self.setPixmap(self.source.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation))


class StatusBadge(QWidget):
    def __init__(self):
        super().__init__(); self.state = 'pending'; self.setFixedSize(20,20)

    def paintEvent(self,event):
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QPolygonF, QFont
        painter = QPainter(self); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        if self.state == 'ready':
            painter.setBrush(QColor('#55c699')); painter.drawEllipse(1,1,18,18)
        elif self.state == 'pending':
            painter.setBrush(QColor('#f4ad7d')); painter.drawPolygon(QPolygonF([QPointF(10,1),QPointF(1,18),QPointF(19,18)]))
        else:
            painter.setBrush(QColor('#66c9df')); painter.drawEllipse(1,1,18,18)
        painter.setPen(QColor('#14333b')); font=QFont();font.setPixelSize(14);font.setBold(True);painter.setFont(font)
        painter.drawText(self.rect(),Qt.AlignmentFlag.AlignCenter,'✓' if self.state=='ready' else '!' if self.state=='pending' else '…')


class WizardNavigation(QWidget):
    currentRowChanged = Signal(int)
    localSectionChanged = Signal(int)

    def __init__(self, titles):
        super().__init__(); self.row = -1; self.section = 0
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(5)
        self.buttons = []; self.subbuttons = []; self.badges = []
        for index, title in enumerate(titles):
            button = QPushButton(f'{index + 1}  {title}'); button.setObjectName('navRow')
            button.clicked.connect(lambda _, n=index: self.setCurrentRow(n)); layout.addWidget(button); self.buttons.append(button)
            if index == 3:
                self.substeps = QWidget(); self.substeps.setObjectName('localSubsteps'); sub = QVBoxLayout(self.substeps); sub.setContentsMargins(0, 0, 0, 4); sub.setSpacing(2)
                for number, text in enumerate(('Engine', 'Video · H3', 'Images · Flux', 'Writing · local')):
                    item = QPushButton(text); item.setObjectName('navRow'); item.setProperty('substep', True)
                    item.clicked.connect(lambda _, n=number: self.select_local(n))
                    badge = StatusBadge(); badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                    line = QHBoxLayout(item); line.setContentsMargins(0, 0, 10, 0); line.addStretch(); line.addWidget(badge)
                    self.badges.append(badge); sub.addWidget(item); self.subbuttons.append(item)
                layout.addWidget(self.substeps)
        layout.addStretch()

    def currentRow(self): return self.row

    def setCurrentRow(self, row):
        row = max(0, min(len(self.buttons) - 1, row))
        changed = row != self.row; self.row = row; self.refresh()
        if changed: self.currentRowChanged.emit(row)

    def select_local(self, section):
        self.section = section; self.setCurrentRow(3); self.refresh(); self.localSectionChanged.emit(section)

    def refresh(self):
        for index, button in enumerate(self.buttons):
            button.setProperty('active', index == self.row); button.style().unpolish(button); button.style().polish(button)
        for index, button in enumerate(self.subbuttons):
            button.setProperty('active', self.row == 3 and index == self.section)
            button.style().unpolish(button); button.style().polish(button)

    def status(self, index, state, explanation):
        names = ('Engine', 'Video · H3', 'Images · Flux', 'Writing · local')
        icon = {'ready': '✓', 'pending': '⚠', 'busy': '◷'}[state]
        button = self.subbuttons[index]
        self.badges[index].state = state; self.badges[index].update()
        button.setToolTip(explanation); button.setAccessibleName(names[index] + ': ' + explanation)



class Showcase(QWidget):
    currentChanged = Signal(int)

    def __init__(self, slides, parent=None):
        super().__init__(parent); self.slides = slides; self.index = 0
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(12)
        frame, content = card()
        self.art = Artwork(ASSETS / slides[0][2], 300); content.addWidget(self.art)
        self.previous = QPushButton('‹'); self.previous.setAccessibleName('Previous showcase slide')
        self.next = QPushButton('›'); self.next.setAccessibleName('Next showcase slide')
        for button in (self.previous,self.next):button.setFixedSize(38,40)
        from PySide6.QtWidgets import QGridLayout
        content.removeWidget(self.art)
        overlay = QGridLayout(); overlay.setContentsMargins(0,0,0,0)
        overlay.addWidget(self.art,0,0)
        overlay.addWidget(self.previous,0,0,Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter)
        overlay.addWidget(self.next,0,0,Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter)
        content.addLayout(overlay)
        self.heading = label('', 'cardTitle'); content.addWidget(self.heading)
        self.body = label(''); content.addWidget(self.body); layout.addWidget(frame)
        dots = QHBoxLayout(); dots.addStretch(); self.dots = []
        for index in range(len(slides)):
            dot = QPushButton('○'); dot.setFixedSize(28, 28); dot.setStyleSheet('padding:0;border:0;background:transparent')
            dot.setAccessibleName(f'Showcase slide {index + 1}'); dot.clicked.connect(lambda _, n=index:self.show_slide(n))
            dots.addWidget(dot); self.dots.append(dot)
        dots.addStretch(); layout.addLayout(dots)
        self.previous.clicked.connect(lambda:self.show_slide(self.index - 1)); self.next.clicked.connect(lambda:self.show_slide(self.index + 1))
        self.show_slide(0)

    def show_slide(self, index):
        self.index = index % len(self.slides); title, body, image = self.slides[self.index]
        self.heading.setText(title); self.body.setText(body)
        self.art.source = QPixmap(str(ASSETS / image)); self.art.fit()
        for n, dot in enumerate(self.dots): dot.setText('●' if n == self.index else '○')
        self.currentChanged.emit(self.index)


TOUR = [
    ('Choose your workspace', 'Choose a project type in the toolbar. Keep your current project, or use New when you are ready to start another.', 'project_type_combo', 'welcome.png'),
    ('Build your timeline', 'Add images, video or text beats. Put them in order and adjust their duration. Your existing media and edits stay in place.', 'timeline', 'getting-started.png'),
    ('Direct and refine', 'Use Segment to change the selected beat, or Global for the full ordered sequence. Review generated changes before accepting them.', 'prompt_scope', 'language.png'),
    ('Use unified output', 'The unified editor holds the production prompt. It has independent Generate and Refine controls. Local video uses this output and the Reference panel.', 'unified_prompt', 'language.png'),
    ('Explore the effects library', 'Search the library and drag an effect onto a segment or its prompt to refine that segment. Add your own effects and thumbnails.', 'effects_dock', 'images.png'),
    ('Preview and hand off', 'Use Video Preview to attach a render or generate locally. Check progress and verbose output, then export your finished work.', 'project_preview_dock', 'local.png'),
]


class WorkspaceTour(QDialog):
    completed = Signal()

    def __init__(self, owner):
        super().__init__(owner, Qt.WindowType.Tool); self.owner = owner; self.index = 0; self.highlight = None
        self.setWindowTitle('Director · interactive quick start'); self.resize(430, 360)
        self.setObjectName('onboardingWizard'); self.setStyleSheet(STYLE)
        layout = QVBoxLayout(self); self.count = label(''); layout.addWidget(self.count)
        self.title = label('', 'cardTitle'); layout.addWidget(self.title)
        self.body = label(''); layout.addWidget(self.body)
        self.hint = label('The highlighted control is in your workspace. This tour never starts a generation.', 'muted'); layout.addWidget(self.hint)
        row = QHBoxLayout(); self.back = QPushButton('Back'); self.next = primary(QPushButton('Next'))
        self.back.clicked.connect(lambda:self.show_step(self.index - 1)); self.next.clicked.connect(self.advance)
        row.addWidget(self.back); row.addStretch(); row.addWidget(self.next); layout.addLayout(row)
        self.show_step(0)

    def show_step(self, index):
        if self.highlight: self.highlight.hide(); self.highlight.deleteLater(); self.highlight = None
        self.index = max(0, min(len(TOUR)-1,index)); title, body, name, _ = TOUR[self.index]
        self.title.setText(title); self.body.setText(body); self.count.setText(f'QUICK START  ·  {self.index + 1} / {len(TOUR)}')
        self.back.setEnabled(self.index > 0); self.next.setText('Complete tour' if self.index == len(TOUR)-1 else 'Next')
        target = getattr(self.owner, name, None)
        if target:
            target.show(); target.raise_(); target.setFocus(Qt.FocusReason.OtherFocusReason)
            self.highlight = QRubberBand(QRubberBand.Shape.Rectangle, target)
            self.highlight.setGeometry(target.rect().adjusted(1, 1, -1, -1)); self.highlight.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents); self.highlight.show()

    def advance(self):
        if self.index == len(TOUR)-1: self.completed.emit(); self.accept()
        else: self.show_step(self.index + 1)

    def done(self, result):
        if self.highlight: self.highlight.hide(); self.highlight.deleteLater(); self.highlight = None
        super().done(result)
