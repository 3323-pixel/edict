# 🚀 快速上手指南

> 从零开始，5 分钟搭建你的三省六部 AI 协同系统

---

## 第一步：安装 OpenClaw

三省六部基于 [OpenClaw](https://openclaw.ai) 运行，请先安装：

```bash
# macOS
brew install openclaw

# 或下载安装包
# https://openclaw.ai/download
```

安装完成后初始化：

```bash
openclaw init
```

## 第二步：克隆并安装三省六部

```bash
git clone https://github.com/cft0808/edict.git
cd edict
chmod +x install.sh && ./install.sh
```

安装脚本会自动完成：
- ✅ 检测并启动 PostgreSQL + Redis（需要 Docker）
- ✅ 安装 EDICT 后端 Python 依赖（自动创建虚拟环境）
- ✅ 创建 12 个 Agent Workspace（`~/.openclaw/workspace-*`）
- ✅ 写入各省部 SOUL.md 人格文件
- ✅ 注册 Agent 及权限矩阵到 `openclaw.json`
- ✅ 链接飞书 Skills 到各 Agent（检测到飞书配置时）
- ✅ 创建共享 outputs 目录
- ✅ 构建 React 前端到 `dashboard/dist/`（需 Node.js 18+）
- ✅ 初始化数据目录
- ✅ 执行首次数据同步
- ✅ 重启 Gateway 使配置生效

> ⚠️ **前置要求**：Docker（用于运行 PostgreSQL 和 Redis）。如果没有 Docker，install.sh 会跳过数据库安装，你需要手动安装。

## 第三步：配置消息渠道

在 OpenClaw 中配置消息渠道（Feishu / Telegram / Signal），将 `taizi`（太子）Agent 设为旨意入口。太子会自动分拣闲聊与指令，指令类消息提炼标题后转发中书省。

```bash
# 查看当前渠道
openclaw channels list

# 添加飞书渠道（入口设为太子）
openclaw channels add --type feishu --agent taizi
```

参考 OpenClaw 文档：https://docs.openclaw.ai/channels

## 第四步：启动服务

需要启动 3 个服务：

```bash
# 1. EDICT 后端（事件驱动派发 + 数据库）
cd edict/backend
.venv-edict/bin/python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 &
cd ../..

# 2. 看板服务器
python3 dashboard/server.py &

# 3. 数据刷新循环（每 15 秒同步 + 每小时清理 session）
bash scripts/run_loop.sh &

# 打开浏览器
open http://127.0.0.1:7891
```

> ⚠️ **EDICT 后端必须启动**：它包含 Orchestrator（自动派发 Agent）和 Dispatcher（执行 Agent 调用）。不启动的话任务状态变更后 Agent 不会自动接力。

> 💡 **看板即开即用**：`server.py` 内嵌 React 前端，无需额外构建。

### 验证服务是否正常

```bash
# 看板
curl http://localhost:7891/healthz
# EDICT 后端
curl http://localhost:8000/health
# Gateway
openclaw daemon status
```

## 第五步：发送第一道旨意

通过消息渠道发送任务（太子会自动识别并转发到中书省）：

```
请帮我用 Python 写一个文本分类器：
1. 使用 scikit-learn
2. 支持多分类
3. 输出混淆矩阵
4. 写完整的文档
```

## 第六步：观察执行过程

打开看板 http://127.0.0.1:7891

1. **📋 旨意看板** — 观察任务在各状态之间流转
2. **🔭 省部调度** — 查看各部门工作分布
3. **📜 奏折阁** — 任务完成后自动归档为奏折

任务流转路径：
```
收件 → 太子分拣 → 中书规划 → 门下审议 → 已派发 → 执行中 → 已完成
```

---

## 🎯 进阶用法

### 使用圣旨模板

> 看板 → 📜 旨库 → 选择模板 → 填写参数 → 下旨

9 个预设模板：周报生成 · 代码审查 · API 设计 · 竞品分析 · 数据报告 · 博客文章 · 部署方案 · 邮件文案 · 站会摘要

### 切换 Agent 模型

> 看板 → ⚙️ 模型配置 → 选择新模型 → 应用更改

约 5 秒后 Gateway 自动重启生效。

### 管理技能

> 看板 → 🛠️ 技能配置 → 查看已安装技能 → 点击添加新技能

### 叫停 / 取消任务

> 在旨意看板或任务详情中，点击 **⏸ 叫停** 或 **🚫 取消** 按钮

### 订阅天下要闻

> 看板 → 📰 天下要闻 → ⚙️ 订阅管理 → 选择分类 / 添加源 / 配飞书推送

---

## ❓ 故障排查

### 看板显示「服务器未启动」
```bash
# 确认服务器正在运行
python3 dashboard/server.py
```

### Agent 不响应
```bash
# 检查 Gateway 状态
openclaw gateway status

# 必要时重启
openclaw gateway restart
```

### 数据不更新
```bash
# 检查刷新循环是否运行
ps aux | grep run_loop

# 手动执行一次同步
python3 scripts/refresh_live_data.py
```

### 心跳显示红色 / 告警
```bash
# 检查对应 Agent 的进程
openclaw agent status <agent-id>

# 重启指定 Agent
openclaw agent restart <agent-id>
```

### 模型切换后不生效
等待约 5 秒让 Gateway 重启完成。仍不生效则：
```bash
python3 scripts/apply_model_changes.py
openclaw gateway restart
```

---

## 📚 更多资源

- [🏠 项目首页](https://github.com/cft0808/edict)
- [📖 README](../README.md)
- [🤝 贡献指南](../CONTRIBUTING.md)
- [💬 OpenClaw 文档](https://docs.openclaw.ai)
- [📮 公众号 · cft0808](wechat.md) — 架构拆解 / 踩坑复盘 / Token 省钱术
