#!/usr/bin/env python3
"""
看板任务更新工具 - 供各省部 Agent 调用

用法:
  # 新建任务（收旨时）
  python3 kanban_update.py create JJC-20260223-012 "任务标题" Zhongshu 中书省 中书令

  # 更新状态
  python3 kanban_update.py state JJC-20260223-012 Menxia "规划方案已提交门下省"

  # 添加流转记录
  python3 kanban_update.py flow JJC-20260223-012 "中书省" "门下省" "规划方案提交审核"

  # 完成任务
  python3 kanban_update.py done JJC-20260223-012 "/path/to/output" "任务完成摘要"

  # 添加/更新子任务 todo
  python3 kanban_update.py todo JJC-20260223-012 1 "实现API接口" in-progress
  python3 kanban_update.py todo JJC-20260223-012 1 "" completed

  # 🔥 实时进展汇报（Agent 主动调用，频率不限）
  python3 kanban_update.py progress JJC-20260223-012 "正在分析需求，拟定3个子方案" "1.调研技术选型|2.撰写设计文档|3.实现原型"

  # 太子专用：转交任务（同时更新状态+流转日志）
  python3 kanban_update.py forward JJC-20260223-012 Zhongshu "太子接旨，整理需求后转交中书省起草方案"
"""
import sys, datetime, logging, os, re, pathlib, subprocess

log = logging.getLogger('kanban')
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(message)s', datefmt='%H:%M:%S')

