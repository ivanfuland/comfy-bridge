# 贡献指南

`master` 是受保护分支：**禁止直接 push（包括管理员）**，所有改动必须经 Pull Request，且 CI 通过后才能合并。

## 分支保护规则

| 规则 | 配置 |
|---|---|
| 直推 `master` | ❌ 禁止，必须经 PR |
| 必需审批数 | 0（单人开发，可自行合并） |
| 必过状态检查 | `test`（CI 的 pytest 作业） |
| 分支须与 `master` 同步 | ✔ strict（落后时需先 rebase/merge） |
| 对管理员强制 | ✔ enforce_admins |

## 标准流程

```bash
# 1. 从最新 master 开分支
git checkout master && git pull
git checkout -b fix/简短描述

# 2. 改代码，本地先跑测试
pytest -q

# 3. 提交并推送分支
git commit -am "fix: ..."
git push -u origin fix/简短描述

# 4. 开 PR
gh pr create --fill

# 5. 等 CI 变绿
gh pr checks --watch

# 6. 合并并删除分支（CI 通过后）
gh pr merge --squash --delete-branch
```

> 若 PR 期间 `master` 有新提交，因开启了 strict，需先把分支更新到最新：
> `git fetch origin && git rebase origin/master`（或在 PR 页点 "Update branch"），等 CI 重新跑绿再合并。

## 本地开发

```powershell
# Windows：创建 venv（uv venv 不带 pip，需先装）
uv venv
uv pip install --python .venv\Scripts\python.exe pip
.venv\Scripts\python.exe -m pip install -e ".[dev]"

# 跑测试
.venv\Scripts\python.exe -m pytest -q
```

CI 在 `.github/workflows/ci.yml`，对 push/PR 到 `master` 用 Python 3.12 跑 `pytest`。

## 项目结构

- `app/` — FastAPI 后端：`router.py`（`/proxy/{vendor}/*` 分发）、`adapters/`（每厂商一个 + `base.py`）、`gating.py`、`config.py`、`assets.py`、`errors.py`
- `custom_nodes/comfy-bridge-gating/` — ComfyUI 端 custom_node（`__init__.py` 服务端剪枝 + `web/*.js` 前端灰显）
- `windows/` — 所有 Windows 脚本（`.bat`/`.ps1`/`.vbs`）。**Windows 脚本一律放这，别放仓库根目录**
- `systemd/` — Linux user service
- `docs/` — 文档（如 `WINDOWS-QUICKSTART.md`）
- `tests/` — pytest 套件

详细目录树见 README「项目结构」。

## 加一个厂商 / 端点

1. **适配器**：`app/adapters/<vendor>.py` 继承 `BaseAdapter`，实现 `async def handle(path, request, raw)`，用 `self.base()` / `self.key()` / `http_client()` 调上游，按需改写请求头/路径/图片引用（参考现有 openai / anthropic / gemini / tripo），末尾 `register("<vendor>", ...)`。
2. **配置**：`app/config.py` 的 `_PROVIDER_*` 加该厂商的 env 名与默认 base。
3. **门控（不改代码）**：节点显隐全走 `.env` —— `BRIDGE_ALLOWED_VENDORS` / `BRIDGE_ALLOWED_NODE_CLASSES`（灰显「未适配」）/ `BRIDGE_HIDDEN_NODE_CLASSES`（菜单硬隐藏），覆盖 `config.py` 的 `DEFAULT_ALLOWED_*` 基线。**别把节点名硬编码进 `gating.py`**。
4. 配套写测试（见下）。

## 测试约定

- `tests/` 用 `pytest` + `respx`（mock 上游 HTTP）+ FastAPI `TestClient`。
- `conftest.py` 设 `BRIDGE_SKIP_DOTENV=1` 隔离真实 `.env`，测试**不读真实密钥**。
- 改适配器 / 路由 / 门控都要配对应测试；PR 的 CI 必须绿。

## 跨平台约定

- 通用逻辑（adapter / gating / 日志 / 资源改写）写在 `app/`，两平台共享。
- 平台专属：Windows → `windows/`（相对路径，不写死盘符）；Linux → `systemd/`。
- 排障：`BRIDGE_LOG_IO`（默认 on）把每笔上游 input(`→`)/output(`←`) 记进日志——Windows 看 `logs/bridge.log`，Linux 看 `journalctl --user -u comfy-bridge`。
- 代码风格：与周围代码一致（命名、注释密度、惯用法）。

## 注意

- `.env` 含真实密钥，**永不提交**（已在 `.gitignore`）。
- 提交信息建议用 [Conventional Commits](https://www.conventionalcommits.org/) 前缀：`fix:` / `feat:` / `ci:` / `docs:` 等。
