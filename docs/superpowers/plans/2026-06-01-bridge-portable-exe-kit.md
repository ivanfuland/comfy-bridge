# comfy-bridge 便携 exe 套件 + GitHub Releases 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 comfy-bridge（FastAPI/uvicorn 代理）打包成 Windows 便携 exe 套件（bridge.exe + gating custom_node + .env 模板 + 一键脚本），经 GitHub Actions（windows-latest，tag 触发）发布到 GitHub Releases，他人下载即可开箱接入自己的 ComfyUI 便携包。

**Architecture:** 边车（sidecar）模型——bridge.exe 与 ComfyUI 两进程并排、靠「ComfyUI 加 `--comfy-api-base` 路由 + gating custom_node」两个胶水点松耦合。PyInstaller onedir 冻结；新增 `run.py` 冻结入口把路径解析/配置预检从 `app/_portable.py`（可单测）取出；release.yml 在 Windows runner 上构建→exe 冒烟测试→组装 zip→上传 Release。

**Tech Stack:** Python 3.12.6、FastAPI 0.136.3、uvicorn 0.48.0、pydantic 2.13.4 / pydantic-core 2.46.4、PyInstaller（onedir）、Windows batch、GitHub Actions、`softprops/action-gh-release`。

**Spec:** `docs/superpowers/specs/2026-06-01-bridge-portable-exe-design.md`（已 Codex 对抗审核，§14 采纳清单）。

---

## 文件结构

**新建：**
- `app/_portable.py` — 便携模式纯函数助手（路径解析 + base-url 预检），可单测
- `run.py` — 冻结入口（薄，调用 `app/_portable.py`）
- `bridge.spec` — PyInstaller 配方（`collect_all` + `collect_submodules`）
- `packaging/constraints-build.txt` — 锁定构建依赖版本
- `packaging/.env.example.kit` — 发布用 .env 模板（预填雷火 base / 日志关 / key 空）
- `packaging/install.bat` — 套件安装器（装 gating + 生成兄弟启动器）
- `packaging/start-bridge.bat` — 套件启动器
- `packaging/uninstall.bat` — 套件卸载器
- `packaging/接入说明.txt` — 接收方说明
- `.github/workflows/release.yml` — 发布工作流
- `tests/test_portable.py` — `app/_portable.py` 单测
- `tests/test_kit_env_template.py` — 校验发布 .env 模板不变量

**修改：**
- `pyproject.toml` — 增 `build` 可选依赖（pyinstaller）

> 现有 `app/config.py` / `app/adapters/base.py` **不改代码**：base-url 兜底/日志默认由发布 .env 模板覆盖；预检由 `run.py` 启动时做。这样不动已 42 测试全绿的核心逻辑。

---

## Task 1: 便携助手 `app/_portable.py`（TDD）

**Files:**
- Create: `app/_portable.py`
- Test: `tests/test_portable.py`

- [ ] **Step 1: 写失败测试**

`tests/test_portable.py`：

```python
import os
from app._portable import resolve_base_dir, missing_bases_for_filled_keys


def test_resolve_finds_env_in_start_dir(tmp_path):
    (tmp_path / ".env").write_text("X=1", encoding="utf-8")
    assert resolve_base_dir(str(tmp_path)) == str(tmp_path)


def test_resolve_walks_up_to_parent(tmp_path):
    (tmp_path / ".env").write_text("X=1", encoding="utf-8")
    deep = tmp_path / "bridge"
    deep.mkdir()
    assert resolve_base_dir(str(deep)) == str(tmp_path)


def test_resolve_stops_after_max_up_and_returns_start(tmp_path):
    # .env 放在 4 层之上，超过 max_up=3 → 找不到 → 退回 start
    (tmp_path / ".env").write_text("X=1", encoding="utf-8")
    deep = tmp_path / "a" / "b" / "c" / "d"
    deep.mkdir(parents=True)
    assert resolve_base_dir(str(deep), max_up=3) == str(deep)


def test_resolve_no_env_returns_start(tmp_path):
    deep = tmp_path / "bridge"
    deep.mkdir()
    assert resolve_base_dir(str(deep)) == str(deep)


def test_missing_bases_flags_key_set_base_empty():
    env = {"OPENAI_API_KEY": "sk-x", "OPENAI_BASE_URL": ""}
    assert missing_bases_for_filled_keys(env) == ["OPENAI"]


def test_missing_bases_ok_when_both_set():
    env = {"OPENAI_API_KEY": "sk-x", "OPENAI_BASE_URL": "https://g"}
    assert missing_bases_for_filled_keys(env) == []


def test_missing_bases_ignores_empty_key():
    env = {"GEMINI_API_KEY": "  ", "GEMINI_BASE_URL": ""}
    assert missing_bases_for_filled_keys(env) == []


def test_missing_bases_covers_all_five_providers():
    env = {f"{p}_API_KEY": "k" for p in ["OPENAI", "ANTHROPIC", "GEMINI", "TRIPO", "BYTEPLUS"]}
    # 所有 BASE 都缺 → 五个都报
    assert sorted(missing_bases_for_filled_keys(env)) == sorted(
        ["OPENAI", "ANTHROPIC", "GEMINI", "TRIPO", "BYTEPLUS"]
    )
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python -m pytest tests/test_portable.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app._portable'`

