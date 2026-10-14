"""Интеграционные проверки интерфейса (без окна на экране): мышь и клавиатура
симулируются, результат проверяется по сохранённому файлу."""
import os
import shutil
import time

import pymupdf as fitz
import pytest

from tests.conftest import pdftotext

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture(scope="module")
def ui(tmp_path_factory):
    # библиотека подписей — во временной папке, не в настоящей
    old = os.environ.get("PDFM_SUPPORT_DIR")
    os.environ["PDFM_SUPPORT_DIR"] = str(tmp_path_factory.mktemp("support"))
    import multiprocessing
    try:
        multiprocessing.set_start_method("spawn")
    except RuntimeError:
        pass
    from PySide6.QtWidgets import QApplication
    from pdfmaster.ui.main_window import AppContext, MainWindow
    app = QApplication.instance() or QApplication([])
    ctx = AppContext()
    win = MainWindow(ctx)
    win.resize(1400, 900)
    win.show()
    yield app, ctx, win
    for i in reversed(range(win.tabs.count())):
        t = win.tabs.widget(i)
        t.view.finish_edit(True)
        t.model.mark_saved()
        win.close_tab(i)
    ctx.service.shutdown()
    if old is None:
        os.environ.pop("PDFM_SUPPORT_DIR", None)
    else:
        os.environ["PDFM_SUPPORT_DIR"] = old


def pump(app, sec=0.2, until=None, timeout=15.0):
    from PySide6.QtCore import QEventLoop
    end = time.time() + (timeout if until else sec)
    while time.time() < end:
        app.processEvents(QEventLoop.AllEvents, 20)
        time.sleep(0.003)
        if until and until():
            return True
    return not until


def type_text(app, w, txt):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication
    for ch in txt:
        for t in (QEvent.KeyPress, QEvent.KeyRelease):
            QApplication.sendEvent(w, QKeyEvent(t, 0, Qt.NoModifier, ch))


def open_copy(app, win, src, tmp_path):
    dst = tmp_path / os.path.basename(src)
    shutil.copy(src, dst)
    win.open_paths([str(dst)])
    tab = win.tab()
    assert pump(app, until=lambda: all(it.base is not None for it, _ in tab.view.visible_pages(0)))
    return tab, str(dst)


def click_char(app, view, page, line, i, frac=0.1):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    c = line["chars"][i]
    d = line["dir"]
    size = line["spans"][c[7]]["size"]
    # чуть правее начала символа вдоль строки и чуть выше базовой линии
    x = c[1] + d[0] * size * 0.12 * (1 + 4 * frac) + d[1] * size * 0.3
    y = c[2] + d[1] * size * 0.12 * (1 + 4 * frac) - d[0] * size * 0.3
    pt = page.to_disp(x, y)
    vp = view.mapFromScene(page.mapToScene(pt))
    QTest.mouseMove(view.viewport(), vp)
    pump(app, 0.05)
    QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, vp)
    pump(app, 0.2)


def save(app, tab, path=None):
    done = []
    tab._save_to(path or tab.model.path, done_cb=lambda ok: done.append(ok))
    assert pump(app, until=lambda: bool(done), timeout=30)
    assert done[0]


