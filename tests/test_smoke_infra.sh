#!/bin/bash
# 冒烟测试：验证基础设施和运维配置
# 用法: bash tests/test_smoke_infra.sh
set -e

PASS=0
FAIL=0
SKIP=0

pass() { echo "  ✅ $1"; PASS=$((PASS+1)); }
fail() { echo "  ❌ $1"; FAIL=$((FAIL+1)); }
skip() { echo "  ⏭️  $1 (跳过)"; SKIP=$((SKIP+1)); }

echo "=== 基础设施冒烟测试 ==="
echo ""

# ── 1. Docker 容器 ──
echo "▸ Docker 容器"
if ! command -v docker &>/dev/null; then
    skip "Docker 未安装"
else
    DOCKER="docker"
    $DOCKER ps &>/dev/null || DOCKER="sudo docker"
    $DOCKER ps --format '{{.Names}}' | grep -q '^edict-pg$' && pass "PostgreSQL 容器运行中" || fail "PostgreSQL 容器未运行"
    $DOCKER ps --format '{{.Names}}' | grep -q '^edict-redis$' && pass "Redis 容器运行中" || fail "Redis 容器未运行"
    PG_RESTART=$($DOCKER inspect edict-pg --format '{{.HostConfig.RestartPolicy.Name}}' 2>/dev/null || echo "none")
    [ "$PG_RESTART" = "always" ] && pass "PostgreSQL restart=always" || fail "PostgreSQL restart=$PG_RESTART (应为 always)"
    REDIS_RESTART=$($DOCKER inspect edict-redis --format '{{.HostConfig.RestartPolicy.Name}}' 2>/dev/null || echo "none")
    [ "$REDIS_RESTART" = "always" ] && pass "Redis restart=always" || fail "Redis restart=$REDIS_RESTART (应为 always)"
fi

# ── 2. 服务端口 ──
echo ""
echo "▸ 服务端口"
curl -sf http://localhost:8000/health > /dev/null 2>&1 && pass "EDICT backend :8000" || fail "EDICT backend :8000 不可达"
curl -sf http://localhost:7891/healthz > /dev/null 2>&1 && pass "Dashboard :7891" || fail "Dashboard :7891 不可达"

# ── 3. EDICT Workers ──
echo ""
echo "▸ EDICT Workers"
BACKEND_LOG="/tmp/edict-backend.log"
if [ -f "$BACKEND_LOG" ]; then
    grep -q "Orchestrator worker started" "$BACKEND_LOG" && pass "Orchestrator worker 已启动" || fail "Orchestrator worker 未启动"
    grep -q "Dispatch worker started" "$BACKEND_LOG" && pass "Dispatch worker 已启动" || fail "Dispatch worker 未启动"
else
    skip "后端日志不存在: $BACKEND_LOG"
fi

# ── 4. dispatch_worker 使用独立 session-id ──
echo ""
echo "▸ Session 隔离"
grep -q 'session_id.*edict-' edict/backend/app/workers/dispatch_worker.py && pass "dispatch_worker 使用独立 session-id" || fail "dispatch_worker 未使用独立 session-id"
grep -q 'session_id.*edict-\|session-id.*edict' dashboard/server.py && pass "dashboard dispatch 使用独立 session-id" || fail "dashboard dispatch 未使用独立 session-id"

# ── 5. OpenClaw session 管理 ──
echo ""
echo "▸ Session 管理配置"
python3 -c "
import json
cfg = json.loads(open('$HOME/.openclaw/openclaw.json').read())
mode = cfg.get('session',{}).get('maintenance',{}).get('mode','')
print(mode)
" 2>/dev/null | grep -q "enforce" && pass "session.maintenance.mode = enforce" || fail "session.maintenance.mode 不是 enforce"

# run_loop.sh 有 session cleanup
grep -q "sessions cleanup" scripts/run_loop.sh && pass "run_loop.sh 包含定时 session cleanup" || fail "run_loop.sh 缺少 session cleanup"

# ── 6. install.sh 完整性 ──
echo ""
echo "▸ install.sh 完整性"
bash -n install.sh 2>/dev/null && pass "install.sh 语法正确" || fail "install.sh 语法错误"
grep -q "setup_edict_infra" install.sh && pass "install.sh 包含 EDICT 基础设施安装" || fail "install.sh 缺少 EDICT 基础设施"
grep -q "edict-pg" install.sh && pass "install.sh 包含 PostgreSQL 创建" || fail "install.sh 缺少 PostgreSQL"
grep -q "edict-redis" install.sh && pass "install.sh 包含 Redis 创建" || fail "install.sh 缺少 Redis"
grep -q "venv-edict" install.sh && pass "install.sh 包含 Python 虚拟环境" || fail "install.sh 缺少虚拟环境"
grep -q "restart=always" install.sh && pass "install.sh 设置容器自动重启" || fail "install.sh 缺少自动重启"

# ── 7. 关键文件存在性 ──
echo ""
echo "▸ 关键文件"
for f in \
    edict/backend/app/main.py \
    edict/backend/app/workers/orchestrator_worker.py \
    edict/backend/app/workers/dispatch_worker.py \
    edict/backend/app/api/admin.py \
    edict/backend/app/api/utils.py \
    scripts/kanban_update.py \
    scripts/edict_client.py \
    dashboard/server.py \
    dashboard/dist/index.html \
    docs/getting-started.md \
    docs/ROADMAP.md \
    CODEX.md; do
    [ -f "$f" ] && pass "$f" || fail "$f 不存在"
done

# ── 8. Redis Streams API ──
echo ""
echo "▸ Redis Streams API"
STREAMS=$(curl -sf http://localhost:8000/api/admin/system/streams 2>/dev/null)
if [ -n "$STREAMS" ]; then
    echo "$STREAMS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  streams: {len(d.get(\"streams\",[]))} topics')" 2>/dev/null && pass "GET /api/admin/system/streams 正常" || fail "streams API 返回格式异常"
else
    fail "GET /api/admin/system/streams 不可达"
fi

# ── 9. Workspace 共享 outputs ──
echo ""
echo "▸ Workspace outputs 共享"
SHARED=0
BROKEN=0
for ws in $HOME/.openclaw/workspace-*/; do
    if [ -L "$ws/outputs" ]; then
        SHARED=$((SHARED+1))
    elif [ -d "$ws" ]; then
        BROKEN=$((BROKEN+1))
    fi
done
[ $SHARED -gt 0 ] && [ $BROKEN -eq 0 ] && pass "outputs 软链 ($SHARED 个 workspace)" || fail "outputs 软链不完整 (ok=$SHARED broken=$BROKEN)"

# ── 结果 ──
echo ""
echo "════════════════════════════"
echo "  ✅ 通过: $PASS"
echo "  ❌ 失败: $FAIL"
echo "  ⏭️  跳过: $SKIP"
echo "════════════════════════════"

[ $FAIL -eq 0 ] && echo "🎉 全部通过！" || echo "⚠️ 有 $FAIL 项失败"
exit $FAIL