- [ ] **Step 3: 写最小实现**

`app/_portable.py`：

```python
"""Portable-mode helpers for the frozen bridge.exe. Pure functions, no I/O side
effects beyond filesystem existence checks — kept out of run.py so they're unit-
testable and importable WITHOUT triggering app.config's top-level load_dotenv()."""
import os

# Providers whose {P}_API_KEY / {P}_BASE_URL pair the bridge proxies (spec §7.1).
_PROVIDERS = ["OPENAI", "ANTHROPIC", "GEMINI", "TRIPO", "BYTEPLUS"]


def resolve_base_dir(start_dir: str, marker: str = ".env", max_up: int = 3) -> str:
    """Walk up from start_dir (inclusive) at most max_up parents looking for a dir
    containing `marker`. Return the first match, else start_dir. Used to locate the
    kit root (which holds .env / asset-cache / logs) from the exe's own location."""
    d = start_dir
    for _ in range(max_up + 1):
        if os.path.exists(os.path.join(d, marker)):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return start_dir


def missing_bases_for_filled_keys(env) -> list:
    """Return providers whose API key is set (non-blank) but BASE_URL is empty/unset.
    Portable fail-fast guard (Codex #6): a filled key with no base would silently fall
    back to the official upstream in config.py — leaking the gateway key to the wrong
    host. Order follows _PROVIDERS for deterministic messaging."""
    missing = []
    for p in _PROVIDERS:
        key = (env.get(f"{p}_API_KEY") or "").strip()
        base = (env.get(f"{p}_BASE_URL") or "").strip()
        if key and not base:
            missing.append(p)
    return missing
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python -m pytest tests/test_portable.py -v`
Expected: PASS（8 passed）

- [ ] **Step 5: 确认未破坏既有测试**

Run: `.venv\Scripts\python -m pytest -q`
Expected: 全绿（原 42 + 新 8）

- [ ] **Step 6: 提交**

```bash
git add app/_portable.py tests/test_portable.py
git commit -m "feat(portable): add base-dir resolution + base-url preflight helpers"
```

---

## Task 2: 冻结入口 `run.py`

**Files:**
- Create: `run.py`

> `run.py` 的核心逻辑（路径/预检）已在 Task 1 单测覆盖；本任务只组装 + 用源码模式实跑验证（冻结后的验证在 Task 4）。

- [ ] **Step 1: 写 `run.py`**

