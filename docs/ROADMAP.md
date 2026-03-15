# 三省六部 · 技术演进路线图

## P0 — 干掉 JSON 双写（预计 1-2 天）
- Dashboard server.py 所有任务操作直连 EDICT backend（8000）
- 废弃 tasks_source.json 作为任务数据源
- 保留 JSON 仅作初始化种子数据
- scheduler scan 直接查 EDICT DB
- **状态：🔄 进行中**

## P1 — Workspace 同步自动化（预计 0.5 天）
- run_loop.sh 检测 agents/*.md 变更，自动 cp 到 workspace
- kanban_update.py 变更同步到所有 workspace
- 消除手动 cp 的运维负担

## P2 — 关键 Agent 模型升级（预计 0.5 天）
- 太子、中书省、尚书省用更强模型（Gemini Flash / Claude Haiku）
- 六部用轻量模型（Flash Lite）
- openclaw.json 按 agent 配置不同 model

## P3 — 飞书/看板入口统一（预计 1 天）
- 飞书为主入口，看板为只读监控
- 飞书下旨 → EDICT DB → 看板实时显示
- 消除 OC-* 和 JJC-* 两套任务 ID

## P4 — 产出物归档到 EDICT DB（预计 0.5 天）
- agent 产出内容存 EDICT `output` 字段（存内容不是路径）
- 看板任务详情直接渲染 Markdown 产出
- 可选：自动上传飞书文档

## P5 — 任务详情实时页（预计 1 天）
- 新建任务详情页，展示完整流转过程
- progress_log 实时渲染
- 中间产物/文档内联展示

## P6 — GitHub Actions CI（预计 0.5 天）
- PR 时跑 mock 测试（12 cases）
- merge 后跑集成测试（7 cases，需 docker-compose）
- 测试失败阻止合并

## P7 — 任务停滞告警（预计 0.5 天）
- 活跃任务超 10 分钟无 progress → 飞书告警
- scheduler scan 结果推送到飞书群
- 看板增加告警面板

## P8 — install.sh 幂等化（预计 0.5 天）
- 支持重复运行不出错
- 自动检测已有配置，增量更新
- 支持 --upgrade 模式
