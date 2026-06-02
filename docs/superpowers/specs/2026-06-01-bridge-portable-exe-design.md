# comfy-bridge 便携 exe 套件 + GitHub Releases 发布 — 设计文档

- **日期**：2026-06-01
- **状态**：设计已与用户确认，待 spec 审阅
- **作者**：Ivan + Claude（brainstorming）
- **关联**：运维基线见 `README.md`「版本兼容性与适配矩阵」；当前验证基线 ComfyUI 0.22.3 / comfy-bridge 0.1.0 / Python ≥3.12

---

## 1. 目标

把现有 comfy-bridge（FastAPI/uvicorn 代理服务）打包成一个**绿色免装的 Windows 套件**，作为 **GitHub Release 资产**发布到 `github.com/ivanfuland/comfy-bridge` 的 Releases 页，供他人下载后**开箱接入自己的 ComfyUI 便携包**——无需安装 Python、无需建 venv、无需命令行。

## 2. 范围与非目标

**范围**
- 把 bridge 用 PyInstaller（onedir）打成 `bridge.exe`。
- 组装一个套件 zip：exe + gating custom_node + `.env` 模板 + 一键脚本。
- 用 GitHub Actions（windows-latest，tag 触发）自动构建并发布 Release。

**非目标（YAGNI）**
- ❌ 不打包 ComfyUI 本体（对方自带便携包）。
- ❌ 不做跨平台（仅 Windows x64；运维栈本就是 Windows）。
- ❌ 不做代码签名（默认接受 SmartScreen 警告，见 §10）。
- ❌ 不做 GUI 配置向导（首次配置=编辑一个 `.env` 文本）。
- ❌ 不在包内附带任何真密钥或共享 key。

## 3. 关键约束与决策（已敲定）

| 维度 | 决策 |
|---|---|
| 打包目的 | 分发给别人（要求最高场景） |
| 分发范围 | bridge.exe + gating custom_node + `.env` 模板 + 一键脚本（「接入套件」） |
| 密钥模型 | 包内零真密钥；对方自带雷火网关 key（前提：对方能自行申请到 key） |
| 目标环境 | Windows x64；对方 ComfyUI 为官方**便携包**（`run_nvidia_gpu.bat`） |
| 打包工具 | PyInstaller **onedir** |
| 发布方式 | GitHub Actions 自动（`release.yml`，push tag `v*` 触发） |

## 4. 架构：边车（sidecar）模型

`bridge.exe` 与 ComfyUI 是**两个独立进程**，并排运行，靠两个「胶水点」松耦合协作；exe **不嵌入、不修改** ComfyUI 任何安装文件。

```
对方这台机器
┌──────────────────────────────────────────────────────────────────┐
│  ComfyUI 便携包 (进程A :8188)            bridge.exe (进程B :8190)        │
│  ┌─────────────────────────┐            ┌────────────────────────┐  │
│  │ 画布 api_node            │            │ FastAPI 代理            │  │
│  │ (OpenAI/Gemini/Tripo…)   │─ 胶水①HTTP ▶│ ①改写协议/字段          │  │
│  │ 本来打 api.comfy.org      │  路由到8190 │ ②注入对方自己的 key      │──▶ 雷火网关
│  │ +--comfy-api-base 后改打  │            └────────────────────────┘  │  (不扣 comfy.org 积分)
│  │ 127.0.0.1:8190           │                       ▲                │
│  └─────────────────────────┘                       │ 胶水②HTTP       │
│  ┌─────────────────────────┐                       │ 读 gating 配置   │
│  │ custom_nodes\           │───────────────────────┘                 │
│  │  comfy-bridge-gating\   │  加载时从 bridge 读白名单，prune 未适配节点  │
│  │  (bridge 不可达→fail-open)│  (节点菜单只剩 bridge 真正支持的厂商)       │
│  └─────────────────────────┘                                         │
└──────────────────────────────────────────────────────────────────┘
```

**数据流**：用户在 ComfyUI 用 api_node → ComfyUI 把请求发到 `127.0.0.1:8190` → bridge 改写协议/字段、注入对方网关 key → 转发雷火网关 → 结果回 ComfyUI。全程不碰 comfy.org 积分。

**两个胶水点 = install.bat 要替对方做的全部「结合」工作：**