```python
"""Frozen entrypoint for the portable bridge.exe (PyInstaller onedir, spec §6.2).

Order is load-bearing (Codex #5): resolve kit root -> chdir -> load_dotenv(override)
-> preflight -> ONLY THEN import app.main (which imports app.config). Importing config
before the .env load would let stale process env vars win / miss the kit .env entirely."""
import os
import sys

from app._portable import resolve_base_dir, missing_bases_for_filled_keys


def _start_dir() -> str:
    # Frozen: dir holding bridge.exe. Source: this file's dir.
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def main() -> None:
    base = resolve_base_dir(_start_dir())
    os.chdir(base)  # so app.config load_dotenv() + default asset-cache/logs resolve to kit root

    from dotenv import load_dotenv
    load_dotenv(os.path.join(base, ".env"), override=True)

    missing = missing_bases_for_filled_keys(os.environ)
    if missing:
        sys.stderr.write(
            "[bridge] 配置错误：以下 provider 填了 API key 但 *_BASE_URL 为空："
            + ", ".join(missing) + "\n"
            "[bridge] 便携套件应使用预填雷火网关地址的 .env（见 .env.example）；"
            "补全对应 *_BASE_URL（如 https://ai.leihuo.netease.com）后重试。\n"
        )
        raise SystemExit(2)

    host = os.getenv("BRIDGE_HOST", "127.0.0.1")
    port = int(os.getenv("BRIDGE_PORT", "8190"))
    log_io = os.getenv("BRIDGE_LOG_IO", "on")
    print(f"[bridge] config from {os.path.join(base, '.env')} | host={host} port={port} log_io={log_io}")

    import uvicorn
    from app.main import app
    uvicorn.run(app, host=host, port=port, loop="asyncio", http="h11", log_config=None)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 源码模式实跑（需一份临时 .env）**

```bash
copy .env .env.runpy.bak 2>nul
.venv\Scripts\python run.py
```
Expected: 控制台打印 `[bridge] config from ... port=8190 ...` 且 uvicorn 在 8190 监听（无 traceback）。

- [ ] **Step 3: 另开终端验证健康端点**

Run: `powershell -Command "(Invoke-RestMethod http://127.0.0.1:8190/comfy-bridge/gating).gating_enabled"`
Expected: `True`。验证完 `Ctrl+C` 停掉 run.py。

- [ ] **Step 4: 提交**

```bash
git add run.py
git commit -m "feat(portable): add run.py frozen entrypoint (chdir + override dotenv + preflight)"
```

---

## Task 3: 构建依赖锁定 `packaging/constraints-build.txt` + pyproject build extra

**Files:**
- Create: `packaging/constraints-build.txt`
- Modify: `pyproject.toml`（`[project.optional-dependencies]` 增 `build`）

- [ ] **Step 1: 写 `packaging/constraints-build.txt`**

（版本取自当前已验证 venv，2026-06-01）

```
# 构建可复现性锁定（Codex #7）。CI 与本地构建共用，避免 >= 解析漂移。
fastapi==0.136.3
uvicorn==0.48.0
httpx==0.28.1
httpcore==1.0.9
h11==0.16.0
pydantic==2.13.4
pydantic-core==2.46.4
anyio==4.13.0
starlette==1.2.0
certifi==2026.5.20
python-dotenv==1.2.2
```

> pyinstaller 的固定版本在 Task 4 首次成功构建后回填到本文件末尾（见 Task 4 Step 5）。

- [ ] **Step 2: 修改 `pyproject.toml` 增 build extra**

把：
```toml
[project.optional-dependencies]
dev = ["pytest", "pytest-asyncio", "respx"]
```
改为：
```toml
[project.optional-dependencies]
dev = ["pytest", "pytest-asyncio", "respx"]
build = ["pyinstaller"]
```

- [ ] **Step 3: 验证 pyproject 可解析**

Run: `.venv\Scripts\python -c "import tomllib; tomllib.load(open('pyproject.toml','rb')); print('ok')"`
Expected: `ok`

- [ ] **Step 4: 提交**

```bash
git add packaging/constraints-build.txt pyproject.toml
git commit -m "build: pin build deps + add build optional-dependency extra"
```

---

## Task 4: PyInstaller 配方 `bridge.spec` + 本地构建冒烟

**Files:**
- Create: `bridge.spec`
- Modify: `packaging/constraints-build.txt`（回填 pyinstaller 版本）

- [ ] **Step 1: 装 pyinstaller（锁定将于 Step 5 回填）**

Run: `.venv\Scripts\python -m pip install pyinstaller`
Expected: 安装成功。

- [ ] **Step 2: 写 `bridge.spec`**

```python
# PyInstaller onedir 配方（spec §6.3）。pydantic v2 带 Rust 扩展 pydantic_core；
# FastAPI 链含 anyio/starlette/httpcore/h11/certifi；adapters 由 load_adapters()
# importlib 动态加载 → 必须 collect_submodules("app")。
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []
for pkg in ("pydantic", "pydantic_core", "anyio", "starlette", "httpx", "httpcore", "certifi"):
    d, b, h = collect_all(pkg)
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
```

- [ ] **Step 3: 本地构建**

Run: `.venv\Scripts\pyinstaller bridge.spec --noconfirm`
Expected: 生成 `dist\bridge\bridge.exe`（无致命 hidden-import 报错）。

- [ ] **Step 4: 冻结产物冒烟测试（关键，Codex #4）**

在 `dist\bridge\` 旁放一份最小 .env 再跑 exe：

```bat
copy ..\..\.env dist\bridge\.env
start "" dist\bridge\bridge.exe
powershell -Command "Start-Sleep 4; (Invoke-RestMethod http://127.0.0.1:8190/comfy-bridge/gating).gating_enabled"
```
Expected: `True`（证明冻结后 fastapi/uvicorn/pydantic_core/adapters 全部收齐）。验证后结束 exe 进程并删 `dist\bridge\.env`。

> 若报缺包（如 `No module named pydantic_core`），把缺失包加进 `bridge.spec` 的 `collect_all` 列表，回 Step 3 重建。

- [ ] **Step 5: 回填 pyinstaller 锁定版本**

Run: `.venv\Scripts\python -c "import importlib.metadata as m; print('pyinstaller=='+m.version('pyinstaller'))"`
把输出（如 `pyinstaller==6.16.0`）追加到 `packaging/constraints-build.txt` 末尾。

- [ ] **Step 6: 提交（不提交 build/ 与 dist/）**

先确保忽略产物——把 `build/` 和 `dist/` 加进 `.gitignore`：
```
build/
dist/
```
再提交：
```bash
git add bridge.spec packaging/constraints-build.txt .gitignore
git commit -m "build: add PyInstaller bridge.spec (onedir) + pin pyinstaller; ignore build artifacts"
```

---

## Task 5: 发布 .env 模板 `packaging/.env.example.kit`（TDD 不变量）

**Files:**
- Create: `packaging/.env.example.kit`
- Test: `tests/test_kit_env_template.py`

- [ ] **Step 1: 写失败测试**

`tests/test_kit_env_template.py`：

```python
import os

