# Windows 快速上手（从 0 到能用）

ComfyUI + comfy-bridge 在 Windows 上的傻瓜安装。让 ComfyUI 网页里的 OpenAI/Anthropic/Gemini/Tripo/ByteDance·Seedance 节点走你自己的 LLM 网关，**不扣 comfy.org 积分**，菜单只留你支持的节点。

> 详细原理 / 踩坑见 Obsidian 笔记《comfy-bridge Windows 迁移实战》。本页只讲「怎么装、怎么用」。

---

## 1. 前置条件（装之前确认）

| 项 | 怎么搞 |
|---|---|
| **NVIDIA GPU + 较新驱动** | ComfyUI 引擎需要；RTX 30/40 系都行 |
| **git** | 装 [Git for Windows](https://git-scm.com/download/win) |
| **uv** | `powershell -c "irm https://astral.sh/uv/install.ps1 \| iex"`（Python 3.12 由它自动装，无需手动） |
| **私有 repo 访问** | `gh auth login` 或配好 git 凭据（comfy-bridge 是私有 repo） |
| **Windows 开发者模式** | 设置 → 隐私和安全性 → 开发者选项 → 开（用于 symlink；没开会自动退化成复制，可用但升级要手动重拷） |
| **~10GB 磁盘** | torch / ComfyUI 大件 |
| **LLM 网关 base URL + key** | 一把 key、多协议通用的自建网关（如 one-api / new-api，含 OpenAI/Anthropic/Gemini/Tripo/ByteDance·Seedance），或各厂商原厂 key |

---

## 2. 三步装好

```powershell
# ① 选个工作目录，clone bridge 进去
cd F:\comfyui-workspace
git clone https://github.com/ivanfuland/comfy-bridge.git

# ② 跑一键安装（幂等，可重跑）
powershell -ExecutionPolicy Bypass -File comfy-bridge\windows\bootstrap.ps1

# ③ 启动 ComfyUI
#    双击 comfy-bridge\windows\start-comfyui.bat，浏览器开 http://127.0.0.1:8188
```

`bootstrap.ps1` 会自动：查前置 → 装 ComfyUI（下 ~2.6GB torch，等几分钟）→ 建 bridge 环境跑测试 → **问你网关 URL 和 key** 写 `.env` → 接好 custom_node → 注册自启 + 看门狗 → 启 bridge → 自检。

> **三个双击入口都在 `comfy-bridge\windows\`**（跨平台 repo，Windows 的 .bat 都收在这）：`start-comfyui.bat`（启 ComfyUI）/ `start-bridge.bat`（改完 .env 重启 bridge）/ `watch-bridge-log.bat`（看流量）。嫌进目录麻烦可右键发送到桌面快捷方式。

> 装完第一次启动 ComfyUI 后，**菜单没收敛就重启一次 ComfyUI**（节点剪枝在加载时跑）。

---

## 3. 验证：体检命令

任何时候想确认整套是否健康：

```powershell
powershell -ExecutionPolicy Bypass -File comfy-bridge\windows\doctor.ps1
```

逐项打印 `[PASS]/[WARN]/[FAIL]`：torch+CUDA、bridge 进程、gating、ComfyUI、菜单剪枝……全绿即可用。

---

## 4. 改了东西，要刷新还是重启？（三档，最容易搞混）

| 改了什么 | 怎么生效 |
|---|---|
| 换网关 / 换 key / 改 `.env` 的 URL·KEY 段 | **双击 `windows\start-bridge.bat`**（重启 bridge 重载 .env），节点重新 Queue 即生效，不用刷新 |
| `.env` 里 `BRIDGE_HIDDEN_NODE_CLASSES`（硬隐藏节点）、装/删 custom_node | 先 `start-bridge.bat`，再 **双击 `start-comfyui.bat` 重启 ComfyUI**（剪枝在加载 custom_node 时跑） |
| `.env` 里 `BRIDGE_ALLOWED_NODE_CLASSES`（灰显未适配）、custom_node 的 `web\*.js` | ComfyUI 前端 **Ctrl+Shift+R 硬刷新** |

> **改完 `.env` 一定要双击 `windows\start-bridge.bat`**——它做的是「停任务 → 清 8190 端口 → 起任务」的正确重启（`Stop-ScheduledTask` 不杀子进程，光 Stop+Start 会重载失败）。**别用 `start-bridge.ps1` 直接跑**：它有幂等守卫，见服务健康就退出、不重载。

---

## 5. 日常运维

| 操作 | 命令 / 做法（.bat 都在 `comfy-bridge\windows\`） |
|---|---|
| 启 ComfyUI | 双击 `windows\start-comfyui.bat`（:8188） |
| 重启 bridge / 重载 .env | 双击 `windows\start-bridge.bat`（:8190） |
| 看 bridge 实时流量 | 双击 `windows\watch-bridge-log.bat`（关了不影响服务） |
| bridge 进程 | **隐藏后台服务**（无窗口，关不掉，登录自启 + 看门狗自愈）；底层 `Start-/Stop-/Get-ScheduledTask -TaskName comfy-bridge` |
| bridge 日志 | `comfy-bridge\logs\bridge.log`（每笔 input `→` / output `←` 都记，重启滚动到 `.1`） |
| 体检 | `powershell -File comfy-bridge\windows\doctor.ps1` |
| 升级 bridge | `cd comfy-bridge; git pull` → 双击 `windows\start-bridge.bat`（symlink 自动同步 custom_node） |
| 卸载自启 | `powershell -File comfy-bridge\windows\uninstall-task-scheduler.ps1` |

> ⚠️ **护积分的是 `--comfy-api-base`，不是菜单收敛**。只用 `windows\start-comfyui.bat` 启动 ComfyUI（它固定带这个参数）；别用 `comfy launch` / ComfyUI Desktop 绕过，否则 api_node 会直连 comfy.org 扣积分。
> **bridge 没有自己的窗口**（隐藏服务），想看流量用 `watch-bridge-log.bat`，别去开 bridge 窗口（也开不出来，且会触发重启）。

---

## 6. 常见问题

| 现象 | 处方 |
|---|---|
| 节点报 `model_not_found` | 网关没这个模型。换网关支持的模型名（如出图用 `gpt-image-2`，别用 dall-e） |
| 节点报 424 `未配置` | `.env` 里对应厂商的 KEY 空。填上，重启 bridge |
| 菜单还显示全部/灰显没生效 | 看 §4：硬隐藏要重启 ComfyUI，灰显/JS 要硬刷新 |
| Tripo 报 `data.status` 校验失败 | 已由 bridge 自动修复；确认 bridge 是最新版（`git pull`） |
| 字节跳动/Seedance 节点不出现或灰显 | `BRIDGE_ALLOWED_VENDORS` 要含 **`bytedance`**（不是 `byteplus`，门控 vendor 名由节点模块推导）；改后**重启 ComfyUI**。2.0 那几个节点显示英文名是前端中文翻译没覆盖到，正常 |
| 改了 .env 没生效 | 多半没重启 bridge，或重启没成功。**双击 `windows\start-bridge.bat`**（正确重启重载），看末尾打印 `gating=True` |
| 节点报 401 / 弹「需要登录」 | 网关 key 失效（前端把上游 401 当未登录）。换有效 key 进 `.env`，双击 `start-bridge.bat`；`logs\bridge.log` 的 `← 401` 行有详情 |
| 看着像「一直挂掉重启」/ 任务管理器两个 python | **多半是误会**：一个 bridge = 两个 `python.exe`（uv 跳板 + 子进程）属正常。真挂掉看 `doctor.ps1` / `:8190` owner 是否稳定 |
| bridge 起不来 / 端口被占 | 双击 `windows\start-bridge.bat`（会清端口重启）；或看 `logs\bridge.log` |
| 一切看着不对 | 先跑 `doctor.ps1`，按 FAIL 项处理 |
| symlink 失败 | 开 Windows 开发者模式后重跑 bootstrap（或接受复制版） |