def test_ui_edit_sum_by_click_and_typing(ui, fx, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    app, ctx, win = ui
    tab, path = open_copy(app, win, fx["act"], tmp_path)
    view = tab.view
    win.set_mode("edit")
    page = view.items_by_pid[tab.model.pages[0]["id"]]
    assert pump(app, until=lambda: tab.lines_cached(page.pid) is not None)
    for prefix in ("Общая стоимость", "Итого"):
        lines = tab.lines_cached(page.pid)
        ln = [l for l in lines if "1 250 000" in l["text"] and (prefix == "Итого" or l["text"].startswith(prefix))]
        ln = [l for l in ln if (prefix != "Итого") or l["text"] == "1 250 000"][0]
        i = ln["text"].index("1 250 000")
        view.ensureVisible(page.mapRectToScene(page.boundingRect()).adjusted(0, ln["bbox"][1] - 50, 0, 0), 0, 0)
        pump(app, 0.2)
        click_char(app, view, page, ln, i, 0.05)
        assert view.editing is not None and view.editing.kind == "line"
        assert view.editing.editor.textCursor().position() == i  # курсор там, куда кликнули
        for _ in range(9):
            QTest.keyClick(view.viewport(), Qt.Key_Right, Qt.ShiftModifier)
        type_text(app, view.viewport(), "2 480 500")
        QTest.keyClick(view.viewport(), Qt.Key_Return)
        assert view.editing is None
        pump(app, until=lambda: not page.ghosts)
    assert tab.model.dirty
    save(app, tab)
    assert not tab.model.dirty
    txt = pdftotext(path)
    assert "1 250 000" not in txt and txt.count("2 480 500") == 2
    # отмена двух правок возвращает исходное
    tab.undo()
    tab.undo()
    assert tab.model.is_pristine()


def test_ui_editor_undo_is_local(ui, fx, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    app, ctx, win = ui
    tab, path = open_copy(app, win, fx["act"], tmp_path)
    view = tab.view
    win.set_mode("edit")
    page = view.items_by_pid[tab.model.pages[0]["id"]]
    assert pump(app, until=lambda: tab.lines_cached(page.pid) is not None)
    ln = [l for l in tab.lines_cached(page.pid) if l["text"].startswith("Иванову")][0]
    click_char(app, view, page, ln, len(ln["text"]) - 2, 0.0)
    QTest.keyClick(view.viewport(), Qt.Key_End)
    type_text(app, view.viewport(), "X")
    assert view.editing.editor.toPlainText().endswith("X")
    win.a_undo.trigger()  # ⌘Z во время набора — отменяет ввод в редакторе
    assert view.editing is not None and view.editing.editor.toPlainText() == ln["text"]
    QTest.keyClick(view.viewport(), Qt.Key_Escape)
    assert not tab.model.can_undo()  # ничего не изменилось — нечего отменять


def test_ui_vertical_text_on_drawing(ui, fx, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    app, ctx, win = ui
    tab, path = open_copy(app, win, fx["drawing"], tmp_path)
    view = tab.view
    win.set_mode("edit")
    page = view.items_by_pid[tab.model.pages[0]["id"]]
    assert pump(app, until=lambda: tab.lines_cached(page.pid) is not None)
    ln = [l for l in tab.lines_cached(page.pid) if "125.40" in l["text"]][0]
    i = ln["text"].index("125.40")
    click_char(app, view, page, ln, i + 2, 0.1)
    ed = view.editing.editor
    assert abs(ed.rotation() - 270) < 0.5  # редактор повёрнут, как надпись
    assert ed.textCursor().position() == i + 2
    QTest.keyClick(view.viewport(), Qt.Key_Delete)
    type_text(app, view.viewport(), "6")
    QTest.keyClick(view.viewport(), Qt.Key_Right)
    QTest.keyClick(view.viewport(), Qt.Key_Delete)
    type_text(app, view.viewport(), "1")
    QTest.keyClick(view.viewport(), Qt.Key_Return)
    save(app, tab)
    txt = pdftotext(path)
    assert "126.10" in txt and "125.40" not in txt


def test_ui_stamp_place_move_resize(ui, fx, tmp_path):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtTest import QTest
    from pdfmaster.core import images
    app, ctx, win = ui
    tab, path = open_copy(app, win, fx["act"], tmp_path)
    view = tab.view
    png = images.to_png(images.remove_background(images.open_image(open(fx["stamp"], "rb").read())))
    it = ctx.library.add(png, "stamp", width_pt=113.4)
    ctx.libraryChanged.emit()
    win.set_mode("stamp")
    tab._pick_asset(it["id"])
    page = view.items_by_pid[tab.model.pages[0]["id"]]
    view.ensureVisible(page.mapRectToScene(page.boundingRect()).adjusted(0, 500, 0, 0), 0, 0)
    pump(app, 0.2)
    vp = view.mapFromScene(page.mapToScene(QPointF(400, 600)))
    QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, vp)
    pump(app, 0.2)
    oid = view.selected
    o = tab.model.find_obj(oid)[1]
    assert o["t"] == "image" and abs(o["w"] - 113.4) < 0.1
    oi = view.obj_items[oid]
    z = view.zoom
    c0 = view.mapFromScene(oi.mapToScene(QPointF(0, 0)))
    QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, c0)
    for k in range(1, 11):
        QTest.mouseMove(view.viewport(), c0 + QPoint(4 * k, 2 * k))
        pump(app, 0.01)
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, Qt.NoModifier, c0 + QPoint(40, 20))
    o = tab.model.find_obj(oid)[1]
    assert abs(o["cx"] - (400 + 40 / z)) < 1.5 and abs(o["cy"] - (600 + 20 / z)) < 1.5
    br = view.mapFromScene(oi.mapToScene(oi.box.bottomRight()))
    QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, br)
    for k in range(1, 11):
        QTest.mouseMove(view.viewport(), br + QPoint(3 * k, 1 * k))
        pump(app, 0.01)
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, Qt.NoModifier, br + QPoint(30, 10))
    o = tab.model.find_obj(oid)[1]
    assert o["w"] > 113.4 + 30 / z - 2 and abs(o["w"] - o["h"]) < 0.01  # пропорции сохранены
    tab.stamp_to_pages("all")
    save(app, tab)
    d = fitz.open(path)
    assert d[0].get_images(full=True)[0][1] > 0  # с прозрачностью


