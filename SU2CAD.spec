from pathlib import Path
import sys


root = Path(SPECPATH)
sys.path.insert(0, str(root / "app"))
sys.path.insert(0, str(root / "scripts"))

a = Analysis(
    [str(root / "app" / "su2cad_app.py")],
    pathex=[str(root), str(root / "app"), str(root / "scripts")],
    binaries=[],
    datas=[(str(root / "scripts" / "export_current_view.rb"), "scripts")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
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
    [],
    exclude_binaries=True,
    name="SU2CAD",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="SU2CAD",
)
