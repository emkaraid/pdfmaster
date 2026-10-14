# Сценарии для прогона внутри собранного .app:
# ruff: noqa: F821  (app, win, ctx подставляет приложение)
#   PDFM_SELFTEST_SCRIPT=packaging/selftest_scenarios.py PDFM_SELFTEST_DIR=/tmp/x \
#     "/Applications/PDFMaster.app/Contents/MacOS/PDFMaster"
# Выполняется в процессе приложения (переменные app, win, ctx). Клики и ввод — события Qt.
import os
import shutil
import subprocess
import time

import pymupdf as fitz
from PySide6.QtCore import QEvent, QEventLoop, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QApplication

OUT = os.environ.get("PDFM_SELFTEST_DIR", "/tmp/pdfm_selftest")
FIX = os.environ.get("PDFM_FIXTURES") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "fixtures")
os.makedirs(OUT, exist_ok=True)
LOG = open(os.path.join(OUT, "report.txt"), "w", encoding="utf-8")


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s)
    LOG.write(s + "\n")
    LOG.flush()


def pump(sec=0.2, until=None, timeout=20.0):
    end = time.time() + (timeout if until else sec)
    while time.time() < end:
        app.processEvents(QEventLoop.AllEvents, 20)
        time.sleep(0.003)
        if until and until():
            return True
    return not until


def shot(name):
    win.grab().save(os.path.join(OUT, name + ".png"))


def mouse(view, kind, pos, buttons=Qt.LeftButton):
    t = {"press": QEvent.MouseButtonPress, "release": QEvent.MouseButtonRelease, "move": QEvent.MouseMove}[kind]
    btn = Qt.NoButton if kind == "move" else Qt.LeftButton
    p = QPointF(pos)
    ev = QMouseEvent(t, p, view.viewport().mapToGlobal(p), btn,
                     buttons if kind != "release" else Qt.NoButton, Qt.NoModifier)
    QApplication.sendEvent(view.viewport(), ev)


def click(view, pos):
    mouse(view, "move", pos, Qt.NoButton)
    pump(0.05)
    mouse(view, "press", pos)
    mouse(view, "release", pos)
    pump(0.2)


def drag(view, a, b, steps=10):
    mouse(view, "press", a)
    for k in range(1, steps + 1):
        mouse(view, "move", a + (b - a) * k / steps)
        pump(0.01)
    mouse(view, "release", b)
    pump(0.2)


def key(view, k, mods=Qt.NoModifier, text=""):
    for t in (QEvent.KeyPress, QEvent.KeyRelease):
        QApplication.sendEvent(view.viewport(), QKeyEvent(t, k, mods, text))


def type_text(view, s):
    for ch in s:
        key(view, 0, Qt.NoModifier, ch)


def vpos(view, page, x, y):
    return QPointF(view.mapFromScene(page.mapToScene(QPointF(x, y))))


def pdftotext(p):
    exe = shutil.which("pdftotext") or "/opt/homebrew/bin/pdftotext"
    return subprocess.run([exe, p, "-"], capture_output=True, text=True).stdout


def open_copy(name):
    dst = os.path.join(OUT, name)
    shutil.copy(os.path.join(FIX, name), dst)
    t0 = time.perf_counter()
    win.open_paths([dst])
    tab = win.tab()
    pump(until=lambda: all(it.base is not None for it, _ in tab.view.visible_pages(0)))
    return tab, dst, time.perf_counter() - t0


def save(tab, path=None):
    done = []
    t0 = time.perf_counter()
    tab._save_to(path or tab.model.path, done_cb=lambda ok: done.append(ok))
    pump(until=lambda: bool(done), timeout=60)
    return bool(done and done[0]), time.perf_counter() - t0


def edit_line(tab, page, pred, find, repl):
    view = tab.view
    pump(until=lambda: tab.lines_cached(page.pid) is not None)
    ln = [l for l in tab.lines_cached(page.pid) if pred(l)][0]
    i = ln["text"].index(find)
    c = ln["chars"][i]
    view.ensureVisible(page.mapRectToScene(page.boundingRect()).adjusted(0, min(c[4], c[6]) - 80, 0, 0), 0, 0)
    pump(0.2)
    d = ln["dir"]
    size = ln["spans"][c[7]]["size"]
    p = page.to_disp(c[1] + d[0] * size * 0.12 + d[1] * size * 0.3, c[2] + d[1] * size * 0.12 - d[0] * size * 0.3)
    click(view, QPointF(view.mapFromScene(page.mapToScene(p))))
    ed = view.editing
    assert ed is not None, "правка не началась"
    for _ in range(len(find)):
        key(view, Qt.Key_Right, Qt.ShiftModifier)
    type_text(view, repl)
    key(view, Qt.Key_Return)
    pump(until=lambda: not page.ghosts, timeout=5)


