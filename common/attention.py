"""
common/attention.py — "attention" cells (red / orange highlights) that must stay visible when selected.

Qt paints the selection on top of a cell's background, so a red or orange highlight vanishes under the
blue of a selected row. Any cell that carries a highlight the user must not miss marks itself with
`ATTENTION_ROLE` (a truthy value); `AttentionDelegate` then paints such a cell WITHOUT the selection
colour — its own background and text colour show — and draws a thin blue frame so the selected row is
still recognisable. The other cells of the row keep the normal blue selection.

Adding a new highlight colour needs nothing here: give the cell its colour as usual (BackgroundRole /
ForegroundRole, or a new kind in app.py's _HIGHLIGHT_COLORS) and let `ATTENTION_ROLE` answer True for it
(PandasModel.attention does this for every highlight kind; the statistics tree uses `mark_attention`).
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

ATTENTION_ROLE = Qt.ItemDataRole.UserRole + 10      # truthy: this cell's highlight must show even when selected
FRAME = QColor(25, 118, 210)                        # PRIMARY: the thin frame that still marks a selected cell


def mark_attention(item, column: int, background: QColor | None = None, foreground: QColor | None = None) -> None:
    """For a QTreeWidgetItem / QTableWidgetItem cell: give it its highlight colours (if any) and mark it so
    AttentionDelegate keeps them visible when the row is selected."""
    if background is not None:
        item.setBackground(column, QBrush(background)) if _is_tree(item) else item.setBackground(QBrush(background))
    if foreground is not None:
        item.setForeground(column, QBrush(foreground)) if _is_tree(item) else item.setForeground(QBrush(foreground))
    item.setData(column, ATTENTION_ROLE, True) if _is_tree(item) else item.setData(ATTENTION_ROLE, True)


def _is_tree(item) -> bool:
    return hasattr(item, "childCount")


class AttentionDelegate(QStyledItemDelegate):
    """Paints selected cells normally, except attention cells: those keep their own colours (see the
    module docstring)."""

    def paint(self, painter, option, index):
        if index.data(ATTENTION_ROLE) and option.state & QStyle.StateFlag.State_Selected:
            opt = QStyleOptionViewItem(option)
            opt.state &= ~(QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_HasFocus)   # no blue / focus tint over it
            super().paint(painter, opt, index)
            painter.save()
            painter.setPen(QPen(FRAME, 1))
            painter.drawRect(option.rect.adjusted(0, 0, -1, -1))   # the selection stays recognisable
            painter.restore()
        else:
            super().paint(painter, option, index)
