"""Legacy 兼容路由 — 通过旧版 task_id (JJC-xxx) 操作任务。

旧版 kanban_update.py 使用自定义 ID (JJC-20260301-007)，
Edict 使用 UUID。此路由通过 tags 或 meta.legacy_id 映射。
"""

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Header, Query
from pydantic import BaseModel, Field
from .utils import MenxiaRejectIn, MenxiaResubmitIn, MenxiaRollbackIn, error_response as _err
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models.task import Task, TaskState
from ..services.event_bus import get_event_bus
from ..services.task_service import TaskService

log = logging.getLogger("edict.api.legacy")
router = APIRouter()


async def _find_by_legacy_id(db: AsyncSession, legacy_id: str) -> Task | None:
    """通过旧版 ID 查找任务（在 tags 或 meta.legacy_id 中搜索）。"""
    task = await db.get(Task, legacy_id)
    if task:
        return task

    if hasattr(Task, "tags"):
        stmt = select(Task).where(Task.tags.contains([legacy_id]))
        result = await db.execute(stmt)
        task = result.scalars().first()
        if task:
            return task

    if hasattr(Task, "meta"):
        stmt = select(Task).where(Task.meta["legacy_id"].astext == legacy_id)
        result = await db.execute(stmt)
        task = result.scalars().first()
        if task:
            return task

    return None


class LegacyTransition(BaseModel):
    new_state: str
    agent: str = "system"
    reason: str = ""


class LegacyProgress(BaseModel):
    agent: str
    content: str
    todos: list[dict] | None = None
    tokens: int = 0
    cost: float = 0.0
    elapsed: int = 0


class LegacyTodoUpdate(BaseModel):
    todos: list[dict]


class LegacyFlow(BaseModel):
    from_dept: str
    to_dept: str
    remark: str = ""


class LegacyBlock(BaseModel):
    reason: str
    agent: str = "system"


class LegacyDone(BaseModel):
    output: str = ""
    summary: str = ""
    agent: str = "system"


