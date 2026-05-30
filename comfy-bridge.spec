# PyInstaller spec — build the standalone comfy-bridge server exe (no Python on target).
# Build via: windows\build-exe.ps1  (installs .[build] then `pyinstaller comfy-bridge.spec`).
#
# Two things PyInstaller's static analysis can't see on its own, hence the explicit collects:
#  - app.adapters.* are imported dynamically (importlib) in adapters.load_adapters() —
#    without collecting the whole `app` package the exe 424s with "adapter not registered".
#  - uvicorn imports its loop/protocol/lifespan implementations by string at runtime.
from PyInstaller.utils.hooks import collect_submodules, collect_all

hiddenimports = []
hiddenimports += collect_submodules("app")
hiddenimports += collect_submodules("uvicorn")

datas = []
binaries = []
for _pkg in ("pydantic", "pydantic_core"):
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h

a = Analysis(
    ["serve.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Keep the build lean: the bridge never imports these heavyweight / dev-only deps.
    excludes=["torch", "numpy", "tkinter", "matplotlib", "PIL", "pytest", "respx", "IPython"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="comfy-bridge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
