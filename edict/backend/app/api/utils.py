"""公共 API 工具 — 消除 legacy.py 和 tasks.py 的重复定义。"""

from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


# ── 门下省审批模型 ──

class MenxiaRejectIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
    opinion: str | None = Field(default=None, max_length=2000)
    client_request_id: str | None = Field(default=None, max_length=128)


class MenxiaResubmitIn(BaseModel):
    summary_of_changes: str | None = Field(default=None, max_length=2000)
    client_request_id: str | None = Field(default=None, max_length=128)


class MenxiaRollbackIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)
    client_request_id: str | None = Field(default=None, max_length=128)


# ── 错误响应构造 ──

def error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    request_id: str | None = None,
    status_before: str | None = None,
    status_after: str | None = None,
    allowed_transitions: list[str] | None = None,
    missing_scopes: list[str] | None = None,
    missing_roles: list[str] | None = None,
):
    body = {
        "code": code,
        "message": message,
        "requestId": request_id,
        "status_before": status_before,
    }
    if status_after is not None:
        body["status_after"] = status_after
    if allowed_transitions is not None:
        body["allowed_transitions"] = allowed_transitions
    if missing_scopes is not None:
        body["missing_scopes"] = missing_scopes
    if missing_roles is not None:
        body["missing_roles"] = missing_roles
    return JSONResponse(status_code=status_code, content=body)
