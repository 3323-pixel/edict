"""Regression tests for dashboard ↔ EDICT sync logic.

Covers:
- #1: compensation sync idempotent (先查后建)
- #2: datetime comparison with Z / +00:00 formats
- #3: compensation not short-circuited by empty live-status
- 高危: same-state progress should reset scheduler stall detection
- 中危: EDICT write failure marks _edict_synced=false
"""

import json
import pathlib
import sys
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'dashboard'))
sys.path.insert(0, str(ROOT / 'scripts'))


def _setup_server(tmp_path, tasks=None):
    """Import server module and patch DATA dir."""
    import server as srv
    data_dir = tmp_path / 'data'
    data_dir.mkdir(exist_ok=True)
    (data_dir / 'tasks_source.json').write_text(json.dumps(tasks or []))
    (data_dir / 'live_status.json').write_text('{}')
    (data_dir / 'officials_stats.json').write_text('{}')
    (data_dir / 'sync_status.json').write_text('{}')
    srv.DATA = data_dir
    return srv


# ── #2: datetime comparison ──

def test_parse_iso_z_format(tmp_path):
    srv = _setup_server(tmp_path)
    dt = srv._parse_iso('2026-03-15T06:00:00Z')
    assert dt is not None
    assert dt.tzinfo is not None


def test_parse_iso_offset_format(tmp_path):
    srv = _setup_server(tmp_path)
    dt = srv._parse_iso('2026-03-15T06:00:00+00:00')
    assert dt is not None
    assert dt.tzinfo is not None


def test_parse_iso_z_equals_offset(tmp_path):
    srv = _setup_server(tmp_path)
    dt_z = srv._parse_iso('2026-03-15T06:00:00Z')
    dt_off = srv._parse_iso('2026-03-15T06:00:00+00:00')
    assert dt_z == dt_off


def test_progress_changed_detects_newer_edict(tmp_path):
    """EDICT updatedAt is newer → progress_changed should be True."""
    srv = _setup_server(tmp_path)
    edict_updated = '2026-03-15T06:10:00+00:00'
    json_updated = '2026-03-15T06:00:00Z'
    edict_dt = srv._parse_iso(edict_updated)
    json_dt = srv._parse_iso(json_updated)
    progress_changed = edict_dt is not None and (json_dt is None or edict_dt > json_dt)
    assert progress_changed is True


def test_progress_changed_ignores_older_edict(tmp_path):
    """EDICT updatedAt is older → progress_changed should be False."""
    srv = _setup_server(tmp_path)
    edict_updated = '2026-03-15T05:50:00+00:00'
    json_updated = '2026-03-15T06:00:00Z'
    edict_dt = srv._parse_iso(edict_updated)
    json_dt = srv._parse_iso(json_updated)
    progress_changed = edict_dt is not None and (json_dt is None or edict_dt > json_dt)
    assert progress_changed is False


# ── Tests for _sync_edict_states_to_json removed ──
# The sync bridge was deleted in the "remove JSON fallback" refactor.
# Equivalent coverage: test_integration_sync.py::TestSchedulerNoFalsePositive


# ── 中危: EDICT write failure handling ──

def test_create_task_fails_when_edict_unavailable(tmp_path):
    """When EDICT POST fails, create should return error (EDICT is primary)."""
    srv = _setup_server(tmp_path, [])

    with patch.object(srv, '_edict_request', return_value=None), \
         patch.object(srv, 'dispatch_for_state'):
        result = srv.handle_create_task('测试EDICT写入失败返回错误')

    assert result['ok'] is False
    assert 'EDICT' in result.get('error', '')


def test_create_task_succeeds_with_edict(tmp_path):
    """When EDICT POST succeeds, task is created."""
    srv = _setup_server(tmp_path, [])

    with patch.object(srv, '_edict_request', return_value={'id': 'JJC-TEST'}), \
         patch.object(srv, 'dispatch_for_state'):
        result = srv.handle_create_task('测试EDICT写入成功创建任务')

    assert result['ok'] is True
    assert result.get('taskId', '').startswith('JJC-')


# ── Tests for compensation sync removed ──
# The _sync_edict_states_to_json and _edict_synced mechanism was removed.
# EDICT is now the sole data source; compensation is no longer needed.
