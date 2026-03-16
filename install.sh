#!/bin/bash
# ══════════════════════════════════════════════════════════════
# 三省六部 · OpenClaw Multi-Agent System 一键安装脚本
# ══════════════════════════════════════════════════════════════
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OC_HOME="$HOME/.openclaw"
OC_CFG="$OC_HOME/openclaw.json"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'

banner() {
  echo ""
  echo -e "${BLUE}╔══════════════════════════════════════════╗${NC}"
  echo -e "${BLUE}║  🏛️  三省六部 · OpenClaw Multi-Agent    ║${NC}"
  echo -e "${BLUE}║       安装向导                            ║${NC}"
  echo -e "${BLUE}╚══════════════════════════════════════════╝${NC}"
  echo ""
}

log()   { echo -e "${GREEN}✅ $1${NC}"; }
warn()  { echo -e "${YELLOW}⚠️  $1${NC}"; }
error() { echo -e "${RED}❌ $1${NC}"; }
info()  { echo -e "${BLUE}ℹ️  $1${NC}"; }

# ── Step 0: 依赖检查 ──────────────────────────────────────────
check_deps() {
  info "检查依赖..."
  
  if ! command -v openclaw &>/dev/null; then
    error "未找到 openclaw CLI。请先安装 OpenClaw: https://openclaw.ai"
    exit 1
  fi
  log "OpenClaw CLI: $(openclaw --version 2>/dev/null || echo 'OK')"

  if ! command -v python3 &>/dev/null; then
    error "未找到 python3"
    exit 1
  fi
  log "Python3: $(python3 --version)"

  if [ ! -f "$OC_CFG" ]; then
    error "未找到 openclaw.json。请先运行 openclaw 完成初始化。"
    exit 1
  fi
  log "openclaw.json: $OC_CFG"
}

# ── Step 0.5: EDICT 后端基础设施（PostgreSQL + Redis）──────────
setup_edict_infra() {
  info "检查 EDICT 后端基础设施..."

  if ! command -v docker &>/dev/null; then
    warn "未找到 docker，跳过 PostgreSQL/Redis 自动安装"
    warn "EDICT 后端需要 PostgreSQL 和 Redis，请手动安装后再启动"
    return
  fi
  log "Docker: $(docker --version 2>/dev/null | head -1)"

  # PostgreSQL
  if docker ps --format '{{.Names}}' | grep -q '^edict-pg$'; then
    log "PostgreSQL 容器已运行: edict-pg"
  elif docker ps -a --format '{{.Names}}' | grep -q '^edict-pg$'; then
    info "启动已有的 PostgreSQL 容器..."
    docker start edict-pg
    log "PostgreSQL 容器已启动: edict-pg"
  else
    info "创建 PostgreSQL 容器..."
    docker run -d --name edict-pg \
      -e POSTGRES_USER=edict \
      -e POSTGRES_PASSWORD=edict \
      -e POSTGRES_DB=edict \
      -p 5432:5432 \
      --restart=always \
      postgres:16-alpine
    log "PostgreSQL 容器已创建: edict-pg (端口 5432)"
  fi

  # Redis
  if docker ps --format '{{.Names}}' | grep -q '^edict-redis$'; then
    log "Redis 容器已运行: edict-redis"
  elif docker ps -a --format '{{.Names}}' | grep -q '^edict-redis$'; then
    info "启动已有的 Redis 容器..."
    docker start edict-redis
    log "Redis 容器已启动: edict-redis"
  else
    info "创建 Redis 容器..."
    docker run -d --name edict-redis \
      -p 6379:6379 \
      --restart=always \
      redis:7-alpine
    log "Redis 容器已创建: edict-redis (端口 6379)"
  fi

  # 等待服务就绪
  sleep 3
  if docker exec edict-pg pg_isready -q 2>/dev/null; then
    log "PostgreSQL 连接正常"
  else
    warn "PostgreSQL 启动中，可能需要几秒..."
  fi
  if docker exec edict-redis redis-cli ping 2>/dev/null | grep -q PONG; then
    log "Redis 连接正常"
  else
    warn "Redis 启动中，可能需要几秒..."
  fi

  # 设置容器自动重启
  docker update --restart=always edict-pg edict-redis &>/dev/null || true

  # 安装 Python 依赖
  if [ -f "$REPO_DIR/edict/backend/requirements.txt" ]; then
    info "安装 EDICT 后端 Python 依赖..."
    if [ -d "$REPO_DIR/.venv-edict" ]; then
      "$REPO_DIR/.venv-edict/bin/pip" install -q -r "$REPO_DIR/edict/backend/requirements.txt" 2>/dev/null && log "依赖已安装（已有虚拟环境）" || warn "依赖安装失败，请手动 pip install"
    else
      python3 -m venv "$REPO_DIR/.venv-edict" 2>/dev/null && \
        "$REPO_DIR/.venv-edict/bin/pip" install -q -r "$REPO_DIR/edict/backend/requirements.txt" 2>/dev/null && \
        log "虚拟环境已创建: .venv-edict" || \
        warn "虚拟环境创建失败，请手动: python3 -m venv .venv-edict && pip install -r edict/backend/requirements.txt"
    fi
  fi
}

