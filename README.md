# comfy-bridge

> 自托管 FastAPI 代理，让 ComfyUI 官方 `comfy_api_nodes`（OpenAI / Anthropic / Gemini / Tripo / ByteDance·Seedance）改走你自己的 key 或 LLM 网关，**绕开 comfy.org 计费**；并通过 custom_node 把菜单收敛到你实际支持的节点。

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

- **一把 key 多厂商**：OpenAI / Anthropic / Gemini / Tripo / ByteDance·Seedance，各自独立 base URL + key，按需启用。
- **协议适配**：OpenAI `/v1/responses`、Anthropic 原生 `/v1/messages`（+ `x-api-key`）、Gemini `generateContent`、Tripo `/v2/openapi/task`、ByteDance/Seedance 视频 `/v1/video/generations` + Seedream 图 `/v1/images/generations`（Ark 方言↔网关方言翻译，含 1.x/2.0 视频与图生资产 base64 重写、virtual-library/资产/认证 shim），含图片 / 多模态引用重写。
- **三层节点门控**（全部 `.env` 配置，不改代码）：厂商级隐藏 / 按类硬隐藏 / 按类灰显「未适配」。
- **Windows 开箱即用**：一键安装 `bootstrap.ps1`、体检 `doctor.ps1`、登录自启 + 每 5 分钟健康自愈的看门狗。
- **零侵入**：不改 ComfyUI 源码，全部能力在并列的 custom_node + 独立代理进程里。

---

## 版本兼容性与适配矩阵 ⚠️

> **核心约束：bridge 不是通用透明代理。** 每个 adapter 都是对「**某个 ComfyUI 版本的 `comfy_api_nodes` 节点契约**」+「**某个供应商的 API 协议 / 模型版本**」做的**精确翻译**——它硬编码了节点类名、请求/响应字段路径、端点路径、模型名映射规则。**任一侧版本漂移都可能让适配静默失效**（菜单门控错位、字段重写漏改、404/424、或返回体校验崩）。升级 ComfyUI 或更换网关前，务必先核对本节。

### 1. 当前验证基线

| 组件 | 锁定 / 验证版本 | 出处 |
|---|---|---|
| ComfyUI core | **0.22.3** | `ComfyUI/comfyui_version.py` |
| comfyui-frontend-package | 1.43.18 | `ComfyUI/requirements.txt` |
| comfyui-workflow-templates | 0.9.85 | `ComfyUI/requirements.txt` |
| comfyui-embedded-docs | 0.5.0 | `ComfyUI/requirements.txt` |
| comfy-bridge | 0.1.0 | `pyproject.toml` |
| Python | ≥ 3.12 | `pyproject.toml` |

> adapter 引用的具体行号/字段路径都是针对 **ComfyUI 0.22.3 的 `comfy_api_nodes`** 校对的。升级 ComfyUI 后这些锚点可能位移——以源码注释里的「符号名」（类名/字段名）为准重新定位，行号仅供参考。

### 2. ComfyUI ↔ bridge 的耦合点（为什么不能随意升 ComfyUI）

| 耦合维度 | bridge 侧位置 | 依赖 ComfyUI 的什么 | 漂移后果 |
|---|---|---|---|
| **节点类名** | `app/config.py` `DEFAULT_ALLOWED_NODE_CLASSES` | `comfy_api_nodes` 各节点 `node_id`（如 `ClaudeNode`/`OpenAIChatNode`/`GeminiNanoBanana2`/`ByteDance2TextToVideoNode`） | 改名 → 门控白名单失配，节点被误灰显/误隐藏 |
| **请求字段路径** | 各 adapter `_rewrite_body` | 节点发出的 JSON 结构：Anthropic `messages[].content[].source.url`、Gemini `contents[].parts[].fileData.fileUri`、Tripo `body.file`/`body.files`、Seedance `content[].role` | 改 schema → 资产重写漏改，网关收到 `127.0.0.1` 内网 URL 而失败 |
| **端点路径** | 各 adapter `handle` | 节点请求的 vendor path（`/v1/responses`、`/v1/messages`、`/v1beta/models/{model}:generateContent`、`/v2/openapi/task`、Ark `api/v3/...`） | 改路由段 → 命中 424「无 handler」/ 404 |
| **门控 vendor 推导** | custom_node 服务端剪枝 | 节点 `python_module`（如 `nodes_bytedance` → vendor `bytedance`） | 改模块名 → 厂商门控失效 |
| **ComfyUI 内部行为** | `app/errors.py` | `util/client.py` 的 `_RETRY_STATUS={408,500,502,503,504}`、424 不触发「请先登录」、Tripo 节点 `pydantic` enum 对空串的处理 | 改重试集/校验 → 错误被吞或无限重试 |

