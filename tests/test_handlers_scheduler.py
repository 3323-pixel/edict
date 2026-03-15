"""Unit tests for handlers/scheduler.py"""

import datetime
from unittest.mock import patch, MagicMock
import pytest
import sys, pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'dashboard'))

from handlers.scheduler import (
    ensure_scheduler, scheduler_add_flow, scheduler_snapshot,
    scheduler_mark_progress, get_scheduler_state,
    handle_scheduler_retry, handle_scheduler_escalate,
    handle_scheduler_rollback, handle_scheduler_scan,
)


# ── ensure_scheduler ──

def test_ensure_scheduler_fills_defaults():
    task = {'state': 'Doing', 'org': '工部'}
    sched = ensure_scheduler(task)
    assert sched['enabled'] is True
    assert sched['stallThresholdSec'] == 180
    assert sched['maxRetry'] == 1
    assert sched['retryCount'] == 0
    assert sched['escalationLevel'] == 0
    assert sched['autoRollback'] is True
    assert sched['stallSince'] is None
    assert sched['lastDispatchStatus'] == 'idle'
    assert sched['snapshot']['state'] == 'Doing'


def test_ensure_scheduler_preserves_existing():
    task = {'state': 'Doing', '_scheduler': {'retryCount': 3, 'maxRetry': 5}}
    sched = ensure_scheduler(task)
    assert sched['retryCount'] == 3
    assert sched['maxRetry'] == 5
    assert sched['escalationLevel'] == 0  # filled default


def test_ensure_scheduler_fixes_non_dict():
    task = {'state': 'Doing', '_scheduler': 'broken'}
    sched = ensure_scheduler(task)
    assert isinstance(sched, dict)
    assert sched['retryCount'] == 0


# ── scheduler_add_flow ──

def test_scheduler_add_flow():
    task = {'org': '中书省'}
    scheduler_add_flow(task, '停滞重试')
    assert len(task['flow_log']) == 1
    assert task['flow_log'][0]['from'] == '太子调度'
    assert '🧭' in task['flow_log'][0]['remark']


# ── scheduler_snapshot ──

def test_scheduler_snapshot():
    task = {'state': 'Zhongshu', 'org': '中书省', 'now': 'working'}
    ensure_scheduler(task)
    scheduler_snapshot(task, 'test-snap')
    snap = task['_scheduler']['snapshot']
    assert snap['state'] == 'Zhongshu'
    assert snap['note'] == 'test-snap'


# ── scheduler_mark_progress ──

def test_scheduler_mark_progress_resets_stall():
    task = {'state': 'Doing', 'org': '工部', '_scheduler': {
        'lastProgressAt': '2026-01-01T00:00:00Z',
        'stallSince': '2026-01-01T00:00:00Z',
        'retryCount': 2,
        'escalationLevel': 1,
    }}
    ensure_scheduler(task)
    scheduler_mark_progress(task, 'test progress')
    sched = task['_scheduler']
    assert sched['stallSince'] is None
    assert sched['retryCount'] == 0
    assert sched['escalationLevel'] == 0


# ── get_scheduler_state ──

@patch('handlers.scheduler.edict_get_task')
def test_get_scheduler_state_returns_stalled_sec(mock_get):
    old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)).isoformat()
    mock_get.return_value = {
        'state': 'Doing', 'org': '工部', 'updatedAt': old,
        '_scheduler': {'lastProgressAt': old},
    }
    result = get_scheduler_state('JJC-TEST')
    assert result['ok'] is True
    assert result['stalledSec'] >= 290  # ~5 min


@patch('handlers.scheduler.edict_get_task', return_value=None)
def test_get_scheduler_state_not_found(mock_get):
    result = get_scheduler_state('JJC-NONEXIST')
    assert result['ok'] is False


# ── handle_scheduler_scan ──

