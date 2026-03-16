"""Integration tests for dashboard endpoints that were previously uncovered.

Requires EDICT backend (8000) + Dashboard server (7891) running.
"""

import json
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


def _edict(method, path, data=None):
    return _request(method, f"{EDICT_URL}{path}", data)


def _dashboard(method, path, data=None):
    return _request(method, f"{DASHBOARD_URL}{path}", data)


def _create_task(title="集成测试任务用于端点覆盖验证"):
    result = _dashboard("POST", "/api/create-task", {"title": title, "org": "中书省"})
    assert result["ok"] is True
    return result["taskId"]


def _cleanup(task_id):
    try:
        _edict("POST", f"/api/tasks/by-legacy/{task_id}/done",
               {"output": "", "summary": "test cleanup", "agent": "test"})
    except Exception:
        pass
    # 清掉测试产生的 dispatch 事件，防止浪费 API 配额
    try:
        _edict("POST", "/api/admin/system/flush-pending?topic=task.dispatch&group=dispatcher", {})
    except Exception:
        pass


@pytest.fixture(autouse=True, scope="module")
def check_services():
    try:
        _edict("GET", "/api/tasks?limit=1")
    except Exception:
        pytest.skip("EDICT backend not running")
    try:
        _dashboard("GET", "/healthz")
    except Exception:
        pytest.skip("Dashboard not running")


# ── handle_task_action: stop/cancel/resume ──

class TestTaskAction:
    def test_stop_task(self):
        task_id = _create_task("测试叫停任务功能验证端点覆盖")
        try:
            _edict("POST", f"/api/tasks/by-legacy/{task_id}/transition",
                   {"new_state": "Zhongshu", "agent": "test", "reason": "setup"})

            result = _dashboard("POST", "/api/task-action",
                                {"taskId": task_id, "action": "stop", "reason": "测试叫停"})
            assert result["ok"] is True

            task = _edict("GET", f"/api/tasks/by-legacy/{task_id}")
            assert task["state"] == "Blocked"
        finally:
            _cleanup(task_id)

    def test_cancel_task(self):
        task_id = _create_task("测试取消任务功能验证端点覆盖")
        try:
            result = _dashboard("POST", "/api/task-action",
                                {"taskId": task_id, "action": "cancel", "reason": "测试取消"})
            assert result["ok"] is True

            task = _edict("GET", f"/api/tasks/by-legacy/{task_id}")
            assert task["state"] == "Cancelled"
        finally:
            _cleanup(task_id)

    def test_resume_task(self):
        task_id = _create_task("测试恢复任务功能验证端点覆盖")
        try:
            _edict("POST", f"/api/tasks/by-legacy/{task_id}/transition",
                   {"new_state": "Zhongshu", "agent": "test", "reason": "setup"})
            # Stop first
            _dashboard("POST", "/api/task-action",
                       {"taskId": task_id, "action": "stop", "reason": "先叫停"})
            # Then resume
            result = _dashboard("POST", "/api/task-action",
                                {"taskId": task_id, "action": "resume", "reason": "恢复"})
            assert result["ok"] is True

            task = _edict("GET", f"/api/tasks/by-legacy/{task_id}")
            assert task["state"] != "Blocked"
        finally:
            _cleanup(task_id)


# ── handle_review_action: approve/reject ──

class TestReviewAction:
    def test_approve_menxia(self):
        task_id = _create_task("测试门下省准奏功能验证端点覆盖")
        try:
            _edict("POST", f"/api/tasks/by-legacy/{task_id}/transition",
                   {"new_state": "Zhongshu", "agent": "test"})
            _edict("POST", f"/api/tasks/by-legacy/{task_id}/transition",
                   {"new_state": "Menxia", "agent": "test"})

            result = _dashboard("POST", "/api/review-action",
                                {"taskId": task_id, "action": "approve", "comment": "准奏"})
            assert result["ok"] is True

            task = _edict("GET", f"/api/tasks/by-legacy/{task_id}")
            assert task["state"] == "Assigned"
        finally:
            _cleanup(task_id)

    def test_reject_menxia(self):
        task_id = _create_task("测试门下省封驳功能验证端点覆盖")
        try:
            _edict("POST", f"/api/tasks/by-legacy/{task_id}/transition",
                   {"new_state": "Zhongshu", "agent": "test"})
            _edict("POST", f"/api/tasks/by-legacy/{task_id}/transition",
                   {"new_state": "Menxia", "agent": "test"})

            result = _dashboard("POST", "/api/review-action",
                                {"taskId": task_id, "action": "reject", "comment": "封驳退回"})
            assert result["ok"] is True

            task = _edict("GET", f"/api/tasks/by-legacy/{task_id}")
            assert task["state"] == "Zhongshu"
        finally:
            _cleanup(task_id)


# ── handle_advance_state ──

class TestAdvanceState:
    def test_advance_taizi_to_zhongshu(self):
        task_id = _create_task("测试手动推进功能验证端点覆盖")
        try:
            result = _dashboard("POST", "/api/advance-state",
                                {"taskId": task_id, "comment": "手动推进测试"})
            assert result["ok"] is True

            task = _edict("GET", f"/api/tasks/by-legacy/{task_id}")
            assert task["state"] == "Zhongshu"
        finally:
            _cleanup(task_id)


# ── handle_archive_task ──

class TestArchiveTask:
    def test_archive_single(self):
        task_id = _create_task("测试归档功能验证端点覆盖测试")
        try:
            _edict("POST", f"/api/tasks/by-legacy/{task_id}/done",
                   {"output": "", "summary": "done", "agent": "test"})

            result = _dashboard("POST", "/api/archive-task",
                                {"taskId": task_id, "archived": True})
            assert result["ok"] is True

            task = _edict("GET", f"/api/tasks/by-legacy/{task_id}")
            assert task.get("archived") is True
        finally:
            _cleanup(task_id)


# ── dispatch-task ──

class TestDispatchTask:
    def test_dispatch_returns_ok(self):
        task_id = _create_task("测试即时派发功能验证端点覆盖")
        try:
            result = _dashboard("POST", "/api/dispatch-task", {"taskId": task_id})
            assert result["ok"] is True
        finally:
            _cleanup(task_id)

    def test_dispatch_nonexistent_returns_error(self):
        try:
            _dashboard("POST", "/api/dispatch-task", {"taskId": "JJC-NONEXIST-999"})
            assert False, "Should have raised"
        except urllib.error.HTTPError as e:
            assert e.code == 404


# ── scheduler endpoints ──

class TestSchedulerEndpoints:
    def test_scheduler_scan_no_crash(self):
        result = _dashboard("POST", "/api/scheduler-scan", {"thresholdSec": 9999})
        assert result["ok"] is True
        assert "actions" in result

    def test_scheduler_state(self):
        task_id = _create_task("测试scheduler状态查询端点覆盖")
        try:
            result = _dashboard("GET", f"/api/scheduler-state/{task_id}")
            assert result["ok"] is True
            assert "stalledSec" in result
        finally:
            _cleanup(task_id)