adapter ↔ 节点锚点速查（升级后用符号名重新定位）：
- **OpenAI** → `nodes_openai.py`（responses create+poll，poll 逻辑约 :1170-1176）
- **Anthropic** → `nodes_anthropic.py`（`AnthropicImageSourceUrl` 约 :147）+ `apis/anthropic.py`
- **Gemini** → `nodes_gemini.py`（约 :48/:497/:710）+ `apis/gemini.py`
- **Tripo** → `nodes_tripo.py`（约 :51/:285-289/:420-440）+ `apis/tripo.py`
- **ByteDance/Seedance** → `nodes_bytedance.py`（Ark 端点 + `SEEDREAM_MODELS`/`SEEDANCE_MODELS` 表）

### 3. 供应商协议 / 模型版本矩阵

> 「当前型号」列是 **ComfyUI 0.22.3 的 `comfy_api_nodes` 现在暴露给前端的模型 ID**，会随 ComfyUI 升级而变。**你的网关后端必须真实支持这些型号**（或在网关侧做型号别名映射），否则节点能选但调用必失败。带日期戳的型号（`-251215` 等）尤其易随厂商更新而被替换。

| 供应商 | 节点类 | bridge 端点 / 协议 | 鉴权 | 当前型号（comfy_api_nodes @ 0.22.3） | 网关侧要求 |
|---|---|---|---|---|---|
| **OpenAI** | `OpenAIChatNode` / `OpenAIGPTImage1` / `OpenAIGPTImageNodeV2` / `OpenAIDalle2` / `OpenAIDalle3` | `POST/GET /v1/responses`、`POST /v1/images/{generations,edits}` | `Authorization: Bearer` | `gpt-5.5-pro`/`gpt-5.5`/`gpt-5`/`gpt-5-mini`/`gpt-5-nano`；图：GPT-Image-1 / v2 / DALL·E 2,3 | 须实现 `/v1/responses`；`GET /v1/responses/{id}` 可缺（bridge 有终态缓存兜底）。base 填 origin-root，会自动去重 `/v1` |
| **Anthropic** | `ClaudeNode` | `POST /v1/messages`（原生协议） | `x-api-key` + `anthropic-version: 2023-06-01` | `claude-opus-4-7`/`-4-6`、`claude-sonnet-4-6`/`-4-5-20250929`、`claude-haiku-4-5-20251001` | 网关须**原生支持 Anthropic `/v1/messages`**（非 OpenAI 兼容层）。base **不要**带 `/v1` |
| **Gemini** | `GeminiNode` / `GeminiImageNode` / `GeminiImage2Node` / `GeminiNanoBanana2` / `GeminiNanoBanana2V2` | `POST /v1beta/models/{model}:generateContent`（节点的 Vertex 壳被译为 GL） | `x-goog-api-key`（AI Studio key） | 文本：`gemini-2.5-pro`/`-2.5-flash`/`-3-pro-preview`/`-3.1-pro-preview`/`-3.1-flash-lite-preview`；图：`gemini-3-pro-image-preview`、`gemini-3.1-flash-image-preview`(=Nano Banana 2) | 须支持 GL `v1beta generateContent` |
| **Tripo** | `TripoImageToModelNode` / `TripoMultiviewToModelNode`（其余任务类型 e2e 验过，按需 `.env` 放行） | `POST /v2/openapi/task`、`GET .../task/{id}`、`POST /v2/openapi/upload` | `Authorization: Bearer` | Tripo v2 OpenAPI（image→3D、multiview→3D） | upload 返回字段名 `image_token`（若 API 漂移需复核） |
| **ByteDance·Seedream（图）** | `ByteDanceImageNode` / `ByteDanceSeedreamNode` / `ByteDanceSeedreamNodeV2` | 节点 Ark `api/v3/images/generations` → 网关 `POST /v1/images/generations` | `Authorization: Bearer` | `seedream-5-0-260128`/`-4-5-251128`/`-4-0-250828`/`-3-0-t2i-250415`（bridge 加 `doubao-` 前缀） | 网易雷火网关方言；模型名映射见 `byteplus.py:_map_image_model` |
| **ByteDance·Seedance（视频）** | 1.x：`ByteDanceTextToVideoNode`/`ImageToVideoNode`/`FirstLastFrameNode`/`ImageReferenceNode`；2.0：`ByteDance2TextToVideoNode`/`2FirstLastFrameNode`/`2ReferenceNode` | 节点 Ark `api/v3/contents/generations/tasks`(+poll) → 网关 `POST/GET /v1/video/generations` | `Authorization: Bearer` | 2.0：`dreamina-seedance-2-0-260128`/`-fast-260128`；1.x：`seedance-1-5-pro-251215`；已弃用：`seedance-1-0-lite-*-250428` | 1.x 参数内联在 prompt；2.0 分离字段（resolution/ratio/duration/seed/watermark）由 bridge 拼成 `--params` 后缀；模型名映射见 `_map_video_model` |