@patch('handlers.scheduler.edict_get_active_tasks')
@patch('handlers.scheduler.edict_update_scheduler')
def test_scan_detects_stalled_task(mock_update, mock_active):
    old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=10)).isoformat()
    mock_active.return_value = [{
        'id': 'JJC-TEST-STALL',
        'state': 'Doing',
        'org': '工部',
        'updatedAt': old,
        '_scheduler': {
            'lastProgressAt': old,
            'retryCount': 0,
            'maxRetry': 1,
            'escalationLevel': 0,
            'stallThresholdSec': 60,
            'autoRollback': True,
        },
    }]
    dispatch = MagicMock()
    result = handle_scheduler_scan(threshold_sec=60, dispatch_fn=dispatch)
    assert result['ok'] is True
    assert len(result['actions']) > 0
    assert result['actions'][0]['taskId'] == 'JJC-TEST-STALL'
    assert result['actions'][0]['action'] == 'retry'


@patch('handlers.scheduler.edict_get_active_tasks')
def test_scan_skips_fresh_tasks(mock_active):
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    mock_active.return_value = [{
        'id': 'JJC-TEST-FRESH',
        'state': 'Doing',
        'org': '工部',
        'updatedAt': now,
        '_scheduler': {'lastProgressAt': now},
    }]
    result = handle_scheduler_scan(threshold_sec=180)
    assert result['actions'] == []


@patch('handlers.scheduler.edict_get_active_tasks')
@patch('handlers.scheduler.edict_update_scheduler')
@patch('handlers.scheduler.edict_add_flow')
def test_scan_escalates_after_max_retry(mock_flow, mock_update, mock_active):
    old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=10)).isoformat()
    mock_active.return_value = [{
        'id': 'JJC-TEST-ESC',
        'state': 'Doing',
        'org': '工部',
        'updatedAt': old,
        '_scheduler': {
            'lastProgressAt': old,
            'retryCount': 1,
            'maxRetry': 1,
            'escalationLevel': 0,
            'stallThresholdSec': 60,
        },
    }]
    wake = MagicMock()
    result = handle_scheduler_scan(threshold_sec=60, wake_fn=wake)
    assert any(a['action'] == 'escalate' for a in result['actions'])


# ── handle_scheduler_retry ──

@patch('handlers.scheduler.edict_get_task')
@patch('handlers.scheduler.edict_update_scheduler')
@patch('handlers.scheduler.edict_add_flow')
def test_retry_increments_count(mock_flow, mock_update, mock_get):
    mock_get.return_value = {
        'state': 'Doing', 'org': '工部',
        '_scheduler': {'retryCount': 0},
    }
    dispatch = MagicMock()
    result = handle_scheduler_retry('JJC-TEST', 'test', dispatch_fn=dispatch)
    assert result['ok'] is True
    assert result['retryCount'] == 1
    dispatch.assert_called_once()


@patch('handlers.scheduler.edict_get_task')
def test_retry_blocked_task_fails(mock_get):
    mock_get.return_value = {'state': 'Blocked', 'org': ''}
    result = handle_scheduler_retry('JJC-TEST', 'test')
    assert result['ok'] is False


# ── handle_scheduler_rollback ──

@patch('handlers.scheduler.edict_get_task')
@patch('handlers.scheduler.edict_transition')
@patch('handlers.scheduler.edict_update_scheduler')
@patch('handlers.scheduler.edict_add_flow')
def test_rollback_restores_snapshot(mock_flow, mock_update, mock_trans, mock_get):
    mock_get.return_value = {
        'state': 'Doing', 'org': '工部',
        '_scheduler': {
            'snapshot': {'state': 'Zhongshu', 'org': '中书省'},
            'retryCount': 3,
        },
    }
    dispatch = MagicMock()
    result = handle_scheduler_rollback('JJC-TEST', 'test', dispatch_fn=dispatch)
    assert result['ok'] is True
    assert '回滚' in result['message']
    mock_trans.assert_called_once()


@patch('handlers.scheduler.edict_get_task')
def test_rollback_no_snapshot_fails(mock_get):
    mock_get.return_value = {
        'state': 'Doing', 'org': '工部',
        '_scheduler': {},
    }
    result = handle_scheduler_rollback('JJC-TEST', 'test')
    assert result['ok'] is False
