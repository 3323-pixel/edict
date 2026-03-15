"""EDICT Backend API 代理层 — 所有模块共用。"""

import json
import logging
import os
import urllib.request
import urllib.error

log = logging.getLogger('server')

EDICT_API_URL = os.environ.get('EDICT_API_URL', 'http://localhost:8000').rstrip('/')
EDICT_API_TIMEOUT = float(os.environ.get('EDICT_API_TIMEOUT', '5'))


def edict_request(method, path, data=None, timeout=None):
    """同步调用 EDICT Backend API，失败返回 None。"""
    url = f'{EDICT_API_URL}{path}'
    body = json.dumps(data, ensure_ascii=False).encode() if data else None
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout or EDICT_API_TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        log.warning(f'[EDICT] {method} {path} failed: {e}')
        return None


def edict_get_task(task_id):
    """从 EDICT 获取任务，返回 dict 或 None。"""
    return edict_request('GET', f'/api/tasks/by-legacy/{task_id}')


def edict_update_scheduler(task_id, sched):
    """更新 EDICT 中任务的 scheduler 元数据。"""
    return edict_request('PUT', f'/api/tasks/by-legacy/{task_id}/scheduler', {'scheduler': sched})


def edict_transition(task_id, new_state, agent='system', reason=''):
    """EDICT 状态流转。"""
    return edict_request('POST', f'/api/tasks/by-legacy/{task_id}/transition', {
        'new_state': new_state, 'agent': agent, 'reason': reason,
    })


def edict_add_flow(task_id, from_dept, to_dept, remark=''):
    """EDICT 添加流转记录。"""
    return edict_request('POST', f'/api/tasks/by-legacy/{task_id}/flow', {
        'from_dept': from_dept, 'to_dept': to_dept, 'remark': remark,
    })


def edict_archive(task_id, archived=True):
    """EDICT 归档/取消归档。"""
    return edict_request('PUT', f'/api/tasks/by-legacy/{task_id}/archive', {'archived': archived})


def edict_get_active_tasks():
    """获取所有活跃任务（非终态、非归档）。"""
    resp = edict_request('GET', '/api/tasks/active')
    if resp and isinstance(resp, dict):
        return resp.get('tasks', [])
    return None


def index_edict_tasks(payload):
    """将 EDICT live-status 响应归一化为 task_id -> task dict。"""
    if not isinstance(payload, dict):
        return {}
    indexed = {}
    for bucket in ('tasks', 'completed_tasks'):
        data = payload.get(bucket, {})
        if isinstance(data, dict):
            for task_id, task in data.items():
                if isinstance(task, dict):
                    indexed[str(task.get('id') or task_id)] = task
        elif isinstance(data, list):
            for task in data:
                if isinstance(task, dict) and task.get('id'):
                    indexed[str(task['id'])] = task
    return indexed
