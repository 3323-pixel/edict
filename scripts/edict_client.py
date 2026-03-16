#!/usr/bin/env python3
"""EDICT HTTP Client — 供 kanban_update.py 和其他脚本调用。

使用标准库 urllib（零依赖），自动识别 JJC-* legacy ID 选择路由。
"""
import json
import os
import urllib.error
import urllib.request


class EdictClient:
    """EDICT Backend HTTP 客户端（同步，纯标准库）。"""

    def __init__(self):
        self.base_url = os.getenv("EDICT_API_URL", "http://localhost:8000").rstrip("/")
        self.timeout = int(os.getenv("EDICT_API_TIMEOUT", "30"))

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _request(self, method: str, path: str, data: dict | None = None) -> dict:
        url = self._url(path)
        body = json.dumps(data, ensure_ascii=False).encode() if data is not None else None
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body_text = e.read().decode()[:300]
            print(f"[EdictClient] {method} {path} failed: {e.code} {body_text}", flush=True)
            raise
        except urllib.error.URLError as e:
            print(f"[EdictClient] {method} {path} connection error: {e.reason}", flush=True)
            raise

    def _get(self, path: str) -> dict:
        return self._request("GET", path)

    def _post(self, path: str, data: dict) -> dict:
        return self._request("POST", path, data)

    def _put(self, path: str, data: dict) -> dict:
        return self._request("PUT", path, data)

    def _legacy_path(self, legacy_id: str, suffix: str = "") -> str:
        return f"/api/tasks/by-legacy/{legacy_id}{suffix}"

    # ── 公开 API ──

    def create_task(self, legacy_id: str, title: str, org: str, creator: str,
                    state: str = "Taizi", official: str = "", remark: str = "") -> dict:
        """创建任务（使用 JJC-* ID）。"""
        return self._post("/api/tasks/legacy", {
            "legacy_id": legacy_id,
            "title": title,
            "state": state,
            "org": org,
            "official": official,
            "remark": remark or f"下旨：{title}",
        })

    def get_task(self, legacy_id: str) -> dict | None:
        """按 legacy ID 获取任务详情。不存在返回 None（不打日志）。"""
        url = self._url(self._legacy_path(legacy_id))
        req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise
        except Exception:
            return None

    def transition(self, legacy_id: str, new_state: str, agent: str, reason: str = "") -> dict:
        """执行状态流转。"""
        return self._post(self._legacy_path(legacy_id, "/transition"), {
            "new_state": new_state,
            "agent": agent,
            "reason": reason,
        })

    def add_flow(self, legacy_id: str, from_dept: str, to_dept: str, remark: str) -> dict:
        """追加流转记录。"""
        return self._post(self._legacy_path(legacy_id, "/flow"), {
            "from_dept": from_dept,
            "to_dept": to_dept,
            "remark": remark,
        })

    def add_progress(self, legacy_id: str, agent: str, content: str,
                     todos: list | None = None, tokens: int = 0,
                     cost: float = 0.0, elapsed: int = 0) -> dict:
        """添加进展记录。"""
        data: dict = {"agent": agent, "content": content}
        if todos is not None:
            data["todos"] = todos
        if tokens:
            data["tokens"] = tokens
        if cost:
            data["cost"] = cost
        if elapsed:
            data["elapsed"] = elapsed
        return self._post(self._legacy_path(legacy_id, "/progress"), data)

    def update_todos(self, legacy_id: str, todos: list) -> dict:
        """更新 TODO 清单。"""
        return self._put(self._legacy_path(legacy_id, "/todos"), {"todos": todos})

    def block(self, legacy_id: str, reason: str, agent: str) -> dict:
        """将任务置为 Blocked。"""
        return self._post(self._legacy_path(legacy_id, "/block"), {
            "reason": reason,
            "agent": agent,
        })

    def done(self, legacy_id: str, output: str, summary: str, agent: str) -> dict:
        """标记任务完成。"""
        return self._post(self._legacy_path(legacy_id, "/done"), {
            "output": output,
            "summary": summary,
            "agent": agent,
        })

    def close(self):
        pass  # urllib 无需显式关闭

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass
