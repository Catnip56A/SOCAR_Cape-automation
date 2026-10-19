"""
hover_text.py — show the full text of a table/tree cell when it does not fit.

`install_elided_tooltips(view)` makes any QAbstractItemView (QTableWidget,
QTableView, QTreeWidget, …) show a cell's whole text in a tooltip while the
mouse rests on a cell whose text is cut off. A cell whose text fits gets no
extra tooltip. A cell that already has a tooltip of its own keeps it: when the
text is cut off, the full text is shown first, then a blank line, then that
tooltip. Nothing is styled — only the standard tooltip is used.
"""

from __future__ import annotations

import html
import re

from PySide6.QtCore import QEvent, QObject, QRect, Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QAbstractItemView, QToolTip, QTreeView

_RICH = re.compile(r"<[A-Za-z/][^>]*>")   # looks like HTML (PySide6 has no mightBeRichText)
_PAD = 10          # cell padding the style takes off the text width (both sides)


def _is_elided(view: QAbstractItemView, index) -> bool:
    text = index.data(Qt.ItemDataRole.DisplayRole)
    if not isinstance(text, str) or not text.strip():
        return False
    rect = view.visualRect(index)
    if rect.isEmpty():
        return False
    font = index.data(Qt.ItemDataRole.FontRole)
    fm = QFontMetrics(font if font is not None else view.font())
    avail = rect.width() - _PAD
    if isinstance(view, QTreeView) and index.column() == view.header().logicalIndex(0):
        depth, p = 1, index.parent()
        while p.isValid():
            depth += 1
            p = p.parent()
        avail -= view.indentation() * depth
    if avail <= 0:
        return True
    if view.wordWrap() and not isinstance(view, QTreeView):
        flags = int(Qt.TextFlag.TextWordWrap)
        need = fm.boundingRect(QRect(0, 0, avail, 100000), flags, text).height()
        return need > rect.height()
    return max(fm.horizontalAdvance(line) for line in text.split("\n")) > avail


def tooltip_for(view: QAbstractItemView, index) -> str | None:
    """The tooltip (rich text) to show for `index`, or None to leave Qt's
    default behaviour (the cell's own tooltip, if any)."""
    if not index.isValid() or not _is_elided(view, index):
        return None
    text = index.data(Qt.ItemDataRole.DisplayRole)
    own = index.data(Qt.ItemDataRole.ToolTipRole)
    body = html.escape(text).replace("\n", "<br>")
    if isinstance(own, str) and own.strip() and own.strip() != text.strip():
        own_html = own if _RICH.search(own) else html.escape(own).replace("\n", "<br>")
        body += "<br><br>" + own_html
    return f'<div style="white-space:pre-wrap">{body}</div>'


class _ElidedTooltipFilter(QObject):
    def __init__(self, view: QAbstractItemView):
        super().__init__(view)
        self._view = view

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.ToolTip:
            index = self._view.indexAt(event.pos())
            tip = tooltip_for(self._view, index)
            if tip is not None:
                QToolTip.showText(event.globalPos(), tip, self._view.viewport())
                return True
        return False


def install_elided_tooltips(view: QAbstractItemView) -> None:
    """Idempotent: calling it twice on the same view installs one filter."""
    if getattr(view, "_elided_tip_filter", None) is not None:
        return
    flt = _ElidedTooltipFilter(view)
    view.viewport().installEventFilter(flt)
    view._elided_tip_filter = flt
