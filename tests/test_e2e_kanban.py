#!/usr/bin/env python3
"""End-to-end style tests for scripts/kanban_update.py."""

import os
import pathlib
import sys

import pytest


SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent.parent / "scripts"
os.chdir(SCRIPTS_DIR)
sys.path.insert(0, str(SCRIPTS_DIR))

from kanban_update import (  # noqa: E402
    _is_valid_task_title,
    _sanitize_remark,
    _sanitize_title,
    cmd_create,
    cmd_flow,
    cmd_forward,
    cmd_progress,
    cmd_todo,
)
import kanban_update as kb  # noqa: E402


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

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _patch_client():
    FakeClient.reset()
    kb.EdictClient = FakeClient
    kb._infer_agent_id_from_runtime = lambda: "taizi"
    yield


def test_dirty_title_cleaned():
    cmd_create(
        "JJC-TEST-E2E-01",
        "全面审查/Users/bingsen/clawd/openclaw-sansheng-liubu/这个项目\nConversation info (xxx)",
        "Zhongshu",
        "中书省",
        "中书令",
        "下旨（自动预建）：全面审查/Users/bingsen/clawd/项目",
    )
    payload = FakeClient.calls[0][1]
    assert "/Users" not in payload["title"]
    assert "Conversation" not in payload["title"]
    assert "自动预建" not in payload["remark"]


def test_pure_path_rejected():
    cmd_create("JJC-TEST-E2E-02", "/Users/bingsen/clawd/openclaw-sansheng-liubu/", "Zhongshu", "中书省", "中书令")
    assert FakeClient.calls == []


def test_normal_title():
    cmd_create("JJC-TEST-E2E-03", "调研工业数据分析大模型应用方案", "Zhongshu", "中书省", "中书令", "太子整理旨意")
    payload = FakeClient.calls[0][1]
    assert payload["title"] == "调研工业数据分析大模型应用方案"


def test_flow_remark_cleaned():
    cmd_flow("JJC-TEST-E2E-04", "太子", "中书省", "旨意传达：审查/Users/bingsen/clawd/xxx项目 Conversation blah")
    assert [name for name, _ in FakeClient.calls] == ["transition", "add_flow"]
    payload = FakeClient.calls[1][1]
    assert payload["from_dept"] == "太子"
    assert "/Users" not in payload["remark"]
    assert "Conversation" not in payload["remark"]


def test_short_title_rejected():
    cmd_create("JJC-TEST-E2E-05", "好的", "Zhongshu", "中书省", "中书令")
    assert FakeClient.calls == []


def test_prefix_stripped():
    cmd_create("JJC-TEST-E2E-06", "传旨：帮我写技术博客文章关于智能体架构", "Zhongshu", "中书省", "中书令")
    payload = FakeClient.calls[0][1]
    assert not payload["title"].startswith("传旨")


def test_forward_calls_transition_and_flow():
    cmd_forward("JJC-TEST-E2E-07", "Menxia", "方案提交门下省审议")
    assert [name for name, _ in FakeClient.calls] == ["transition", "add_flow"]
    assert FakeClient.calls[0][1]["new_state"] == "Menxia"
    assert FakeClient.calls[1][1]["to_dept"] == "门下省"


def test_progress_and_todo_update():
    cmd_progress("JJC-TEST-E2E-08", "正在分析需求", "1.调研|2.方案", 256, 1.25, 12)
    FakeClient.task_payload = {"todos": [{"id": "1", "title": "已有", "status": "not-started"}]}
    cmd_todo("JJC-TEST-E2E-08", "2", "方案", "in-progress", "开始写")

    progress_payload = FakeClient.calls[0][1]
    todo_payload = FakeClient.calls[-1][1]
    assert progress_payload["tokens"] == 256
    assert len(progress_payload["todos"]) == 2
    assert any(td["id"] == "2" and td["status"] == "in-progress" for td in todo_payload["todos"])


def test_sanitizers_and_validator():
    assert "/Users" not in _sanitize_title("传旨：检查 /Users/me/project")
    assert "Conversation" not in _sanitize_remark("Conversation hello")
    valid, _ = _is_valid_task_title("调研智能体记忆系统")
    assert valid is True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