# ── Step 0.6: 备份已有 Agent 数据 ──────────────────────────────
backup_existing() {
  AGENTS_DIR="$OC_HOME"
  BACKUP_DIR="$OC_HOME/backups/pre-install-$(date +%Y%m%d-%H%M%S)"
  HAS_EXISTING=false

  # 检查是否有已存在的 workspace
  for d in "$AGENTS_DIR"/workspace-*/; do
    if [ -d "$d" ]; then
      HAS_EXISTING=true
      break
    fi
  done

  if $HAS_EXISTING; then
    info "检测到已有 Agent Workspace，自动备份中..."
    mkdir -p "$BACKUP_DIR"

    # 备份所有 workspace 目录
    for d in "$AGENTS_DIR"/workspace-*/; do
      if [ -d "$d" ]; then
        ws_name=$(basename "$d")
        cp -R "$d" "$BACKUP_DIR/$ws_name"
      fi
    done

    # 备份 openclaw.json
    if [ -f "$OC_CFG" ]; then
      cp "$OC_CFG" "$BACKUP_DIR/openclaw.json"
    fi

    # 备份 agents 目录（agent 注册信息）
    if [ -d "$AGENTS_DIR/agents" ]; then
      cp -R "$AGENTS_DIR/agents" "$BACKUP_DIR/agents"
    fi

    log "已备份到: $BACKUP_DIR"
    info "如需恢复，运行: cp -R $BACKUP_DIR/workspace-* $AGENTS_DIR/"
  fi
}