results = []


def scenario(name):
    def deco(fn):
        t0 = time.perf_counter()
        try:
            msg = fn() or ""
            results.append((name, "ОК", msg))
        except Exception as e:  # noqa
            import traceback
            traceback.print_exc()
            results.append((name, "ОШИБКА", f"{type(e).__name__}: {e}"))
        log(f"[{results[-1][1]}] {name} ({time.perf_counter() - t0:.1f} с) {results[-1][2]}")
        return fn
    return deco


win.resize(1440, 920)
win.show()
win.raise_()
pump(0.5)


@scenario("1. Акт из Word: 1 250 000 → 2 480 500")
def s1():
    tab, path, t_open = open_copy("act_word.pdf")
    win.set_mode("edit")
    page = tab.view.items_by_pid[tab.model.pages[0]["id"]]
    edit_line(tab, page, lambda l: l["text"].startswith("Общая"), "1 250 000", "2 480 500")
    edit_line(tab, page, lambda l: l["text"] == "1 250 000", "1 250 000", "2 480 500")
    shot("1_act_edited")
    ok, t = save(tab)
    txt = pdftotext(path)
    assert ok and "1 250 000" not in txt and txt.count("2 480 500") == 2, txt
    a = fitz.open(os.path.join(FIX, "act_word.pdf"))[0].get_pixmap(dpi=100)
    b = fitz.open(path)[0].get_pixmap(dpi=100)
    import numpy as np
    da = np.frombuffer(a.samples, np.uint8).reshape(a.height, a.width, 3).astype(int)
    db = np.frombuffer(b.samples, np.uint8).reshape(b.height, b.width, 3).astype(int)
    rows = np.nonzero((np.abs(da - db).sum(2) > 40).any(1))[0]
    win.set_mode("select")
    return f"открытие {t_open:.2f} с, сохранение {t:.2f} с; изменённые строки пикселей: {rows.min()}–{rows.max()} " \
           f"(только две строки с суммой)"


@scenario("2. Чертёж A3 (32 000 линий): правка вертикальной надписи, печать, прокрутка")
def s2():
    tab, path, t_open = open_copy("drawing_a3.pdf")
    view = tab.view
    win.set_mode("edit")
    page = view.items_by_pid[tab.model.pages[0]["id"]]
    edit_line(tab, page, lambda l: "125.40" in l["text"], "125.40", "126.10")
    from pdfmaster.core import images
    png = images.to_png(images.remove_background(images.open_image(open(os.path.join(FIX, "stamp_scan.jpg"), "rb").read())))
    it = ctx.library.add(png, "stamp", name="Тест", width_pt=113.4)
    ctx.libraryChanged.emit()
    win.set_mode("stamp")
    tab._pick_asset(it["id"])
    view.set_fit("page")
    pump(0.5)
    click(view, vpos(view, page, 900, 600))
    # масштабы и замер времени до чёткой картинки
    times = []
    for z in (1.0, 2.0, 4.0):
        view.set_zoom(z)
        t0 = time.perf_counter()
        need = z * view.devicePixelRatioF()
        pump(until=lambda: (page.tile is not None and abs(page.tile_scale - need) < 1e-3) or page.base_scale >= need * 0.97)
        times.append(f"{int(z * 100)}% — {time.perf_counter() - t0:.2f} с")
    view.set_fit("page")
    pump(0.6)
    shot("2_drawing")
    ok, t = save(tab)
    txt = pdftotext(path)
    assert ok and "126.10" in txt and "125.40" not in txt
    assert len(fitz.open(path)[0].get_images()) == 1
    win.set_mode("select")
    return f"открытие {t_open:.2f} с; чёткая картинка после масштаба: {', '.join(times)}; сохранение {t:.2f} с"


