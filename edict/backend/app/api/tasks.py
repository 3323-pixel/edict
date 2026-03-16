"""Tasks API — 任务的 CRUD 和状态流转。"""

import uuid
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Header
from pydantic import BaseModel, Field
from .utils import MenxiaRejectIn, MenxiaResubmitIn, MenxiaRollbackIn, error_response as _err
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models.task import TaskState
from ..services.event_bus import EventBus, get_event_bus
from ..services.task_service import TaskService

log = logging.getLogger("edict.api.tasks")
router = APIRouter()


# ── Schemas ──

class TaskCreate(BaseModel):
    title: str
    description: str = ""
    priority: str = "中"
    assignee_org: str | None = None
    creator: str = "emperor"
    tags: list[str] = []
    meta: dict | None = None


class TaskTransition(BaseModel):
    new_state: str
    agent: str = "system"
    reason: str = ""


class TaskProgress(BaseModel):
    agent: str
    content: str
    todos: list[dict] | None = None
    tokens: int = 0
    cost: float = 0.0
    elapsed: int = 0


class TaskTodoUpdate(BaseModel):
    todos: list[dict]


class TaskFlow(BaseModel):
    from_dept: str
    to_dept: str
    remark: str = ""


class TaskBlock(BaseModel):
    reason: str
    agent: str = "system"


class TaskCreateLegacy(BaseModel):
    legacy_id: str
    title: str
    state: str = "Taizi"
    org: str = "太子"
    official: str = ""
    remark: str = ""


class TaskDone(BaseModel):
    output: str = ""
    summary: str = ""
    agent: str = "system"


class TaskSchedulerUpdate(BaseModel):
    scheduler: dict


# MenxiaRejectIn, MenxiaResubmitIn, MenxiaRollbackIn, _err 从 .utils 导入

class TaskOut(BaseModel):
    id: str
    title: str
    priority: str
    state: str
    org: str
    official: str
    now: str
    eta: str
    block: str
    output: str
    archived: bool
    flow_log: list
    progress_log: list
    todos: list
    createdAt: str
    updatedAt: str

    class Config:
        from_attributes = True


# ── 依赖注入 helper ──

async def get_task_service(
    db: AsyncSession = Depends(get_db),
) -> TaskService:
    bus = await get_event_bus()
    return TaskService(db, bus)


# ── Endpoints ──



# _err = error_response（从 .utils 导入）


def _menxia_bucket(task: dict) -> dict:
    sched = task.get("_scheduler") or {}
    mx = sched.get("menxia")
    if not isinstance(mx, dict):
        mx = {}
        sched["menxia"] = mx
    if "review_round" not in mx:
        # review_round 由服务端维护：统计 reject 次数（0 起）。
        mx["review_round"] = 0
    if "reviews" not in mx or not isinstance(mx.get("reviews"), list):
        mx["reviews"] = []
    if "audit" not in mx or not isinstance(mx.get("audit"), list):
        mx["audit"] = []
    if "idem" not in mx or not isinstance(mx.get("idem"), dict):
        mx["idem"] = {}
    task["_scheduler"] = sched
    return mx

@router.get("")
async def list_tasks(
    state: str | None = None,
    assignee_org: str | None = None,
    priority: str | None = None,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    svc: TaskService = Depends(get_task_service),
):
    """获取任务列表。"""
    task_state = TaskState(state) if state else None
    tasks = await svc.list_tasks(
        state=task_state,
        assignee_org=assignee_org,
        priority=priority,
        limit=limit,
        offset=offset,
    )
    return {"tasks": [t.to_dict() for t in tasks], "count": len(tasks)}


@router.get("/live-status")
async def live_status(svc: TaskService = Depends(get_task_service)):
    """兼容旧 live_status.json 格式的全局状态。"""
    return await svc.get_live_status()


@router.get("/active")
async def active_tasks(svc: TaskService = Depends(get_task_service)):
    """列出所有活跃任务（非终态、非归档）。"""
    tasks = await svc.list_active_tasks()
    return {"tasks": [t.to_dict() for t in tasks]}


@router.get("/stats")
async def task_stats(svc: TaskService = Depends(get_task_service)):
    """任务统计。"""
    stats = {}
    for s in TaskState:
        stats[s.value] = await svc.count_tasks(s)
    total = sum(stats.values())
    return {"total": total, "by_state": stats}


@router.post("", status_code=201)
async def create_task(
    body: TaskCreate,
    svc: TaskService = Depends(get_task_service),
):
    """创建新任务。"""
    task = await svc.create_task(
        title=body.title,
        description=body.description,
        priority=body.priority,
        assignee_org=body.assignee_org,
        creator=body.creator,
        tags=body.tags,
        meta=body.meta,
    )
    return {"task_id": task.id, "state": task.state.value}


