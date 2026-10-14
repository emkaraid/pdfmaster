# -*- mode: python ; coding: utf-8 -*-
# Сборка для Windows: pyinstaller --noconfirm packaging/pdfmaster_win.spec  (из корня проекта)
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

a = Analysis(
    [os.path.join(ROOT, "packaging", "launcher.py")],
    pathex=[ROOT],
    datas=[(os.path.join(ROOT, "pdfmaster", "resources"), "pdfmaster/resources")],
    hiddenimports=["pillow_heif", "certifi", "PySide6.QtPrintSupport", "pdfmaster.render.worker", "pdfmaster.app"],
    excludes=["tkinter", "matplotlib", "scipy", "pandas", "IPython", "pytest", "PySide6.QtWebEngineCore",
              "PySide6.QtQml", "PySide6.QtQuick", "PySide6.Qt3DCore", "PySide6.QtMultimedia",
              "PySide6.QtNetwork", "PySide6.QtOpenGL", "PySide6.QtSql", "PySide6.QtPdf"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="PDFMaster",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=os.path.join(ROOT, "build", "icon.ico"),
    version=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="PDFMaster")
