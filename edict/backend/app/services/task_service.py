"""任务服务层 — CRUD + 状态机逻辑。

所有业务规则集中在此：
- 创建任务 → 发布 task.created 事件
- 状态流转 → 校验合法性 + 发布状态事件
- 查询、过滤、聚合
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.task import Task, TaskState, STATE_TRANSITIONS, TERMINAL_STATES
from .event_bus import (
    EventBus,
    TOPIC_TASK_CREATED,
    TOPIC_TASK_STATUS,
    TOPIC_TASK_COMPLETED,
    TOPIC_TASK_DISPATCH,
)

log = logging.getLogger("edict.task_service")


class TaskService:
    def __init__(self, db: AsyncSession, event_bus: EventBus):
        self.db = db
        self.bus = event_bus

    # ── 创建 ──

    async def create_task(
        self,
        title: str,
        description: str = "",
        priority: str = "中",
        assignee_org: str | None = None,
        creator: str = "emperor",
        tags: list[str] | None = None,
        initial_state: TaskState = TaskState.Taizi,
        meta: dict | None = None,
    ) -> Task:
        """创建任务并发布 task.created 事件。"""
        now = datetime.now(timezone.utc)
        trace_id = str(uuid.uuid4())
        task_id = uuid.uuid4().hex

        task = Task(
            id=task_id,
            title=title,
            priority=priority,
            state=initial_state,
            org=assignee_org or "太子",
            official=creator,
            now=description,
            flow_log=[
                {
                    "at": now.isoformat(),
                    "from": "系统",
                    "to": assignee_org or "太子",
                    "remark": description or "任务创建",
                }
            ],
            progress_log=[],
            todos=[],
            scheduler=meta or {},
        )
        self.db.add(task)
        await self.db.flush()

        # 发布事件
        await self.bus.publish(
            topic=TOPIC_TASK_CREATED,
            trace_id=trace_id,
            event_type="task.created",
            producer="task_service",
            payload={
                "task_id": task.id,
                "title": title,
                "state": initial_state.value,
                "priority": priority,
                "assignee_org": assignee_org,
            },
        )

        await self.db.commit()
        log.info(f"Created task {task.id}: {title} [{initial_state.value}]")
        return task

    # ── 状态流转 ──

    async def transition_state(
        self,
        task_id: uuid.UUID,
        new_state: TaskState,
        agent: str = "system",
        reason: str = "",
    ) -> Task:
        """执行状态流转，校验合法性。"""
        task = await self._get_task(task_id)
        return await self._transition_task(task, str(task_id), new_state, agent, reason)

    # ── 派发请求 ──

    async def request_dispatch(
        self,
        task_id: uuid.UUID,
        target_agent: str,
        message: str = "",
    ):
        """发布 task.dispatch 事件，由 DispatchWorker 消费执行。"""
        task = await self._get_task(task_id)
        await self.bus.publish(
            topic=TOPIC_TASK_DISPATCH,
            trace_id=str(uuid.uuid4()),
            event_type="task.dispatch.request",
            producer="task_service",
            payload={
                "task_id": str(task_id),
                "agent": target_agent,
                "message": message,
                "state": task.state.value,
            },
        )
        log.info(f"Dispatch requested: task {task_id} → agent {target_agent}")

    # ── 进度/备注更新 ──

    async def add_progress(
        self,
        task_id: uuid.UUID,
        agent: str,
        content: str,
    ) -> Task:
        task = await self._get_task(task_id)
        entry = {
            "agent": agent,
            "content": content,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        if task.progress_log is None:
            task.progress_log = []
        task.progress_log = [*task.progress_log, entry]
        task.updated_at = datetime.now(timezone.utc)
        await self.db.commit()
        return task

    async def update_todos(
        self,
        task_id: uuid.UUID,
        todos: list[dict],
    ) -> Task:
        task = await self._get_task(task_id)
        task.todos = todos
        task.updated_at = datetime.now(timezone.utc)
        await self.db.commit()
        return task

    async def update_scheduler(
        self,
        task_id: uuid.UUID,
        scheduler: dict,
    ) -> Task:
        task = await self._get_task(task_id)
        task.scheduler = scheduler
        task.updated_at = datetime.now(timezone.utc)
        await self.db.commit()
        return task

    # ── 查询 ──

    async def get_task(self, task_id: uuid.UUID) -> Task:
        return await self._get_task(task_id)

    async def list_tasks(
        self,
        state: TaskState | None = None,
        assignee_org: str | None = None,
        priority: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Task]:
        stmt = select(Task)
        conditions = []
        if state is not None:
            conditions.append(Task.state == state)
        if assignee_org is not None:
            conditions.append(Task.org == assignee_org)
        if priority is not None:
            conditions.append(Task.priority == priority)
        if conditions:
            stmt = stmt.where(and_(*conditions))
        stmt = stmt.order_by(Task.created_at.desc()).limit(limit).offset(offset)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_live_status(self) -> dict[str, Any]:
        """生成兼容旧 live_status.json 格式的全局状态。"""
        tasks = await self.list_tasks(limit=200)
        active_tasks = {}
        completed_tasks = {}
        for t in tasks:
            d = t.to_dict()
            if t.state in TERMINAL_STATES:
                completed_tasks[t.id] = d
            else:
                active_tasks[t.id] = d
        return {
            "tasks": active_tasks,
            "completed_tasks": completed_tasks,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    async def count_tasks(self, state: TaskState | None = None) -> int:
        stmt = select(func.count(Task.id))
        if state is not None:
            stmt = stmt.where(Task.state == state)
        result = await self.db.execute(stmt)
        return result.scalar_one()

    # ── Legacy ID 操作（String PK） ──

    async def create_task_legacy(
        self,
        legacy_id: str,
        title: str,
        state: TaskState = TaskState.Taizi,
        org: str = "太子",
        official: str = "",
        remark: str = "",
    ) -> Task:
        """用 JJC-* 风格 ID 创建任务。"""
        now = datetime.now(timezone.utc)
        trace_id = str(uuid.uuid4())
        task = Task(
            id=legacy_id,
            title=title,
            state=state,
            org=org,
            official=official,
            flow_log=[{
                "at": now.isoformat(),
                "from": "皇上",
                "to": org,
                "remark": remark or f"下旨：{title}",
            }],
        )
        self.db.add(task)
        await self.db.flush()

        await self.bus.publish(
            topic=TOPIC_TASK_CREATED,
            trace_id=trace_id,
            event_type="task.created",
            producer="task_service",
            payload={
                "task_id": task.id,
                "title": title,
                "state": state.value,
                "assignee_org": org,
            },
        )

        await self.db.commit()
        log.info(f"Created legacy task {legacy_id}: {title}")
        return task

    async def get_task_by_legacy_id(self, legacy_id: str) -> Task:
        return await self._get_task_by_legacy_id(legacy_id)

    async def transition_state_legacy(
        self,
        legacy_id: str,
        new_state: TaskState,
        agent: str = "system",
        reason: str = "",
    ) -> Task:
        """状态流转（legacy String ID）。"""
        task = await self._get_task_by_legacy_id(legacy_id)
        return await self._transition_task(task, legacy_id, new_state, agent, reason)

    async def add_flow_entry(
        self,
        task_ref: str | uuid.UUID,
        from_dept: str,
        to_dept: str,
        remark: str,
    ) -> Task:
        """追加流转记录（原子操作）。"""
        task = await self._get_task_ref(task_ref)
        entry = {
            "at": datetime.now(timezone.utc).isoformat(),
            "from": from_dept,
            "to": to_dept,
            "remark": remark,
        }
        task.flow_log = [*(task.flow_log or []), entry]
        task.updated_at = datetime.now(timezone.utc)
        await self.db.commit()
        return task

    async def block_task(
        self,
        task_ref: str | uuid.UUID,
        reason: str,
        agent: str,
    ) -> Task:
        """将任务置为 Blocked + 记录 progress。"""
        task = await self._get_task_ref(task_ref)
        old_state = task.state
        task.state = TaskState.Blocked
        task.org = "阻塞"
        task.block = reason
        task.now = reason
        entry = {
            "agent": agent,
            "content": f"任务阻塞: {reason}",
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        task.progress_log = [*(task.progress_log or []), entry]
        task.updated_at = datetime.now(timezone.utc)

        task_id = str(task.id)
        await self.bus.publish(
            topic=TOPIC_TASK_STATUS,
            trace_id=str(uuid.uuid4()),
            event_type="task.state.Blocked",
            producer=agent,
            payload={
                "task_id": task_id,
                "from": old_state.value if old_state else "",
                "to": TaskState.Blocked.value,
                "reason": reason,
            },
        )

        await self.db.commit()
        log.warning(f"Task {task_id} blocked by {agent}: {reason}")
        return task

    async def add_progress_legacy(
        self,
        legacy_id: str,
        agent: str,
        content: str,
        todos: list | None = None,
        tokens: int = 0,
        cost: float = 0.0,
        elapsed: int = 0,
    ) -> Task:
        """添加进展记录（legacy，支持 todos/tokens/cost/elapsed）。"""
        task = await self._get_task_by_legacy_id(legacy_id)
        entry: dict[str, Any] = {
            "agent": agent,
            "content": content,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        if todos is not None:
            entry["todos"] = todos
        if tokens > 0:
            entry["tokens"] = tokens
        if cost > 0:
            entry["cost"] = cost
        if elapsed > 0:
            entry["elapsed"] = elapsed
        task.progress_log = [*(task.progress_log or []), entry]
        task.now = content
        if todos is not None:
            task.todos = todos
        task.updated_at = datetime.now(timezone.utc)
        await self.db.commit()
        return task

    async def update_todos_legacy(self, legacy_id: str, todos: list) -> Task:
        task = await self._get_task_by_legacy_id(legacy_id)
        task.todos = todos
        task.updated_at = datetime.now(timezone.utc)
        await self.db.commit()
        return task

    async def complete_task_legacy(
        self,
        legacy_id: str,
        output: str,
        summary: str,
        agent: str,
    ) -> Task:
        """完成任务（Done 状态）。"""
        task = await self._get_task_by_legacy_id(legacy_id)
        task.state = TaskState.Done
        task.output = output
        task.now = summary or "任务已完成"
        flow_entry = {
            "at": datetime.now(timezone.utc).isoformat(),
            "from": task.org or "执行部门",
            "to": "皇上",
            "remark": f"✅ 完成：{summary or '任务已完成'}",
        }
        task.flow_log = [*(task.flow_log or []), flow_entry]
        task.updated_at = datetime.now(timezone.utc)

        await self.bus.publish(
            topic=TOPIC_TASK_COMPLETED,
            trace_id=str(uuid.uuid4()),
            event_type="task.state.Done",
            producer=agent,
            payload={
                "task_id": legacy_id,
                "from": "Review",
                "to": TaskState.Done.value,
                "reason": task.now,
            },
        )

        await self.db.commit()
        log.info(f"Legacy task {legacy_id} done by {agent}")
        return task

    # ── 内部 ──

    async def _transition_task(
        self,
        task: Task,
        task_id: str,
        new_state: TaskState,
        agent: str,
        reason: str,
    ) -> Task:
        old_state = task.state

        allowed = STATE_TRANSITIONS.get(old_state, set())
        if new_state not in allowed:
            raise ValueError(
                f"Invalid transition: {old_state.value} → {new_state.value}. "
                f"Allowed: {[s.value for s in allowed]}"
            )

        task.state = new_state
        task.updated_at = datetime.now(timezone.utc)

        state_org_map = {
            TaskState.Taizi: "太子",
            TaskState.Zhongshu: "中书省",
            TaskState.Menxia: "门下省",
            TaskState.Assigned: "尚书省",
            TaskState.Review: "尚书省",
            TaskState.Done: "完成",
            TaskState.Blocked: "阻塞",
        }
        if new_state in state_org_map:
            task.org = state_org_map[new_state]
        if reason:
            task.now = reason

        flow_entry = {
            "from": old_state.value,
            "to": new_state.value,
            "agent": agent,
            "reason": reason,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        if task.flow_log is None:
            task.flow_log = []
        task.flow_log = [*task.flow_log, flow_entry]

        topic = TOPIC_TASK_COMPLETED if new_state in TERMINAL_STATES else TOPIC_TASK_STATUS
        await self.bus.publish(
            topic=topic,
            trace_id=str(uuid.uuid4()),
            event_type=f"task.state.{new_state.value}",
            producer=agent,
            payload={
                "task_id": task_id,
                "from": old_state.value,
                "to": new_state.value,
                "reason": reason,
            },
        )

        await self.db.commit()
        log.info(f"Task {task_id} state: {old_state.value} → {new_state.value} by {agent}")
        return task

    async def _get_task_ref(self, task_ref: str | uuid.UUID) -> Task:
        return await self._get_task_by_legacy_id(str(task_ref))

    async def _get_task_by_legacy_id(self, legacy_id: str) -> Task:
        task = await self.db.get(Task, legacy_id)
        if task is None:
            raise ValueError(f"Task not found: {legacy_id}")
        return task

    async def _get_task(self, task_id: uuid.UUID) -> Task:
        task = await self.db.get(Task, task_id)
        if task is None:
            raise ValueError(f"Task not found: {task_id}")
        return task
