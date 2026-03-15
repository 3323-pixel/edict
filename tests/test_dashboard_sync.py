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


# ── 高危: same-state progress resets scheduler ──

def test_same_state_progress_resets_stall(tmp_path):
    """Task in Doing with updated progress should reset stallSince."""
    now = datetime.now(timezone.utc)
    old_time = (now - timedelta(minutes=10)).isoformat()
    new_time = now.isoformat()

    tasks = [{
        'id': 'JJC-TEST-001',
        'title': 'test task',
        'state': 'Doing',
        'org': '工部',
        'now': 'old progress',
        'updatedAt': old_time,
        '_scheduler': {
            'lastProgressAt': old_time,
            'stallSince': old_time,
            'retryCount': 1,
            'escalationLevel': 1,
        },
    }]

    srv = _setup_server(tmp_path, tasks)

    # EDICT returns same state but newer updatedAt
    edict_live = {
        'tasks': {
            'JJC-TEST-001': {
                'id': 'JJC-TEST-001',
                'state': 'Doing',
                'org': '工部',
                'now': 'new progress from agent',
                'updatedAt': new_time,
                'progress_log': [{'content': 'working', 'ts': new_time}],
            }
        },
        'completed_tasks': {},
    }

    with patch.object(srv, '_edict_request', return_value=edict_live):
        result = srv._sync_edict_states_to_json()

    assert result is True
    synced = json.loads((tmp_path / 'data' / 'tasks_source.json').read_text())
    task = synced[0]
    assert task['now'] == 'new progress from agent'
    assert task['_scheduler']['stallSince'] is None
    # retryCount should NOT reset for same-state progress (only for state change)
    assert task['_scheduler']['retryCount'] == 1


def test_state_change_resets_retry_count(tmp_path):
    """State change should reset retryCount and escalationLevel."""
    now = datetime.now(timezone.utc)
    old_time = (now - timedelta(minutes=10)).isoformat()
    new_time = now.isoformat()

    tasks = [{
        'id': 'JJC-TEST-002',
        'title': 'test task 2',
        'state': 'Taizi',
        'org': '太子',
        'updatedAt': old_time,
        '_scheduler': {
            'lastProgressAt': old_time,
            'stallSince': old_time,
            'retryCount': 2,
            'escalationLevel': 1,
        },
    }]

    srv = _setup_server(tmp_path, tasks)

    edict_live = {
        'tasks': {
            'JJC-TEST-002': {
                'id': 'JJC-TEST-002',
                'state': 'Zhongshu',
                'org': '中书省',
                'now': 'planning',
                'updatedAt': new_time,
            }
        },
        'completed_tasks': {},
    }

    with patch.object(srv, '_edict_request', return_value=edict_live):
        srv._sync_edict_states_to_json()

    synced = json.loads((tmp_path / 'data' / 'tasks_source.json').read_text())
    task = synced[0]
    assert task['state'] == 'Zhongshu'
    assert task['_scheduler']['retryCount'] == 0
    assert task['_scheduler']['escalationLevel'] == 0
    assert task['_scheduler']['stallSince'] is None


# ── 中危: EDICT write failure handling ──

def test_create_task_marks_edict_synced_false_on_failure(tmp_path):
    """When EDICT POST fails, task should be marked _edict_synced=false."""
    srv = _setup_server(tmp_path, [])

    with patch.object(srv, '_edict_request', return_value=None), \
         patch.object(srv, 'dispatch_for_state'):
        result = srv.handle_create_task('测试EDICT写入失败的任务标记')

    assert result['ok'] is True
    assert '⚠️' in result['message']

    tasks = json.loads((tmp_path / 'data' / 'tasks_source.json').read_text())
    task = next(t for t in tasks if t['title'] == '测试EDICT写入失败的任务标记')
    assert task['_edict_synced'] is False


def test_create_task_no_flag_on_success(tmp_path):
    """When EDICT POST succeeds, no _edict_synced flag."""
    srv = _setup_server(tmp_path, [])

    with patch.object(srv, '_edict_request', return_value={'id': 'JJC-TEST'}), \
         patch.object(srv, 'dispatch_for_state'):
        result = srv.handle_create_task('测试EDICT写入成功无标记')

    assert result['ok'] is True
    assert '⚠️' not in result['message']

    tasks = json.loads((tmp_path / 'data' / 'tasks_source.json').read_text())
    task = next(t for t in tasks if t['title'] == '测试EDICT写入成功无标记')
    assert '_edict_synced' not in task


# ── #1: compensation sync idempotent ──

def test_compensation_clears_flag_when_task_exists(tmp_path):
    """If task already exists in EDICT, clear _edict_synced without POST."""
    tasks = [{
        'id': 'JJC-TEST-003',
        'title': 'already synced',
        'state': 'Taizi',
        'org': '太子',
        '_edict_synced': False,
    }]

    srv = _setup_server(tmp_path, tasks)

    call_log = []

    def mock_edict(method, path, data=None):
        call_log.append((method, path))
        if method == 'GET' and 'by-legacy' in path:
            return {'id': 'JJC-TEST-003', 'state': 'Taizi'}  # exists
        if method == 'GET' and 'live-status' in path:
            return {'tasks': {}, 'completed_tasks': {}}
        return None

    with patch.object(srv, '_edict_request', side_effect=mock_edict):
        srv._sync_edict_states_to_json()

    synced = json.loads((tmp_path / 'data' / 'tasks_source.json').read_text())
    assert '_edict_synced' not in synced[0]
    # Should NOT have called POST (no duplicate create)
    post_calls = [c for c in call_log if c[0] == 'POST']
    assert len(post_calls) == 0


# ── #3: compensation not blocked by empty live-status ──

def test_compensation_runs_when_livestatus_empty(tmp_path):
    """Compensation should run even when EDICT live-status returns empty."""
    tasks = [{
        'id': 'JJC-TEST-004',
        'title': 'needs compensation',
        'state': 'Taizi',
        'org': '太子',
        '_edict_synced': False,
    }]

    srv = _setup_server(tmp_path, tasks)

    def mock_edict(method, path, data=None):
        if method == 'GET' and 'by-legacy' in path:
            return None  # task doesn't exist in EDICT
        if method == 'GET' and 'live-status' in path:
            return {'tasks': {}, 'completed_tasks': {}}  # empty
        if method == 'POST' and 'legacy' in path:
            return {'id': 'JJC-TEST-004', 'state': 'Taizi'}  # create success
        return None

    with patch.object(srv, '_edict_request', side_effect=mock_edict):
        result = srv._sync_edict_states_to_json()

    assert result is True
    synced = json.loads((tmp_path / 'data' / 'tasks_source.json').read_text())
    assert '_edict_synced' not in synced[0]


def test_compensation_stays_marked_when_all_fail(tmp_path):
    """If both GET and POST fail, _edict_synced stays False."""
    tasks = [{
        'id': 'JJC-TEST-005',
        'title': 'all fails',
        'state': 'Taizi',
        'org': '太子',
        '_edict_synced': False,
    }]

    srv = _setup_server(tmp_path, tasks)

    def mock_edict(method, path, data=None):
        if 'live-status' in path:
            return {'tasks': {}, 'completed_tasks': {}}
        return None  # everything fails

    with patch.object(srv, '_edict_request', side_effect=mock_edict):
        srv._sync_edict_states_to_json()

    synced = json.loads((tmp_path / 'data' / 'tasks_source.json').read_text())
    assert synced[0].get('_edict_synced') is False
