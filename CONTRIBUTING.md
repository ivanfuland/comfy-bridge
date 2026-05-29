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

## 注意

- `.env` 含真实密钥，**永不提交**（已在 `.gitignore`）。
- 提交信息建议用 [Conventional Commits](https://www.conventionalcommits.org/) 前缀：`fix:` / `feat:` / `ci:` / `docs:` 等。