TEMPLATE = os.path.join("packaging", ".env.example.kit")
GATEWAY = "https://ai.leihuo.netease.com"
PROVIDERS = ["OPENAI", "ANTHROPIC", "GEMINI", "TRIPO", "BYTEPLUS"]


def _parse(path):
    """Return {KEY: VALUE} from active (non-comment, non-blank) `K=V` lines."""
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def test_all_base_urls_prefilled_to_gateway():
    env = _parse(TEMPLATE)
    for p in PROVIDERS:
        assert env.get(f"{p}_BASE_URL") == GATEWAY, f"{p}_BASE_URL must be pre-filled to gateway"


def test_all_api_keys_present_and_blank():
    env = _parse(TEMPLATE)
    for p in PROVIDERS:
        assert f"{p}_API_KEY" in env, f"{p}_API_KEY line must exist"
        assert env[f"{p}_API_KEY"] == "", f"{p}_API_KEY must ship blank (bring-your-own-key)"


def test_log_io_defaults_off():
    assert _parse(TEMPLATE).get("BRIDGE_LOG_IO") == "off"


def test_port_not_actively_exposed():
    # 套件锁死 8190：不应有 active 的 BRIDGE_PORT= 行（注释说明可以有）
    assert "BRIDGE_PORT" not in _parse(TEMPLATE)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python -m pytest tests/test_kit_env_template.py -v`
Expected: FAIL —文件不存在 / 断言失败。

- [ ] **Step 3: 写 `packaging/.env.example.kit`**

```dotenv
# ===== comfy-bridge 便携套件配置 =====
# 只需做一件事：把你自己的【雷火网关 key】粘到下面要用的 *_API_KEY 后面。
# 雷火网关同一把 key 通吃 OpenAI/Anthropic/Gemini/ByteDance；Tripo 同理。
# 改完保存，双击 start-bridge.bat 即可。

# --- 网关地址（已预填，请勿改）---
OPENAI_BASE_URL=https://ai.leihuo.netease.com
ANTHROPIC_BASE_URL=https://ai.leihuo.netease.com
GEMINI_BASE_URL=https://ai.leihuo.netease.com
TRIPO_BASE_URL=https://ai.leihuo.netease.com
BYTEPLUS_BASE_URL=https://ai.leihuo.netease.com

# --- 你的 key（填这里）---
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
GEMINI_API_KEY=
TRIPO_API_KEY=
BYTEPLUS_API_KEY=

