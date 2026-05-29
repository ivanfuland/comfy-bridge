# comfy-bridge

> 自托管 FastAPI 代理，让 ComfyUI 官方 `comfy_api_nodes`（OpenAI / Anthropic / Gemini / Tripo）改走你自己的 key 或 LLM 网关，**绕开 comfy.org 计费**；并通过 custom_node 把菜单收敛到你实际支持的节点。

跨平台：**Windows**（一键脚本 + Task Scheduler 自启 + 看门狗）/ **Linux**（systemd user service）。

---

## 工作原理

```
[ComfyUI 前端] ──/object_info──▶ [ComfyUI :8188] ──/proxy/{vendor}/*──▶ [comfy-bridge :8190] ──▶ [你的网关 / 原厂 API]
                                       └── custom_node 启动时按门控规则裁剪节点菜单
```

ComfyUI 启动加 `--comfy-api-base=http://127.0.0.1:8190` 后，所有 `comfy_api_nodes` 请求重定向到 bridge。bridge 按厂商分 adapter 改写请求头 / 路径 / 图片引用（bridge 内部资源 URL → base64 或厂商上传 token），再转发到你配置的 base URL。

> **关键**：真正绕开 comfy.org 计费的是 `--comfy-api-base`（请求路由），不是菜单门控。请始终用带该参数的启动方式。

---

## 特性

- **一把 key 多厂商**：OpenAI / Anthropic / Gemini / Tripo，各自独立 base URL + key，按需启用。
- **协议适配**：OpenAI `/v1/responses`、Anthropic 原生 `/v1/messages`（+ `x-api-key`）、Gemini `generateContent`、Tripo `/v2/openapi/task`，含图片 / 多模态引用重写。
- **三层节点门控**（全部 `.env` 配置，不改代码）：厂商级隐藏 / 按类硬隐藏 / 按类灰显「未适配」。
- **Windows 开箱即用**：一键安装 `bootstrap.ps1`、体检 `doctor.ps1`、登录自启 + 每 5 分钟健康自愈的看门狗。
- **零侵入**：不改 ComfyUI 源码，全部能力在并列的 custom_node + 独立代理进程里。

---

## 快速开始

### Windows（推荐：一键）

```powershell
cd C:\your\workspace
git clone https://github.com/ivanfuland/comfy-bridge.git
powershell -ExecutionPolicy Bypass -File comfy-bridge\windows\bootstrap.ps1
# 按提示输入网关 URL + key；完成后双击 comfy-bridge\windows\start-comfyui.bat
```

所有 Windows 双击入口都在 `comfy-bridge\windows\`：`start-comfyui.bat`（启 ComfyUI）/ `start-bridge.bat`（改完 .env 重启 bridge 重载）/ `watch-bridge-log.bat`（看实时流量）。

`bootstrap.ps1` 幂等地完成：前置检查 → 装 ComfyUI → 建 bridge 环境跑测试 → 写 `.env` → 接入 custom_node → 注册自启 + 看门狗 → 启动 → 体检。
详见 **[docs/WINDOWS-QUICKSTART.md](docs/WINDOWS-QUICKSTART.md)**（前置清单 / 刷新-重启规则 / 运维 / 常见问题）。

### Linux（systemd user service）

```bash
git clone https://github.com/ivanfuland/comfy-bridge.git ~/projects/comfyui/comfy-bridge
cd ~/projects/comfyui/comfy-bridge

uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e .

cp .env.example .env && chmod 600 .env   # 填 key / base URL

# custom_node（symlink，便于升级自动同步）
ln -sf "$(pwd)/custom_nodes/comfy-bridge-gating" /PATH/TO/ComfyUI/custom_nodes/comfy-bridge-gating

# 自启
ln -sf "$(pwd)/systemd/comfy-bridge.service" ~/.config/systemd/user/comfy-bridge.service
systemctl --user daemon-reload && systemctl --user enable --now comfy-bridge