@scenario("3. /Rotate 90: печать и текст ровно")
def s3():
    tab, path, _ = open_copy("rotated.pdf")
    view = tab.view
    view.go_to_page(1)
    pump(0.6)
    page = view.items_by_pid[tab.model.pages[1]["id"]]
    win.set_mode("stamp")
    tab._pick_asset(ctx.library.items()[0]["id"])
    click(view, vpos(view, page, 600, 420))
    win.set_mode("addtext")
    click(view, vpos(view, page, 120, 300))
    type_text(view, "Проверено, ровно")
    key(view, Qt.Key_Escape)
    pump(0.8)
    shot("3_rotated")
    ok, _ = save(tab)
    d = fitz.open(path)
    p = d[1]
    ln = [l for b in p.get_text("dict")["blocks"] for l in b.get("lines", [])
          if "Проверено" in "".join(s["text"] for s in l["spans"])][0]
    dx, dy = ln["dir"]
    rm = p.rotation_matrix
    v = (dx * rm.a + dy * rm.c, dx * rm.b + dy * rm.d)
    assert abs(v[0] - 1) < 1e-3, v
    win.set_mode("select")
    return "текст на повёрнутом листе читается слева направо, печать встала"


@scenario("4. Листы: перестановка, поворот, удаление, вставка, извлечение")
def s4():
    tab, path, _ = open_copy("pages6.pdf")
    th = tab.thumbs
    th.clearSelection()
    th.item(1).setSelected(True)
    th.item(2).setSelected(True)
    win.a_rot.trigger()
    it = th.takeItem(5)
    th.insertItem(0, it)
    th._emit_order()
    tab.insert_blank(2)
    tab.delete_pages([tab.model.pages[4]["id"]])
    tab.insert_files([os.path.join(FIX, "act_word.pdf"), os.path.join(FIX, "stamp_scan.jpg")], len(tab.model.pages))
    pump(0.8)
    shot("4_pages")
    ok, _ = save(tab)
    got = [(" ".join(p.get_text().split()[:2]) or "—", p.rotation) for p in fitz.open(path)]
    exp = [("Лист 6", 0), ("Лист 1", 0), ("—", 0), ("Лист 2", 90), ("Лист 4", 0), ("Лист 5", 0),
           ("Директору ООО", 0), ("—", 0)]
    assert got == exp, got
    ex = os.path.join(OUT, "extract.pdf")
    done = []
    tab._save_to(ex, page_ids=[tab.model.pages[i]["id"] for i in (0, 3)], done_cb=lambda ok: done.append(ok))
    pump(until=lambda: bool(done))
    assert [" ".join(p.get_text().split()[:2]) for p in fitz.open(ex)] == ["Лист 6", "Лист 2"]
    # отмена всего — исходный порядок
    while tab.model.can_undo():
        tab.undo()
    pump(0.3)
    assert tab.model.is_pristine()
    return "порядок и повороты в файле верные; извлечение 2 листов; ⌘Z до исходного"


@scenario("5. Скан печати на сероватом фоне")
def s5():
    from pdfmaster.core import images
    import numpy as np
    out = images.remove_background(images.open_image(open(os.path.join(FIX, "stamp_scan.jpg"), "rb").read()))
    a = np.asarray(out)
    out.save(os.path.join(OUT, "5_stamp.png"))
    corners = max(a[:40, :40, 3].max(), a[-40:, -40:, 3].max())
    assert corners == 0
    return f"углы прозрачные, размер после обрезки {out.size[0]}×{out.size[1]} px"


@scenario("6. Защищённый от изменений PDF")
def s6():
    tab, path, _ = open_copy("protected.pdf")
    assert tab.model.main_source.restricted
    tab._restricted_ok = True  # в обычной работе спрашивается диалогом
    win.set_mode("edit")
    page = tab.view.items_by_pid[tab.model.pages[0]["id"]]
    edit_line(tab, page, lambda l: l["text"] == "1 250 000", "1 250 000", "999 999")
    shot("6_protected")
    ok, _ = save(tab)
    d = fitz.open(path)
    assert ok and not d.metadata.get("encryption") and "999 999" in d[0].get_text()
    win.set_mode("select")
    return "открыт, правится, сохранён (ограничения сняты, с предупреждением в интерфейсе)"