def test_ui_pages_and_search(ui, fx, tmp_path):
    app, ctx, win = ui
    tab, path = open_copy(app, win, fx["pages"], tmp_path)
    th = tab.thumbs
    th.item(1).setSelected(True)
    th.item(2).setSelected(True)
    win.a_rot.trigger()
    assert [p["rot"] for p in tab.model.pages] == [0, 90, 90, 0, 0, 0]
    it = th.takeItem(5)
    th.insertItem(0, it)
    th._emit_order()
    tab.insert_blank(2)
    tab.delete_pages([tab.model.pages[4]["id"]])
    tab.insert_files([fx["act"]], len(tab.model.pages))
    win.a_find.trigger()
    tab.searchbar.edit.setText("Лист")
    tab.searchbar._enter()
    assert pump(app, until=lambda: "из" in tab.searchbar.count.text())
    assert tab.searchbar.count.text().endswith("из 5")
    tab.close_search()
    save(app, tab)
    d = fitz.open(path)
    got = [(" ".join(p.get_text().split()[:2]), p.rotation) for p in d]
    assert got == [("Лист 6", 0), ("Лист 1", 0), ("", 0), ("Лист 2", 90), ("Лист 4", 0), ("Лист 5", 0),
                   ("Директору ООО", 0)]


def test_ui_overflow_switches_to_paragraph(ui, fx, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    app, ctx, win = ui
    tab, path = open_copy(app, win, fx["act"], tmp_path)
    view = tab.view
    win.set_mode("edit")
    page = view.items_by_pid[tab.model.pages[0]["id"]]
    assert pump(app, until=lambda: tab.lines_cached(page.pid) is not None)
    ln = [l for l in tab.lines_cached(page.pid) if l["text"].startswith("Общая стоимость")][0]
    click_char(app, view, page, ln, len(ln["text"]) - 2, 0.0)
    QTest.keyClick(view.viewport(), Qt.Key_End)
    type_text(app, view.viewport(), " (два миллиона четыреста восемьдесят тысяч пятьсот рублей)")
    assert pump(app, until=lambda: view.editing is not None and view.editing.kind == "para", timeout=3)
    QTest.keyClick(view.viewport(), Qt.Key_Escape)
    save(app, tab)
    d = fitz.open(path)
    right = max(l["bbox"][2] for l in tab.lines_cached(page.pid) if l["text"].startswith("Мы,"))
    lines = [l for b in d[0].get_text("dict")["blocks"] for l in b.get("lines", [])]
    assert all(l["bbox"][2] <= right + 2 for l in lines), "текст вылез за правое поле"
    txt = " ".join(pdftotext(path).split())
    assert "(два миллиона четыреста восемьдесят тысяч пятьсот рублей)" in txt
    assert "коп., НДС не облагается." in txt


def test_ui_select_text_copy_and_pan(ui, fx, tmp_path):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    app, ctx, win = ui
    tab, path = open_copy(app, win, fx["act"], tmp_path)
    view = tab.view
    win.set_mode("select")
    page = view.items_by_pid[tab.model.pages[0]["id"]]
    assert pump(app, until=lambda: tab.view_lines(page.pid) is not None)
    lines = tab.view_lines(page.pid)
    a = [l for l in lines if l["text"].startswith("Заказчик")][0]
    b = [l for l in lines if l["text"].startswith("Подрядчик:")][0]

    def vp_of(l, i):
        c = l["chars"][i]
        return view.mapFromScene(page.mapToScene(page.to_disp(c[1] + 0.5, (c[4] + c[6]) / 2)))

    view.ensureVisible(page.mapRectToScene(page.boundingRect()).adjusted(0, a["bbox"][1] - 50, 0, 0), 0, 0)
    pump(app, 0.2)
    p0, p1 = vp_of(a, 0), vp_of(b, len(b["text"]) - 1)
    QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, p0)
    for k in range(1, 8):
        QTest.mouseMove(view.viewport(), p0 + (p1 - p0) * k / 7)
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, Qt.NoModifier, p1)
    win.a_copy.trigger()
    got = QApplication.clipboard().text()
    assert got.startswith("Заказчик") and "Подрядчик" in got and got.count("\n") == 1
    # на пустом месте — «рука»
    v0 = view.verticalScrollBar().value()
    empty = view.mapFromScene(page.mapToScene(QPointF(30, 700)))
    QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, empty)
    QTest.mouseMove(view.viewport(), empty + (empty - empty) + type(empty)(0, -60))
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, Qt.NoModifier, empty + type(empty)(0, -60))
    assert view.verticalScrollBar().value() != v0 or view.verticalScrollBar().maximum() == 0


