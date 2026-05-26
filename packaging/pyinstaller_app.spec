import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(globals().get("SPECPATH", ".")).resolve().parent

os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(ROOT / "build" / "paddlex_cache"))
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
try:
    os.makedirs(os.environ["PADDLE_PDX_CACHE_HOME"], exist_ok=True)
except Exception:
    pass

hiddenimports = collect_submodules("pdfplumber")
hiddenimports += collect_submodules("paddleocr")
hiddenimports += collect_submodules("paddlex")
hiddenimports += collect_submodules("paddle")
hiddenimports += collect_submodules("cv2")
hiddenimports += collect_submodules("shapely")
hiddenimports += collect_submodules("pyclipper")

a = Analysis(
    [str(ROOT / "app.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "templates"), "templates"),
        (str(ROOT / "static"), "static"),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="DebtExtractor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)

if sys.platform == "darwin":
    app = BUNDLE(exe, name="DebtExtractor.app")
else:
    app = exe