# --- 其它（一般不用动）---
ANTHROPIC_VERSION=2023-06-01
BRIDGE_HOST=127.0.0.1
BRIDGE_CORS_ORIGINS=http://127.0.0.1:8188,http://localhost:8188
# 端口锁定 8190：gating 节点与启动器都写死 8190，改端口需同步改，普通用户勿动。
# BRIDGE_PORT=8190
# 日志默认关闭；开启会把你的 prompt/响应内容写进 logs\，注意隐私后再开。
BRIDGE_LOG_IO=off
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python -m pytest tests/test_kit_env_template.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add packaging/.env.example.kit tests/test_kit_env_template.py
git commit -m "feat(kit): add release .env template (gateway-prefilled, keys blank, log off) + invariants test"
```

---

## Task 6: 套件启动器 `packaging/start-bridge.bat`

**Files:**
- Create: `packaging/start-bridge.bat`

> 套件 zip 解压后布局：根目录有 `start-bridge.bat` / `.env` / `bridge\bridge.exe`。`run.py` 从 `bridge\` 向上找 `.env`（Task 1）→ 命中根目录。

- [ ] **Step 1: 写 `packaging/start-bridge.bat`**

```bat
@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist ".env" (
  echo [comfy-bridge] 未找到 .env。
  echo   请先把 .env.example 复制为 .env，并填入你的雷火网关 key。
  pause
  exit /b 1
)

rem 至少一个 *_API_KEY 非空（行尾 = 后面有字符）
findstr /R /C:"_API_KEY=." ".env" >nul
if errorlevel 1 (
  echo [comfy-bridge] .env 里所有 *_API_KEY 都为空。
  echo   请打开 .env 填入你的雷火网关 key 后再启动。
  pause
  exit /b 1
)

if not exist "bridge\bridge.exe" (
  echo [comfy-bridge] 缺少 bridge\bridge.exe，套件可能未完整解压。
  pause
  exit /b 1
)

echo [comfy-bridge] starting on http://127.0.0.1:8190  （关闭本窗口即停止服务）
"%~dp0bridge\bridge.exe"
```

- [ ] **Step 2: 语法自检（在已构建的 dist 上手验）**

把 Task 4 的 `dist\bridge\` 复制为 `kittest\bridge\`、`packaging\start-bridge.bat` 复制到 `kittest\`、`.env` 复制到 `kittest\`，双击 `kittest\start-bridge.bat`：
Expected: 打印 starting 并在 8190 起服务；删空 key 的 .env 时应提示填 key 并 pause。验证后关窗、删 `kittest\`。

- [ ] **Step 3: 提交**

```bash
git add packaging/start-bridge.bat
git commit -m "feat(kit): add start-bridge.bat launcher with .env/key preflight"
```

---

## Task 7: 套件安装器 `packaging/install.bat`

**Files:**
- Create: `packaging/install.bat`

实现 spec §8.1：定位 ComfyUI 便携包 → 拷 gating 节点 → 生成兄弟启动器（full-replicate：复制官方 bat 全文，仅在 `main.py` 行尾插 `--comfy-api-base`）→ 复制 .env → 结构预检。

- [ ] **Step 1: 写 `packaging/install.bat`**

```bat
@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo   comfy-bridge 接入 ComfyUI 便携包
echo ============================================================
echo.

rem ---- 1. 定位 ComfyUI 便携包根目录 ----
set "ROOT=%~1"
if "%ROOT%"=="" (
  echo 请把【ComfyUI 便携包根目录】（含 run_nvidia_gpu.bat 那层）拖到本窗口后回车：
  set /p "ROOT=> "
)
rem 去掉可能的成对引号
set "ROOT=%ROOT:"=%"
if "%ROOT%"=="" ( echo 未输入路径。& pause & exit /b 1 )

rem ---- 2. 结构预检（Codex #11，便携包合法性）----
if not exist "%ROOT%\run_nvidia_gpu.bat" ( echo [错误] 找不到 %ROOT%\run_nvidia_gpu.bat，确认是 ComfyUI 便携包根目录。& pause & exit /b 1 )
if not exist "%ROOT%\ComfyUI\main.py"   ( echo [错误] 找不到 %ROOT%\ComfyUI\main.py。& pause & exit /b 1 )
if not exist "%ROOT%\python_embeded\python.exe" ( echo [警告] 未见 python_embeded，可能非标准便携包，继续需自行确认。& pause )

rem ---- 3. 拷 gating custom_node ----
set "DEST=%ROOT%\ComfyUI\custom_nodes\comfy-bridge-gating"
echo [1/3] 安装 gating 节点 -^> %DEST%
if not exist "comfy-bridge-gating\__init__.py" ( echo [错误] 套件缺少 comfy-bridge-gating，解压不完整。& pause & exit /b 1 )
robocopy "comfy-bridge-gating" "%DEST%" /MIR /NJH /NJS /NDL /NP >nul
if errorlevel 8 ( echo [错误] 复制 gating 节点失败。& pause & exit /b 1 )