def test_ui_organizer_grid(ui, fx, tmp_path):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtTest import QTest
    app, ctx, win = ui
    tab, path = open_copy(app, win, fx["pages"], tmp_path)
    win.a_organize.trigger()
    assert tab.organizer_shown()
    g = tab.organizer.grid
    pump(app, 0.3)
    assert g.count() == 6
    # кнопка поворота на миниатюре 2
    it = g.item(1)
    br = g.itemDelegate().buttons(g.visualItemRect(it), it.data(Qt.UserRole + 1))["rot"]
    QTest.mouseClick(g.viewport(), Qt.LeftButton, Qt.NoModifier, br.center().toPoint())
    assert tab.model.pages[1]["rot"] == 90
    # кнопка удаления на миниатюре 6
    it = g.item(5)
    br = g.itemDelegate().buttons(g.visualItemRect(it), it.data(Qt.UserRole + 1))["delete"]
    QTest.mouseClick(g.viewport(), Qt.LeftButton, Qt.NoModifier, br.center().toPoint())
    assert len(tab.model.pages) == 5 and g.count() == 5
    # «+» между листами 1 и 2
    r = g.visualItemRect(g.item(1))
    assert g._gap_at(QPointF(r.left() - g.spacing() / 2, r.center().y()).toPoint()) == 1
    tab.insert_blank(1)
    assert g.count() == 6
    win.a_organize.trigger()
    assert not tab.organizer_shown()
    save(app, tab)
    assert [p.rotation for p in fitz.open(path)] == [0, 0, 90, 90, 0, 0]


def test_ui_ruler_on_plan(ui, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from tests.test_measure import _page_with_vp
    app, ctx, win = ui
    d, p = _page_with_vp(0.17639)
    src = tmp_path / "plan.pdf"
    d.save(src)
    win.open_paths([str(src)])
    tab = win.tab()
    view = tab.view
    pump(app, until=lambda: all(it.base is not None for it, _ in view.visible_pages(0)))
    win.set_mode("measure")
    page = view.items_by_pid[tab.model.pages[0]["id"]]
    tab.measure._ensure_snap(page)
    assert pump(app, until=lambda: page.pid in tab.measure.snaps)
    # кликаем рядом с концами линии (100,300)-(400,300) — привязка к концам
    a = view.mapFromScene(page.mapToScene(page.to_disp(102, 302)))
    b = view.mapFromScene(page.mapToScene(page.to_disp(398, 299)))
    for pnt in (a, b):
        QTest.mouseMove(view.viewport(), pnt)
        QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, pnt)
    m = tab.measure.done[-1]
    assert m["pts"] == [(100.0, 300.0), (400.0, 300.0)]
    expect = M_len = 300 * 25.4 / 72 * 0.5  # 1:500, м
    assert tab.side.measure.result.text() == f"{M_len:.2f} м".replace(".", ",")
    tab.measure.to_document()
    out = tmp_path / "plan_out.pdf"
    save(app, tab, str(out))
    assert f"{expect:.2f}".replace(".", ",") in fitz.open(out)[0].get_text()


def test_ui_ruler_relabels_when_scale_arrives(ui, tmp_path):
    """Измерение, сделанное до прихода масштаба листа, пересчитывается в метры
    (14.10.2026: на большом чертеже подписи оставались «155,6 мм»)."""
    from PySide6.QtWidgets import QGraphicsSimpleTextItem
    from tests.test_measure import _page_with_vp
    app, ctx, win = ui
    d, p = _page_with_vp(0.17639)
    src = tmp_path / "plan2.pdf"
    d.save(src)
    win.open_paths([str(src)])
    tab = win.tab()
    view = tab.view
    pump(app, until=lambda: all(it.base is not None for it, _ in view.visible_pages(0)))
    win.set_mode("measure")
    page = view.items_by_pid[tab.model.pages[0]["id"]]
    tool = tab.measure
    tool.page, tool.pts = page, [(100.0, 300.0), (400.0, 300.0)]
    tool.finish()                                   # масштаба ещё нет — на бумаге
    assert tab.side.measure.result.text().endswith("мм")
    tool._ensure_snap(page)
    assert pump(app, until=lambda: page.pid in tool.vps)
    want = f"{300 * 25.4 / 72 * 0.5:.2f} м".replace(".", ",")
    assert tab.side.measure.result.text() == want
    texts = [it.text() for it in tool.done[-1]["items"] if isinstance(it, QGraphicsSimpleTextItem)]
    assert texts == [want]
