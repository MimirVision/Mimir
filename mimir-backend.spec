# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


ROOT = Path(SPECPATH)

datas = []

for filename in [
    "config.py",
    "yolo11n.pt",
    "yolov8n.pt",
]:
    path = ROOT / filename
    if path.exists():
        datas.append((str(path), "."))


a = Analysis(
    ["mimir_backend_cli.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "tesla_ai_sorter",
        "mimir_clip_actions",
        "config",
        "cv2",
        "numpy",
        "requests",
        "rich",
        "ultralytics",
        "torch",
        "PIL",
    ],
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
    name="mimir-backend",
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

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="mimir-backend",
)