rem ---- 4. 生成兄弟启动器（full-replicate + 插参；不动官方 bat）----
set "SRC=%ROOT%\run_nvidia_gpu.bat"
set "DST=%ROOT%\run_nvidia_gpu_bridge.bat"
echo [2/3] 生成启动器 -^> %DST%
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$flag=' --comfy-api-base=http://127.0.0.1:8190';" ^
  "$lines = Get-Content -LiteralPath '%SRC%';" ^
  "$out = $lines | ForEach-Object { if ($_ -match 'python.*main\.py' -and $_ -notmatch 'comfy-api-base') { ($_.TrimEnd() + $flag) } else { $_ } };" ^
  "Set-Content -LiteralPath '%DST%' -Value $out -Encoding Default"
if not exist "%DST%" ( echo [错误] 启动器生成失败。& pause & exit /b 1 )
findstr /C:"comfy-api-base" "%DST%" >nul || ( echo [错误] 启动器未含 --comfy-api-base，官方 bat 启动行可能非常规，请手动加参数。& pause & exit /b 1 )

rem ---- 5. 准备 .env ----
echo [3/3] 准备配置文件
if not exist ".env" ( copy /Y ".env.example" ".env" >nul & echo   已生成 .env，请记得填入你的雷火网关 key。)

echo.
echo ============================================================
echo   完成！日常用法：
echo   1) 双击本套件的 start-bridge.bat 启动 bridge
echo   2) 双击 %ROOT%\run_nvidia_gpu_bridge.bat 启动 ComfyUI
echo   （仍用官方 run_nvidia_gpu.bat 则不会接入 bridge）
echo ============================================================
pause
```

> 设计依据（spec §8.1）：ComfyUI 官方便携 `run_nvidia_gpu.bat` 的启动行通常**不带** `%*`，故采用 full-replicate（复制全文、仅 `main.py` 行尾插参），而非 wrap-via-call。`/MIR` 使重复安装幂等；`%notmatch comfy-api-base%` 防重复插参。

- [ ] **Step 2: 用一份真实/模拟便携包验证**

准备一个目录 `mockcomfy\`，内含 `run_nvidia_gpu.bat`（一行：`.\python_embeded\python.exe -s ComfyUI\main.py --windows-standalone-build` + 一行 `pause`）、`ComfyUI\main.py`、`python_embeded\python.exe`（空占位）。把 `packaging\install.bat` 与 `comfy-bridge-gating\`（从仓库 `custom_nodes\` 拷）放到套件目录后运行：

Run: `packaging\install.bat <绝对路径>\mockcomfy`
Expected:
- `mockcomfy\ComfyUI\custom_nodes\comfy-bridge-gating\__init__.py` 存在；
- `mockcomfy\run_nvidia_gpu_bridge.bat` 存在，且 main.py 行尾含 `--comfy-api-base=http://127.0.0.1:8190`；官方 `run_nvidia_gpu.bat` 内容未变；
- 含空格路径（`mock comfy\`）重跑仍成功；再跑一次幂等（不重复插参）。

- [ ] **Step 3: 提交**

```bash
git add packaging/install.bat
git commit -m "feat(kit): add install.bat (copy gating + generate sibling launcher, idempotent)"
```

---

## Task 8: 套件卸载器 `packaging/uninstall.bat`

**Files:**
- Create: `packaging/uninstall.bat`

- [ ] **Step 1: 写 `packaging/uninstall.bat`**

```bat
@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "ROOT=%~1"
if "%ROOT%"=="" (
  echo 请把【ComfyUI 便携包根目录】拖到本窗口后回车：
  set /p "ROOT=> "
)
set "ROOT=%ROOT:"=%"
if "%ROOT%"=="" ( echo 未输入路径。& pause & exit /b 1 )

set "DEST=%ROOT%\ComfyUI\custom_nodes\comfy-bridge-gating"
if exist "%DEST%" ( rmdir /S /Q "%DEST%" & echo 已删除 gating 节点。) else ( echo 未发现 gating 节点，跳过。)

if exist "%ROOT%\run_nvidia_gpu_bridge.bat" ( del /Q "%ROOT%\run_nvidia_gpu_bridge.bat" & echo 已删除 run_nvidia_gpu_bridge.bat。) else ( echo 未发现兄弟启动器，跳过。)

