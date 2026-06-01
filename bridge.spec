# PyInstaller onedir 配方（spec §6.3）。pydantic v2 带 Rust 扩展 pydantic_core；
# FastAPI 链含 anyio/starlette/httpcore/h11/certifi；adapters 由 load_adapters()
# importlib 动态加载 → 必须 collect_submodules("app")。
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []
# 含 httpx outbound 链路依赖（Codex plan-review #6）：h11/idna/sniffio + dotenv。
# try/except 包裹：某可选包未装时跳过而非让构建崩。
for pkg in ("pydantic", "pydantic_core", "anyio", "sniffio", "starlette",
            "httpx", "httpcore", "h11", "certifi", "idna", "dotenv"):
    try:
        d, b, h = collect_all(pkg)
    except Exception:
        continue
    datas += d
    binaries += b
    hiddenimports += h

hiddenimports += collect_submodules("app")  # app.adapters.* / app.adapters.fal_ai.*（动态 import）
hiddenimports += [
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.lifespan.on",
]

a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="bridge",
    console=True,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False, name="bridge",
)
