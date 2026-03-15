"""Scheduler 逻辑 — 停滞检测、重试、升级、回滚。

所有函数从 server.py 提取，通过 server 模块的全局变量访问
now_iso、_TERMINAL_STATES、dispatch_for_state、wake_agent。
"""

import datetime
import logging

from .edict_proxy import (
    edict_get_task, edict_update_scheduler, edict_transition,
    edict_add_flow, edict_get_active_tasks, edict_request,
)

log = logging.getLogger('server')


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z')


def _parse_iso(ts):
    if not ts or not isinstance(ts, str):
        return None
    try:
        return datetime.datetime.fromisoformat(ts.replace('Z', '+00:00'))
    except Exception:
        return None


_TERMINAL_STATES = {'Done', 'Cancelled'}


def ensure_scheduler(task):
    sched = task.setdefault('_scheduler', {})
    if not isinstance(sched, dict):
        sched = {}
        task['_scheduler'] = sched
    sched.setdefault('enabled', True)
    sched.setdefault('stallThresholdSec', 180)
    sched.setdefault('maxRetry', 1)
    sched.setdefault('retryCount', 0)
    sched.setdefault('escalationLevel', 0)
    sched.setdefault('autoRollback', True)
    if not sched.get('lastProgressAt'):
        sched['lastProgressAt'] = task.get('updatedAt') or _now_iso()
    if 'stallSince' not in sched:
        sched['stallSince'] = None
    if 'lastDispatchStatus' not in sched:
        sched['lastDispatchStatus'] = 'idle'
    if 'snapshot' not in sched:
        sched['snapshot'] = {
            'state': task.get('state', ''),
            'org': task.get('org', ''),
            'now': task.get('now', ''),
            'savedAt': _now_iso(),
            'note': 'init',
        }
    return sched


def scheduler_add_flow(task, remark, to=''):
    task.setdefault('flow_log', []).append({
        'at': _now_iso(),
        'from': '太子调度',
        'to': to or task.get('org', ''),
        'remark': f'🧭 {remark}'
    })


def scheduler_snapshot(task, note=''):
    sched = ensure_scheduler(task)
    sched['snapshot'] = {
        'state': task.get('state', ''),
        'org': task.get('org', ''),
        'now': task.get('now', ''),
        'savedAt': _now_iso(),
        'note': note or 'snapshot',
    }


def scheduler_mark_progress(task, note=''):
    sched = ensure_scheduler(task)
    sched['lastProgressAt'] = _now_iso()
    sched['stallSince'] = None
    sched['retryCount'] = 0
    sched['escalationLevel'] = 0
    sched['lastEscalatedAt'] = None
    if note:
        scheduler_add_flow(task, f'进展确认：{note}')


def update_task_scheduler(task_id, updater):
    task = edict_get_task(task_id)
    if not task:
        return False
    sched = task.get('_scheduler') or task.get('scheduler') or {}
    ensure_scheduler(task)
    sched = task.get('_scheduler', sched)
    updater(task, sched)
    edict_update_scheduler(task_id, sched)
    return True


def get_scheduler_state(task_id):
    task = edict_get_task(task_id)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    sched = task.get('_scheduler') or task.get('scheduler') or {}
    last_progress = _parse_iso(sched.get('lastProgressAt') or task.get('updatedAt'))
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    stalled_sec = 0
    if last_progress:
        stalled_sec = max(0, int((now_dt - last_progress).total_seconds()))
    return {
        'ok': True,
        'taskId': task_id,
        'state': task.get('state', ''),
        'org': task.get('org', ''),
        'scheduler': sched,
        'stalledSec': stalled_sec,
        'checkedAt': _now_iso(),
    }