echo 卸载完成。官方 run_nvidia_gpu.bat 未受影响。
pause
```

- [ ] **Step 2: 验证复原**

在 Task 7 的 `mockcomfy\` 上运行：
Run: `packaging\uninstall.bat <绝对路径>\mockcomfy`
Expected: `comfy-bridge-gating\` 与 `run_nvidia_gpu_bridge.bat` 均被删；`run_nvidia_gpu.bat` 仍在。

- [ ] **Step 3: 提交**

```bash
git add packaging/uninstall.bat
git commit -m "feat(kit): add uninstall.bat (remove gating + sibling launcher)"
```

---

## Task 9: 接收方说明 `packaging/接入说明.txt`

**Files:**
- Create: `packaging/接入说明.txt`

- [ ] **Step 1: 写 `packaging/接入说明.txt`**

```text
comfy-bridge 便携套件 · 接入说明
====================================

前提：你已装好 ComfyUI 官方便携包（含 run_nvidia_gpu.bat），且有一把雷火网关 key。

三步接入：
1) 用记事本打开本文件夹里的 .env，把你的雷火网关 key 粘到要用的 *_API_KEY 后面，保存。
   （没有 .env？先把 .env.example 复制一份改名为 .env）
2) 双击 install.bat，按提示把【ComfyUI 便携包根目录】拖进去回车。
   它会装好节点，并在便携包里生成一个 run_nvidia_gpu_bridge.bat。
3) 双击本文件夹的 start-bridge.bat 启动 bridge（这个黑窗口别关）；
   再双击便携包里的 run_nvidia_gpu_bridge.bat 启动 ComfyUI。

完成。ComfyUI 里的 OpenAI/Gemini/Tripo/ByteDance 等云节点会走你的 key、不扣 comfy.org 积分。

注意事项：
- 首次运行 bridge.exe，Windows 可能弹 SmartScreen 蓝盾：点“更多信息”→“仍要运行”。
- 端口固定 8190，请勿改 .env 里的端口。
- 想撤销：双击 uninstall.bat 指向便携包即可干净复原。
- 启动器二选一：要接入 bridge 用 run_nvidia_gpu_bridge.bat；用官方 run_nvidia_gpu.bat 则不接入。
```

- [ ] **Step 2: 提交**

```bash
git add "packaging/接入说明.txt"
git commit -m "docs(kit): add recipient setup guide"
```

---

## Task 10: 发布工作流 `.github/workflows/release.yml`

**Files:**
- Create: `.github/workflows/release.yml`

- [ ] **Step 1: 写 `.github/workflows/release.yml`**

```yaml
name: Release Portable Kit

on:
  push:
    tags: ["v*"]

permissions:
  contents: write

jobs:
  build-kit:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install deps (pinned)
        run: |
          python -m pip install --upgrade pip
          pip install -e . -c packaging/constraints-build.txt
          pip install pyinstaller -c packaging/constraints-build.txt

      - name: Build exe (onedir)
        run: pyinstaller bridge.spec --noconfirm

      - name: Smoke test frozen exe
        shell: pwsh
        run: |
          Copy-Item packaging/.env.example.kit dist/bridge/.env
          $p = Start-Process dist/bridge/bridge.exe -PassThru
          try {
            $ok = $false
            foreach ($i in 1..15) {
              Start-Sleep 2
              try {
                $r = Invoke-RestMethod http://127.0.0.1:8190/comfy-bridge/gating -TimeoutSec 3
                if ($r.gating_enabled) { $ok = $true; break }
              } catch {}
            }
            if (-not $ok) { throw "frozen exe smoke test failed: /comfy-bridge/gating not healthy" }
            Write-Host "smoke test OK"
          } finally {
            Stop-Process -Id $p.Id -Force
            Remove-Item dist/bridge/.env -Force
          }

      - name: Verify gating node files present
        shell: pwsh
        run: |
          foreach ($f in @("custom_nodes/comfy-bridge-gating/__init__.py",
                           "custom_nodes/comfy-bridge-gating/web/comfy-bridge-gating.js")) {
            if (-not (Test-Path $f)) { throw "missing $f" }
          }

      - name: Assemble kit
        shell: pwsh
        run: |
          $kit = "comfy-bridge-kit"
          New-Item -ItemType Directory -Force -Path $kit | Out-Null
          Copy-Item dist/bridge "$kit/bridge" -Recurse
          Copy-Item custom_nodes/comfy-bridge-gating "$kit/comfy-bridge-gating" -Recurse
          Copy-Item packaging/.env.example.kit "$kit/.env.example"
          Copy-Item packaging/install.bat "$kit/"
          Copy-Item packaging/start-bridge.bat "$kit/"
          Copy-Item packaging/uninstall.bat "$kit/"
          Copy-Item "packaging/接入说明.txt" "$kit/"
          $zip = "comfy-bridge-kit-$($env:GITHUB_REF_NAME).zip"
          Compress-Archive -Path "$kit/*" -DestinationPath $zip -Force
          echo "ZIP=$zip" >> $env:GITHUB_ENV

      - name: Publish release
        uses: softprops/action-gh-release@v2
        with:
          files: ${{ env.ZIP }}
          fail_on_unmatched_files: true
          generate_release_notes: true
