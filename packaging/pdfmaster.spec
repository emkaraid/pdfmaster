# -*- mode: python ; coding: utf-8 -*-
# Сборка: pyinstaller --noconfirm packaging/pdfmaster.spec  (запускать из корня проекта)
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
APP_NAME = "PDF-мастерская"
VERSION = open(os.path.join(ROOT, "pdfmaster", "resources", "VERSION")).read().strip()

a = Analysis(
    [os.path.join(ROOT, "packaging", "launcher.py")],
    pathex=[ROOT],
    datas=[(os.path.join(ROOT, "pdfmaster", "resources"), "pdfmaster/resources")],
    hiddenimports=["pillow_heif", "certifi", "PySide6.QtPrintSupport", "pdfmaster.render.worker", "pdfmaster.app"],
    excludes=["tkinter", "matplotlib", "scipy", "pandas", "IPython", "pytest", "PySide6.QtWebEngineCore",
              "PySide6.QtQml", "PySide6.QtQuick", "PySide6.Qt3DCore", "PySide6.QtMultimedia",
              "PySide6.QtNetwork", "PySide6.QtOpenGL", "PySide6.QtSql", "PySide6.QtDBus",
              "PySide6.QtPdf"],
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
    argv_emulation=False,
    target_arch="arm64",
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ROOT, "build", "icon.icns"),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="PDFMaster")
app = BUNDLE(
    coll,
    name=f"{APP_NAME}.app",
    icon=os.path.join(ROOT, "build", "icon.icns"),
    bundle_identifier="ru.ildar.pdfmaster",
    version=VERSION,
    info_plist={
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "CFBundleDevelopmentRegion": "ru",
        "CFBundleLocalizations": ["ru"],
        "LSApplicationCategoryType": "public.app-category.productivity",
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,
        "NSHumanReadableCopyright": "Для личного использования. MuPDF/PyMuPDF — AGPL.",
        "CFBundleDocumentTypes": [
            {
                "CFBundleTypeName": "PDF",
                "CFBundleTypeRole": "Editor",
                "LSHandlerRank": "Alternate",
                "LSItemContentTypes": ["com.adobe.pdf"],
                "CFBundleTypeIconSystemGenerated": True,
            },
            {
                "CFBundleTypeName": "Изображение",
                "CFBundleTypeRole": "Viewer",
                "LSHandlerRank": "Alternate",
                "LSItemContentTypes": ["public.jpeg", "public.png", "public.heic", "public.tiff"],
            },
        ],
    },
)