def handle_scheduler_retry(task_id, reason='', dispatch_fn=None):
    task = edict_get_task(task_id)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    state = task.get('state', '')
    if state in _TERMINAL_STATES or state == 'Blocked':
        return {'ok': False, 'error': f'任务 {task_id} 当前状态 {state} 不支持重试'}

    sched = task.get('_scheduler') or task.get('scheduler') or {}
    sched['retryCount'] = int(sched.get('retryCount') or 0) + 1
    sched['lastRetryAt'] = _now_iso()
    sched['lastDispatchTrigger'] = 'taizi-retry'
    edict_add_flow(task_id, '太子调度', task.get('org', ''), f'触发重试第{sched["retryCount"]}次：{reason or "超时未推进"}')
    edict_update_scheduler(task_id, sched)

    if dispatch_fn:
        dispatch_fn(task_id, task, state, trigger='taizi-retry')
    return {'ok': True, 'message': f'{task_id} 已触发重试派发', 'retryCount': sched['retryCount']}


def handle_scheduler_escalate(task_id, reason='', wake_fn=None):
    task = edict_get_task(task_id)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    state = task.get('state', '')
    if state in _TERMINAL_STATES:
        return {'ok': False, 'error': f'任务 {task_id} 已结束，无需升级'}

    sched = task.get('_scheduler') or task.get('scheduler') or {}
    current_level = int(sched.get('escalationLevel') or 0)
    next_level = min(current_level + 1, 2)
    target = 'menxia' if next_level == 1 else 'shangshu'
    target_label = '门下省' if next_level == 1 else '尚书省'

    sched['escalationLevel'] = next_level
    sched['lastEscalatedAt'] = _now_iso()
    edict_add_flow(task_id, '太子调度', target_label, f'升级到{target_label}协调：{reason or "任务停滞"}')
    edict_update_scheduler(task_id, sched)

    msg = (
        f'🧭 太子调度升级通知\n'
        f'任务ID: {task_id}\n'
        f'当前状态: {state}\n'
        f'停滞处理: 请你介入协调推进\n'
        f'原因: {reason or "任务超过阈值未推进"}\n'
        f'⚠️ 看板已有任务，请勿重复创建。'
    )
    if wake_fn:
        wake_fn(target, msg)

    return {'ok': True, 'message': f'{task_id} 已升级至{target_label}', 'escalationLevel': next_level}


def handle_scheduler_rollback(task_id, reason='', dispatch_fn=None):
    task = edict_get_task(task_id)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    sched = task.get('_scheduler') or task.get('scheduler') or {}
    snapshot = sched.get('snapshot') or {}
    snap_state = snapshot.get('state')
    if not snap_state:
        return {'ok': False, 'error': f'任务 {task_id} 无可用回滚快照'}

    old_state = task.get('state', '')
    edict_transition(task_id, snap_state, 'scheduler', f'回滚：{old_state} → {snap_state}')
    sched['retryCount'] = 0
    sched['escalationLevel'] = 0
    sched['stallSince'] = None
    sched['lastProgressAt'] = _now_iso()
    edict_update_scheduler(task_id, sched)
    edict_add_flow(task_id, '太子调度', snapshot.get('org', ''), f'执行回滚：{old_state} → {snap_state}，原因：{reason or "停滞恢复"}')

    if snap_state not in _TERMINAL_STATES and dispatch_fn:
        dispatch_fn(task_id, task, snap_state, trigger='taizi-rollback')

    return {'ok': True, 'message': f'{task_id} 已回滚到 {snap_state}'}


