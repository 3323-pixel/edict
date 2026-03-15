"""Unit tests for handlers/activity.py"""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'dashboard'))

from handlers.activity import (
    _compute_phase_durations, _compute_todos_summary, _compute_todos_diff,
    _parse_iso,
)


# ── _compute_phase_durations ──

def test_phase_durations_basic():
    flow_log = [
        {'at': '2026-03-15T06:00:00Z', 'from': '皇上', 'to': '太子'},
        {'at': '2026-03-15T06:01:00Z', 'from': '太子', 'to': '中书省'},
        {'at': '2026-03-15T06:05:00Z', 'from': '中书省', 'to': '门下省'},
    ]
    durations = _compute_phase_durations(flow_log)
    assert len(durations) >= 2
    # First phase: 太子 1 min
    assert durations[0]['phase'] == '太子'
    assert durations[0]['durationSec'] == 60
    # Second phase: 中书省 4 min
    assert durations[1]['phase'] == '中书省'
    assert durations[1]['durationSec'] == 240


def test_phase_durations_empty():
    assert _compute_phase_durations([]) == []


def test_phase_durations_single():
    flow_log = [{'at': '2026-03-15T06:00:00Z', 'from': '皇上', 'to': '太子'}]
    durations = _compute_phase_durations(flow_log)
    assert len(durations) == 1
    assert durations[0]['ongoing'] is True


# ── _compute_todos_summary ──

def test_todos_summary():
    todos = [
        {'status': 'completed'},
        {'status': 'completed'},
        {'status': 'in-progress'},
        {'status': 'not-started'},
    ]
    summary = _compute_todos_summary(todos)
    assert summary['total'] == 4
    assert summary['completed'] == 2
    assert summary['inProgress'] == 1
    assert summary['percent'] == 50


def test_todos_summary_empty():
    summary = _compute_todos_summary([])
    # Empty list returns None (no summary to show)
    assert summary is None or summary.get('total', 0) == 0


# ── _compute_todos_diff ──

def test_todos_diff_added():
    prev = []
    curr = [{'id': '1', 'title': 'new', 'status': 'not-started'}]
    diff = _compute_todos_diff(prev, curr)
    assert len(diff['added']) == 1
    assert diff['added'][0]['id'] == '1'


def test_todos_diff_changed():
    prev = [{'id': '1', 'title': 'task', 'status': 'not-started'}]
    curr = [{'id': '1', 'title': 'task', 'status': 'completed'}]
    diff = _compute_todos_diff(prev, curr)
    assert len(diff['changed']) == 1
    assert diff['changed'][0]['from'] == 'not-started'
    assert diff['changed'][0]['to'] == 'completed'


def test_todos_diff_removed():
    prev = [{'id': '1', 'title': 'task', 'status': 'completed'}]
    curr = []
    diff = _compute_todos_diff(prev, curr)
    assert len(diff['removed']) == 1


def test_todos_diff_no_change():
    todos = [{'id': '1', 'title': 'task', 'status': 'in-progress'}]
    diff = _compute_todos_diff(todos, todos)
    # No change returns None or empty diff
    if diff is None:
        pass  # acceptable
    else:
        assert diff['added'] == []
        assert diff['changed'] == []
        assert diff['removed'] == []


# ── _parse_iso ──

def test_parse_iso_z():
    dt = _parse_iso('2026-03-15T06:00:00Z')
    assert dt is not None
    assert dt.hour == 6


def test_parse_iso_offset():
    dt = _parse_iso('2026-03-15T06:00:00+00:00')
    assert dt is not None


def test_parse_iso_none():
    assert _parse_iso(None) is None
    assert _parse_iso('') is None
    assert _parse_iso('not-a-date') is None
