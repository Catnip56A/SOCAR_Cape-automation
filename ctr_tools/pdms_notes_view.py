"""
ctr_tools/pdms_notes_view.py — the floating window of the PDMS proposal's notes.

A window of its own (not modal: it can stay open beside the Generator while the proposal is filled in). It shows
the notes by place with a tick each (the ticked ones go into the proposal, in the order shown), a search bar, ↑ ↓
to reorder inside a place, Edit / Add / Delete for the notes themselves, and one box per placeholder used by a
ticked note ({working_days}, {hours} …). A placeholder box remembers what was typed, offers the closest earlier
values first while typing and can forget one. Everything that changes the list — a text, the places of a note, its
order, a remembered value — is saved at once (pdms_notes.py); what is ticked and the values belong to the proposal.

The data and the rules are in pdms_notes.py; builder_pdms.py writes the paragraphs.
"""

from __future__ import annotations

from PySide6.QtCore import QSettings, QSize, QStringListModel, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QCompleter, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QStyledItemDelegate, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ctr_tools import pdms_notes as N

_MUTED = "color: #757575; font-size: 11px;"
_ROLE = Qt.ItemDataRole.UserRole
_GEOMETRY_KEY = "pdms/notes_window_geometry"
_KIND_LABELS = {"numbered": "Numbered item (3.1, 3.2 …)", "bullet": "“* ” line under the item before"}
_PREFIX = {"bullet": "•  ", "numbered": ""}


class _WrapDelegate(QStyledItemDelegate):
    """Rows of the notes tree wrap to the width of the window instead of being cut off."""

    def sizeHint(self, option, index):
        view = self.parent()
        width = max(120, view.viewport().width() - view.indentation() * 2 - 40)        # indent and the tick
        text = str(index.data() or "")
        h = option.fontMetrics.boundingRect(0, 0, width, 10000, int(Qt.TextFlag.TextWordWrap), text).height()
        return QSize(width, h + 6)


class _PlaceholderBox(QWidget):
    """One placeholder: its label, an editable drop-down of remembered values (closest to the typing first) and a
    button that forgets the value shown."""

    typed = Signal(str, str)                # (name, value) while the user types: the proposal follows, nothing is remembered
    committed = Signal(str, str)            # (name, value) chosen or finished: remembered
    forgotten = Signal(str, str)

    def __init__(self, name: str, data: dict, parent=None):
        super().__init__(parent)
        self._name, self._data = name, data
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.combo = QComboBox()
        self.combo.setEditable(True)
        self.combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.combo.setMaxVisibleItems(8)
        self.combo.setMinimumWidth(220)
        # While typing, the popup lists the remembered values closest to what is typed first (as a search
        # bar does); with nothing typed the drop-down lists the newest first.
        self._model = QStringListModel(self)
        completer = QCompleter(self._model, self)
        completer.setCompletionMode(QCompleter.CompletionMode.UnfilteredPopupCompletion)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.combo.setCompleter(completer)
        self.combo.lineEdit().textEdited.connect(self._typed)
        completer.activated.connect(lambda text: self.committed.emit(self._name, str(text)))
        self.combo.activated.connect(lambda _i: self.committed.emit(self._name, self.combo.currentText()))
        self.combo.lineEdit().editingFinished.connect(lambda: self.committed.emit(self._name, self.combo.currentText()))
        self.forget_btn = QPushButton("✕")
        self.forget_btn.setFixedWidth(28)
        self.forget_btn.setToolTip("Forget the value shown: it is no longer offered in this list.")
        self.forget_btn.clicked.connect(self._forget)
        lay.addWidget(self.combo, 1)
        lay.addWidget(self.forget_btn)
        self._fill("")

    def _fill(self, typed: str) -> None:
        text = self.combo.currentText()
        was = self.combo.blockSignals(True)
        self.combo.clear()
        items = N.suggest(self._data, self._name, typed, limit=30)
        self.combo.addItems(items)
        self._model.setStringList(items[:8])
        self.combo.setEditText(text)
        self.combo.blockSignals(was)

    def _typed(self, text: str) -> None:
        self._model.setStringList(N.suggest(self._data, self._name, text, limit=8))
        self.typed.emit(self._name, text)

    def _forget(self) -> None:
        value = self.combo.currentText().strip()
        if value:
            N.forget(self._data, self._name, value)
            self.forgotten.emit(self._name, value)
            self._fill("")

    def set_value(self, value: str) -> None:
        was = self.combo.blockSignals(True)
        self.combo.setEditText(value)
        self.combo.blockSignals(was)
        self._fill("")

    def value(self) -> str:
        return self.combo.currentText().strip()