# ComfyUI 启动加 --comfy-api-base=http://127.0.0.1:8190，然后验证：
curl http://127.0.0.1:8190/comfy-bridge/gating
```

> systemd unit 用 `%h` 占位，默认假设装在 `~/projects/comfyui/comfy-bridge/`。其它路径用 drop-in 覆盖 `WorkingDirectory` / `EnvironmentFile` / `ExecStart`。

---

## 配置（`.env`）

| 变量 | 默认 | 说明 |
|---|---|---|
| `BRIDGE_HOST` | `127.0.0.1` | 绑定地址。**勿设 `0.0.0.0` 暴露公网**（无鉴权） |
| `BRIDGE_PORT` | `8190` | 监听端口 |
| `BRIDGE_ASSET_DIR` | `<bridge_dir>/asset-cache` | 资源本地暂存目录 |
| `BRIDGE_GATING` | `on` | 节点门控总开关（`off` = 纯透传不裁剪菜单） |
| `BRIDGE_CORS_ORIGINS` | `http://127.0.0.1:8188,http://localhost:8188` | CORS 允许来源 |
| `BRIDGE_ALLOWED_VENDORS` | 见 `config.py` | 厂商白名单（逗号分隔，覆盖基线） |
| `BRIDGE_ALLOWED_NODE_CLASSES` | 见 `config.py` | 类白名单；允许厂商但不在此的类灰显「未适配」 |
| `BRIDGE_HIDDEN_NODE_CLASSES` | 空 | 类硬隐藏黑名单；从菜单彻底移除（改后需**重启 ComfyUI**） |
| `OPENAI_BASE_URL` / `OPENAI_API_KEY` | `https://api.openai.com` / — | OpenAI 兼容网关 + key |
| `ANTHROPIC_BASE_URL` / `ANTHROPIC_API_KEY` | `https://api.anthropic.com` / — | 网关须**原生支持** Anthropic 协议 |
| `ANTHROPIC_VERSION` | `2023-06-01` | `anthropic-version` 头 |
| `GEMINI_BASE_URL` / `GEMINI_API_KEY` | `https://generativelanguage.googleapis.com` / — | Gemini |
| `TRIPO_BASE_URL` / `TRIPO_API_KEY` | `https://api.tripo3d.ai` / — | Tripo |

> 只填要用的厂商；缺 key 的厂商节点返回 HTTP 424「未配置」，不影响其它。base URL 填 origin-root（OpenAI 会自动去重 `/v1`，Anthropic **不要**带 `/v1`）。

### 三层节点门控

| 层 | 配置 | 效果 | 生效方式 |
|---|---|---|---|
| 厂商隐藏 | `BRIDGE_ALLOWED_VENDORS` | 非白名单厂商节点从菜单移除（服务端剪枝） | 重启 ComfyUI |
| 按类硬隐藏 | `BRIDGE_HIDDEN_NODE_CLASSES` | 指定类从菜单移除，优先级最高（服务端剪枝） | 重启 ComfyUI |
| 按类灰显 | `BRIDGE_ALLOWED_NODE_CLASSES` | 允许厂商但不在白名单的类，画布上灰显「未适配」并禁用 | 前端硬刷新 |

> 改 `*_BASE_URL` / `*_API_KEY` 等后端配置只需**重启 bridge**，无需刷新前端。

---

## 自测

```bash
# 直接验 bridge → 网关，不依赖 ComfyUI（返回 200 + 真实回答 = 通）
curl -X POST http://127.0.0.1:8190/proxy/openai/v1/responses \
  -H 'Content-Type: application/json' -d '{"model":"gpt-5","input":"hi"}'
curl -X POST http://127.0.0.1:8190/proxy/anthropic/v1/messages \
  -H 'Content-Type: application/json' \
  -d '{"model":"claude-opus-4-7","max_tokens":50,"messages":[{"role":"user","content":"hi"}]}'
```

Windows 一键体检（torch/CUDA、bridge、gating、ComfyUI、菜单剪枝、自启、看门狗）：

```powershell
powershell -ExecutionPolicy Bypass -File windows\doctor.ps1
```

---

## 运维

