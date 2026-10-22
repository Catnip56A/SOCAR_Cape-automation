"""The mouse wheel never changes a drop-down (the page scrolls instead); spin boxes keep the wheel."""
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QComboBox, QDoubleSpinBox, QScrollArea, QVBoxLayout, QWidget

import harness as H
from common.no_wheel import install_no_wheel


def _wheel(widget, dy=-120):
    pos = QPointF(widget.width() / 2, widget.height() / 2)
    ev = QWheelEvent(pos, widget.mapToGlobal(pos), QPoint(0, 0), QPoint(0, dy), Qt.MouseButton.NoButton,
                     Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(widget, ev)


def test_wheel_leaves_a_dropdown_alone_and_scrolls_the_page():
    app = QApplication.instance() or QApplication([])
    flt = install_no_wheel(app)
    inner = QWidget()
    lay = QVBoxLayout(inner)
    combo = QComboBox(); combo.addItems(["a", "b", "c"])
    spin = QDoubleSpinBox(); spin.setValue(5.0)
    lay.addWidget(combo); lay.addWidget(spin)
    for _ in range(30):
        lay.addWidget(QComboBox())               # something tall to scroll
    area = QScrollArea(); area.setWidget(inner); area.setWidgetResizable(True); area.resize(300, 200)
    area.show()
    H.pump(10)
    before = area.verticalScrollBar().value()
    _wheel(combo)
    H.pump(5)
    assert combo.currentIndex() == 0                       # the value did not change
    assert area.verticalScrollBar().value() > before       # the page scrolled instead
    spin.setFocus()
    _wheel(spin, 120)
    assert spin.value() != 5.0                             # the spin box keeps the wheel
    app.removeEventFilter(flt)
