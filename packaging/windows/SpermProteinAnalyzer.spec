# -*- mode: python ; coding: utf-8 -*-

import os

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

project_root = os.path.abspath(os.path.join(SPECPATH, "..", ".."))

hiddenimports = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "cv2",
    "tifffile",
    "openpyxl",
]
hiddenimports += collect_submodules("cv2")
hiddenimports += collect_submodules("tifffile")
hiddenimports += collect_submodules("openpyxl")

datas = [
    (os.path.join(project_root, "assets"), "assets"),
    (os.path.join(project_root, "pipelines"), "pipelines"),
    (os.path.join(project_root, "config.ini"), "."),
    (os.path.join(project_root, "pipeline_params.ini"), "."),
]
datas += collect_data_files("cv2")
datas += collect_data_files("tifffile")
datas += collect_data_files("openpyxl")

binaries = collect_dynamic_libs("cv2")

a = Analysis(
    [os.path.join(project_root, "main.py")],
    pathex=[project_root],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SpermProteinAnalyzer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[os.path.join(project_root, "assets", "app_icon.ico")],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="SpermProteinAnalyzer",
)
