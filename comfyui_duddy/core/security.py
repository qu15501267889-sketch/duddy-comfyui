"""安全原语：审批 token、路径白名单、目录探测。

这是整个项目的地基之一。审批 token 的核心约束：

    token 在 create_plan 时生成，
    只通过 WebSocket 下发给「前端浏览器」，
    绝不出现在任何 HTTP 响应里。

AI 客户端（MCP）不持有浏览器那条 WebSocket，物理上拿不到 token，
因此无法自己调用 /duddy/plan/{id}/approve 绕过人类确认。

诚实说明边界：这防的是「AI 偷懒跳过流程」，
防不了「AI 主动攻击本机」——本机 Python 本身就是完全权限，
任何声称能防住这点的设计都是吹牛。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from typing import Dict, Optional

APPROVAL_TTL = 1800


def new_token(nbytes: int = 24) -> str:
    return secrets.token_urlsafe(nbytes)


def new_plan_id() -> str:
    return "plan_" + secrets.token_hex(8)


def constant_time_eq(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


class PathGuard:
    """限制文件类操作只能落在白名单根目录内（M5 文件监控会用到）。"""

    def __init__(self) -> None:
        self.roots: list[str] = []

    def add_root(self, path: Optional[str]) -> None:
        if not path:
            return
        p = os.path.realpath(os.path.expanduser(path))
        if os.path.isdir(p) and p not in self.roots:
            self.roots.append(p)

    def is_allowed(self, path: str) -> bool:
        if not self.roots:
            return False
        target = os.path.realpath(os.path.expanduser(path))
        for root in self.roots:
            if target == root or target.startswith(root + os.sep):
                return True
        return False

    def check(self, path: str) -> str:
        if not self.is_allowed(path):
            raise PermissionError(f"path outside allowed roots: {path}")
        return path

    def snapshot(self) -> Dict[str, object]:
        return {"roots": list(self.roots), "count": len(self.roots)}


guard = PathGuard()


def detect_comfy_roots() -> Dict[str, str]:
    """尽力探测 ComfyUI 各目录。拿不到就返回空，不报错。"""
    found: Dict[str, str] = {}
    try:
        import folder_paths  # type: ignore

        for attr, key in (
            ("models_dir", "models"),
            ("base_path", "base"),
            ("custom_nodes_dir", "custom_nodes"),
            ("output_directory", "output"),
            ("input_directory", "input"),
        ):
            val = getattr(folder_paths, attr, None)
            if val:
                found[key] = os.path.realpath(str(val))
    except Exception:
        pass

    if "base" not in found:
        # comfyui_duddy/ 的上一级就是 ComfyUI/
        here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if os.path.isdir(os.path.join(here, "comfy")):
            found["base"] = os.path.realpath(here)

    base = found.get("base")
    if base:
        for key, sub in (
            ("custom_nodes", "custom_nodes"),
            ("models", "models"),
            ("input", "input"),
            ("output", "output"),
            ("user", "user"),
        ):
            if key not in found:
                cand = os.path.join(base, sub)
                if os.path.isdir(cand):
                    found[key] = os.path.realpath(cand)
    return found


def init_guard() -> Dict[str, str]:
    roots = detect_comfy_roots()
    for p in roots.values():
        guard.add_root(p)
    return roots


class ApprovalVault:
    """内存中的 plan_id -> token 指纹映射。进程重启即失效（刻意的）。"""

    def __init__(self) -> None:
        self._items: Dict[str, Dict[str, object]] = {}

    def issue(self, plan_id: str) -> str:
        token = new_token()
        self._items[plan_id] = {"fp": token_fingerprint(token), "issued_at": time.time()}
        return token

    def verify(self, plan_id: str, token: str) -> bool:
        item = self._items.get(plan_id)
        if not item:
            return False
        if time.time() - float(item["issued_at"]) > APPROVAL_TTL:  # type: ignore[arg-type]
            self._items.pop(plan_id, None)
            return False
        return constant_time_eq(str(item["fp"]), token_fingerprint(token))

    def revoke(self, plan_id: str) -> None:
        self._items.pop(plan_id, None)

    def __len__(self) -> int:
        return len(self._items)


vault = ApprovalVault()
