"""Integration tests — real HTTP against live EDICT backend + dashboard server.

Requires:
- EDICT backend running on localhost:8000 (PostgreSQL + Redis)
- Dashboard server running on localhost:7891

Run: .venv-edict/bin/python -m pytest tests/test_integration_sync.py -v
"""

import json
import time
import urllib.request
import urllib.error
import pytest

EDICT_URL = "http://localhost:8000"
DASHBOARD_URL = "http://localhost:7891"


def _request(method, url, data=None, timeout=10):
    body = json.dumps(data, ensure_ascii=False).encode() if data else None
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _get(url, timeout=10):
    return _request("GET", url, timeout=timeout)


def _post(url, data, timeout=10):
    return _request("POST", url, data, timeout=timeout)


def _edict_get(path):
    return _get(f"{EDICT_URL}{path}")


def _edict_post(path, data):
    return _post(f"{EDICT_URL}{path}", data)


def _dashboard_get(path):
    return _get(f"{DASHBOARD_URL}{path}")


def _dashboard_post(path, data):
    return _post(f"{DASHBOARD_URL}{path}", data)


def _edict_task(legacy_id):
    """Get task from EDICT by legacy ID, returns None if not found."""
    try:
        return _edict_get(f"/api/tasks/by-legacy/{legacy_id}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def _json_task(task_id):
    """Get task from dashboard live-status by ID."""
    data = _dashboard_get("/api/live-status")
    tasks = data.get("tasks", [])
    if isinstance(tasks, list):
        return next((t for t in tasks if t.get("id") == task_id), None)
    return None


def _cleanup_task(task_id):
    """Best-effort cleanup: mark task Done + flush dispatch events to avoid wasting API quota."""
    try:
        _edict_post(f"/api/tasks/by-legacy/{task_id}/done", {
            "output": "", "summary": "test cleanup", "agent": "test"
        })
    except Exception:
        pass
    try:
        _dashboard_post("/api/task-action", {
            "taskId": task_id, "action": "cancel", "reason": "test cleanup"
        })
    except Exception:
        pass
    # 清掉测试产生的 dispatch 事件，防止 dispatcher 对测试任务调用真实 agent
    try:
        _edict_post("/api/admin/system/flush-pending?topic=task.dispatch&group=dispatcher", {})
    except Exception:
        pass


# ── Preflight checks ──

@pytest.fixture(autouse=True, scope="module")
def check_services():
    """Skip all tests if backend or dashboard are not running."""
    try:
        _edict_get("/api/tasks?limit=1")
    except Exception:
        pytest.skip("EDICT backend not running on localhost:8000")
    try:
        _dashboard_get("/healthz")
    except Exception:
        pytest.skip("Dashboard server not running on localhost:7891")


# ── Test 1: Dashboard 下旨 → EDICT DB ──

class TestDashboardCreate:
    """Dashboard create-task should write to EDICT DB."""

    def test_create_writes_to_edict(self):
        result = _dashboard_post("/api/create-task", {
            "title": "集成测试验证Dashboard创建任务到EDICT",
            "org": "中书省",
            "priority": "normal",
        })
        assert result["ok"] is True
        task_id = result["taskId"]

        try:
            # Verify EDICT DB
            edict_task = _edict_task(task_id)
            assert edict_task is not None, f"{task_id} not in EDICT DB"
            assert edict_task["state"] == "Taizi"

            # Verify live-status shows it
            json_task = _json_task(task_id)
            assert json_task is not None, f"{task_id} not in dashboard live-status"
        finally:
            _cleanup_task(task_id)


# ── Test 2: Agent updates EDICT → dashboard syncs ──

class TestAgentProgressSync:
    """Agent writes to EDICT, dashboard live-status reflects changes."""

    def test_state_change_visible_in_dashboard(self):
        # Create via dashboard
        result = _dashboard_post("/api/create-task", {
            "title": "集成测试：状态同步验证",
            "org": "中书省",
        })
        task_id = result["taskId"]

        try:
            # Simulate agent: transition Taizi → Zhongshu in EDICT
            _edict_post(f"/api/tasks/by-legacy/{task_id}/transition", {
                "new_state": "Zhongshu",
                "agent": "test",
                "reason": "integration test",
            })

            # Verify EDICT updated
            edict_task = _edict_task(task_id)
            assert edict_task["state"] == "Zhongshu"

            # Trigger sync via live-status (which calls _sync_edict_states_to_json)
            _dashboard_get("/api/live-status")

            # Now check JSON reflects the change
            json_task = _json_task(task_id)
            assert json_task is not None
            assert json_task["state"] == "Zhongshu", \
                f"Expected Zhongshu, got {json_task['state']} — sync failed"
        finally:
            _cleanup_task(task_id)

    def test_same_state_progress_syncs(self):
        """Agent writes progress in same state → dashboard should update."""
        result = _dashboard_post("/api/create-task", {
            "title": "集成测试：同状态进展同步",
            "org": "中书省",
        })
        task_id = result["taskId"]

        try:
            # Transition to Zhongshu
            _edict_post(f"/api/tasks/by-legacy/{task_id}/transition", {
                "new_state": "Zhongshu",
                "agent": "test",
                "reason": "setup",
            })

            # Sync first state change
            _dashboard_get("/api/live-status")

            # Write progress without state change
            _edict_post(f"/api/tasks/by-legacy/{task_id}/progress", {
                "agent": "test",
                "content": "正在执行第二阶段",
            })

            # Trigger another sync
            _dashboard_get("/api/live-status")

            json_task = _json_task(task_id)
            assert json_task is not None
            assert json_task["now"] == "正在执行第二阶段", \
                f"Progress not synced, got: {json_task.get('now')}"
        finally:
            _cleanup_task(task_id)


# ── Test 3: Scheduler scan doesn't kill active tasks ──

class TestSchedulerNoFalsePositive:
    """Scheduler scan should not kill tasks that have recent EDICT progress."""

    def test_scan_spares_recently_updated_task(self):
        result = _dashboard_post("/api/create-task", {
            "title": "集成测试：scheduler不误杀验证",
            "org": "中书省",
        })
        task_id = result["taskId"]

        try:
            # Agent advances state
            _edict_post(f"/api/tasks/by-legacy/{task_id}/transition", {
                "new_state": "Zhongshu",
                "agent": "test",
                "reason": "test",
            })

            # Write recent progress
            _edict_post(f"/api/tasks/by-legacy/{task_id}/progress", {
                "agent": "test",
                "content": "actively working",
            })

            # Trigger scheduler scan (which syncs first)
            scan_result = _dashboard_post("/api/scheduler-scan", {})

            # Task should NOT appear in scan actions
            actions = scan_result.get("actions", [])
            task_actions = [a for a in actions if a.get("taskId") == task_id]
            assert len(task_actions) == 0, \
                f"Scheduler falsely acted on active task: {task_actions}"

            # Verify task is still alive (not cancelled)
            json_task = _json_task(task_id)
            assert json_task is not None
            assert json_task["state"] not in ("Cancelled", "Blocked"), \
                f"Task was killed: state={json_task['state']}"
        finally:
            _cleanup_task(task_id)


# ── Test 4: EDICT live-status endpoint ──

class TestEdictLiveStatus:
    """EDICT /api/tasks/live-status should work and return valid data."""

    def test_live_status_returns_200(self):
        data = _edict_get("/api/tasks/live-status")
        assert "tasks" in data
        assert "completed_tasks" in data

    def test_live_status_contains_created_task(self):
        result = _dashboard_post("/api/create-task", {
            "title": "集成测试：live-status包含验证",
            "org": "中书省",
        })
        task_id = result["taskId"]

        try:
            data = _edict_get("/api/tasks/live-status")
            all_tasks = {}
            all_tasks.update(data.get("tasks", {}))
            all_tasks.update(data.get("completed_tasks", {}))
            assert task_id in all_tasks, \
                f"{task_id} not found in EDICT live-status"
        finally:
            _cleanup_task(task_id)


# ── Test 5: Full pipeline ──

class TestFullPipeline:
    """End-to-end: Dashboard create → agent transition → dashboard sees update."""

    def test_create_transition_progress_done(self):
        # 1. Create
        result = _dashboard_post("/api/create-task", {
            "title": "集成测试：完整流水线验证",
            "org": "中书省",
        })
        task_id = result["taskId"]

        try:
            # 2. Agent transitions through states
            for transition in [
                ("Zhongshu", "太子转交"),
                ("Menxia", "方案已起草"),
            ]:
                _edict_post(f"/api/tasks/by-legacy/{task_id}/transition", {
                    "new_state": transition[0],
                    "agent": "test",
                    "reason": transition[1],
                })

            # 3. Sync
            _dashboard_get("/api/live-status")

            # 4. Verify dashboard sees latest state
            json_task = _json_task(task_id)
            assert json_task is not None
            assert json_task["state"] == "Menxia", \
                f"Expected Menxia, got {json_task['state']}"

            # 5. Complete
            _edict_post(f"/api/tasks/by-legacy/{task_id}/done", {
                "output": "test output",
                "summary": "integration test done",
                "agent": "test",
            })

            # 6. Sync again
            _dashboard_get("/api/live-status")

            # 7. Verify done
            edict_task = _edict_task(task_id)
            assert edict_task["state"] == "Done"
        finally:
            _cleanup_task(task_id)
