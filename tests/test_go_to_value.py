"""'Go to value' (click = next, arrow = list), the orange target cell, and the statistics list navigation."""
from stats import cost_stats
import harness as H
from stats import stats_view as SV
from harness import W


def test_the_list_holds_missing_values_then_codes_not_in_sage():
    w = H.generator()
    items = w._goto_items()
    kinds = [i["kind"] for i in items]
    assert "missing" in kinds and "sage" in kinds
    assert kinds == sorted(kinds, key=lambda k: 0 if k == "missing" else 1), "missing values come first"
    w._update_goto_button()
    assert w._qty_jump_btn.text() == f"Go to value ({len(items)})"


def test_clicking_goes_to_the_next_one_and_wraps():
    w = H.generator()
    n = len(w._goto_items())
    w._empty_qty_idx = -1
    seen = []
    for _ in range(n + 1):
        w._jump_empty_qty()
        seen.append(w._empty_qty_idx)
    assert seen == list(range(n)) + [0]


def test_a_missing_value_is_selected_orange_and_not_opened_for_editing():
    w = H.generator()
    w._empty_qty_idx = -1
    w._jump_empty_qty()
    tbl, row, col = w._jump_target
    assert tbl.item(row, col).data(W._JUMP_ROLE) is True
    assert tbl.selectionModel().isRowSelected(row, tbl.rootIndex())
    assert tbl.state() != tbl.State.EditingState
    others = [t for t, *_ in w._required_specs() if t is not tbl]
    assert all(not t.selectedItems() for t in others)
    tbl.item(row, col).setText("5")                       # filling it in removes the orange
    w._refresh_stats()
    assert w._jump_target is None or w._jump_target[:3] != (tbl, row, col) or not tbl.item(row, col).data(W._JUMP_ROLE)


def test_picking_from_the_list_goes_there():
    w = H.generator()
    w._fill_goto_menu()
    last = w._goto_list.count() - 1
    assert w._goto_list.item(last).text().startswith("Not in SAGE")
    w._on_goto_picked(w._goto_list.item(last))
    H.pump()
    tree = w._stats_panel._tree
    assert tree.currentItem() is not None and tree.currentItem().data(0, SV._ROLE_KIND) == "item"
    assert tree.state() == tree.State.EditingState, "its Unit cost should be open for typing"


def test_typing_a_cost_removes_the_code_from_the_list():
    w = H.generator()
    side, doc, key = w._stats_panel.no_sage_keys()[0]
    before = len(w._goto_items())
    w._stats_panel._manual[cost_stats.norm_key(key)] = 3.5
    w._stats_panel.refresh()
    w._refresh_stats()
    assert len(w._goto_items()) == before - 1


def _doc_items(w):
    tree = w._stats_panel._tree
    grp = tree.topLevelItem(0)
    docs = {grp.child(i).text(0): grp.child(i) for i in range(grp.childCount())}
    for d in docs.values():
        d.setExpanded(True)
    H.pump()
    return tree, docs


def _items(doc_item):
    return [doc_item.child(i) for i in range(doc_item.childCount())
            if doc_item.child(i).data(0, SV._ROLE_KIND) == "item"]


def test_double_clicking_a_statistics_row_shows_the_line_in_its_table():
    w = H.generator()
    tree, docs = _doc_items(w)
    sb = w._content_scroll.verticalScrollBar()
    it = _items(docs["Equipment"])[5]
    sb.setValue(0)
    tree.itemDoubleClicked.emit(it, SV.C_NAME)
    H.pump()
    eq = w._req_equip_tbl
    rows = sorted({i.row() for i in eq.selectedItems()})
    assert rows and not w._req_cons_tbl.selectedItems()
    r = rows[0]
    key = eq.item(r, W._EQ_MATCH).text() or eq.item(r, W._EQ_CODE).text() or eq.item(r, W._EQ_DESC).text()
    assert cost_stats.norm_key(key) == it.data(0, SV._ROLE_KEY)
    assert sb.value() > 0


def test_a_single_click_or_the_unit_cost_cell_does_not_navigate():
    w = H.generator()
    tree, docs = _doc_items(w)
    sb = w._content_scroll.verticalScrollBar()
    it = _items(docs["Equipment"])[5]
    sb.setValue(0)
    tree.itemClicked.emit(it, SV.C_NAME)
    tree.itemDoubleClicked.emit(it, SV.C_UNIT)
    H.pump()
    assert not w._req_equip_tbl.selectedItems() and sb.value() == 0


def test_double_clicking_a_document_row_brings_its_table_into_view():
    w = H.generator()
    tree, docs = _doc_items(w)
    sb = w._content_scroll.verticalScrollBar()
    sb.setValue(0)
    tree.itemDoubleClicked.emit(docs["Consumables"], 0)
    H.pump()
    assert sb.value() > 0 and not w._req_cons_tbl.selectedItems()