def handle_scheduler_scan(threshold_sec=180, dispatch_fn=None, wake_fn=None):
    """扫描活跃任务，检测停滞并自动重试/升级/回滚。"""
    threshold_sec = max(30, int(threshold_sec or 180))
    edict_tasks = edict_get_active_tasks()
    tasks = edict_tasks if edict_tasks is not None else []

    now_dt = datetime.datetime.now(datetime.timezone.utc)
    pending_retries = []
    pending_escalates = []
    pending_rollbacks = []
    actions = []
    changed = False

    for task in tasks:
        task_id = task.get('id', '')
        state = task.get('state', '')
        if not task_id or state in _TERMINAL_STATES or task.get('archived'):
            continue
        if state == 'Blocked':
            continue

        sched = task.get('_scheduler') or task.get('scheduler') or {}
        ensure_scheduler(task)
        sched = task.get('_scheduler', sched)
        task_threshold = int(sched.get('stallThresholdSec') or threshold_sec)
        last_progress = _parse_iso(sched.get('lastProgressAt') or task.get('updatedAt'))
        if not last_progress:
            continue
        stalled_sec = max(0, int((now_dt - last_progress).total_seconds()))
        if stalled_sec < task_threshold:
            continue

        if not sched.get('stallSince'):
            sched['stallSince'] = _now_iso()
            changed = True

        retry_count = int(sched.get('retryCount') or 0)
        max_retry = max(0, int(sched.get('maxRetry') or 1))
        level = int(sched.get('escalationLevel') or 0)

        if retry_count < max_retry:
            sched['retryCount'] = retry_count + 1
            sched['lastRetryAt'] = _now_iso()
            sched['lastDispatchTrigger'] = 'taizi-scan-retry'
            scheduler_add_flow(task, f'停滞{stalled_sec}秒，触发自动重试第{sched["retryCount"]}次')
            pending_retries.append((task_id, state))
            actions.append({'taskId': task_id, 'action': 'retry', 'stalledSec': stalled_sec})
            changed = True
            continue

        if level < 2:
            next_level = level + 1
            target = 'menxia' if next_level == 1 else 'shangshu'
            target_label = '门下省' if next_level == 1 else '尚书省'
            sched['escalationLevel'] = next_level
            sched['lastEscalatedAt'] = _now_iso()
            scheduler_add_flow(task, f'停滞{stalled_sec}秒，升级至{target_label}协调', to=target_label)
            pending_escalates.append((task_id, state, target, target_label, stalled_sec))
            actions.append({'taskId': task_id, 'action': 'escalate', 'to': target_label, 'stalledSec': stalled_sec})
            changed = True
            continue

        if sched.get('autoRollback', True):
            snapshot = sched.get('snapshot') or {}
            snap_state = snapshot.get('state')
            if snap_state and snap_state != state:
                old_state = state
                task['state'] = snap_state
                task['org'] = snapshot.get('org', task.get('org', ''))
                task['now'] = '↩️ 太子调度自动回滚到稳定节点'
                task['block'] = '无'
                sched['retryCount'] = 0
                sched['escalationLevel'] = 0
                sched['stallSince'] = None
                sched['lastProgressAt'] = _now_iso()
                scheduler_add_flow(task, f'连续停滞，自动回滚：{old_state} → {snap_state}')
                pending_rollbacks.append((task_id, snap_state))
                actions.append({'taskId': task_id, 'action': 'rollback', 'toState': snap_state})
                changed = True

    if changed:
        for task in tasks:
            tid = task.get('id', '')
            if tid and tid.startswith('JJC-'):
                sched = task.get('_scheduler') or task.get('scheduler')
                if sched:
                    edict_update_scheduler(tid, sched)

    for task_id, state in pending_retries:
        retry_task = next((t for t in tasks if t.get('id') == task_id), None)
        if retry_task and dispatch_fn:
            dispatch_fn(task_id, retry_task, state, trigger='taizi-scan-retry')

    for task_id, state, target, target_label, stalled_sec in pending_escalates:
        msg = (
            f'🧭 太子调度升级通知\n'
            f'任务ID: {task_id}\n'
            f'当前状态: {state}\n'
            f'已停滞: {stalled_sec} 秒\n'
            f'请立即介入协调推进\n'
            f'⚠️ 看板已有任务，请勿重复创建。'
        )
        if wake_fn:
            wake_fn(target, msg)

    for task_id, state in pending_rollbacks:
        rollback_task = next((t for t in tasks if t.get('id') == task_id), None)
        if rollback_task and state not in _TERMINAL_STATES and dispatch_fn:
            dispatch_fn(task_id, rollback_task, state, trigger='taizi-auto-rollback')

    return {
        'ok': True,
        'thresholdSec': threshold_sec,
        'actions': actions,
        'summary': {
            'scanned': len(tasks),
            'retries': len(pending_retries),
            'escalations': len(pending_escalates),
            'rollbacks': len(pending_rollbacks),
        },
    }
