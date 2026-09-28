"""Inline spell checking for Qt text boxes using installed system dictionaries."""
from __future__ import annotations

import re
from functools import lru_cache

from PySide6.QtCore import QLocale, Qt
from PySide6.QtGui import QAction, QColor, QSyntaxHighlighter, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QTextEdit

try:
    import enchant
except (ImportError, OSError):
    enchant = None


WORDS = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*", re.UNICODE)
DIRECTIVES = re.compile(r'(?<!\w)/(?:refine-global|refine|keep|avoid|focus)\b', re.IGNORECASE)


@lru_cache(maxsize=1)
def system_dictionary():
    if enchant is None:
        return None
    try:
        locale = QLocale.system().name()
        candidates = (locale, locale.split('_')[0]) if locale not in ('C', 'POSIX') else ('en_US',)
        return next((enchant.Dict(tag) for tag in candidates if enchant.dict_exists(tag)), None)
    except (enchant.Error, OSError):
        return None


class SpellHighlighter(QSyntaxHighlighter):
    def __init__(self, editor: QTextEdit, dictionary):
        super().__init__(editor.document())
        self.dictionary = dictionary
        self._known: dict[str, bool] = {}
        self._format = QTextCharFormat()
        self._format.setUnderlineStyle(QTextCharFormat.UnderlineStyle.SpellCheckUnderline)
        self._format.setUnderlineColor(QColor('#e98686'))

    def misspelled(self, word: str) -> bool:
        if self.dictionary is None or len(word) < 3 or word.isupper():
            return False
        key = word.casefold()
        if key not in self._known:
            self._known[key] = not self.dictionary.check(word)
        return self._known[key]

    def highlightBlock(self, text: str) -> None:
        for match in WORDS.finditer(text):
            if not any(tag.start() <= match.start() < tag.end() for tag in DIRECTIVES.finditer(text)) and self.misspelled(match.group()):
                self.setFormat(match.start(), len(match.group()), self._format)
        pill = QTextCharFormat()
        pill.setForeground(QColor('#d0f3ff'))
        pill.setBackground(QColor('#245163'))
        pill.setFontWeight(700)
        for match in DIRECTIVES.finditer(text):
            self.setFormat(match.start(), len(match.group()), pill)

    def accept(self, word: str) -> None:
        self.dictionary.add_to_session(word)
        self._known.clear()
        self.rehighlight()


def install_spellcheck(editor: QTextEdit) -> SpellHighlighter | None:
    """Decorate an existing text box; preserve Qt editing and the plain text."""
    if getattr(editor, '_spell_highlighter', None):
        return editor._spell_highlighter
    dictionary = system_dictionary()
    highlighter = SpellHighlighter(editor, dictionary)
    editor._spell_highlighter = highlighter
    editor.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    def menu_at(point):
        menu = spellcheck_menu(editor, point, highlighter)
        menu.exec(editor.viewport().mapToGlobal(point))
        menu.deleteLater()

    editor.customContextMenuRequested.connect(menu_at)
    return highlighter


def spellcheck_menu(editor: QTextEdit, point, highlighter: SpellHighlighter):
    """Build a standard editor menu with inline suggestions before editing actions."""
    cursor = editor.cursorForPosition(point)
    cursor.select(QTextCursor.SelectionType.WordUnderCursor)
    word = cursor.selectedText().strip()
    menu = editor.createStandardContextMenu(point)
    if highlighter.dictionary and WORDS.fullmatch(word) and highlighter.misspelled(word):
        anchor = menu.actions()[0] if menu.actions() else None
        for replacement in highlighter.dictionary.suggest(word)[:7]:
            action = QAction(replacement, menu)
            if anchor:
                menu.insertAction(anchor, action)
            else:
                menu.addAction(action)
            action.triggered.connect(lambda checked=False, value=replacement, selected=QTextCursor(cursor): _replace(editor, selected, value))
        ignore = QAction(f'Ignore “{word}” for this session', menu)
        if anchor:
            menu.insertAction(anchor, ignore)
            menu.insertSeparator(anchor)
        else:
            menu.addAction(ignore)
            menu.addSeparator()
        ignore.triggered.connect(lambda checked=False, value=word: highlighter.accept(value))
    return menu


def _replace(editor: QTextEdit, cursor: QTextCursor, replacement: str) -> None:
    cursor.insertText(replacement)
    editor.setTextCursor(cursor)