| 胶水点 | 作用 | 落地方式 | 不做的后果 |
|---|---|---|---|
| ① 请求路由（积分保护核心） | api_node 流量打到 bridge 而非 comfy.org | ComfyUI 启动命令追加 `--comfy-api-base=http://127.0.0.1:8190` | 仍打 comfy.org，扣积分、不用对方 key |
| ② 节点显隐（gating） | 加载时从 bridge 读白名单删未适配节点 | 把 `comfy-bridge-gating\` 拷进 `ComfyUI\custom_nodes\` | 菜单显示全部 ~192 个 api_node，多数点了报错 |

**耦合性质**：松耦合、纯本地 HTTP。exe 放哪个盘都行；启动顺序无所谓（路由是每次请求实时打，gating 节点带 ~16s 重试且 fail-open）。卸载=删 custom_node + 删兄弟启动器，干净复原。

## 5. 交付物结构（套件 zip）

对方解压到任意目录即用，所有可写状态落在套件根目录：

```
comfy-bridge-kit/                     ← 解压到任意位置
├─ bridge/                            ← PyInstaller onedir 产物
│  ├─ bridge.exe                      ← 真 exe
│  └─ _internal/...                   ← 依赖（fastapi/uvicorn/httpx/pydantic…）
├─ comfy-bridge-gating/               ← 去 symlink 的真实节点文件夹（__init__.py + web/）
├─ .env.example                       ← 配置模板，零真密钥
├─ install.bat                        ← 一键接入：装节点 + 生成兄弟启动器
├─ start-bridge.bat                   ← 双击启动 bridge
├─ uninstall.bat                      ← 卸载：删节点 + 删兄弟启动器
└─ 接入说明.txt                        ← 3 步图文
```

运行时 `.env` / `asset-cache\` / `logs\` 都落在套件根目录，**不写进 `bridge\_internal\`**（升级换包不丢配置）。

## 6. bridge.exe 打包（PyInstaller onedir）

核实现有代码后，有**三点不处理就会破坏「便携」**，方案逐一解决：

### 6.1 三点修复

1. **路径基于 CWD → 改为基于 exe 自身位置。**
   现状：`app/config.py` 顶层 `load_dotenv()`（无参，从 CWD 向上找）+ 默认 `asset-cache = os.getcwd()/asset-cache`（`_default_asset_dir()`）。冻结后 exe 的 CWD 取决于「谁拉起它」，对方双击位置不定 → 找不到 `.env`、缓存乱写。
   解决：新增冻结入口 `run.py`，按 `sys.executable` 定位自身目录并向上找 `.env`，`chdir` 到该目录后再启动。

2. **不用 `uvicorn app.main:app` 字符串入口 → 把 app 对象直接喂 `uvicorn.run()`。** 字符串入口冻结后常因 import 解析失败；显式传 app 对象 + 指定 loop/http 实现可砍掉 `[standard]` 动态导入坑。

3. **adapters 是 `load_adapters()` 动态加载，PyInstaller 静态分析抓不到 → 冻结后适配器全丢。** 用 `--collect-submodules app`（含 `app.adapters.fal_ai`）强制收齐。

### 6.2 冻结入口 `run.py`（新增文件，置于**仓库根**，与 `bridge.spec` 同级）

```python
import os, sys