```

- [ ] **Step 2: 本地静态校验 YAML**

Run: `.venv\Scripts\python -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml',encoding='utf-8')); print('yaml ok')"`
Expected: `yaml ok`
（若无 pyyaml：`.venv\Scripts\python -m pip install pyyaml` 后重试。）

- [ ] **Step 3: 提交**

```bash
git add .github/workflows/release.yml
git commit -m "ci: add release.yml (windows build + frozen smoke test + kit zip to GitHub Release)"
```

---

## Task 11: 端到端验证（人工，干净机器）

**Files:** 无（验证任务，对应 spec §11）

- [ ] **Step 1: 触发一次预发布构建**

```bash
git push origin feat/portable-exe-kit
git tag v0.1.0-rc1
git push origin v0.1.0-rc1
```
> 注：push / tag 推送属对外动作，按用户「push/PR 单独问」习惯，执行前需用户确认。
Expected: GitHub Actions `Release Portable Kit` 绿；Releases 出现 `v0.1.0-rc1` 且带 `comfy-bridge-kit-v0.1.0-rc1.zip`。

- [ ] **Step 2: 干净机器（未装 Python 的 Win x64 / VM）冒烟**

下载并解压 zip → 编辑 `.env` 填测试 key → 双击 `start-bridge.bat`：
Run（同机另开 PowerShell）: `(Invoke-RestMethod http://127.0.0.1:8190/comfy-bridge/gating).gating_enabled`
Expected: `True`（证明无 Python 环境也能跑）。

- [ ] **Step 3: 接入真实便携 ComfyUI 并出图**

双击 `install.bat` 指向一份官方 ComfyUI 便携包 → 用 `run_nvidia_gpu_bridge.bat` 启动 ComfyUI：
Expected: 节点菜单只剩已适配厂商（gating 生效）；跑一个真实 api_node（如 Gemini NanoBanana2 或 Seedream V2 4.5）成功出图，且不扣 comfy.org 积分。

- [ ] **Step 4: 卸载复原**

双击 `uninstall.bat` 指向便携包：
Expected: gating 节点与兄弟启动器被删，官方 bat 未动。

- [ ] **Step 5: 正式发布**

确认 rc 无误后：
```bash
git tag v0.1.0
git push origin v0.1.0
```
（同样属对外动作，执行前需用户确认。）
Expected: Releases 出现正式 `v0.1.0` 套件。

---

## 自审记录

- **Spec 覆盖**：§4 边车（Task 7/9 落地胶水点）、§5 套件结构（Task 10 组装）、§6 打包（Task 1/2/4）、§7 配置（Task 1 预检 / Task 5 模板 / Task 6 自检）、§8 脚本（Task 6/7/8）、§9 发布（Task 10）、§10 坑（Task 4 冒烟 / Task 5 模板 / Task 7 兄弟启动器）、§11 验证（Task 11）、§14 采纳清单逐条对应（#4→Task4、#5→Task2、#6→Task1+5、#7→Task3、#8→Task5、#9→Task7、#10→Task5、#11→Task7、#12→Task10 verify step）。CRITICAL #1–3 = Task 2/4、6/7/8、10。
- **占位符扫描**：无 TBD/TODO；pyinstaller 版本以「实跑回填」具体动作替代占位（Task 4 Step 5）。
- **类型/命名一致**：`resolve_base_dir` / `missing_bases_for_filled_keys` 在 Task 1 定义，Task 2 `run.py` 同名调用；`.env.example.kit` 在 Task 5 产出、Task 10 组装时复制为 `.env.example`；端口常量 8190 在模板/启动器/install/release 全一致。