class NewPlaceholderDialog(QDialog):
    """Define a placeholder of your own: its name (used as {name} in a note), what it is called in the list, and the
    value a new proposal starts with."""

    def __init__(self, parent, data: dict):
        super().__init__(parent)
        self._data = data
        self.name = ""
        self.setWindowTitle("New placeholder")
        lay = QVBoxLayout(self)
        note = QLabel("A placeholder is a word in braces inside a note, for example {crane_days}, that is filled in when the "
                      "proposal is written.")
        note.setWordWrap(True)
        note.setStyleSheet(_MUTED)
        lay.addWidget(note)
        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("crane_days  (letters, digits and _)")
        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText("What it is, for example: Crane days")
        self.default_edit = QLineEdit()
        self.default_edit.setPlaceholderText("Optional: the value a new proposal starts with")
        form.addRow("Name", self.name_edit)
        form.addRow("Called", self.label_edit)
        form.addRow("Starts as", self.default_edit)
        lay.addLayout(form)
        row = QHBoxLayout()
        row.addStretch(1)
        ok, cancel = QPushButton("Add"), QPushButton("Cancel")
        ok.clicked.connect(self._accept)
        cancel.clicked.connect(self.reject)
        row.addWidget(ok)
        row.addWidget(cancel)
        lay.addLayout(row)

    def _accept(self) -> None:
        try:
            self.name = N.add_placeholder(self._data, self.name_edit.text(), self.label_edit.text(), self.default_edit.text())
        except ValueError as exc:
            QMessageBox.warning(self, "New placeholder", str(exc))
            return
        self.accept()


