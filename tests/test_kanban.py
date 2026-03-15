"""Tests for scripts/kanban_update.py against the HTTP client path."""

import pathlib
import sys


SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import kanban_update as kb


class FakeClient:
    calls = []
    task_payload = {"todos": []}

    def __init__(self):
        pass

    @classmethod
    def reset(cls):
        cls.calls = []
        cls.task_payload = {"todos": []}

    def create_task(self, **kwargs):
        self.calls.append(("create_task", kwargs))
        return {"ok": True}

    def transition(self, legacy_id, new_state, agent, reason=""):
        self.calls.append(
            (
                "transition",
                {
                    "legacy_id": legacy_id,
                    "new_state": new_state,
                    "agent": agent,
                    "reason": reason,
                },
            )
        )
        return {"ok": True}

    def add_flow(self, legacy_id, from_dept, to_dept, remark):
        self.calls.append(
            (
                "add_flow",
                {
                    "legacy_id": legacy_id,
                    "from_dept": from_dept,
                    "to_dept": to_dept,
                    "remark": remark,
                },
            )
        )
        return {"ok": True}

    def add_progress(self, legacy_id, agent, content, todos=None, tokens=0, cost=0.0, elapsed=0):
        self.calls.append(
            (
                "add_progress",
                {
                    "legacy_id": legacy_id,
                    "agent": agent,
                    "content": content,
                    "todos": todos,
                    "tokens": tokens,
                    "cost": cost,
                    "elapsed": elapsed,
                },
            )
        )
        return {"ok": True}

    def get_task(self, legacy_id):
        self.calls.append(("get_task", {"legacy_id": legacy_id}))
        return self.task_payload

    def update_todos(self, legacy_id, todos):
        self.calls.append(("update_todos", {"legacy_id": legacy_id, "todos": todos}))
        self.task_payload = {"todos": todos}
        return {"ok": True}

    def block(self, legacy_id, reason, agent):
        self.calls.append(("block", {"legacy_id": legacy_id, "reason": reason, "agent": agent}))
        return {"ok": True}

    def done(self, legacy_id, output, summary, agent):
        self.calls.append(
            (
                "done",
                {
                    "legacy_id": legacy_id,
                    "output": output,
                    "summary": summary,
                    "agent": agent,
                },
            )
        )
        return {"ok": True}

    def close(self):
        pass


def setup_function():
    FakeClient.reset()
    kb.EdictClient = FakeClient
    kb._infer_agent_id_from_runtime = lambda: "taizi"


def test_create_calls_http_client():
    kb.cmd_create("TEST-001", "测试任务创建和查询功能验证", "Taizi", "太子", "太子")

    # 第一个调用是 get_task（ID 冲突检测），之后是 create_task
    call_names = [c[0] for c in FakeClient.calls]
    assert "create_task" in call_names
    create_call = next(c for c in FakeClient.calls if c[0] == "create_task")
    assert create_call[1]["legacy_id"] == "TEST-001"
    assert create_call[1]["title"] == "测试任务创建和查询功能验证"
    assert create_call[1]["state"] == "Taizi"


def test_forward_updates_state_and_flow():
    kb.cmd_forward("TEST-002", "Zhongshu", "太子接旨，整理需求后转交中书省起草方案")

    assert [name for name, _ in FakeClient.calls] == ["transition", "add_flow"]
    assert FakeClient.calls[0][1]["new_state"] == "Zhongshu"
    assert FakeClient.calls[1][1]["to_dept"] == "中书省"


def test_block_delegates_to_http_client():
    kb.cmd_block("TEST-003", "等待依赖")

    assert FakeClient.calls[0][0] == "block"
    assert FakeClient.calls[0][1]["legacy_id"] == "TEST-003"
    assert FakeClient.calls[0][1]["reason"] == "等待依赖"