# ── Step 1: 创建 Workspace ──────────────────────────────────
create_workspaces() {
  info "创建 Agent Workspace..."
  
  AGENTS=(taizi zhongshu menxia shangshu hubu libu bingbu xingbu gongbu libu_hr zaochao)
  for agent in "${AGENTS[@]}"; do
    ws="$OC_HOME/workspace-$agent"
    mkdir -p "$ws/skills"
    if [ -f "$REPO_DIR/agents/$agent/SOUL.md" ]; then
      if [ -f "$ws/SOUL.md" ]; then
        # 已存在的 SOUL.md，先备份再覆盖
        cp "$ws/SOUL.md" "$ws/SOUL.md.bak.$(date +%Y%m%d-%H%M%S)"
        warn "已备份旧 SOUL.md → $ws/SOUL.md.bak.*"
      fi
      sed "s|__REPO_DIR__|$REPO_DIR|g" "$REPO_DIR/agents/$agent/SOUL.md" > "$ws/SOUL.md"
    fi
    log "Workspace 已创建: $ws"
  done

  # ── 共享 outputs 目录 ──
  mkdir -p "$REPO_DIR/outputs"
  for agent in "${AGENTS[@]}"; do
    ws="$OC_HOME/workspace-$agent"
    if [ -d "$ws/outputs" ] && [ ! -L "$ws/outputs" ]; then
      rm -rf "$ws/outputs"
    fi
    if [ ! -e "$ws/outputs" ]; then
      ln -s "$REPO_DIR/outputs" "$ws/outputs"
    fi
  done
  log "共享 outputs 目录已链接到所有 Workspace"

  # ── Agent Skills 链接（仅在配置了飞书时生效） ──
  EXT="$OC_HOME/extensions/openclaw-lark/skills"
  FEISHU_CONFIGURED=$(python3 -c "
import json, pathlib
cfg = json.loads((pathlib.Path.home() / '.openclaw/openclaw.json').read_text())
ch = cfg.get('channels', {}).get('feishu', {})
print('yes' if ch.get('enabled') and ch.get('accounts') else 'no')
" 2>/dev/null || echo "no")
  if [ -d "$EXT" ] && [ "$FEISHU_CONFIGURED" = "yes" ]; then
    declare -A SKILL_MAP=(
      [zhongshu]="feishu-create-doc feishu-fetch-doc"
      [menxia]="feishu-fetch-doc"
      [gongbu]="feishu-create-doc"
      [bingbu]="feishu-create-doc"
      [xingbu]="feishu-im-read"
      [hubu]="feishu-bitable"
      [libu]="feishu-create-doc feishu-update-doc"
      [libu_hr]="feishu-bitable"
    )
    for agent in "${!SKILL_MAP[@]}"; do
      ws="$OC_HOME/workspace-$agent/skills"
      mkdir -p "$ws"
      for skill in ${SKILL_MAP[$agent]}; do
        if [ -d "$EXT/$skill" ] && [ ! -e "$ws/$skill" ]; then
          ln -sfn "$EXT/$skill" "$ws/$skill"
        fi
      done
    done
    log "Agent Skills 已链接到各 Workspace"
  else
    info "未配置飞书或未安装 openclaw-lark 扩展，跳过飞书 Skills 链接"
  fi

  # ── 尚书省 dispatch skill ──
  DISPATCH_DIR="$OC_HOME/workspace-shangshu/skills/dispatch"
  if [ ! -f "$DISPATCH_DIR/SKILL.md" ]; then
    mkdir -p "$DISPATCH_DIR"
    cat > "$DISPATCH_DIR/SKILL.md" << 'DISPATCH_EOF'
# dispatch — 尚书省任务派发路由

## 六部路由表

| 部门 | agent_id | 职责范围 |
|------|----------|---------|
| 工部 | gongbu | 开发/架构/代码/工程实现 |
| 兵部 | bingbu | 基础设施/部署/安全/运维 |
| 户部 | hubu | 数据分析/报表/成本/财务 |
| 礼部 | libu | 文档/UI/对外沟通/公关 |
| 刑部 | xingbu | 审查/测试/合规/质量 |
| 吏部 | libu_hr | 人事/Agent管理/培训/考核 |

## 派发规则

1. 根据任务内容匹配最相关的部门
2. 如果任务涉及多个部门，选择主要职责部门，其他作为协作
3. 默认优先派发工部（开发类任务最多）
4. 派发后用 `kanban_update.py flow` 记录流转
DISPATCH_EOF
    log "尚书省 dispatch skill 已创建"
  fi

  # 通用 AGENTS.md（工作协议）
  for agent in "${AGENTS[@]}"; do
    cat > "$OC_HOME/workspace-$agent/AGENTS.md" << 'AGENTS_EOF'
# AGENTS.md · 工作协议

1. 接到任务先回复"已接旨"。
2. 输出必须包含：任务ID、结果、证据/文件路径、阻塞项。
3. 需要协作时，回复尚书省请求转派，不跨部直连。
4. 涉及删除/外发动作必须明确标注并等待批准。
AGENTS_EOF
  done
}

# ── Step 2: 注册 Agents ─────────────────────────────────────
register_agents() {
  info "注册三省六部 Agents..."

  # 备份配置
  cp "$OC_CFG" "$OC_CFG.bak.sansheng-$(date +%Y%m%d-%H%M%S)"
  log "已备份配置: $OC_CFG.bak.*"

  python3 << 'PYEOF'
import json, pathlib, sys

cfg_path = pathlib.Path.home() / '.openclaw' / 'openclaw.json'
cfg = json.loads(cfg_path.read_text())

AGENTS = [
  {"id": "taizi",    "subagents": {"allowAgents": ["zhongshu"]}},
    {"id": "zhongshu", "subagents": {"allowAgents": ["menxia", "shangshu"]}},
    {"id": "menxia",   "subagents": {"allowAgents": ["shangshu", "zhongshu"]}},
  {"id": "shangshu", "subagents": {"allowAgents": ["zhongshu", "menxia", "hubu", "libu", "bingbu", "xingbu", "gongbu", "libu_hr"]}},
    {"id": "hubu",     "subagents": {"allowAgents": ["shangshu"]}},
    {"id": "libu",     "subagents": {"allowAgents": ["shangshu"]}},
    {"id": "bingbu",   "subagents": {"allowAgents": ["shangshu"]}},
    {"id": "xingbu",   "subagents": {"allowAgents": ["shangshu"]}},
    {"id": "gongbu",   "subagents": {"allowAgents": ["shangshu"]}},
  {"id": "libu_hr",  "subagents": {"allowAgents": ["shangshu"]}},
  {"id": "zaochao",  "subagents": {"allowAgents": []}},
]

agents_cfg = cfg.setdefault('agents', {})
agents_list = agents_cfg.get('list', [])
existing_ids = {a['id'] for a in agents_list}

added = 0
for ag in AGENTS:
    ag_id = ag['id']
    ws = str(pathlib.Path.home() / f'.openclaw/workspace-{ag_id}')
    if ag_id not in existing_ids:
        entry = {'id': ag_id, 'workspace': ws, **{k:v for k,v in ag.items() if k!='id'}}
        agents_list.append(entry)
        added += 1
        print(f'  + added: {ag_id}')
    else:
        print(f'  ~ exists: {ag_id} (skipped)')

agents_cfg['list'] = agents_list
cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
print(f'Done: {added} agents added')
PYEOF

  log "Agents 注册完成"
}

# ── Step 3: 初始化 Data ─────────────────────────────────────
init_data() {
  info "初始化数据目录..."
  
  mkdir -p "$REPO_DIR/data"
  
  # 初始化空文件
  for f in live_status.json agent_config.json model_change_log.json; do
    if [ ! -f "$REPO_DIR/data/$f" ]; then
      echo '{}' > "$REPO_DIR/data/$f"
    fi
  done
  echo '[]' > "$REPO_DIR/data/pending_model_changes.json"

  # 初始任务文件
  if [ ! -f "$REPO_DIR/data/tasks_source.json" ]; then
    python3 << 'PYEOF'
import json, pathlib
tasks = [
    {
        "id": "JJC-DEMO-001",
        "title": "🎉 系统初始化完成",
        "official": "工部尚书",
        "org": "工部",
        "state": "Done",
        "now": "三省六部系统已就绪",
        "eta": "-",
        "block": "无",
        "output": "",
        "ac": "系统正常运行",
        "flow_log": [
            {"at": "2024-01-01T00:00:00Z", "from": "皇上", "to": "中书省", "remark": "下旨初始化三省六部系统"},
            {"at": "2024-01-01T00:01:00Z", "from": "中书省", "to": "门下省", "remark": "规划方案提交审核"},
            {"at": "2024-01-01T00:02:00Z", "from": "门下省", "to": "尚书省", "remark": "✅ 准奏"},
            {"at": "2024-01-01T00:03:00Z", "from": "尚书省", "to": "工部", "remark": "派发：系统初始化"},
            {"at": "2024-01-01T00:04:00Z", "from": "工部", "to": "尚书省", "remark": "✅ 完成"},
        ]
    }
]
p = pathlib.Path(__file__).parent if '__file__' in dir() else pathlib.Path('.')
# Write to data dir
import os
data_dir = pathlib.Path(os.environ.get('REPO_DIR', '.')) / 'data'
data_dir.mkdir(exist_ok=True)
(data_dir / 'tasks_source.json').write_text(json.dumps(tasks, ensure_ascii=False, indent=2))
print('tasks_source.json 已初始化')
PYEOF
  fi

  log "数据目录初始化完成: $REPO_DIR/data"
}

# ── Step 4: 构建前端 ──────────────────────────────────────────
build_frontend() {
  info "构建 React 前端..."

  if ! command -v node &>/dev/null; then
    warn "未找到 node，跳过前端构建。看板将使用预构建版本（如果存在）"
    warn "请安装 Node.js 18+ 后运行: cd edict/frontend && npm install && npm run build"
    return
  fi

  if [ -f "$REPO_DIR/edict/frontend/package.json" ]; then
    cd "$REPO_DIR/edict/frontend"
    npm install --silent 2>/dev/null || npm install
    npm run build 2>/dev/null
    cd "$REPO_DIR"
    if [ -f "$REPO_DIR/dashboard/dist/index.html" ]; then
      log "前端构建完成: dashboard/dist/"
    else
      warn "前端构建可能失败，请手动检查"
    fi
  else
    warn "未找到 edict/frontend/package.json，跳过前端构建"
  fi
}

# ── Step 5: 首次数据同步 ────────────────────────────────────
first_sync() {
  info "执行首次数据同步..."
  cd "$REPO_DIR"
  
  REPO_DIR="$REPO_DIR" python3 scripts/sync_agent_config.py || warn "sync_agent_config 有警告"
  python3 scripts/refresh_live_data.py || warn "refresh_live_data 有警告"
  
  log "首次同步完成"
}

# ── Step 6: 重启 Gateway ────────────────────────────────────
restart_gateway() {
  info "重启 OpenClaw Gateway..."
  if openclaw gateway restart 2>/dev/null; then
    log "Gateway 重启成功"
  else
    warn "Gateway 重启失败，请手动重启：openclaw gateway restart"
  fi
}

# ── Main ────────────────────────────────────────────────────
banner
check_deps
setup_edict_infra
backup_existing
create_workspaces
register_agents
init_data
build_frontend
first_sync
restart_gateway

# ── Step 8: 启动所有服务 ──────────────────────────────────────
start_services() {
  info "启动所有服务..."

  # EDICT Backend
  if [ -f "$REPO_DIR/.venv-edict/bin/python3" ]; then
    cd "$REPO_DIR/edict/backend"
    nohup "$REPO_DIR/.venv-edict/bin/python3" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 > /tmp/edict-backend.log 2>&1 &
    cd "$REPO_DIR"
    sleep 3
    if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
      log "EDICT 后端已启动 (端口 8000)"
    else
      warn "EDICT 后端启动可能失败，请检查: tail /tmp/edict-backend.log"
    fi
  else
    warn "未找到 .venv-edict，跳过 EDICT 后端启动"
  fi

  # Dashboard
  nohup python3 "$REPO_DIR/dashboard/server.py" > /tmp/dashboard-server.log 2>&1 &
  sleep 2
  if curl -sf http://localhost:7891/healthz > /dev/null 2>&1; then
    log "看板服务器已启动 (端口 7891)"
  else
    warn "看板服务器启动可能失败，请检查: tail /tmp/dashboard-server.log"
  fi

  # run_loop.sh
  nohup bash "$REPO_DIR/scripts/run_loop.sh" > /tmp/run_loop.log 2>&1 &
  log "数据刷新循环已启动"
}

start_services

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║  🎉  三省六部安装完成！所有服务已启动             ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════╝${NC}"
echo ""
echo "服务状态："
echo "  🏛️  EDICT 后端:    http://127.0.0.1:8000 (含 Orchestrator + Dispatcher)"
echo "  📊 看板:          http://127.0.0.1:7891"
echo "  🔄 数据刷新循环:  后台运行中"
echo "  🦞 OpenClaw 网关: 后台运行中"
echo ""
echo "管理命令："
echo "  查看日志:   tail -f /tmp/edict-backend.log"
echo "  重启后端:   kill \$(pgrep -f 'uvicorn app.main') && cd edict/backend && .venv-edict/bin/python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 &"
echo "  重启看板:   kill \$(pgrep -f 'dashboard/server.py') && python3 dashboard/server.py &"
echo ""
info "文档: docs/getting-started.md"
