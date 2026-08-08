from pathlib import Path
import sys


root = Path(SPECPATH)
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "app"))
sys.path.insert(0, str(root / "scripts"))

a = Analysis(
    [str(root / "app" / "sidecar.py")],
    pathex=[str(root), str(root / "app"), str(root / "scripts")],
    binaries=[],
    datas=[
        (str(root / "scripts" / "export_current_view.rb"), "scripts"),
        (str(root / "scripts" / "repair_cad_dialogs.ps1"), "scripts"),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "customtkinter",
        "tkinter",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "matplotlib",
        "pandas",
        "scipy",
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="su2cad-core",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
