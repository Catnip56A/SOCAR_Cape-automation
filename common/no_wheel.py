"""
no_wheel.py — the mouse wheel never changes a drop-down.

Qt changes a QComboBox's value when the wheel turns over it, so scrolling a page or table
past a drop-down silently changed the answer. `install_no_wheel(app)` stops that for every
combo box in the application (also the ones that open inside a table cell): the wheel is
handed to the widget around it instead, so the page keeps scrolling. Spin boxes and other
inputs, for which the wheel is meant to change the value, are left alone. The open list of a
drop-down scrolls as usual (it is not a combo box).
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QAbstractScrollArea, QApplication, QComboBox


class _NoWheelFilter(QObject):
    def eventFilter(self, obj, event):
        if event.type() != QEvent.Type.Wheel or not isinstance(obj, QComboBox):
            return False
        area = obj.parentWidget()
        while area is not None and not isinstance(area, QAbstractScrollArea):
            area = area.parentWidget()               # the page / table this combo box sits in
        if area is not None:                         # let it scroll instead
            target = area.viewport()
            local = target.mapFromGlobal(event.globalPosition().toPoint())
            QApplication.sendEvent(target, QWheelEvent(
                local, event.globalPosition(), event.pixelDelta(), event.angleDelta(),
                event.buttons(), event.modifiers(), event.phase(), event.inverted(), event.source()))
        return True                                  # the combo box itself never sees it


def install_no_wheel(app: QApplication) -> QObject:
    """Install the filter on the whole application; keep the returned object alive."""
    flt = _NoWheelFilter(app)
    app.installEventFilter(flt)
    return flt