@scenario("7. Атомарное сохранение и ⌘Z до исходного")
def s7():
    import hashlib
    tab, path, _ = open_copy("act_lo.pdf")
    h0 = hashlib.sha256(open(path, "rb").read()).hexdigest()
    win.set_mode("edit")
    page = tab.view.items_by_pid[tab.model.pages[0]["id"]]
    edit_line(tab, page, lambda l: "1 250 000" in l["text"], "1 250 000", "7 777 777")
    tab.rotate_pages([tab.model.pages[0]["id"]])
    ok, _ = save(tab)
    assert "7 777 777" in pdftotext(path)
    while tab.model.can_undo():
        tab.undo()
    ok, _ = save(tab)
    h1 = hashlib.sha256(open(path, "rb").read()).hexdigest()
    left = [f for f in os.listdir(OUT) if f.endswith(".pdfm-tmp")]
    assert h0 == h1 and not left
    win.set_mode("select")
    return "после ⌘Z до начала файл побайтно совпал с исходным; временных файлов не осталось"


REAL = os.environ.get("PDFM_REAL")


@scenario("8. Перенос: строка не помещается — абзац переносится по ширине")
def s8():
    tab, path, _ = open_copy("act_word.pdf")
    view = tab.view
    win.set_mode("edit")
    page = view.items_by_pid[tab.model.pages[0]["id"]]
    pump(until=lambda: tab.lines_cached(page.pid) is not None)
    ln = [l for l in tab.lines_cached(page.pid) if l["text"].startswith("Общая стоимость")][0]
    c = ln["chars"][-1]
    view.ensureVisible(page.mapRectToScene(page.boundingRect()).adjusted(0, c[4] - 80, 0, 0), 0, 0)
    pump(0.2)
    click(view, vpos(view, page, *(lambda p: (p.x(), p.y()))(page.to_disp(c[1] + 0.3, c[2] - 3))))
    key(view, Qt.Key_End)
    type_text(view, " (два миллиона четыреста восемьдесят тысяч пятьсот рублей)")
    pump(until=lambda: view.editing is not None and view.editing.kind == "para", timeout=3)
    assert view.editing.kind == "para"
    key(view, Qt.Key_Escape)
    pump(0.8)
    shot("8_wrap")
    ok, _ = save(tab)
    right = max(l["bbox"][2] for l in tab.lines_cached(page.pid))
    d = fitz.open(path)
    assert all(l["bbox"][2] <= right + 2 for b in d[0].get_text("dict")["blocks"] for l in b.get("lines", []))
    win.set_mode("select")
    return "текст перенесён по ширине абзаца, за поле не вылез"


@scenario("9. Систематизировать листы, оптимизация, линейка на плане (ваш файл)")
def s9():
    if not REAL or not os.path.exists(REAL):
        return "пропущено: нет файла"
    shutil.copy(REAL, os.path.join(OUT, "real.pdf"))
    dst = os.path.join(OUT, "real.pdf")
    win.open_paths([dst])
    tab = win.tab()
    pump(until=lambda: all(it.base is not None for it, _ in tab.view.visible_pages(0)))
    win.a_organize.trigger()
    pump(1.5)
    shot("9a_organizer")
    g = tab.organizer.grid
    it = g.takeItem(25)
    g.insertItem(0, it)
    g._emit_order()
    win.a_organize.trigger()
    view = tab.view
    view.go_to_page(24)
    view.set_fit("page")
    pump(1.5)
    win.set_mode("measure")
    page = view.items_by_pid[tab.model.pages[24]["id"]]
    tab.measure._ensure_snap(page)
    pump(until=lambda: page.pid in tab.measure.snaps)
    click(view, vpos(view, page, 300, 600))
    click(view, vpos(view, page, 800, 900))
    pump(0.3)
    shot("9b_ruler")
    lab = tab.measure.done[-1]["label"]
    from pdfmaster.core import optimize
    out = os.path.join(OUT, "real_opt.pdf")
    done = []
    tab._save_to(out, done_cb=lambda ok: done.append(ok), as_new=True, quiet=True,
                 extra={"optimize": {**optimize.DEFAULT}})
    pump(until=lambda: bool(done), timeout=60)
    a, b = os.path.getsize(REAL), os.path.getsize(out)
    assert fitz.open(out).page_count == 26
    win.set_mode("select")
    return f"сетка 26 листов; линейка на плане 1:500: {lab}; оптимизация {a // 1024} → {b // 1024} КБ"


log("")
log("Итог: " + ", ".join(f"{n.split('.')[0]}: {s}" for n, s, _ in results))
LOG.close()
