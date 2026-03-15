"""Unit tests for handlers/edict_proxy.py"""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'dashboard'))

from handlers.edict_proxy import index_edict_tasks


# ── index_edict_tasks ──

def test_index_dict_format():
    """Standard live-status response with dict tasks."""
    payload = {
        'tasks': {
            'JJC-001': {'id': 'JJC-001', 'state': 'Doing'},
            'JJC-002': {'id': 'JJC-002', 'state': 'Zhongshu'},
        },
        'completed_tasks': {
            'JJC-003': {'id': 'JJC-003', 'state': 'Done'},
        },
    }
    idx = index_edict_tasks(payload)
    assert len(idx) == 3
    assert idx['JJC-001']['state'] == 'Doing'
    assert idx['JJC-003']['state'] == 'Done'


def test_index_list_format():
    """Some endpoints return tasks as list."""
    payload = {
        'tasks': [
            {'id': 'JJC-001', 'state': 'Doing'},
            {'id': 'JJC-002', 'state': 'Zhongshu'},
        ],
        'completed_tasks': {},
    }
    idx = index_edict_tasks(payload)
    assert len(idx) == 2
    assert 'JJC-001' in idx


def test_index_mixed_format():
    """Dict tasks + list completed."""
    payload = {
        'tasks': {'JJC-001': {'id': 'JJC-001', 'state': 'Doing'}},
        'completed_tasks': [{'id': 'JJC-002', 'state': 'Done'}],
    }
    idx = index_edict_tasks(payload)
    assert len(idx) == 2


def test_index_empty():
    assert index_edict_tasks({}) == {}
    assert index_edict_tasks({'tasks': {}, 'completed_tasks': {}}) == {}


def test_index_none():
    assert index_edict_tasks(None) == {}


def test_index_non_dict():
    assert index_edict_tasks("not a dict") == {}
    assert index_edict_tasks([]) == {}


def test_index_uses_task_id_over_key():
    """Task's 'id' field takes precedence over dict key."""
    payload = {
        'tasks': {
            'wrong-key': {'id': 'JJC-REAL', 'state': 'Doing'},
        },
        'completed_tasks': {},
    }
    idx = index_edict_tasks(payload)
    assert 'JJC-REAL' in idx
    assert 'wrong-key' not in idx


def test_index_skips_non_dict_items():
    payload = {
        'tasks': {'JJC-001': 'not a dict', 'JJC-002': {'id': 'JJC-002', 'state': 'Doing'}},
        'completed_tasks': {},
    }
    idx = index_edict_tasks(payload)
    assert len(idx) == 1
    assert 'JJC-002' in idx
