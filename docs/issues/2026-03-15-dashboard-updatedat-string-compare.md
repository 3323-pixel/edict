# Issue Draft: dashboard 同步逻辑使用字符串比较 `updatedAt`

## Title

dashboard: compare parsed datetimes instead of raw updatedAt strings during EDICT sync

## Summary

`dashboard/server.py` 在 `_sync_edict_states_to_json()` 中使用下面的逻辑判断同状态内是否有新进展：

`progress_changed = edict_updated and edict_updated > (json_updated or '')`

这里直接比较的是时间字符串，而不是解析后的时间。当前系统内两边常见的时间格式并不完全一致：

- dashboard JSON 常见：`2026-03-15T06:00:00Z`
- EDICT API 常见：`2026-03-15T06:00:00+00:00`

字符串顺序并不可靠地代表真实时间顺序，因此可能出现：

- 明明有新进展，但没被识别出来
- 实际没有更新，却被误判成更新

这会直接影响 scheduler 的停滞检测和重试/升级/回滚逻辑。

## Impact

- `stallSince` 可能无法在真实进展时被重置
- scheduler 仍可能误判活跃任务为停滞
- 也可能反过来掩盖真实停滞

## Reproduction

1. dashboard 中某任务 `updatedAt = 2026-03-15T06:00:00Z`
2. EDICT 返回相同或相近时间，但格式为 `2026-03-15T06:00:00+00:00`
3. 直接用字符串比较两者
4. 比较结果不稳定，依赖字符串字面顺序，而非真实时间先后

## Expected

- 先把两边时间都解析成 `datetime`
- 再做时间先后比较

## Actual

- 当前逻辑直接比较原始字符串

## Suggested Fix

1. 复用已有 `_parse_iso()` 或新增统一的时间解析 helper
2. 解析 `edict_updated` 和 `json_updated`
3. 仅在解析成功且 EDICT 时间真正更晚时，才认定 `progress_changed`
4. 解析失败时保守降级，并记录 debug 日志

## Relevant Code

- `dashboard/server.py`
- 函数：`_sync_edict_states_to_json()`