@router.get("/{task_id}")
async def get_task(
    task_id: uuid.UUID,
    svc: TaskService = Depends(get_task_service),
):
    """获取任务详情。"""
    try:
        task = await svc.get_task(task_id)
        return task.to_dict()
    except ValueError:
        raise HTTPException(status_code=404, detail="Task not found")


@router.post("/{task_id}/transition")
async def transition_task(
    task_id: uuid.UUID,
    body: TaskTransition,
    svc: TaskService = Depends(get_task_service),
):
    """执行状态流转。"""
    try:
        new_state = TaskState(body.new_state)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid state: {body.new_state}")

    try:
        task = await svc.transition_state(
            task_id=task_id,
            new_state=new_state,
            agent=body.agent,
            reason=body.reason,
        )
        return {"task_id": task.id, "state": task.state.value, "message": "ok"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{task_id}/dispatch")
async def dispatch_task(
    task_id: uuid.UUID,
    agent: str = Query(description="目标 agent"),
    message: str = Query(default="", description="派发消息"),
    svc: TaskService = Depends(get_task_service),
):
    """手动派发任务给指定 agent。"""
    try:
        await svc.request_dispatch(task_id, agent, message)
        return {"message": "dispatch requested", "agent": agent}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{task_id}/progress")
async def add_progress(
    task_id: uuid.UUID,
    body: TaskProgress,
    svc: TaskService = Depends(get_task_service),
):
    """添加进度记录。"""
    try:
        await svc.add_progress(task_id, body.agent, body.content)
        return {"message": "ok"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/{task_id}/todos")
async def update_todos(
    task_id: uuid.UUID,
    body: TaskTodoUpdate,
    svc: TaskService = Depends(get_task_service),
):
    """更新任务 TODO 清单。"""
    try:
        await svc.update_todos(task_id, body.todos)
        return {"message": "ok"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/{task_id}/scheduler")
async def update_scheduler(
    task_id: uuid.UUID,
    body: TaskSchedulerUpdate,
    svc: TaskService = Depends(get_task_service),
):
    """更新任务排期信息。"""
    try:
        await svc.update_scheduler(task_id, body.scheduler)
        return {"message": "ok"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── flow/block (UUID-based) ──

@router.post("/{task_id}/flow")
async def add_flow(
    task_id: uuid.UUID,
    body: TaskFlow,
    svc: TaskService = Depends(get_task_service),
):
    """追加流转记录。"""
    try:
        await svc.add_flow_entry(str(task_id), body.from_dept, body.to_dept, body.remark)
        return {"message": "ok"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{task_id}/block")
async def block_task(
    task_id: uuid.UUID,
    body: TaskBlock,
    svc: TaskService = Depends(get_task_service),
):
    """将任务置为 Blocked。"""
    try:
        await svc.block_task(str(task_id), body.reason, body.agent)
        return {"message": "ok"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── Legacy ID 创建路由 ──

@router.post("/legacy", status_code=201)
async def create_task_legacy(
    body: TaskCreateLegacy,
    svc: TaskService = Depends(get_task_service),
):
    """用 JJC-* 风格 ID 创建任务。"""
    try:
        state = TaskState(body.state)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid state: {body.state}")
    try:
        task = await svc.create_task_legacy(
            legacy_id=body.legacy_id,
            title=body.title,
            state=state,
            org=body.org,
            official=body.official,
            remark=body.remark,
        )
        return {"task_id": task.id, "state": task.state.value}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Menxia actions (UUID-based) ──

@router.post("/{task_id}/menxia/reject")
async def menxia_reject(
    task_id: uuid.UUID,
    body: MenxiaRejectIn,
    x_actor: str | None = Header(default=None, alias="X-Actor"),
    x_role: str | None = Header(default=None, alias="X-Role"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    svc: TaskService = Depends(get_task_service),
):
    """门下省封驳：Menxia → Zhongshu。

    验收口径：
    - 仅 status==Menxia 允许；否则 409
    - review_round 由服务端维护：reject 时 +1
    - 当 review_round==3 且已 auto_approve_round3 后，禁止再 reject（409, ROUND3_FORCED_APPROVAL）
    """
    try:
        task = await svc.get_task(task_id)
    except ValueError:
        return _err(status_code=404, code="NOT_FOUND", message="Task not found")

    req_id = body.client_request_id or idempotency_key or str(uuid.uuid4())
    status_before = task.state.value if task.state else ""

    # 终态禁止
    if task.state in {TaskState.Done, TaskState.Cancelled} or task.archived:
        return _err(
            status_code=409,
            code="TERMINAL_STATE",
            message="Task is terminal/archived; action not allowed",
            request_id=req_id,
            status_before=status_before,
            allowed_transitions=[],
        )

    if status_before != TaskState.Menxia.value:
        return _err(
            status_code=409,
            code="INVALID_STATE",
            message="REJECT only allowed when status==Menxia",
            request_id=req_id,
            status_before=status_before,
            allowed_transitions=[TaskState.Zhongshu.value] if status_before == TaskState.Menxia.value else [],
        )

    # 权限矩阵（简化实现：依赖头部角色与当前处理人=official）
    if (x_role or "").lower() != "menxia":
        return _err(
            status_code=403,
            code="MISSING_ROLES",
            message="Missing role: Menxia",
            request_id=req_id,
            status_before=status_before,
            missing_roles=["Menxia"],
        )
    if not x_actor:
        return _err(
            status_code=403,
            code="MISSING_ACTOR",
            message="Missing actor header: X-Actor",
            request_id=req_id,
            status_before=status_before,
        )
    if task.official and task.official != x_actor:
        return _err(
            status_code=403,
            code="NOT_REVIEW_ASSIGNEE",
            message="Only current review assignee can reject",
            request_id=req_id,
            status_before=status_before,
        )

    mx = task.scheduler.get("menxia") if isinstance(task.scheduler, dict) else None
    if not isinstance(mx, dict):
        mx = {"review_round": 0, "reviews": [], "audit": [], "idem": {}}
        task.scheduler = {**(task.scheduler or {}), "menxia": mx}

    # 幂等：同一 key 重放返回同结果
    if req_id in (mx.get("idem") or {}):
        return mx["idem"][req_id]

    # Round3 强制通过后禁止再 reject
    if mx.get("forced_approved_round3") is True:
        return _err(
            status_code=409,
            code="ROUND3_FORCED_APPROVAL",
            message="Round3 forced approval; reject is no longer allowed",
            request_id=req_id,
            status_before=status_before,
        )

    # reject：review_round + 1
    mx["review_round"] = int(mx.get("review_round") or 0) + 1
    round_no = mx["review_round"]

    review_entry = {
        "task_id": str(task.id),
        "legacy_id": task.id if isinstance(task.id, str) and task.id.startswith("JJC-") else None,
        "round": round_no,
        "decision": "Rejected",
        "reason": body.reason,
        "opinion": body.opinion,
        "actor": x_actor,
        "role": "Menxia",
        "created_at": datetime.utcnow().isoformat() + "Z",
        "request_id": req_id,
    }
    mx["reviews"] = [*(mx.get("reviews") or []), review_entry]
    mx["audit"] = [
        *(mx.get("audit") or []),
        {
            "action": "reject",
            "actor": x_actor,
            "role": "Menxia",
            "round": round_no,
            "request_id": req_id,
            "created_at": review_entry["created_at"],
        },
    ]

    # 状态流转：Menxia → Zhongshu
    try:
        await svc.transition_state(task_id=task_id, new_state=TaskState.Zhongshu, agent="menxia", reason=body.reason)
    except Exception as e:
        return _err(status_code=409, code="TRANSITION_FAILED", message=str(e), request_id=req_id, status_before=status_before)

    # 返回成功体（固定 200）
    resp = {
        "task_id": str(task.id),
        "legacy_id": task.id if isinstance(task.id, str) and task.id.startswith("JJC-") else None,
        "status_before": status_before,
        "status_after": TaskState.Zhongshu.value,
        "review_round": mx["review_round"],
        "requestId": req_id,
    }
    mx.setdefault("idem", {})[req_id] = resp
    await svc.db.commit()
    return resp


@router.post("/{task_id}/menxia/resubmit")
async def menxia_resubmit(
    task_id: uuid.UUID,
    body: MenxiaResubmitIn,
    x_actor: str | None = Header(default=None, alias="X-Actor"),
    x_role: str | None = Header(default=None, alias="X-Role"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    svc: TaskService = Depends(get_task_service),
):
    """中书再呈：Zhongshu → Menxia，且 round3 触发强制准奏。"""
    try:
        task = await svc.get_task(task_id)
    except ValueError:
        return _err(status_code=404, code="NOT_FOUND", message="Task not found")

    req_id = body.client_request_id or idempotency_key or str(uuid.uuid4())
    status_before = task.state.value if task.state else ""

    if task.state in {TaskState.Done, TaskState.Cancelled} or task.archived:
        return _err(
            status_code=409,
            code="TERMINAL_STATE",
            message="Task is terminal/archived; action not allowed",
            request_id=req_id,
            status_before=status_before,
            allowed_transitions=[],
        )

    if status_before != TaskState.Zhongshu.value:
        return _err(
            status_code=409,
            code="INVALID_STATE",
            message="RESUBMIT only allowed when status==Zhongshu",
            request_id=req_id,
            status_before=status_before,
        )

    if (x_role or "").lower() != "zhongshu":
        return _err(
            status_code=403,
            code="MISSING_ROLES",
            message="Missing role: Zhongshu",
            request_id=req_id,
            status_before=status_before,
            missing_roles=["Zhongshu"],
        )
    if not x_actor:
        return _err(
            status_code=403,
            code="MISSING_ACTOR",
            message="Missing actor header: X-Actor",
            request_id=req_id,
            status_before=status_before,
        )

    mx = task.scheduler.get("menxia") if isinstance(task.scheduler, dict) else None
    if not isinstance(mx, dict):
        mx = {"review_round": 0, "reviews": [], "audit": [], "idem": {}}
        task.scheduler = {**(task.scheduler or {}), "menxia": mx}

    if req_id in (mx.get("idem") or {}):
        return mx["idem"][req_id]

    # Round3 强制通过：当 review_round==3 且收到 RESUBMIT
    if int(mx.get("review_round") or 0) == 3:
        mx["forced_approved_round3"] = True
        created_at = datetime.utcnow().isoformat() + "Z"
        mx["audit"] = [
            *(mx.get("audit") or []),
            {
                "action": "auto_approve_round3",
                "actor": "system",
                "role": "system",
                "round": 3,
                "request_id": req_id,
                "created_at": created_at,
            },
        ]
        mx["reviews"] = [
            *(mx.get("reviews") or []),
            {
                "task_id": str(task.id),
                "legacy_id": task.id if isinstance(task.id, str) and task.id.startswith("JJC-") else None,
                "round": 3,
                "decision": "Approved",
                "reason": "auto_approve_round3",
                "opinion": body.summary_of_changes,
                "actor": "system",
                "role": "system",
                "created_at": created_at,
                "request_id": req_id,
            },
        ]
        # 这里按最小闭环：直接进入 Assigned（由尚书省派发），避免再次进入 Menxia。
        try:
            await svc.transition_state(task_id=task_id, new_state=TaskState.Assigned, agent="system", reason="auto_approve_round3")
        except Exception as e:
            return _err(status_code=409, code="TRANSITION_FAILED", message=str(e), request_id=req_id, status_before=status_before)

        resp = {
            "task_id": str(task.id),
            "legacy_id": task.id if isinstance(task.id, str) and task.id.startswith("JJC-") else None,
            "status_before": status_before,
            "status_after": TaskState.Assigned.value,
            "action": "auto_approve_round3",
            "review_round": mx["review_round"],
            "requestId": req_id,
        }
        mx.setdefault("idem", {})[req_id] = resp
        await svc.db.commit()
        return resp

    # 正常再呈：Zhongshu → Menxia
    try:
        await svc.transition_state(task_id=task_id, new_state=TaskState.Menxia, agent="zhongshu", reason=body.summary_of_changes or "resubmit")
    except Exception as e:
        return _err(status_code=409, code="TRANSITION_FAILED", message=str(e), request_id=req_id, status_before=status_before)

    mx["audit"] = [
        *(mx.get("audit") or []),
        {
            "action": "resubmit",
            "actor": x_actor,
            "role": "Zhongshu",
            "round": int(mx.get("review_round") or 0),
            "request_id": req_id,
            "created_at": datetime.utcnow().isoformat() + "Z",
        },
    ]

    resp = {
        "task_id": str(task.id),
        "legacy_id": task.id if isinstance(task.id, str) and task.id.startswith("JJC-") else None,
        "status_before": status_before,
        "status_after": TaskState.Menxia.value,
        "review_round": mx["review_round"],
        "requestId": req_id,
    }
    mx.setdefault("idem", {})[req_id] = resp
    await svc.db.commit()
    return resp


@router.post("/{task_id}/menxia/rollback")
async def menxia_rollback(
    task_id: uuid.UUID,
    body: MenxiaRollbackIn,
    x_actor: str | None = Header(default=None, alias="X-Actor"),
    x_role: str | None = Header(default=None, alias="X-Role"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    svc: TaskService = Depends(get_task_service),
):
    """回退/撤销封驳（如产品需要）：当前实现为 Menxia → Zhongshu，不增加 review_round。"""
    try:
        task = await svc.get_task(task_id)
    except ValueError:
        return _err(status_code=404, code="NOT_FOUND", message="Task not found")

    req_id = body.client_request_id or idempotency_key or str(uuid.uuid4())
    status_before = task.state.value if task.state else ""

    if status_before != TaskState.Menxia.value:
        return _err(status_code=409, code="INVALID_STATE", message="ROLLBACK only allowed when status==Menxia", request_id=req_id, status_before=status_before)

    if (x_role or "").lower() != "menxia":
        return _err(status_code=403, code="MISSING_ROLES", message="Missing role: Menxia", request_id=req_id, status_before=status_before, missing_roles=["Menxia"])

    mx = task.scheduler.get("menxia") if isinstance(task.scheduler, dict) else None
    if not isinstance(mx, dict):
        mx = {"review_round": 0, "reviews": [], "audit": [], "idem": {}}
        task.scheduler = {**(task.scheduler or {}), "menxia": mx}

    if req_id in (mx.get("idem") or {}):
        return mx["idem"][req_id]

    try:
        await svc.transition_state(task_id=task_id, new_state=TaskState.Zhongshu, agent="menxia", reason=body.reason or "rollback")
    except Exception as e:
        return _err(status_code=409, code="TRANSITION_FAILED", message=str(e), request_id=req_id, status_before=status_before)

    mx["audit"] = [
        *(mx.get("audit") or []),
        {
            "action": "rollback",
            "actor": x_actor or "unknown",
            "role": "Menxia",
            "round": int(mx.get("review_round") or 0),
            "request_id": req_id,
            "created_at": datetime.utcnow().isoformat() + "Z",
        },
    ]

    resp = {
        "task_id": str(task.id),
        "legacy_id": task.id if isinstance(task.id, str) and task.id.startswith("JJC-") else None,
        "status_before": status_before,
        "status_after": TaskState.Zhongshu.value,
        "review_round": mx["review_round"],
        "requestId": req_id,
    }
    mx.setdefault("idem", {})[req_id] = resp
    await svc.db.commit()
    return resp


@router.get("/{task_id}/menxia/reviews")
async def menxia_reviews(
    task_id: uuid.UUID,
    round: int | None = Query(default=None),
    actor: str | None = Query(default=None),
    decision: str | None = Query(default=None),
    timeRange: str | None = Query(default=None, description="ISO8601 start,end"),
    svc: TaskService = Depends(get_task_service),
):
    """获取门下审议记录，可按 round/actor/decision/timeRange 检索。"""
    try:
        task = await svc.get_task(task_id)
    except ValueError:
        return _err(status_code=404, code="NOT_FOUND", message="Task not found")

    mx = task.scheduler.get("menxia") if isinstance(task.scheduler, dict) else {}
    reviews = list((mx or {}).get("reviews") or [])

    if round is not None:
        reviews = [r for r in reviews if int(r.get("round") or 0) == int(round)]
    if actor:
        reviews = [r for r in reviews if (r.get("actor") or "") == actor]
    if decision:
        reviews = [r for r in reviews if (r.get("decision") or "").lower() == decision.lower()]
    # timeRange: "start,end"; best-effort filter on created_at
    if timeRange and "," in timeRange:
        start, end = timeRange.split(",", 1)
        start = start.strip()
        end = end.strip()
        if start:
            reviews = [r for r in reviews if (r.get("created_at") or "") >= start]
        if end:
            reviews = [r for r in reviews if (r.get("created_at") or "") <= end]

    return {
        "task_id": str(task.id),
        "review_round": int((mx or {}).get("review_round") or 0),
        "reviews": reviews,
    }


@router.get("/{task_id}/flows")
async def task_flows(task_id: uuid.UUID, svc: TaskService = Depends(get_task_service)):
    """取证：获取任务 flows（流转日志）。"""
    try:
        task = await svc.get_task(task_id)
    except ValueError:
        return _err(status_code=404, code="NOT_FOUND", message="Task not found")
    return {"task_id": str(task.id), "flows": task.flow_log or []}


@router.get("/{task_id}/audit-logs")
async def task_audit_logs(task_id: uuid.UUID, svc: TaskService = Depends(get_task_service)):
    """取证：获取审计日志（当前实现：progress_log + flow_log + menxia.audit）。"""
    try:
        task = await svc.get_task(task_id)
    except ValueError:
        return _err(status_code=404, code="NOT_FOUND", message="Task not found")

    mx = task.scheduler.get("menxia") if isinstance(task.scheduler, dict) else {}
    return {
        "task_id": str(task.id),
        "progress": task.progress_log or [],
        "flows": task.flow_log or [],
        "menxia_audit": (mx or {}).get("audit") or [],
    }