# 定位套件根：含 .env 的目录（exe 同级或其上 1-2 层）
def _resolve_base():
    start = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) \
            else os.path.dirname(os.path.abspath(__file__))
    d = start
    for _ in range(3):
        if os.path.exists(os.path.join(d, ".env")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return start  # 没找到 .env 时退回 exe 同级

BASE = _resolve_base()
os.chdir(BASE)                                   # load_dotenv() + 默认 asset-cache 都解析到这里
from dotenv import load_dotenv
load_dotenv(os.path.join(BASE, ".env"), override=True)  # override：套件 .env 为单一事实来源，压过系统同名环境变量；且先于 app 导入

import uvicorn
from app.main import app                         # 此时 .env 已就位
if __name__ == "__main__":
    uvicorn.run(
        app,
        host=os.getenv("BRIDGE_HOST", "127.0.0.1"),
        port=int(os.getenv("BRIDGE_PORT", "8190")),
        loop="asyncio", http="h11", log_config=None,
    )
```

> 注：`app/main.py` 在 import 时即 `create_app()`→`load_config()`，但厂商 key 是 **per-request** 校验（缺 key 返回 424，不在启动时崩）。因此即便对方还没填 key，bridge 也能正常启动，只是调用时返回 424，直到填好 key——符合「自带 key」模型。
>
> **时序硬约束（Codex #5）**：`run.py` 必须先 `load_dotenv(..., override=True)`，**再** `from app.main import app`——绝不能在 load 之前 import 到 `app.config`（它顶层就 `load_dotenv()`）。`run.py` 是唯一入口，按上面顺序排列即满足。启动时建议打印一行 `[bridge] config from <BASE>\.env, port=<port>, log_io=<on/off>` 以便对方自查配置来源。`app/config.py` 顶层那次 `load_dotenv()` 保持不变（CWD 已 chdir 到 BASE，找得到同一份 `.env`，且不 override 不会回退覆盖已加载值）。

### 6.3 构建命令骨架

```bat
pyinstaller --onedir --name bridge --collect-submodules app ^
  --hidden-import uvicorn.loops.asyncio ^
  --hidden-import uvicorn.protocols.http.h11_impl ^
  --hidden-import uvicorn.lifespan.on ^
  run.py
```

> 上面只是探路骨架，**不可作为最终配方**（Codex #4）。pydantic v2 带 Rust 扩展 `pydantic_core`，FastAPI 依赖链还有 `anyio`/`starlette`/`httpcore`/`h11`/`certifi`，仅靠 `--collect-submodules app` 不足。最终 `bridge.spec` 必须显式：
>
> ```python
> from PyInstaller.utils.hooks import collect_all, collect_submodules
> datas, binaries, hiddenimports = [], [], []
> for pkg in ("pydantic", "pydantic_core", "anyio", "starlette", "httpx", "httpcore", "certifi"):
>     d, b, h = collect_all(pkg); datas += d; binaries += b; hiddenimports += h
> hiddenimports += collect_submodules("app")  # 含 app.adapters.* 动态加载的适配器
> hiddenimports += ["uvicorn.loops.asyncio", "uvicorn.protocols.http.h11_impl", "uvicorn.lifespan.on"]
> ```
>
> `bridge.spec` 提交入库，CI 与本地都用它构建（一致性）。**强制 CI 冒烟测试（Codex #4，见 §11）**：构建后在 runner 上直接跑 `dist\bridge\bridge.exe`、curl `/comfy-bridge/gating` 必须 200——把「本地打包成功、用户机器缺包闪退」这类坑挡在发布前。新版 PyInstaller 虽自带部分 uvicorn/pydantic hook，但不依赖其存在。

## 7. 配置外置与首次使用

> ⚠️ **已核实的现状坑（Codex #6，必须修）**：仓库现有 `.env.example` 的 5 个 `*_BASE_URL` **全为空**，而 `app/config.py:_PROVIDER_DEFAULT_BASE` 默认把 openai/anthropic/gemini/tripo 指向**各官方上游**（仅 byteplus 默认雷火）。若沿用现状模板，对方填了雷火 key 却漏填 base → key 被发到 `api.openai.com` 等官方端点，**失败甚至把 key 泄漏给错误上游**。因此发布用的 `.env.example` 是一个**新模板**，不是仓库现有那份。

发布模板（`packaging/.env.example.kit`，区别于仓库根的开发用 `.env.example`）原则：
- **预填（非密，写死）**：5 个 `*_BASE_URL` **全部**指向雷火网关 `https://ai.leihuo.netease.com`；`BRIDGE_PORT=8190`；`BRIDGE_CORS_ORIGINS=http://127.0.0.1:8188,http://localhost:8188`。
- **默认关日志（Codex #10）**：写死 `BRIDGE_LOG_IO=off`。已核实 `app/adapters/base.py:138` 默认是 `"on"`，会把每次上游调用的输入/输出 body 落进 `logs\`（位于套件根目录），分发场景偏敏感；模板默认关闭，并在注释里提示「开启后日志含你的 prompt/响应内容」。
- **留空（对方填）**：`OPENAI_API_KEY=` / `ANTHROPIC_API_KEY=` / `GEMINI_API_KEY=` / `TRIPO_API_KEY=` / `BYTEPLUS_API_KEY=`。雷火「同一把 key 通吃 4 厂商」，对方把自己那把 key 粘到要用的字段即可。
- **portable 模式 fail-fast（Codex #6）**：`run.py` 启动时若检测到「某 provider 填了 KEY 但对应 BASE_URL 为空」，直接报错退出并提示，而不是静默落到官方上游。
- **端口锁定（Codex #8）**：gating 端口 `8190` 在 `custom_nodes/comfy-bridge-gating/__init__.py`（×2）和 `web/comfy-bridge-gating.js` 中**硬编码**，与 `.env` 的 `BRIDGE_PORT` 脱钩。套件场景**锁死 8190**：发布模板不暴露 `BRIDGE_PORT`（注释说明改端口需同步改 gating 节点与启动器，普通用户勿动）。`--comfy-api-base` 与 `start-bridge.bat` 均用 8190 常量，保证三处一致。

### 7.2 路径与可写状态
- `run.py`（§6.2）向上找 `.env` 并 `chdir` 到套件根；`asset-cache\`、`logs\` 随之生成在根目录。
- 对方**全程只编辑根目录一个 `.env`**。

### 7.3 首次使用三步（写进 `接入说明.txt`）
1. 编辑 `.env`，填入自己的雷火网关 key。
2. 双击 `install.bat`，按提示指定 ComfyUI 便携包目录。
3. 双击 `start-bridge.bat` 起 bridge；再用 install 生成的 `run_nvidia_gpu_bridge.bat` 启动 ComfyUI。
- `start-bridge.bat` 启动前自检 `.env` 是否存在且 key 非空，缺失则弹提示而非静默失败。

## 8. 接入套件脚本

### 8.1 `install.bat`（幂等 + 可逆 + 不破坏官方文件）
1. **定位 ComfyUI**：让对方把便携包根目录（含 `run_nvidia_gpu.bat` 那层）拖入窗口，或自动探测常见路径。
2. **装 gating 节点**：拷 `comfy-bridge-gating\` → `<便携包>\ComfyUI\custom_nodes\comfy-bridge-gating\`（已存在则覆盖更新）。
3. **加启动参数——生成兄弟启动器，不改官方 bat**：在便携包根目录生成 `run_nvidia_gpu_bridge.bat`，尾部带 `--comfy-api-base=http://127.0.0.1:8190`。官方 `run_nvidia_gpu.bat` 一字不动。
   > 为何不原地改官方 bat：官方启动行各版本会变，正则原地改易错且难回滚；兄弟启动器零风险、易卸载。
   >
   > **生成策略（Codex #9，避免脆弱复刻）**：优先**包装而非复制**——
   > - **首选 wrap-via-call**：若官方 bat 的 python 启动行透传了 `%*`（接受额外参数），生成器只写 `call "%~dp0run_nvidia_gpu.bat" --comfy-api-base=http://127.0.0.1:8190 %*`，完全不碰官方启动行。
   > - **回退 full-replicate**：官方行不带 `%*`（ComfyUI 便携包常见）时，**逐行复制**官方 bat 全文（保留所有前置 `set`/环境变量/`%~dp0`/`call`），仅在 `... main.py ...` 那一行尾部插入参数——不能只复制单行。
   > - **测试矩阵**：含空格的安装路径、重复安装（幂等）、exe 移动后重装、官方 bat 带/不带 `pause`/`%*` 的版本差异，均需覆盖（§11）。
4. 复制发布模板 `.env.example`→套件根 `.env`（若不存在）并提示对方编辑。
5. **接入前置检查（Codex #11）**：安装前探测对方 ComfyUI——版本是否 ≥ 适配基线、`--comfy-api-base` 是否被该版本接受、`/object_info` 是否含 gating 依赖的 `python_module` 字段；不满足则警告并让对方确认是否继续，而非默默装上一个会 fail-open 的 gating。

### 8.2 `start-bridge.bat`
- `cd` 到 `bridge\` 同级；自检 `.env` 与 key；运行 `bridge\bridge.exe`，前台输出（关窗即停，与现有 `watch-bridge-log.bat` 思路一致）。

### 8.3 `uninstall.bat`
- 删 `<便携包>\ComfyUI\custom_nodes\comfy-bridge-gating\` + 删 `run_nvidia_gpu_bridge.bat`，复原。

## 9. 发布到 GitHub Releases（`release.yml`）

- **新增独立工作流**，与现有 `ci.yml`（ubuntu 跑 pytest，保持不动）分开。
- **触发**：push tag `v*`（如 `git tag v0.1.0 && git push origin v0.1.0`）。
- **runner = `windows-latest`**（PyInstaller 必须在 Windows 上产出 Windows exe；bridge 无 torch/GPU 依赖，CPU 构建数分钟）。
- **步骤**：
  1. `actions/checkout@v4`
  2. `actions/setup-python@v5`（3.12）
  3. `pip install -e . -c packaging/constraints-build.txt` + `pip install pyinstaller==<pinned>`
  4. 跑 `bridge.spec`（§6）产出 `dist/bridge/`
  5. **构建后冒烟测试（Codex #4）**：在 runner 上启动 `dist\bridge\bridge.exe`，`Invoke-RestMethod http://127.0.0.1:8190/comfy-bridge/gating` 必须 200，否则 fail（拦截缺包）。
  6. 组装套件目录（`bridge/` + `comfy-bridge-gating/` + 发布模板 `.env.example` + 三个 `.bat` + `接入说明.txt`）
  7. 压成 `comfy-bridge-kit-${TAG}.zip`
  8. `softprops/action-gh-release` 上传 zip 为 Release 资产
- **可复现构建（Codex #7）**：现有 `pyproject.toml` 依赖全是 `>=` 下限，每次 release 可能解析到不同 FastAPI/uvicorn/httpx/pydantic/PyInstaller 组合 → 冻结行为漂移。新增 `packaging/constraints-build.txt` pin 住已验证版本（含 `pydantic`/`pydantic-core`/`fastapi`/`uvicorn`/`httpx`/`pyinstaller`），CI 与本地都用它。
- **零密钥保证**：CI 只见仓库内容，`.env` 已被 `.gitignore` 忽略，仅打包发布模板 `.env.example`（预填雷火 base、留空 key）。真 key 永不进包/进 CI。
- **gating 节点来源（Codex #12，修正文档）**：仓库内 `custom_nodes/comfy-bridge-gating/` 是**普通目录**（非 symlink，无 `.gitmodules`），CI checkout 即拿到真实文件。release.yml 组装前显式校验 `comfy-bridge-gating/__init__.py` 与 `web/comfy-bridge-gating.js` 存在，缺失即 fail。
  - （澄清：会 symlink 的是 ComfyUI **那一侧**——把 gating 节点 symlink 进 ComfyUI 的 `custom_nodes`，那是开发机的便利做法，与本仓库内这份真实源码无关。）

## 10. 已知坑与规避

| 坑 | 规避 |
|---|---|
| 未签名 exe → SmartScreen 蓝盾 + 杀软可能误报 | 默认接受（非目标不做签名）；`接入说明.txt` 说明「更多信息→仍要运行」；onedir 比 onefile 误报低。后续如需彻底消除再评估代码签名证书。 |
| uvicorn/pydantic/pydantic_core 动态导入冻结后缺失 | `bridge.spec` 用 `collect_all` 收 `pydantic_core/anyio/starlette/httpx/httpcore/certifi` + `collect_submodules("app")` + 显式 uvicorn hidden-import + `loop=asyncio,http=h11`；**CI 冒烟测试**跑 exe 验证（§6.3/§9/§11）。 |
| 默认记录上游 body（`BRIDGE_LOG_IO` 默认 on）→ 分发场景泄露 prompt/响应 | 发布模板写死 `BRIDGE_LOG_IO=off` + 注释提示（§7.1）。 |
| provider 填 key 漏填 base → key 发往官方上游 | 发布模板预填全部 5 个雷火 base + `run.py` portable fail-fast（§7.1）。 |
| gating 端口 8190 硬编码、与 `BRIDGE_PORT` 脱钩 | 套件锁死 8190、模板不暴露 `BRIDGE_PORT`（§7.1）。 |
| 构建不可复现（依赖全 `>=`） | `packaging/constraints-build.txt` pin 版本（§9）。 |
| CWD 漂移导致找不到 `.env`/缓存乱写 | `run.py` 按 `sys.executable` 定位并 `chdir`（§6.2）。 |
| 版本耦合（adapter 绑死 ComfyUI 节点契约/型号） | Release 说明标注「适配基线 ComfyUI 0.22.3」；对方 ComfyUI 大版本漂移可能静默失效——沿用 README 兼容矩阵。 |
| 端口 8190 被占（重复启动/残留进程） | bridge 第二实例无法 bind 会自退；`start-bridge.bat` 可选探活提示。 |
| 兄弟启动器与官方 bat 启动行不一致（版本差异） | 优先 wrap-via-call（官方带 `%*` 时）；否则逐行复制全文仅在 main.py 行插参；测试矩阵覆盖空格路径/重装/带不带 `%*`（§8.1）。 |
| 日志编码（现有 PS 路径用 UTF-16+BOM） | exe 前台直接 stdout，不经 PS Tee，无 BOM 问题；`asset-cache`/`logs` 用 UTF-8。 |

## 11. 验证计划（干净机器冒烟测试）

在**未装 Python** 的 Windows x64（或干净 VM）上：
1. 解压套件 → 编辑 `.env` 填一把测试 key。
2. 双击 `start-bridge.bat` → `Invoke-RestMethod http://127.0.0.1:8190/comfy-bridge/gating` 返回 `gating_enabled=true`（证明 exe 起、配置加载、adapters 收齐）。
3. 双击 `install.bat` 指向一份官方 ComfyUI 便携包 → 确认 `custom_nodes\comfy-bridge-gating\` 到位、`run_nvidia_gpu_bridge.bat` 生成且含 `--comfy-api-base`。
4. 用兄弟启动器开 ComfyUI → 节点菜单只剩已适配厂商（gating 生效）；跑一个真实 api_node（如 Gemini NanoBanana2 或 Seedream V2 4.5）出图，证明请求经 bridge 走雷火网关、不扣 comfy.org 积分。
5. `uninstall.bat` → 确认复原。
- CI 侧（Codex #4）：`release.yml` 在 `windows-latest` 上**构建后即跑 exe 冒烟测试**（`/comfy-bridge/gating` 返 200），通过 + Release 资产可下载才算构建通过。
- `install.bat` 测试矩阵（Codex #9）：含空格安装路径、重复安装幂等、exe 移动后重装、官方 bat 带/不带 `%*` 与 `pause` 的版本差异。

## 12. 前置假设与风险

- **假设**：接收方能自行申请到雷火网关 key（用户已确认成立）。若不成立，密钥模型需回炉（改共享 key 或换网关）。
- **风险**：对方 ComfyUI 非便携包（Desktop/手动）时 `install.bat` 不适用——本设计明确仅覆盖便携包，其余形态留手动说明或后续迭代。
- **风险**：杀软误报导致对方无法运行——onedir 缓解，必要时再上签名。

## 13. 未决 / 后续迭代（YAGNI 之外）

- 代码签名证书（彻底消除 SmartScreen）。
- 兼容 ComfyUI Desktop / 手动安装形态。
- 自动更新机制（当前=重新下 Release）。
- 多网关/多 key 切换 UI。

## 14. 对抗性审核（Codex）采纳清单

2026-06-01 经 Codex 对抗性审核。逐条处置：

| # | 严重度 | 发现 | 处置 |
|---|---|---|---|
| 1 | CRITICAL | `run.py`/`bridge.spec` 不存在 | **非 spec 缺陷**：这是设计描述、待实现阶段产出的交付物。spec §6 已定义其内容；实现计划首批任务。 |
| 2 | CRITICAL | `install/start/uninstall.bat` 不存在 | 同上，§8 已定义，实现阶段产出（与源码安装脚本 `bootstrap.ps1` 分离）。 |
| 3 | CRITICAL | `release.yml` 不存在 | 同上，§9 已定义，实现阶段产出。 |
| 4 | HIGH | hidden-import 不全（缺 `pydantic_core` 等） | **已并入** §6.3：`collect_all` 全量收集 + CI 冒烟测试。 |
| 5 | HIGH | `load_dotenv` 时序/override | **已并入** §6.2：`override=True` + 强约束 import 顺序 + 启动打印配置来源。 |
| 6 | HIGH | `.env.example` base URL 全空、代码默认官方上游 | **已并入** §7.1：发布模板预填雷火 base + portable fail-fast（已核实属实）。 |
| 7 | HIGH | 依赖 `>=` 不可复现、缺 pyinstaller | **已并入** §9：`packaging/constraints-build.txt` pin 版本。 |
| 8 | MED | gating 端口 8190 硬编码 | **已并入** §7.1：套件锁死 8190、不暴露 `BRIDGE_PORT`（已核实属实）。 |
| 9 | MED | 复刻启动行脆弱 | **已并入** §8.1：优先 wrap-via-call、回退全文复制 + 测试矩阵。 |
| 10 | MED | `BRIDGE_LOG_IO` 默认 on 记录 body | **已并入** §7.1：发布模板默认 off + 隐私提示（已核实属实）。 |
| 11 | MED | gating 依赖 ComfyUI 内部 API、无版本检查 | **已并入** §8.1 步骤 5：install 前置版本/字段探测。 |
| 12 | LOW | 文档误称 gating 为 symlink | **已修正** §9：澄清为普通目录 + CI 校验文件存在。 |

净结论：方向获 Codex 认可；所有 HIGH/MED 已并入 spec；3 条 CRITICAL 是「尚未实现」而非设计错误，构成实现计划的首批任务。
