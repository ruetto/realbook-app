# PyInstaller build for Windows (run from the repository root: pyinstaller packaging/RealBookGuitar.spec)
import os
import lilypond

ROOT = os.path.abspath(os.getcwd())
LILY = os.path.join(os.path.dirname(lilypond.__file__), "lilypond-binaries")

QT_UNUSED = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.Qt3DCore",
    "PySide6.Qt3DRender", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtQuick",
    "PySide6.QtQuick3D", "PySide6.QtQml", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtBluetooth",
    "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtDesigner",
]

a = Analysis(
    [os.path.join(ROOT, "RealBookGuitar.py")],
    pathex=[ROOT],
    datas=[
        (os.path.join(ROOT, "models"), "models"),
        (LILY, "lilypond-binaries"),
        (os.path.join(ROOT, "tests", "fixtures", "test_tune.json"), "fixtures"),
    ],
    hiddenimports=["PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtPrintSupport", "PySide6.QtSvg"],
    excludes=["torch", "tkinter", "music21", "matplotlib", "pytest", "IPython", "pandas", "scipy", "sklearn"] + QT_UNUSED,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="RealBookGuitar", console=False,
          icon=os.path.join(ROOT, "packaging", "icon.ico") if os.path.exists(os.path.join(ROOT, "packaging", "icon.ico")) else None)
coll = COLLECT(exe, a.binaries, a.datas, name="RealBookGuitar")
