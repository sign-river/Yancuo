# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files


packaging_dir = Path(SPECPATH)
windows_dir = packaging_dir.parent
source_dir = windows_dir / "src"

datas = collect_data_files("yancuo_win")
hiddenimports = ["keyring.backends.Windows", "keyring.backends.fail"]

analysis = Analysis(
    [str(source_dir / "yancuo_win" / "__main__.py")],
    pathex=[str(source_dir)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "IPython",
        "PIL",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "docutils",
        "jedi",
        "matplotlib",
        "numpy",
        "pytest",
        "sphinx",
        "tkinter",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="Yancuo",
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
bundle = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Yancuo",
)