# 确保 scripts/ 目录在 sys.path，无论从哪里调用都能找到 edict_client
_SCRIPTS_DIR = str(pathlib.Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

def _notify_dashboard_sync(task_id=None):
    """通知 dashboard server 同步状态，并立即触发 agent 派发。"""
    import urllib.request
    try:
        urllib.request.urlopen(
            urllib.request.Request('http://localhost:7891/api/live-status', method='GET'),
            timeout=3)
    except Exception:
        pass
    # 立即触发该任务的 agent 派发（不等 120 秒 scheduler 轮询）
    if task_id:
        try:
            body = json.dumps({"taskId": task_id}).encode()
            urllib.request.urlopen(
                urllib.request.Request('http://localhost:7891/api/dispatch-task',
                                      data=body, headers={'Content-Type': 'application/json'},
                                      method='POST'),
                timeout=5)
        except Exception:
            pass

try:
    from edict_client import EdictClient
except ImportError as _e:
    print(f'[看板] 无法导入 edict_client：{_e}', flush=True)
    sys.exit(1)

STATE_ORG_MAP = {
    'Taizi': '太子', 'Zhongshu': '中书省', 'Menxia': '门下省', 'Assigned': '尚书省',
    'Doing': '执行中', 'Review': '尚书省', 'Done': '完成', 'Blocked': '阻塞',
}

_STATE_AGENT_MAP = {
    'Taizi': 'main',
    'Zhongshu': 'zhongshu',
    'Menxia': 'menxia',
    'Assigned': 'shangshu',
    'Review': 'shangshu',
    'Pending': 'zhongshu',
}

_ORG_AGENT_MAP = {
    '礼部': 'libu', '户部': 'hubu', '兵部': 'bingbu',
    '刑部': 'xingbu', '工部': 'gongbu', '吏部': 'libu_hr',
    '中书省': 'zhongshu', '门下省': 'menxia', '尚书省': 'shangshu',
}

_AGENT_LABELS = {
    'main': '太子', 'taizi': '太子',
    'zhongshu': '中书省', 'menxia': '门下省', 'shangshu': '尚书省',
    'libu': '礼部', 'hubu': '户部', 'bingbu': '兵部', 'xingbu': '刑部',
    'gongbu': '工部', 'libu_hr': '吏部', 'zaochao': '钦天监',
}

# 旨意标题最低要求
_MIN_TITLE_LEN = 6
_JUNK_TITLES = {
    '?', '？', '好', '好的', '是', '否', '不', '不是', '对', '了解', '收到',
    '嗯', '哦', '知道了', '开启了么', '可以', '不行', '行', 'ok', 'yes', 'no',
    '你去开启', '测试', '试试', '看看',
}


def _sanitize_text(raw, max_len=80):
    """清洗文本：剥离文件路径、URL、Conversation 元数据、传旨前缀、截断过长内容。"""
    t = (raw or '').strip()
    t = re.split(r'\n*Conversation\b', t, maxsplit=1)[0].strip()
    t = re.split(r'\n*```', t, maxsplit=1)[0].strip()
    t = re.sub(r'[/\\.~][A-Za-z0-9_\-./]+(?:\.(?:py|js|ts|json|md|sh|yaml|yml|txt|csv|html|css|log))?', '', t)
    t = re.sub(r'https?://\S+', '', t)
    t = re.sub(r'^(传旨|下旨)([（(][^)）]*[)）])?[：:\uff1a]\s*', '', t)
    t = re.sub(r'(message_id|session_id|chat_id|open_id|user_id|tenant_key)\s*[:=]\s*\S+', '', t)
    t = re.sub(r'\s+', ' ', t).strip()
    if len(t) > max_len:
        t = t[:max_len] + '…'
    return t


def _sanitize_title(raw):
    """清洗标题（最长 80 字符）。"""
    return _sanitize_text(raw, 80)


def _sanitize_remark(raw):
    """清洗流转备注（最长 120 字符）。"""
    return _sanitize_text(raw, 120)


def _infer_agent_id_from_runtime():
    """尽量推断当前执行该命令的 Agent。"""
    for k in ('OPENCLAW_AGENT_ID', 'OPENCLAW_AGENT', 'AGENT_ID'):
        v = (os.environ.get(k) or '').strip()
        if v:
            return v

    cwd = str(pathlib.Path.cwd())
    m = re.search(r'workspace-([a-zA-Z0-9_\-]+)', cwd)
    if m:
        return m.group(1)

    fpath = str(pathlib.Path(__file__).resolve())
    m2 = re.search(r'workspace-([a-zA-Z0-9_\-]+)', fpath)
    if m2:
        return m2.group(1)

    return ''


def _resolve_output_path(output_path: str) -> pathlib.Path | None:
    """尽量解析产出文件路径。"""
    if not output_path:
        return None
    raw = pathlib.Path(output_path)
    candidates = []
    if raw.is_absolute():
        candidates.append(raw)
    else:
        candidates.extend([
            pathlib.Path.cwd() / raw,
            pathlib.Path(__file__).resolve().parent.parent / raw,
            pathlib.Path(__file__).resolve().parent.parent / 'outputs' / raw.name,
        ])
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _extract_feishu_doc_url(text: str) -> str:
    match = re.search(r'https://www\.feishu\.cn/docx/[A-Za-z0-9]+', text or '')
    return match.group(0) if match else ''


def _maybe_create_feishu_doc_link(task_id: str, output_path: str) -> str:
    """尝试将本地 Markdown 产物上传为飞书云文档，成功返回链接。"""
    if os.getenv('EDICT_DISABLE_FEISHU_DOC_EXPORT') == '1':
        return ''
    file_path = _resolve_output_path(output_path)
    if file_path is None or file_path.suffix.lower() != '.md':
        return ''

    title = file_path.stem.replace('_', ' ')
    try:
        first_line = file_path.read_text(encoding='utf-8', errors='ignore').splitlines()[:1]
        if first_line:
            heading = first_line[0].lstrip('#').strip()
            if heading:
                title = heading
    except Exception:
        pass

    doc_agent = os.getenv('OPENCLAW_DOC_AGENT', 'taizi')
    prompt = (
        f'读取本机文件 {file_path} 的内容，使用飞书创建云文档工具创建一个新文档，'
        f'标题设为《{title}》，任务ID为 {task_id}。'
        '如果成功，只回复文档直链 URL；如果失败，只回复 FAIL: 原因。'
    )
    try:
        result = subprocess.run(
            ['openclaw', 'agent', '--agent', doc_agent, '-m', prompt, '--timeout', '180'],
            capture_output=True,
            text=True,
            timeout=210,
        )
        combined = '\n'.join(part for part in [result.stdout, result.stderr] if part)
        url = _extract_feishu_doc_url(combined)
        if url:
            log.info(f'🔗 {task_id} 已生成飞书云文档: {url}')
            return url
        if combined.strip():
            log.warning(f'⚠️ {task_id} 飞书云文档创建失败: {combined.strip()[:500]}')
    except Exception as e:
        log.warning(f'⚠️ {task_id} 飞书云文档创建异常: {e}')
    return ''


def _is_valid_task_title(title):
    """校验标题是否足够作为一个旨意任务。"""
    t = (title or '').strip()
    if len(t) < _MIN_TITLE_LEN:
        return False, f'标题过短（{len(t)}<{_MIN_TITLE_LEN}字），疑似非旨意'
    if t.lower() in _JUNK_TITLES:
        return False, f'标题 "{t}" 不是有效旨意'
    if re.fullmatch(r'[\s?？!！.。,，…·\-—~]+', t):
        return False, '标题只有标点符号'
    if re.match(r'^[/\\~.]', t) or re.search(r'/[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+', t):
        return False, f'标题看起来像文件路径，请用中文概括任务'
    if re.fullmatch(r'[\s\W]*', t):
        return False, '标题清洗后为空'
    return True, ''


def _sync_task_to_json(task_id, title, state, org, official, remark=''):
    """将任务同步写入 tasks_source.json，让 Dashboard 看板能显示。"""
    try:
        data_dir = pathlib.Path(__file__).resolve().parent.parent / 'data'
        tasks_file = data_dir / 'tasks_source.json'
        if not tasks_file.exists():
            return
        # 用 file_lock 保证并发安全
        try:
            from file_lock import atomic_json_read, atomic_json_write
        except ImportError:
            return
        tasks = atomic_json_read(tasks_file, [])
        # 避免重复
        if any(t.get('id') == task_id for t in tasks):
            return
        now_ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        new_task = {
            'id': task_id,
            'title': title,
            'official': official,
            'org': org,
            'state': state,
            'now': f'等待处理',
            'eta': '-',
            'block': '无',
            'output': '',
            'priority': 'normal',
            'flow_log': [{'at': now_ts, 'from': '皇上', 'to': org, 'remark': remark or f'下旨：{title}'}],
            'updatedAt': now_ts,
        }
        tasks.insert(0, new_task)
        atomic_json_write(tasks_file, tasks)
        log.info(f'📋 {task_id} 已同步到看板 JSON')
    except Exception as e:
        log.warning(f'[JSON同步] {task_id} 写入失败: {e}')


def cmd_create(task_id, title, state, org, official, remark=None):
    """新建任务（收旨时立即调用）"""
    title = _sanitize_title(title)
    valid, reason = _is_valid_task_title(title)
    if not valid:
        log.warning(f'⚠️ 拒绝创建 {task_id}：{reason}')
        print(f'[看板] 拒绝创建：{reason}', flush=True)
        return
    actual_org = STATE_ORG_MAP.get(state, org)
    clean_remark = _sanitize_remark(remark) if remark else f"下旨：{title}"
    agent_id = _infer_agent_id_from_runtime()
    client = EdictClient()

    # ID 冲突检测：如果 EDICT 已有这个 ID，自动递增
    original_id = task_id
    for attempt in range(20):
        try:
            existing = client.get_task(task_id)
            if existing and existing.get('id'):
                # ID 已存在，递增序号
                parts = task_id.rsplit('-', 1)
                if len(parts) == 2 and parts[1].isdigit():
                    next_seq = int(parts[1]) + 1
                    task_id = f'{parts[0]}-{next_seq:03d}'
                else:
                    task_id = f'{task_id}-{attempt+2}'
                continue
        except Exception:
            pass
        break  # ID 不存在或查询失败，可以用
    if task_id != original_id:
        log.info(f'📋 ID 冲突：{original_id} → {task_id}')

    try:
        client.create_task(
            legacy_id=task_id,
            title=title,
            state=state,
            org=actual_org,
            creator=agent_id or official,
            official=official,
            remark=clean_remark,
        )
        log.info(f'✅ 创建 {task_id} | {title[:30]} | state={state}')
        # 同步到 Dashboard 看板 JSON
        _sync_task_to_json(task_id, title, state, actual_org, official, clean_remark)
        _notify_dashboard_sync(task_id)
    except Exception as e:
        log.error(f'❌ 创建任务失败 {task_id}: {e}')
        sys.exit(1)
    finally:
        client.close()


def cmd_state(task_id, new_state, now_text=None):
    """更新任务状态"""
    agent_id = _infer_agent_id_from_runtime()
    client = EdictClient()
    try:
        client.transition(
            legacy_id=task_id,
            new_state=new_state,
            agent=agent_id or 'system',
            reason=now_text or '',
        )
        log.info(f'✅ {task_id} 状态更新 → {new_state}')
        _notify_dashboard_sync(task_id)
    except Exception as e:
        log.error(f'❌ 状态更新失败 {task_id}: {e}')
        sys.exit(1)
    finally:
        client.close()


def cmd_flow(task_id, from_dept, to_dept, remark):
    """添加流转记录（太子→中书省时自动同步状态）"""
    clean_remark = _sanitize_remark(remark)
    # 自动状态同步：flow 隐含状态变更
    _FLOW_STATE_MAP = {
        ('太子', '中书省'): 'Zhongshu',
        ('中书省', '门下省'): 'Menxia',
        ('门下省', '尚书省'): 'Assigned',
    }
    auto_state = _FLOW_STATE_MAP.get((from_dept, to_dept))
    agent_id = _infer_agent_id_from_runtime()
    client = EdictClient()
    try:
        if auto_state:
            # 先查当前状态，已是目标状态则跳过（避免 400 报错刷日志）
            try:
                current = client.get_task(task_id)
                if current.get('state') != auto_state:
                    client.transition(legacy_id=task_id, new_state=auto_state,
                                      agent=agent_id or from_dept, reason=clean_remark)
            except Exception:
                pass  # EDICT 不可用时静默跳过，flow_log 仍会写入
        client.add_flow(task_id, from_dept, to_dept, clean_remark)
        log.info(f'✅ {task_id} 流转: {from_dept} → {to_dept}' + (f' [state→{auto_state}]' if auto_state else ''))
        if auto_state:
            _notify_dashboard_sync(task_id)
    except Exception as e:
        log.error(f'❌ 流转记录失败 {task_id}: {e}')
        sys.exit(1)
    finally:
        client.close()


def cmd_done(task_id, output_path='', summary=''):
    """标记任务完成"""
    agent_id = _infer_agent_id_from_runtime()
    client = EdictClient()
    try:
        feishu_doc_url = _maybe_create_feishu_doc_link(task_id, output_path)
        final_output = feishu_doc_url or output_path
        client.done(task_id, final_output, summary or '任务已完成', agent_id or 'system')
        log.info(f'✅ {task_id} 已完成')
        _notify_dashboard_sync(task_id)
    except Exception as e:
        log.error(f'❌ 完成任务失败 {task_id}: {e}')
        sys.exit(1)
    finally:
        client.close()


def cmd_block(task_id, reason):
    """标记阻塞"""
    agent_id = _infer_agent_id_from_runtime()
    client = EdictClient()
    try:
        client.block(task_id, reason, agent_id or 'system')
        log.warning(f'⚠️ {task_id} 已阻塞: {reason}')
    except Exception as e:
        log.error(f'❌ 阻塞任务失败 {task_id}: {e}')
        sys.exit(1)
    finally:
        client.close()


def cmd_progress(task_id, now_text, todos_pipe='', tokens=0, cost=0.0, elapsed=0):
    """🔥 实时进展汇报

    now_text: 当前正在做什么的一句话描述（必填）
    todos_pipe: 可选，用 | 分隔的 todo 列表，格式：
        "已完成的事项✅|正在做的事项🔄|计划做的事项"
    tokens/cost/elapsed: 可选资源消耗
    """
    clean = _sanitize_remark(now_text)

    # 解析 todos_pipe
    parsed_todos = None
    if todos_pipe:
        new_todos = []
        for i, item in enumerate(todos_pipe.split('|'), 1):
            item = item.strip()
            if not item:
                continue
            if item.endswith('✅'):
                status = 'completed'
                title = item[:-1].strip()
            elif item.endswith('🔄'):
                status = 'in-progress'
                title = item[:-1].strip()
            else:
                status = 'not-started'
                title = item
            new_todos.append({'id': str(i), 'title': title, 'status': status})
        if new_todos:
            parsed_todos = new_todos

    # 解析资源消耗参数
    try:
        tokens = int(tokens) if tokens else 0
    except (ValueError, TypeError):
        tokens = 0
    try:
        cost = float(cost) if cost else 0.0
    except (ValueError, TypeError):
        cost = 0.0
    try:
        elapsed = int(elapsed) if elapsed else 0
    except (ValueError, TypeError):
        elapsed = 0

    agent_id = _infer_agent_id_from_runtime()
    client = EdictClient()
    try:
        client.add_progress(
            task_id, agent_id or 'system', clean,
            todos=parsed_todos,
            tokens=tokens, cost=cost, elapsed=elapsed,
        )
        res_info = ''
        if tokens or cost or elapsed:
            res_info = f' [res: {tokens}tok/${cost:.4f}/{elapsed}s]'
        done_cnt = sum(1 for t in (parsed_todos or []) if t.get('status') == 'completed')
        total_cnt = len(parsed_todos or [])
        log.info(f'📡 {task_id} 进展: {clean[:40]}... [{done_cnt}/{total_cnt}]{res_info}')
    except Exception as e:
        log.error(f'❌ 进展汇报失败 {task_id}: {e}')
        sys.exit(1)
    finally:
        client.close()


def cmd_todo(task_id, todo_id, title, status='not-started', detail=''):
    """添加或更新子任务 todo

    status: not-started / in-progress / completed
    detail: 可选，该子任务的详细产出/说明
    """
    if status not in ('not-started', 'in-progress', 'completed'):
        status = 'not-started'

    # 读取现有 todos，patch 后更新
    client = EdictClient()
    try:
        task = client.get_task(task_id)
        todos = task.get('todos') or []

        existing = next((td for td in todos if str(td.get('id')) == str(todo_id)), None)
        if existing:
            existing['status'] = status
            if title:
                existing['title'] = title
            if detail:
                existing['detail'] = detail
        else:
            item: dict = {'id': todo_id, 'title': title, 'status': status}
            if detail:
                item['detail'] = detail
            todos.append(item)

        client.update_todos(task_id, todos)
        done_cnt = sum(1 for td in todos if td.get('status') == 'completed')
        log.info(f'✅ {task_id} todo [{done_cnt}/{len(todos)}]: {todo_id} → {status}')
    except Exception as e:
        log.error(f'❌ todo 更新失败 {task_id}: {e}')
        sys.exit(1)
    finally:
        client.close()


def cmd_forward(task_id, new_state, remark):
    """太子专用：转交任务到下一个省（同时更新状态 + 流转日志，只需一条命令）"""
    state_org_map = {
        'Zhongshu': ('中书省', '太子'), 'Menxia': ('门下省', '中书省'),
        'Assigned': ('尚书省', '门下省'), 'Doing': ('执行中', '尚书省'),
    }
    to_org, from_org = state_org_map.get(new_state, ('中书省', '太子'))
    agent_id = _infer_agent_id_from_runtime()
    clean_remark = _sanitize_remark(remark)
    client = EdictClient()
    try:
        client.transition(legacy_id=task_id, new_state=new_state,
                          agent=agent_id or '太子', reason=clean_remark)
        client.add_flow(task_id, from_org, to_org, clean_remark)
        log.info(f'✅ {task_id} 已转交 {from_org} → {to_org} [{new_state}]')
        _notify_dashboard_sync(task_id)
    except Exception as e:
        log.error(f'❌ 转交失败 {task_id}: {e}')
        sys.exit(1)
    finally:
        client.close()


_CMD_MIN_ARGS = {
    'create': 6, 'state': 3, 'flow': 5, 'done': 2, 'block': 3, 'todo': 4, 'progress': 3,
    'forward': 4,
}

if __name__ == '__main__':
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)
    cmd = args[0]
    if cmd in _CMD_MIN_ARGS and len(args) < _CMD_MIN_ARGS[cmd]:
        print(f'错误："{cmd}" 命令至少需要 {_CMD_MIN_ARGS[cmd]} 个参数，实际 {len(args)} 个')
        print(__doc__)
        sys.exit(1)
    if cmd == 'create':
        cmd_create(args[1], args[2], args[3], args[4], args[5], args[6] if len(args) > 6 else None)
    elif cmd == 'state':
        cmd_state(args[1], args[2], args[3] if len(args) > 3 else None)
    elif cmd == 'flow':
        cmd_flow(args[1], args[2], args[3], args[4])
    elif cmd == 'done':
        cmd_done(args[1], args[2] if len(args) > 2 else '', args[3] if len(args) > 3 else '')
    elif cmd == 'block':
        cmd_block(args[1], args[2])
    elif cmd == 'todo':
        todo_pos = []
        todo_detail = ''
        ti = 1
        while ti < len(args):
            if args[ti] == '--detail' and ti + 1 < len(args):
                todo_detail = args[ti + 1]; ti += 2
            else:
                todo_pos.append(args[ti]); ti += 1
        cmd_todo(
            todo_pos[0] if len(todo_pos) > 0 else '',
            todo_pos[1] if len(todo_pos) > 1 else '',
            todo_pos[2] if len(todo_pos) > 2 else '',
            todo_pos[3] if len(todo_pos) > 3 else 'not-started',
            detail=todo_detail,
        )
    elif cmd == 'progress':
        pos_args = []
        kw = {}
        i = 1
        while i < len(args):
            if args[i] == '--tokens' and i + 1 < len(args):
                kw['tokens'] = args[i + 1]; i += 2
            elif args[i] == '--cost' and i + 1 < len(args):
                kw['cost'] = args[i + 1]; i += 2
            elif args[i] == '--elapsed' and i + 1 < len(args):
                kw['elapsed'] = args[i + 1]; i += 2
            else:
                pos_args.append(args[i]); i += 1
        cmd_progress(
            pos_args[0] if len(pos_args) > 0 else '',
            pos_args[1] if len(pos_args) > 1 else '',
            pos_args[2] if len(pos_args) > 2 else '',
            tokens=kw.get('tokens', 0),
            cost=kw.get('cost', 0.0),
            elapsed=kw.get('elapsed', 0),
        )
    elif cmd == 'forward':
        cmd_forward(args[1], args[2], args[3] if len(args) > 3 else '太子接旨转交')
    else:
        print(__doc__)
        sys.exit(1)