> **ByteDance 三段路由 vs 门控 vendor 名不一致**（易踩坑）：adapter 注册三个路由段 `byteplus`/`byteplus-seedance2`/`seedance`（来自端点路径），共用一对 `BYTEPLUS_BASE_URL`/`BYTEPLUS_API_KEY`；而 `.env` 门控里写的是 **`bytedance`**（由 `python_module=nodes_bytedance` 推导）。两者名字不同，别混。

### 4. 升级 checklist

升级 ComfyUI / 切换网关 / 厂商出新模型时，按序核对：

1. **升级前**：`git -C ComfyUI log --oneline` 留意 `comfy_api_nodes/` 的改动；记录当前 `comfyui_version.py`。
2. **节点契约**：对照「§2 锚点速查」逐 adapter 复核——节点类名（`node_id`）、请求字段路径、端点路径是否变化；变了就同步改 `config.py` 白名单与对应 adapter。
3. **模型型号**：对照「§3 当前型号」——`nodes_*.py` 里的模型 enum 是否新增/弃用日期戳型号；确认你的网关后端支持新型号，必要时更新 `byteplus.py` 的 `_map_*_model` 映射。
4. **跑回归**：`pytest tests -q`（当前 60 passed）+ Windows `doctor.ps1`。
5. **端到端**：用「§自测」的 curl 直打 bridge→网关，再在 ComfyUI 画布上各厂商各跑一次。
6. **更新本节**：把新的「验证基线」版本号与型号回填到 §1/§3。

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
| `BRIDGE_HTTP_TIMEOUT` | `300` | 上游读超时（秒）；同步出图模型（如 gpt-image-2）耗时长时调大 |
| `BRIDGE_NO_PROXY` | 空 | 逗号分隔域名并入 `NO_PROXY`，让发往网关的请求绕过系统 HTTP(S) 代理/VPN（如 v2rayN，否则长连接被掐 ReadTimeout） |
| `OPENAI_BASE_URL` / `OPENAI_API_KEY` | `https://api.openai.com` / — | OpenAI 兼容网关 + key |
| `ANTHROPIC_BASE_URL` / `ANTHROPIC_API_KEY` | `https://api.anthropic.com` / — | 网关须**原生支持** Anthropic 协议 |
| `ANTHROPIC_VERSION` | `2023-06-01` | `anthropic-version` 头 |
| `GEMINI_BASE_URL` / `GEMINI_API_KEY` | `https://generativelanguage.googleapis.com` / — | Gemini |
| `TRIPO_BASE_URL` / `TRIPO_API_KEY` | `https://api.tripo3d.ai` / — | Tripo |
| `BYTEPLUS_BASE_URL` / `BYTEPLUS_API_KEY` | — | ByteDance/Seedance（Seedream 图 + Seedance 1.x/2.0 视频）；三个路由段 `byteplus`/`byteplus-seedance2`/`seedance` 共用此对 |

> 只填要用的厂商；缺 key 的厂商节点返回 HTTP 424「未配置」，不影响其它。base URL 填 origin-root（OpenAI 会自动去重 `/v1`，Anthropic **不要**带 `/v1`）。

### 三层节点门控

| 层 | 配置 | 效果 | 生效方式 |
|---|---|---|---|
| 厂商隐藏 | `BRIDGE_ALLOWED_VENDORS` | 非白名单厂商节点从菜单移除（服务端剪枝） | 重启 ComfyUI |
| 按类硬隐藏 | `BRIDGE_HIDDEN_NODE_CLASSES` | 指定类从菜单移除，优先级最高（服务端剪枝） | 重启 ComfyUI |
| 按类灰显 | `BRIDGE_ALLOWED_NODE_CLASSES` | 允许厂商但不在白名单的类，画布上灰显「未适配」并禁用 | 前端硬刷新 |

> 改 `*_BASE_URL` / `*_API_KEY` 等后端配置只需**重启 bridge**，无需刷新前端。

> **ByteDance/Seedance 门控小坑**：`BRIDGE_ALLOWED_VENDORS` 里写的是 **`bytedance`**（门控 vendor 由节点 `python_module=nodes_bytedance` 推导），而 adapter 注册的路由段是 `byteplus`/`byteplus-seedance2`/`seedance`（来自端点路径）—— 两者名字不同，别混。

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
.venv/Scripts/python -m pytest tests -q           # 64 passed
```

测试用 `BRIDGE_SKIP_DOTENV=1`（conftest）隔离，不读真实 `.env`。

---

## 项目结构

```
comfy-bridge/
├── app/                      # FastAPI 后端
│   ├── main.py               #   app 工厂、路由、CORS
│   ├── router.py             #   /proxy/{vendor}/{path} 分发
│   ├── adapters/             #   openai / anthropic / gemini / tripo / byteplus + base
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
├── tests/                    # pytest（60）
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
