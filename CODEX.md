# CODEX.md — 开发约定

## Git 配置

- **上游 (origin)**: `git@github.com:cft0808/edict.git`（只读，项目原作者）
- **Fork (myfork)**: `git@github.com:3323-pixel/edict.git`（可推送）
- 推代码用 `myfork`，不要推 `origin`
- SSH key: `~/.ssh/id_ed25519`（已配置）

## 分支约定

- `main` 跟踪上游，不直接推
- 功能分支命名：`fix/xxx`、`feat/xxx`
- 推送示例：`git push -u myfork fix/some-branch`

## 项目结构

- `edict/backend/` — FastAPI + PostgreSQL + Redis Streams
- `edict/frontend/` — React + Vite dashboard
- `scripts/kanban_update.py` — Agent 调用的看板 CLI（通过 edict_client.py 走 HTTP API）
- `agents/*/SOUL.md` — 各 Agent 的指令文件（源码），需同步到 `~/.openclaw/workspace-*/SOUL.md`

## 注意事项

- Agent 运行在 OpenClaw 框架下，模型为 Gemini，workspace 在 `~/.openclaw/workspace-{agent}/`
- 修改 SOUL.md 后必须 `cp` 到对应 workspace 目录才能生效
- `kanban_update.py` 的 `flow` 命令在太子→中书省等关键流转时会自动同步 state
- 测试用虚拟环境：`.venv-edict/`（已在 .gitignore）