| | Windows（.bat 在 `windows\`） | Linux |
|---|---|---|
| 启动 / 重启重载 .env | 双击 `windows\start-bridge.bat`（隐藏后台服务，无窗口） | `systemctl --user restart comfy-bridge` |
| 看日志 / 流量 | 双击 `windows\watch-bridge-log.bat` 或看 `logs\bridge.log`（每笔 `→`/`←`） | `journalctl --user -u comfy-bridge -f` |
| 自愈 | `comfy-bridge-watchdog` 任务每 5min 健康探测 + 重启 | systemd `Restart=on-failure` |
| 升级 | `git pull` → 双击 `windows\start-bridge.bat`（symlink 自动同步 custom_node） | `git pull` → restart |

> Windows `start-bridge.bat` 做的是正确重启：停任务 → 清 8190 端口（`Stop-ScheduledTask` 不杀子进程）→ 起任务（重载 .env）。**别直接跑 `start-bridge.ps1`**——它有幂等守卫，见服务健康即退出、不重载。

> **Windows 上一个 bridge = 两个 `python.exe` 是正常的**：uv venv 的 `python.exe` 是 trampoline（跳板），运行时 spawn base python 作子进程（Windows 无 `exec()`）。判健康看 `doctor.ps1` 或 `:8190` 的 owner 是否稳定，**别数进程数**。启动脚本带幂等守卫（已健康则不再起第二个），重复启动是无害 no-op；勿在自启任务运行时手动 `start-bridge`（要前台调试先 `Stop-ScheduledTask`）。

---

## 开发

```bash
uv venv --python 3.12 .venv
.venv/Scripts/python -m pip install -e ".[dev]"   # Windows；Linux 用 .venv/bin/python
.venv/Scripts/python -m pytest tests -q           # 41 passed
```

测试用 `BRIDGE_SKIP_DOTENV=1`（conftest）隔离，不读真实 `.env`。

---

## 项目结构

```
comfy-bridge/
├── app/                      # FastAPI 后端
│   ├── main.py               #   app 工厂、路由、CORS
│   ├── router.py             #   /proxy/{vendor}/{path} 分发
│   ├── adapters/             #   openai / anthropic / gemini / tripo + base
│   ├── assets.py             #   本地资源 slot
│   ├── gating.py             #   GET /comfy-bridge/gating
│   ├── config.py             #   .env 配置 + 门控基线默认
│   └── errors.py             #   424 / vendor 错误
├── custom_nodes/comfy-bridge-gating/
│   ├── __init__.py           #   服务端剪枝（厂商隐藏 + 按类硬隐藏）
│   └── web/...js             #   前端灰显「未适配」
├── windows/                  # 所有 Windows .bat/.ps1/.vbs 都在这（跨平台，不放根目录）
│   ├── bootstrap.ps1         #   一键安装（幂等）
│   ├── doctor.ps1            #   体检
│   ├── start-comfyui.bat     #   双击启 ComfyUI（相对路径）
│   ├── start-bridge.bat      #   双击重启 bridge + 重载 .env
│   ├── watch-bridge-log.bat  #   双击看实时流量
│   ├── start-bridge.ps1      #   服务启动器（任务经 run-hidden.vbs 调用）
│   ├── run-hidden.vbs        #   无窗口启动器（隐藏服务）
│   ├── healthcheck-bridge.ps1#   看门狗健康检查
│   └── *-task-scheduler.ps1  #   注册 / 卸载自启 + 看门狗
├── systemd/comfy-bridge.service
├── tests/                    # pytest（42）
├── docs/WINDOWS-QUICKSTART.md
├── .env.example
├── pyproject.toml
└── README.md
```

---

## 安全说明

- `.env` 含真实 key，**勿提交 git**（`.gitignore` 已排除）。
- bridge 默认仅绑 `127.0.0.1`，**当前版本无鉴权**——勿暴露公网；远程访问走 Tailscale / WireGuard / SSH 隧道。
- 自建网关的 key 建议配用量监控。

---

## 许可

暂未指定开源许可证。如需在自己项目中复用，请先与作者确认。