@router.post("/by-legacy/{legacy_id}/transition")
async def legacy_transition(
    legacy_id: str,
    body: LegacyTransition,
    db: AsyncSession = Depends(get_db),
):
    task = await _find_by_legacy_id(db, legacy_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Legacy task not found: {legacy_id}")

    bus = await get_event_bus()
    svc = TaskService(db, bus)
    try:
        new_state = TaskState(body.new_state)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid state: {body.new_state}")

    try:
        t = await svc.transition_state_legacy(task.id, new_state, body.agent, body.reason)
        return {"task_id": t.id, "state": t.state.value}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/by-legacy/{legacy_id}/progress")
async def legacy_progress(
    legacy_id: str,
    body: LegacyProgress,
    db: AsyncSession = Depends(get_db),
):
    task = await _find_by_legacy_id(db, legacy_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Legacy task not found: {legacy_id}")

    bus = await get_event_bus()
    svc = TaskService(db, bus)
    await svc.add_progress_legacy(
        legacy_id,
        body.agent,
        body.content,
        todos=body.todos,
        tokens=body.tokens,
        cost=body.cost,
        elapsed=body.elapsed,
    )
    return {"message": "ok"}


@router.put("/by-legacy/{legacy_id}/todos")
async def legacy_todos(
    legacy_id: str,
    body: LegacyTodoUpdate,
    db: AsyncSession = Depends(get_db),
):
    task = await _find_by_legacy_id(db, legacy_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Legacy task not found: {legacy_id}")

    bus = await get_event_bus()
    svc = TaskService(db, bus)
    await svc.update_todos_legacy(task.id, body.todos)
    return {"message": "ok"}


@router.post("/by-legacy/{legacy_id}/flow")
async def legacy_flow(
    legacy_id: str,
    body: LegacyFlow,
    db: AsyncSession = Depends(get_db),
):
    task = await _find_by_legacy_id(db, legacy_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Legacy task not found: {legacy_id}")

    bus = await get_event_bus()
    svc = TaskService(db, bus)
    await svc.add_flow_entry(task.id, body.from_dept, body.to_dept, body.remark)
    return {"message": "ok"}


@router.post("/by-legacy/{legacy_id}/block")
async def legacy_block(
    legacy_id: str,
    body: LegacyBlock,
    db: AsyncSession = Depends(get_db),
):
    task = await _find_by_legacy_id(db, legacy_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Legacy task not found: {legacy_id}")

    bus = await get_event_bus()
    svc = TaskService(db, bus)
    await svc.block_task(task.id, body.reason, body.agent)
    return {"message": "ok"}


@router.post("/by-legacy/{legacy_id}/done")
async def legacy_done(
    legacy_id: str,
    body: LegacyDone,
    db: AsyncSession = Depends(get_db),
):
    task = await _find_by_legacy_id(db, legacy_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Legacy task not found: {legacy_id}")

    bus = await get_event_bus()
    svc = TaskService(db, bus)
    await svc.complete_task_legacy(legacy_id, body.output, body.summary, body.agent)
    return {"message": "ok"}


class LegacyArchive(BaseModel):
    archived: bool = True


class LegacySchedulerUpdate(BaseModel):
    scheduler: dict


@router.put("/by-legacy/{legacy_id}/archive")
async def legacy_archive(
    legacy_id: str,
    body: LegacyArchive,
    db: AsyncSession = Depends(get_db),
):
    task = await _find_by_legacy_id(db, legacy_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Legacy task not found: {legacy_id}")
    task.archived = body.archived
    await db.commit()
    return {"message": "ok", "archived": task.archived}


@router.put("/by-legacy/{legacy_id}/scheduler")
async def legacy_scheduler(
    legacy_id: str,
    body: LegacySchedulerUpdate,
    db: AsyncSession = Depends(get_db),
):
    task = await _find_by_legacy_id(db, legacy_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Legacy task not found: {legacy_id}")
    task.scheduler = body.scheduler
    await db.commit()
    return {"message": "ok"}


@router.get("/by-legacy/{legacy_id}")
async def legacy_get(
    legacy_id: str,
    db: AsyncSession = Depends(get_db),
):
    task = await _find_by_legacy_id(db, legacy_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Legacy task not found: {legacy_id}")
    return task.to_dict()


# ── Menxia actions (by-legacy) ──

@router.post("/by-legacy/{legacy_id}/menxia/reject")
async def legacy_menxia_reject(
    legacy_id: str,
    body: MenxiaRejectIn,
    x_actor: str | None = Header(default=None, alias="X-Actor"),
    x_role: str | None = Header(default=None, alias="X-Role"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
):
    task = await _find_by_legacy_id(db, legacy_id)
    if not task:
        return _err(status_code=404, code="NOT_FOUND", message=f"Legacy task not found: {legacy_id}")

    req_id = body.client_request_id or idempotency_key or str(uuid.uuid4())
    status_before = task.state.value if task.state else ""

    if task.state in {TaskState.Done, TaskState.Cancelled} or task.archived:
        return _err(status_code=409, code="TERMINAL_STATE", message="Task is terminal/archived; action not allowed", request_id=req_id, status_before=status_before, allowed_transitions=[])

    if status_before != TaskState.Menxia.value:
        return _err(status_code=409, code="INVALID_STATE", message="REJECT only allowed when status==Menxia", request_id=req_id, status_before=status_before)

    if (x_role or "").lower() != "menxia":
        return _err(status_code=403, code="MISSING_ROLES", message="Missing role: Menxia", request_id=req_id, status_before=status_before, missing_roles=["Menxia"])

    if not x_actor:
        return _err(status_code=403, code="MISSING_ACTOR", message="Missing actor header: X-Actor", request_id=req_id, status_before=status_before)

    if task.official and task.official != x_actor:
        return _err(status_code=403, code="NOT_REVIEW_ASSIGNEE", message="Only current review assignee can reject", request_id=req_id, status_before=status_before)

    mx = task.scheduler.get("menxia") if isinstance(task.scheduler, dict) else None
    if not isinstance(mx, dict):
        mx = {"review_round": 0, "reviews": [], "audit": [], "idem": {}}
        task.scheduler = {**(task.scheduler or {}), "menxia": mx}

    if req_id in (mx.get("idem") or {}):
        return mx["idem"][req_id]

    if mx.get("forced_approved_round3") is True:
        return _err(status_code=409, code="ROUND3_FORCED_APPROVAL", message="Round3 forced approval; reject is no longer allowed", request_id=req_id, status_before=status_before)

    mx["review_round"] = int(mx.get("review_round") or 0) + 1
    round_no = mx["review_round"]
    created_at = datetime.utcnow().isoformat() + "Z"

    mx["reviews"] = [
        *(mx.get("reviews") or []),
        {
            "task_id": str(task.id),
            "legacy_id": legacy_id,
            "round": round_no,
            "decision": "Rejected",
            "reason": body.reason,
            "opinion": body.opinion,
            "actor": x_actor,
            "role": "Menxia",
            "created_at": created_at,
            "request_id": req_id,
        },
    ]
    mx["audit"] = [
        *(mx.get("audit") or []),
        {
            "action": "reject",
            "actor": x_actor,
            "role": "Menxia",
            "round": round_no,
            "request_id": req_id,
            "created_at": created_at,
        },
    ]

    bus = await get_event_bus()
    svc = TaskService(db, bus)
    try:
        await svc.transition_state_legacy(legacy_id, TaskState.Zhongshu, agent="menxia", reason=body.reason)
    except Exception as e:
        return _err(status_code=409, code="TRANSITION_FAILED", message=str(e), request_id=req_id, status_before=status_before)

    resp = {
        "task_id": str(task.id),
        "legacy_id": legacy_id,
        "status_before": status_before,
        "status_after": TaskState.Zhongshu.value,
        "review_round": mx["review_round"],
        "requestId": req_id,
    }
    mx.setdefault("idem", {})[req_id] = resp
    await db.commit()
    return resp


@router.post("/by-legacy/{legacy_id}/menxia/resubmit")
async def legacy_menxia_resubmit(
    legacy_id: str,
    body: MenxiaResubmitIn,
    x_actor: str | None = Header(default=None, alias="X-Actor"),
    x_role: str | None = Header(default=None, alias="X-Role"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
):
    task = await _find_by_legacy_id(db, legacy_id)
    if not task:
        return _err(status_code=404, code="NOT_FOUND", message=f"Legacy task not found: {legacy_id}")

    req_id = body.client_request_id or idempotency_key or str(uuid.uuid4())
    status_before = task.state.value if task.state else ""

    if status_before != TaskState.Zhongshu.value:
        return _err(status_code=409, code="INVALID_STATE", message="RESUBMIT only allowed when status==Zhongshu", request_id=req_id, status_before=status_before)

    if (x_role or "").lower() != "zhongshu":
        return _err(status_code=403, code="MISSING_ROLES", message="Missing role: Zhongshu", request_id=req_id, status_before=status_before, missing_roles=["Zhongshu"])

    mx = task.scheduler.get("menxia") if isinstance(task.scheduler, dict) else None
    if not isinstance(mx, dict):
        mx = {"review_round": 0, "reviews": [], "audit": [], "idem": {}}
        task.scheduler = {**(task.scheduler or {}), "menxia": mx}

    if req_id in (mx.get("idem") or {}):
        return mx["idem"][req_id]

    bus = await get_event_bus()
    svc = TaskService(db, bus)

    if int(mx.get("review_round") or 0) == 3:
        mx["forced_approved_round3"] = True
        created_at = datetime.utcnow().isoformat() + "Z"
        mx["audit"] = [
            *(mx.get("audit") or []),
            {"action": "auto_approve_round3", "actor": "system", "role": "system", "round": 3, "request_id": req_id, "created_at": created_at},
        ]
        mx["reviews"] = [
            *(mx.get("reviews") or []),
            {"task_id": str(task.id), "legacy_id": legacy_id, "round": 3, "decision": "Approved", "reason": "auto_approve_round3", "opinion": body.summary_of_changes, "actor": "system", "role": "system", "created_at": created_at, "request_id": req_id},
        ]
        await svc.transition_state_legacy(legacy_id, TaskState.Assigned, agent="system", reason="auto_approve_round3")
        resp = {"task_id": str(task.id), "legacy_id": legacy_id, "status_before": status_before, "status_after": TaskState.Assigned.value, "action": "auto_approve_round3", "review_round": mx["review_round"], "requestId": req_id}
        mx.setdefault("idem", {})[req_id] = resp
        await db.commit()
        return resp

    await svc.transition_state_legacy(legacy_id, TaskState.Menxia, agent="zhongshu", reason=body.summary_of_changes or "resubmit")
    mx["audit"] = [
        *(mx.get("audit") or []),
        {"action": "resubmit", "actor": x_actor or "unknown", "role": "Zhongshu", "round": int(mx.get("review_round") or 0), "request_id": req_id, "created_at": datetime.utcnow().isoformat() + "Z"},
    ]
    resp = {"task_id": str(task.id), "legacy_id": legacy_id, "status_before": status_before, "status_after": TaskState.Menxia.value, "review_round": mx["review_round"], "requestId": req_id}
    mx.setdefault("idem", {})[req_id] = resp
    await db.commit()
    return resp


@router.post("/by-legacy/{legacy_id}/menxia/rollback")
async def legacy_menxia_rollback(
    legacy_id: str,
    body: MenxiaRollbackIn,
    x_role: str | None = Header(default=None, alias="X-Role"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
):
    task = await _find_by_legacy_id(db, legacy_id)
    if not task:
        return _err(status_code=404, code="NOT_FOUND", message=f"Legacy task not found: {legacy_id}")

    req_id = body.client_request_id or idempotency_key or str(uuid.uuid4())
    status_before = task.state.value if task.state else ""

    if status_before != TaskState.Menxia.value:
        return _err(status_code=409, code="INVALID_STATE", message="ROLLBACK only allowed when status==Menxia", request_id=req_id, status_before=status_before)

    if (x_role or "").lower() != "menxia":
        return _err(status_code=403, code="MISSING_ROLES", message="Missing role: Menxia", request_id=req_id, status_before=status_before, missing_roles=["Menxia"])

    bus = await get_event_bus()
    svc = TaskService(db, bus)
    await svc.transition_state_legacy(legacy_id, TaskState.Zhongshu, agent="menxia", reason=body.reason or "rollback")
    return {"task_id": str(task.id), "legacy_id": legacy_id, "status_before": status_before, "status_after": TaskState.Zhongshu.value, "requestId": req_id}


@router.get("/by-legacy/{legacy_id}/menxia/reviews")
async def legacy_menxia_reviews(
    legacy_id: str,
    round: int | None = Query(default=None),
    actor: str | None = Query(default=None),
    decision: str | None = Query(default=None),
    timeRange: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    task = await _find_by_legacy_id(db, legacy_id)
    if not task:
        return _err(status_code=404, code="NOT_FOUND", message=f"Legacy task not found: {legacy_id}")

    mx = task.scheduler.get("menxia") if isinstance(task.scheduler, dict) else {}
    reviews = list((mx or {}).get("reviews") or [])

    if round is not None:
        reviews = [r for r in reviews if int(r.get("round") or 0) == int(round)]
    if actor:
        reviews = [r for r in reviews if (r.get("actor") or "") == actor]
    if decision:
        reviews = [r for r in reviews if (r.get("decision") or "").lower() == decision.lower()]
    if timeRange and "," in timeRange:
        start, end = timeRange.split(",", 1)
        start = start.strip()
        end = end.strip()
        if start:
            reviews = [r for r in reviews if (r.get("created_at") or "") >= start]
        if end:
            reviews = [r for r in reviews if (r.get("created_at") or "") <= end]

    return {"task_id": str(task.id), "legacy_id": legacy_id, "review_round": int((mx or {}).get("review_round") or 0), "reviews": reviews}
