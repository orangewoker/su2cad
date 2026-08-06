from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_data_files


root = Path(SPECPATH)
sys.path.insert(0, str(root / "app"))
sys.path.insert(0, str(root / "scripts"))
customtkinter_data = collect_data_files("customtkinter")
app_icon = root / "assets" / "su2cad.ico"
app_icon_pngs = [
    root / "assets" / f"su2cad-{size}.png"
    for size in (16, 20, 24, 32, 40, 48, 64, 128, 256)
]

a = Analysis(
    [str(root / "app" / "su2cad_app.py")],
    pathex=[str(root), str(root / "app"), str(root / "scripts")],
    binaries=[],
    datas=[
        (str(root / "scripts" / "export_current_view.rb"), "scripts"),
        (str(root / "scripts" / "repair_cad_dialogs.ps1"), "scripts"),
        (str(app_icon), "assets"),
        *((str(path), "assets") for path in app_icon_pngs),
        *customtkinter_data,
    ],
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
    icon=str(app_icon),
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
