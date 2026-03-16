# Issue Draft: dashboard EDICT 补偿同步缺少幂等处理

## Title

dashboard: make EDICT compensation sync idempotent for timed-out task creation

## Summary

`dashboard/server.py` 在 `handle_create_task()` 中调用 `POST /api/tasks/legacy` 失败时，会把任务标记为 `_edict_synced=false`。后续 `_sync_edict_states_to_json()` 会对这些任务执行补偿同步，但当前补偿逻辑仍然直接重复 `POST /api/tasks/legacy`，没有做幂等检查。

如果第一次创建实际上已经成功写入 EDICT，只是 dashboard 侧因为超时或瞬时网络错误没有拿到响应，那么后续补偿会不断尝试重复创建。对 EDICT 来说这通常会返回“已存在”或等价错误，dashboard 又会把它当成失败，因此 `_edict_synced` 无法清除，任务会长期处于“假未同步”状态并持续刷错误日志。

## Impact

- 任务实际已存在于 EDICT，但 dashboard 长期认为它未同步
- 每次同步周期都重复发创建请求
- 日志持续出现误导性错误
- 后续如果围绕 `_edict_synced` 做更多流程控制，会放大这个状态错乱

## Reproduction

1. 从 dashboard 创建任务
2. 让 `POST /api/tasks/legacy` 在服务端成功，但客户端超时或连接中断
3. dashboard 将任务标记为 `_edict_synced=false`
4. 下一次 `_sync_edict_states_to_json()` 触发补偿同步
5. 重复 `POST /api/tasks/legacy` 返回“已存在”或等价错误
6. `_edict_synced` 仍然保留，之后每次同步都会重复此过程

## Expected

- 补偿逻辑应当是幂等的
- 如果任务已经存在于 EDICT，则应视为补偿成功
- `_edict_synced` 应被清除，不应持续重试

## Actual

- 当前补偿逻辑直接重复创建，不区分“真的不存在”和“已存在但上次响应丢失”
- 导致任务长期处于未同步状态

## Suggested Fix

可选方案：

1. 补偿前先调用 `GET /api/tasks/by-legacy/{id}`，存在则直接清除 `_edict_synced`
2. 或者把 EDICT 返回的“已存在”错误识别为成功语义
3. 更进一步，把 `/api/tasks/legacy` 设计为天然幂等的 upsert 风格接口

## Relevant Code

- `dashboard/server.py`
- 补偿逻辑：`_sync_edict_states_to_json()`
- 创建标记逻辑：`handle_create_task()`

