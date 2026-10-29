"""
formula_widgets.py — formulas ("=25/12") in the plain number boxes (QLineEdit, QDoubleSpinBox).

The box holds the number, as before, so every reader of `.text()` / `.value()` is unaffected. The formula
is kept beside it and comes back while the box has focus; it is live only while the box still holds the value
it gave. The cells of the tables do the same in the delegates of ctr_tools/window.py (see common/formula.py).
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QCursor, QValidator
from PySide6.QtWidgets import QDoubleSpinBox, QLineEdit, QToolTip

from common.formula import FormulaError, evaluate, format_number, is_formula

__all__ = ["install_formula_edit", "FormulaDoubleSpinBox"]


def _hint(base: str, formula: str | None, value: str | None) -> str:
    if not formula:
        return base
    line = f"Formula: {formula}  →  {value}"
    return f"{base}\n\n{line}" if base else line


class _LineEditFormula(QObject):
    def __init__(self, edit: QLineEdit):
        super().__init__(edit)
        self.edit = edit
        self.base_tip = edit.toolTip()
        self.formula: tuple[str, str] | None = None        # (formula, the value text it gave)
        self.before = ""
        edit.installEventFilter(self)

    def _set_text_silently(self, text: str) -> None:
        was = self.edit.blockSignals(True)
        self.edit.setText(text)
        self.edit.blockSignals(was)

    def eventFilter(self, obj, event):
        t = event.type()
        if t == QEvent.Type.FocusIn:
            self.before = self.edit.text()
            if self.formula and self.before == self.formula[1]:
                self.edit.setProperty("_showing_formula", True)
                self._set_text_silently(self.formula[0])
                QTimer.singleShot(0, self.edit.selectAll)
        elif t == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.commit()
        elif t == QEvent.Type.FocusOut:
            self.commit()
        return False

    def commit(self) -> None:
        showing = self.edit.property("_showing_formula") is True
        self.edit.setProperty("_showing_formula", False)
        text = self.edit.text()
        if is_formula(text):
            if showing and self.formula and text.strip() == self.formula[0]:
                self._set_text_silently(self.formula[1])               # left as it was opened
                return
            try:
                value = format_number(evaluate(text))
            except FormulaError as exc:
                self._set_text_silently(self.before)
                QToolTip.showText(QCursor.pos(), f"Formula not accepted: {exc}. The box keeps its value.", self.edit)
                return
            self.formula = (text.strip(), value)
            self.edit.setText(value)                                   # textChanged: readers see the number
        elif showing:                                                  # the formula was replaced by plain text
            self.formula = None
        elif self.formula and text != self.formula[1]:
            self.formula = None
        f = self.formula if self.formula and self.edit.text() == self.formula[1] else None
        self.edit.setToolTip(_hint(self.base_tip, f[0] if f else None, f[1] if f else None))


def install_formula_edit(edit: QLineEdit) -> None:
    """Let `edit` take formulas. Idempotent."""
    if getattr(edit, "_formula_helper", None) is None:
        edit._formula_helper = _LineEditFormula(edit)


class FormulaDoubleSpinBox(QDoubleSpinBox):
    """A spin box that also takes a formula (with or without its suffix) and shows it again on focus."""

    def __init__(self, *a):
        super().__init__(*a)
        self._formula: tuple[str, float] | None = None
        self._base_tip = ""
        self.setKeyboardTracking(False)               # a formula is only worked out when the typing is finished
        self.lineEdit().installEventFilter(self)
        self.valueChanged.connect(lambda _v: self._show_tip())      # a plain number retires the formula line

    def setToolTip(self, text: str) -> None:           # the base text; the formula line is added to it
        self._base_tip = text
        self._show_tip()

    def _live(self):
        f = self._formula
        return f if f and abs(f[1] - self.value()) < 1e-9 else None

    def _show_tip(self) -> None:
        f = self._live()
        super().setToolTip(_hint(self._base_tip, f[0] if f else None, format_number(f[1]) if f else None))

    def _strip(self, text: str) -> str:
        t = text.strip()
        for tail in (self.suffix().strip(), self.prefix().strip()):
            if tail and t.endswith(tail):
                t = t[: -len(tail)].strip()
        return t

    def validate(self, text, pos):
        if is_formula(text):
            return QValidator.State.Intermediate, text, pos
        return super().validate(text, pos)

    def _refresh_text(self) -> None:
        was = self.lineEdit().blockSignals(True)
        self.lineEdit().setText(self.prefix() + self.textFromValue(self.value()) + self.suffix())
        self.lineEdit().blockSignals(was)

    def _commit_formula(self) -> None:
        """Qt reads the box's text on its own (not through valueFromText), so a formula is worked out here,
        just before Return / leaving the box lets Qt read it."""
        t = self._strip(self.lineEdit().text())
        if not is_formula(t):
            return
        try:
            v = evaluate(t)
        except FormulaError as exc:
            self._refresh_text()
            QToolTip.showText(QCursor.pos(), f"Formula not accepted: {exc}. The box keeps its value.", self)
            return
        v = min(max(v, self.minimum()), self.maximum())
        self._formula = (t, v)
        self.setValue(v)
        self._refresh_text()
        self._show_tip()

    def eventFilter(self, obj, event):
        if obj is self.lineEdit():
            t = event.type()
            if t == QEvent.Type.FocusIn and self._live():
                QTimer.singleShot(0, self._reopen_formula)
            elif t == QEvent.Type.FocusOut:
                self._commit_formula()
                QTimer.singleShot(0, self._show_tip)
            elif t == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._commit_formula()
        return super().eventFilter(obj, event)

    def _reopen_formula(self) -> None:
        f = self._live()
        if f and self.lineEdit().hasFocus():
            was = self.lineEdit().blockSignals(True)
            self.lineEdit().setText(f[0])
            self.lineEdit().blockSignals(was)
            self.lineEdit().selectAll()
