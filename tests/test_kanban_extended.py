"""Extended tests for kanban_update.py — ID conflict detection + output archival."""

import pathlib
import sys
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))


class FakeClient:
    calls = []
    _tasks = {}  # simulate existing tasks

    def __init__(self):
        FakeClient.calls = []

    def get_task(self, task_id):
        if task_id in FakeClient._tasks:
            return FakeClient._tasks[task_id]
        raise Exception("404")

    def create_task(self, **kwargs):
        FakeClient.calls.append(("create_task", kwargs))
        return kwargs

    def done(self, task_id, output, summary, agent):
        FakeClient.calls.append(("done", {"task_id": task_id, "output": output, "summary": summary}))

    def transition(self, **kwargs):
        FakeClient.calls.append(("transition", kwargs))

    def add_flow(self, *args):
        FakeClient.calls.append(("add_flow", args))

    def close(self):
        pass


import kanban_update as kb


@pytest.fixture(autouse=True)
def patch_client(monkeypatch):
    monkeypatch.setattr(kb, 'EdictClient', FakeClient)
    monkeypatch.setattr(kb, '_infer_agent_id_from_runtime', lambda: "test")
    monkeypatch.setattr(kb, '_notify_dashboard_sync', lambda task_id=None: None)
    FakeClient.calls = []
    FakeClient._tasks = {}


# ── ID Conflict Detection ──

def test_create_auto_increments_on_conflict():
    """When task ID exists in EDICT, cmd_create should auto-increment."""
    FakeClient._tasks = {
        "JJC-20260315-001": {"id": "JJC-20260315-001", "state": "Done"},
        "JJC-20260315-002": {"id": "JJC-20260315-002", "state": "Done"},
    }

    kb.cmd_create("JJC-20260315-001", "测试ID冲突自动递增功能验证", "Zhongshu", "中书省", "中书令")

    create_call = next((c for c in FakeClient.calls if c[0] == "create_task"), None)
    assert create_call is not None
    # Should have incremented past 001 and 002
    assert create_call[1]["legacy_id"] == "JJC-20260315-003"

    FakeClient._tasks = {}


def test_create_no_increment_when_no_conflict():
    """When task ID doesn't exist, use it directly."""
    FakeClient._tasks = {}
    kb.cmd_create("JJC-20260315-099", "测试无冲突直接创建功能验证", "Zhongshu", "中书省", "中书令")

    create_call = next((c for c in FakeClient.calls if c[0] == "create_task"), None)
    assert create_call is not None
    assert create_call[1]["legacy_id"] == "JJC-20260315-099"


# ── Output Archival (P4) ──

def test_done_reads_file_content(tmp_path):
    """cmd_done should read file content into output field."""

    # Create a test output file
    output_file = tmp_path / "test_report.md"
    output_file.write_text("# Test Report\n\nContent here.")

    # Disable feishu doc export
    import os
    os.environ["EDICT_DISABLE_FEISHU_DOC_EXPORT"] = "1"

    kb.cmd_done("JJC-TEST-DONE", str(output_file), "test done")

    done_call = next((c for c in FakeClient.calls if c[0] == "done"), None)
    assert done_call is not None
    assert done_call[1]["output"] == "# Test Report\n\nContent here."

    del os.environ["EDICT_DISABLE_FEISHU_DOC_EXPORT"]


def test_done_falls_back_to_path_for_large_files(tmp_path):
    """cmd_done should keep path for files > 50KB."""

    large_file = tmp_path / "big_report.md"
    large_file.write_text("x" * 60000)  # 60KB

    import os
    os.environ["EDICT_DISABLE_FEISHU_DOC_EXPORT"] = "1"

    kb.cmd_done("JJC-TEST-BIG", str(large_file), "big file")

    done_call = next((c for c in FakeClient.calls if c[0] == "done"), None)
    assert done_call is not None
    # Should store path, not content
    assert done_call[1]["output"] == str(large_file)

    del os.environ["EDICT_DISABLE_FEISHU_DOC_EXPORT"]


def test_done_handles_missing_file():
    """cmd_done with nonexistent file should pass path through."""

    import os
    os.environ["EDICT_DISABLE_FEISHU_DOC_EXPORT"] = "1"

    kb.cmd_done("JJC-TEST-MISSING", "/nonexistent/report.md", "missing file")

    done_call = next((c for c in FakeClient.calls if c[0] == "done"), None)
    assert done_call is not None
    assert done_call[1]["output"] == "/nonexistent/report.md"

    del os.environ["EDICT_DISABLE_FEISHU_DOC_EXPORT"]