class PlaceholderManager(QDialog):
    """Prepare the values a placeholder offers (add, delete, reorder, choose the one a new proposal starts with) and
    remove a placeholder of your own. `on_change` is called after every change (the window saves and refreshes)."""

    def __init__(self, parent, data: dict, on_change, name: str | None = None):
        super().__init__(parent)
        self._data, self._on_change = data, on_change
        self.setWindowTitle("Placeholder values")
        self.resize(800, 460)
        lay = QVBoxLayout(self)
        cols = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(QLabel("<b>Placeholders</b>"))
        self.names = QListWidget()
        self.names.currentRowChanged.connect(lambda _r: self._fill_values())
        self.names.setMinimumWidth(300)
        self.names.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left.addWidget(self.names, 1)
        new = QPushButton("New placeholder…")
        new.clicked.connect(self._new)
        self.del_ph = QPushButton("Delete this placeholder")
        self.del_ph.clicked.connect(self._delete_placeholder)
        left.addWidget(new)
        left.addWidget(self.del_ph)
        right = QVBoxLayout()
        self.caption = QLabel("")
        self.caption.setWordWrap(True)
        right.addWidget(self.caption)
        self.values = QListWidget()
        self.values.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        right.addWidget(self.values, 1)
        add = QHBoxLayout()
        self.add_edit = QLineEdit()
        self.add_edit.setPlaceholderText("A value to offer, for example Monday to Saturday")
        self.add_edit.returnPressed.connect(self._add_value)
        self.add_btn = QPushButton("Add")
        self.add_btn.clicked.connect(self._add_value)
        add.addWidget(self.add_edit, 1)
        add.addWidget(self.add_btn)
        right.addLayout(add)
        ops = QHBoxLayout()
        self.up_btn, self.down_btn = QPushButton("↑"), QPushButton("↓")
        self.up_btn.clicked.connect(lambda: self._move(-1))
        self.down_btn.clicked.connect(lambda: self._move(1))
        self.default_btn = QPushButton("Use as the starting value")
        self.default_btn.setToolTip("A new proposal starts with this value for the placeholder (a value typed in a "
                                    "proposal does not replace it).")
        self.default_btn.clicked.connect(self._make_default)
        self.del_btn = QPushButton("Delete selected")
        self.del_btn.clicked.connect(self._delete_values)
        for b in (self.up_btn, self.down_btn, self.default_btn, self.del_btn):
            ops.addWidget(b)
        ops.addStretch(1)
        right.addLayout(ops)
        cols.addLayout(left, 2)
        cols.addLayout(right, 3)
        lay.addLayout(cols, 1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)
        self._fill_names(name)

    def current_name(self) -> str | None:
        it = self.names.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it is not None else None

    def _fill_names(self, select: str | None = None) -> None:
        keep = select or self.current_name()
        was = self.names.blockSignals(True)
        self.names.clear()
        for name in N.placeholder_names(self._data):
            it = QListWidgetItem(f"{{{name}}}  —  {N.label_of(self._data, name)}")
            it.setData(Qt.ItemDataRole.UserRole, name)
            self.names.addItem(it)
            if name == keep:
                self.names.setCurrentItem(it)
        self.names.blockSignals(was)
        if self.names.currentItem() is None and self.names.count():
            self.names.setCurrentRow(0)
        self._fill_values()

    def _fill_values(self) -> None:
        name = self.current_name()
        self.values.clear()
        if name is None:
            return
        own = self._data.get("placeholders", {}).get(name, {}).get("default") or ""
        base = N.default_of(self._data, name)
        for v in self._data["values"].get(name, []):
            mark = "   ← starts as" if own and v == own else ""
            it = QListWidgetItem(v + mark)
            it.setData(Qt.ItemDataRole.UserRole, v)
            self.values.addItem(it)
        auto = N.PLACEHOLDERS.get(name, {}).get("auto")
        self.caption.setText(f"<b>{{{name}}}</b> — {N.label_of(self._data, name)}"
                             + (f"<br><i>The app fills it from the {auto}; a value you type over it wins.</i>" if auto else "")
                             + (f"<br>Starts as: {own or base or '(nothing)'}"))
        self.del_ph.setEnabled(not N.is_known(name))

    def _changed(self) -> None:
        self._on_change()
        self._fill_values()

    def _new(self) -> None:
        dlg = NewPlaceholderDialog(self, self._data)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._on_change()
            self._fill_names(dlg.name)
        dlg.deleteLater()

    def _delete_placeholder(self) -> None:
        name = self.current_name()
        if name and not N.is_known(name) and QMessageBox.question(
                self, "Delete placeholder", f"Delete {{{name}}} and its values?\n\nA note that uses it keeps the {{{name}}} "
                "in its text (an empty value).", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Yes:
            self.delete_placeholder(name)

    def delete_placeholder(self, name: str) -> None:
        if N.remove_placeholder(self._data, name):
            self._on_change()
            self._fill_names()

    def _add_value(self) -> None:
        name = self.current_name()
        if name and N.add_value(self._data, name, self.add_edit.text()):
            self.add_edit.clear()
            self._changed()

    def _selected_values(self) -> list[str]:
        return [i.data(Qt.ItemDataRole.UserRole) for i in self.values.selectedItems()]

    def _delete_values(self) -> None:
        name = self.current_name()
        for v in self._selected_values():
            N.forget(self._data, name, v)
        if self._selected_values() or name:
            self._changed()

    def _move(self, delta: int) -> None:
        name, sel = self.current_name(), self._selected_values()
        if name and len(sel) == 1 and N.move_value(self._data, name, sel[0], delta):
            self._changed()
            for i in range(self.values.count()):
                if self.values.item(i).data(Qt.ItemDataRole.UserRole) == sel[0]:
                    self.values.setCurrentRow(i)

    def _make_default(self) -> None:
        name, sel = self.current_name(), self._selected_values()
        if name and len(sel) == 1:
            N.set_default(self._data, name, sel[0])
            self._changed()


class NoteEditor(QDialog):
    """Edit one note: its text (with Insert placeholder), its kind and the places it is in, each with its own
    pre-tick. `note` None = a new note."""

    def __init__(self, parent, data: dict, note: dict | None):
        super().__init__(parent)
        self._data, self._note = data, note
        self.setWindowTitle("Edit note" if note else "Add note")
        self.resize(560, 420)
        lay = QVBoxLayout(self)
        self.text = QPlainTextEdit(note["text"] if note else "")
        self.text.setPlaceholderText("The text of the note. Use {name} for a value filled in when the proposal is written, "
                                     "for example {working_days}.")
        lay.addWidget(self.text, 1)
        ph = QHBoxLayout()
        ph.addWidget(QLabel("Insert placeholder:"))
        self.ph_combo = QComboBox()
        for name in N.placeholder_names(data):
            self.ph_combo.addItem(f"{{{name}}}  —  {N.label_of(data, name)}", name)
        ins = QPushButton("Insert")
        ins.clicked.connect(lambda: self.text.insertPlainText("{%s}" % self.ph_combo.currentData()))
        newp = QPushButton("New placeholder…")
        newp.clicked.connect(self._new_placeholder)
        ph.addWidget(self.ph_combo, 1)
        ph.addWidget(ins)
        ph.addWidget(newp)
        lay.addLayout(ph)
        form = QFormLayout()
        self.kind = QComboBox()
        for k in N.KINDS:
            self.kind.addItem(_KIND_LABELS[k], k)
        self.kind.setCurrentIndex(max(0, self.kind.findData(note["kind"] if note else "numbered")))
        form.addRow("Looks like", self.kind)
        self.hint = QLineEdit((note or {}).get("hint", ""))
        self.hint.setPlaceholderText("e.g. ONLY AZN — when this note is for (shown in the list, never written into the proposal)")
        form.addRow("Hint", self.hint)
        lay.addLayout(form)
        lay.addWidget(QLabel("Where it appears (tick “Pre-ticked” to have it used unless it is unticked):"))
        self._place_boxes: dict[str, tuple[QCheckBox, QCheckBox]] = {}
        for key, label in data["places"].items():
            row = QHBoxLayout()
            here = QCheckBox(label)
            pre = QCheckBox("Pre-ticked")
            spec = (note or {}).get("places", {}).get(key)
            here.setChecked(spec is not None)
            pre.setChecked(bool(spec["pretick"]) if spec else True)
            pre.setEnabled(here.isChecked())
            here.toggled.connect(pre.setEnabled)
            row.addWidget(here, 1)
            row.addWidget(pre)
            lay.addLayout(row)
            self._place_boxes[key] = (here, pre)
        btns = QHBoxLayout()
        btns.addStretch(1)
        ok = QPushButton("Save")
        ok.clicked.connect(self._accept)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        btns.addWidget(ok)
        btns.addWidget(cancel)
        lay.addLayout(btns)

    def _new_placeholder(self) -> None:
        dlg = NewPlaceholderDialog(self, self._data)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            N.save_notes(self._data)
            self.add_placeholder_choice(dlg.name)
        dlg.deleteLater()

    def add_placeholder_choice(self, name: str) -> None:
        """Offer a placeholder that was just defined, and put it into the text."""
        self.ph_combo.addItem(f"{{{name}}}  —  {N.label_of(self._data, name)}", name)
        self.ph_combo.setCurrentIndex(self.ph_combo.count() - 1)
        self.text.insertPlainText("{%s}" % name)

    def _accept(self) -> None:
        if not self.text.toPlainText().strip():
            QMessageBox.warning(self, "Note", "The note has no text.")
            return
        self.accept()

    def apply(self) -> dict:
        """Write the edit into the data and return the note."""
        text, kind, hint = self.text.toPlainText().strip(), self.kind.currentData(), self.hint.text().strip()
        note = self._note
        if note is None:
            note = N.add_note(self._data, text, kind, hint=hint)
        else:
            note["text"], note["kind"], note["hint"] = text, kind, hint
        for key, (here, pre) in self._place_boxes.items():
            N.set_place(self._data, note["id"], key, here.isChecked(), pre.isChecked())
        return note


class PdmsNotesWindow(QWidget):
    """The floating notes window of one Generator. `changed` fires when a tick, a value or the list changes."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("PDMS proposal — notes")
        self.data = N.load_notes()
        self.ticks: dict[str, set[str]] = {}
        self.values: dict[str, str] = {}
        self.auto: dict[str, str] = {}
        self._touched: set[str] = set()
        self._boxes: dict[str, _PlaceholderBox] = {}
        self.reset_for_new_task()

        lay = QVBoxLayout(self)
        intro = QLabel("The ticked notes go into the proposal, in the order shown. A change to a note is saved at once.")
        intro.setStyleSheet(_MUTED)
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.template_note = QLabel("")
        self.template_note.setWordWrap(True)
        self.template_note.setStyleSheet("color: #C62828; font-size: 11px;")
        self.template_note.setVisible(False)
        lay.addWidget(self.template_note)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search the notes…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self._fill_tree())
        lay.addWidget(self.search)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setWordWrap(True)
        self.tree.setTextElideMode(Qt.TextElideMode.ElideNone)          # a long note wraps instead of being cut off
        self.tree.setUniformRowHeights(False)
        self.tree.setItemDelegate(_WrapDelegate(self.tree))
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.itemChanged.connect(self._on_tick)
        lay.addWidget(self.tree, 3)
        row = QHBoxLayout()
        self.up_btn, self.down_btn = QPushButton("↑"), QPushButton("↓")
        for b, d in ((self.up_btn, -1), (self.down_btn, 1)):
            b.setFixedWidth(34)
            b.setToolTip("Move the note up / down inside its place.")
            b.clicked.connect(lambda _=False, d=d: self._move(d))
            row.addWidget(b)
        self.edit_btn, self.add_btn, self.del_btn, self.reset_btn = (
            QPushButton("Edit…"), QPushButton("Add note…"), QPushButton("Delete note"), QPushButton("Reset ticks"))
        self.edit_btn.clicked.connect(self._edit)
        self.add_btn.clicked.connect(self._add)
        self.del_btn.clicked.connect(self._delete)
        self.reset_btn.setToolTip("Tick again exactly what is pre-ticked.")
        self.reset_btn.clicked.connect(self._reset_ticks)
        for b in (self.edit_btn, self.add_btn, self.del_btn):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(self.reset_btn)
        lay.addLayout(row)
        vrow = QHBoxLayout()
        vrow.addWidget(QLabel("<b>Values</b> for the placeholders of the ticked notes:"), 1)
        self.new_ph_btn, self.manage_btn = QPushButton("New placeholder…"), QPushButton("Manage values…")
        self.new_ph_btn.setToolTip("Define a placeholder of your own, to use as {name} in a note.")
        self.manage_btn.setToolTip("Add, delete or reorder the values a placeholder offers, and choose its starting value.")
        self.new_ph_btn.clicked.connect(self._new_placeholder)
        self.manage_btn.clicked.connect(self._manage_values)
        vrow.addWidget(self.new_ph_btn)
        vrow.addWidget(self.manage_btn)
        lay.addLayout(vrow)
        self._values_host = QWidget()
        self._values_form = QFormLayout(self._values_host)
        self._values_form.setContentsMargins(0, 0, 0, 0)
        self._values_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)      # the label above its box: nothing is cut off
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self._values_host)
        scroll.setMinimumHeight(110)
        lay.addWidget(scroll, 2)
        self.count = QLabel("")
        self.count.setStyleSheet(_MUTED)
        lay.addWidget(self.count)
        self.resize(640, 700)
        try:
            g = QSettings("SOCAR", "CTRGenerator").value(_GEOMETRY_KEY)
            if g:
                self.restoreGeometry(g)
        except Exception:
            pass
        self._fill_tree()
        self._fill_values()

    def set_template_has_tags(self, has: bool | None) -> None:
        """Say so when the PDMS Template set in the Generator has no note tags: then these notes are not used."""
        bad = has is False
        self.template_note.setVisible(bad)
        if bad:
            self.template_note.setText("The PDMS Template chosen in the Generator has no note tags, so these notes are NOT "
                                       "written into the proposal (its fixed texts are used). Choose a template with the "
                                       "tags, for example PDMS_TEMPLATE_short_v2.docx.")

    # ── what the proposal gets ───────────────────────────────────────────────

    def reset_for_new_task(self) -> None:
        """Back to what is pre-ticked, and the values the app knows (a new CTR Request, another job)."""
        self.ticks = {p: set(ids) for p, ids in N.default_ticks(self.data).items()}
        self._touched.clear()
        self._apply_auto_values()
        if hasattr(self, "tree"):
            self._fill_tree()
            self._fill_values()
        self.changed.emit() if hasattr(self, "tree") else None

    def set_auto(self, auto: dict) -> None:
        """What the app knows for the placeholders (location, currency, the mark-ups); a value the user typed wins."""
        self.auto = {k: str(v) for k, v in auto.items() if v not in (None, "")}
        self._apply_auto_values()
        for name, box in self._boxes.items():
            box.set_value(self.values.get(name, ""))
        if hasattr(self, "tree"):
            self._fill_tree()

    def _apply_auto_values(self) -> None:
        for name in N.placeholder_names(self.data):
            if name not in self._touched:
                self.values[name] = N.initial_value(self.data, name, self.auto)

    def ticked_notes(self, place: str) -> list[dict]:
        return [n for n in N.notes_for(self.data, place) if n["id"] in self.ticks.get(place, set())]

    def payload(self) -> dict:
        """{"items": {place: [{"text", "kind"}]}, "values": {name: value}} for builder_pdms.build_pdms."""
        return {"items": {p: [{"text": n["text"], "kind": n["kind"]} for n in self.ticked_notes(p)]
                          for p in self.data["places"]},
                "values": dict(self.values)}

    def used_placeholders(self) -> list[str]:
        out = []
        for p in self.data["places"]:
            for n in self.ticked_notes(p):
                out += [x for x in N.placeholders_of(n["text"]) if x not in out]
        return out

    def missing(self) -> list[str]:
        """Placeholders of the ticked notes that have no value."""
        return [n for n in self.used_placeholders() if not self.values.get(n)]

    def counts(self) -> tuple[int, int]:
        """(ticked, offered): note lines in the places, a note in two places counts twice."""
        total = sum(len(N.notes_for(self.data, p)) for p in self.data["places"])
        return sum(len(self.ticked_notes(p)) for p in self.data["places"]), total

    # ── the tree ─────────────────────────────────────────────────────────────

    def _rendered(self, note: dict) -> str:
        hint = f"   [{note['hint']}]" if note.get("hint") else ""
        return _PREFIX.get(note["kind"], "") + N.render(note["text"], self.values) + hint

    def _fill_tree(self) -> None:
        query = self.search.text().strip().lower().split() if hasattr(self, "search") else []
        keep = self._selected()
        was = self.tree.blockSignals(True)
        self.tree.clear()
        for place, label in self.data["places"].items():
            notes = N.notes_for(self.data, place)
            if query:
                notes = [n for n in notes
                         if all(w in (n["text"] + " " + N.render(n["text"], self.values) + " " + n.get("hint", "")).lower()
                                for w in query)]
            top = QTreeWidgetItem([label])
            f = top.font(0)
            f.setBold(True)
            top.setFont(0, f)
            top.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.tree.addTopLevelItem(top)
            for n in notes:
                it = QTreeWidgetItem([self._rendered(n)])
                it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(0, Qt.CheckState.Checked if n["id"] in self.ticks.get(place, set())
                                 else Qt.CheckState.Unchecked)
                it.setData(0, _ROLE, (place, n["id"]))
                it.setToolTip(0, n["text"])
                top.addChild(it)
                if keep == (place, n["id"]):
                    self.tree.setCurrentItem(it)
            top.setExpanded(True)
            if not notes:
                top.setDisabled(True)
        self.tree.blockSignals(was)
        ticked, total = self.counts()
        self.count.setText(f"{ticked} of {total} note lines ticked")

    def _selected(self) -> tuple[str, str] | None:
        it = self.tree.currentItem() if hasattr(self, "tree") else None
        d = it.data(0, _ROLE) if it is not None else None
        return tuple(d) if d else None

    def _on_tick(self, item, _col) -> None:
        d = item.data(0, _ROLE)
        if not d:
            return
        place, nid = d
        s = self.ticks.setdefault(place, set())
        (s.add if item.checkState(0) == Qt.CheckState.Checked else s.discard)(nid)
        self._fill_values()
        ticked, total = self.counts()
        self.count.setText(f"{ticked} of {total} note lines ticked")
        self.changed.emit()

    def tick(self, place: str, note_id: str, on: bool) -> None:
        """The same as clicking the tick."""
        (self.ticks.setdefault(place, set()).add if on else self.ticks.setdefault(place, set()).discard)(note_id)
        self._fill_tree()
        self._fill_values()
        self.changed.emit()

    def _reset_ticks(self) -> None:
        self.ticks = {p: set(ids) for p, ids in N.default_ticks(self.data).items()}
        self._fill_tree()
        self._fill_values()
        self.changed.emit()

    # ── editing the list (saved at once) ─────────────────────────────────────

    def _save(self) -> None:
        N.save_notes(self.data)

    def _move(self, delta: int) -> None:
        sel = self._selected()
        if sel and N.move(self.data, sel[0], sel[1], delta):
            self._save()
            self._fill_tree()
            self.changed.emit()

    def _edit(self) -> None:
        sel = self._selected()
        if not sel:
            return
        note = next((n for n in self.data["notes"] if n["id"] == sel[1]), None)
        if note is None:
            return
        self._edit_note(note)

    def _add(self) -> None:
        self._edit_note(None)

    def _edit_note(self, note: dict | None) -> None:
        dlg = NoteEditor(self, self.data, note)
        ok = dlg.exec() == QDialog.DialogCode.Accepted
        if ok:
            saved = dlg.apply()
            self.after_edit(saved)
        dlg.deleteLater()

    def after_edit(self, note: dict) -> None:
        """A note was changed or added: it is ticked where it is pre-ticked and no longer where it is gone; save."""
        for place in self.data["places"]:
            here = self.ticks.setdefault(place, set())
            spec = note["places"].get(place)
            if spec is None:
                here.discard(note["id"])
            elif note["id"] not in here and spec["pretick"]:
                here.add(note["id"])
        self._apply_auto_values()
        self._save()
        self._fill_tree()
        self._fill_values()
        self.changed.emit()

    def _delete(self) -> None:
        sel = self._selected()
        if not sel:
            return
        note = next((n for n in self.data["notes"] if n["id"] == sel[1]), None)
        if note is None:
            return
        if QMessageBox.question(self, "Delete note", "Delete this note from every place?\n\n" + note["text"],
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return
        self.delete_note(note["id"])

    def delete_note(self, note_id: str) -> None:
        N.delete_note(self.data, note_id)
        for s in self.ticks.values():
            s.discard(note_id)
        self._save()
        self._fill_tree()
        self._fill_values()
        self.changed.emit()

    # ── the placeholder values ───────────────────────────────────────────────

    def _fill_values(self, force: bool = False) -> None:
        names = self.used_placeholders()
        if not force and list(self._boxes) == names:
            return
        while self._values_form.rowCount():
            self._values_form.removeRow(0)
        self._boxes = {}
        for name in names:
            box = _PlaceholderBox(name, self.data)
            box.set_value(self.values.get(name, ""))
            box.typed.connect(lambda n, v: self._value_edited(n, v, commit=False))
            box.committed.connect(lambda n, v: self._value_edited(n, v, commit=True))
            box.forgotten.connect(lambda _n, _v: self._save())
            label = N.label_of(self.data, name)
            auto = N.PLACEHOLDERS.get(name, {}).get("auto")
            cap = QLabel(f"<b>{{{name}}}</b>  {label}" + (f"  <i>(from the {auto})</i>" if auto else ""))
            cap.setWordWrap(True)
            cap.setStyleSheet(_MUTED)
            self._values_form.addRow(cap, box)
            self._boxes[name] = box

    def after_placeholder_change(self) -> None:
        """A placeholder or its values were changed in a dialog: save, and show the new state."""
        self._save()
        self._apply_auto_values()
        for name in self._boxes:
            if name not in self._touched:
                self._boxes[name].set_value(self.values.get(name, ""))
        self._fill_values(force=True)
        self._fill_tree()
        self.changed.emit()

    def _new_placeholder(self) -> None:
        dlg = NewPlaceholderDialog(self, self.data)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.after_placeholder_change()
        dlg.deleteLater()

    def _manage_values(self, name: str | None = None) -> None:
        dlg = PlaceholderManager(self, self.data, self.after_placeholder_change, name if isinstance(name, str) else None)
        dlg.exec()
        dlg.deleteLater()

    def set_value(self, name: str, value: str, remember: bool = True) -> None:
        """The same as typing a value into the placeholder's box."""
        self._value_edited(name, value, commit=remember)

    def _value_edited(self, name: str, value: str, commit: bool = True) -> None:
        value = value.strip()
        self.values[name] = value
        self._touched.add(name)
        if commit and value:                              # remembered, so it is offered next time
            N.remember(self.data, name, value)
            self._save()
        self._fill_tree()
        self.changed.emit()
        box = self._boxes.get(name)
        if box is not None and box.value() != value:
            box.set_value(value)

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        self.tree.scheduleDelayedItemsLayout()          # the rows wrap to the width the window really has

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self.tree.scheduleDelayedItemsLayout()

    def closeEvent(self, ev) -> None:
        try:
            QSettings("SOCAR", "CTRGenerator").setValue(_GEOMETRY_KEY, self.saveGeometry())
        except Exception:
            pass
        super().closeEvent(ev)
