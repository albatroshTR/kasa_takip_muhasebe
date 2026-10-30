# -*- mode: python ; coding: utf-8 -*-
# PyInstaller yapilandirmasi.  Kullanim:  pyinstaller --noconfirm --clean KasaTakip.spec
# Cikti: dist\KasaTakip\KasaTakip.exe  (klasor halinde; kurulum dosyasi icin kurulum.iss kullanilir)

EXCLUDES = [
    "tkinter", "unittest", "pydoc_data", "matplotlib", "numpy", "pandas",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel",
    "PySide6.QtWebSockets", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtQuick3D",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DExtras", "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtBluetooth", "PySide6.QtPositioning", "PySide6.QtLocation",
    "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner",
    "PySide6.QtHelp", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtRemoteObjects",
    "PySide6.QtScxml", "PySide6.QtSpatialAudio", "PySide6.QtTextToSpeech", "PySide6.QtNfc",
    "PySide6.QtNetwork",  # program internete cikmaz: Qt ag modulu pakete alinmaz
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[("assets", "assets")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)

# Programin kullanmadigi Qt parcalarini (QML/Quick, sanal klavye, PDF modulu, diger diller) paketten cikar.
_DROP = ("qt6quick", "qt6qml", "qt6virtualkeyboard", "qt6pdf", "/qml/", "platforminputcontexts", "qt6network")


def _keep(entry):
    dest = entry[0].replace("\\", "/").lower()
    if any(x in dest for x in _DROP):
        return False
    if "/translations/" in dest and dest.endswith(".qm"):
        return dest.rsplit("/", 1)[1].startswith(("qtbase_tr", "qt_tr"))
    return True


a.binaries = [b for b in a.binaries if _keep(b)]
a.datas = [d for d in a.datas if _keep(d)]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="KasaTakip",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon="assets/app.ico",
    version="version_info.txt",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="KasaTakip",
)
