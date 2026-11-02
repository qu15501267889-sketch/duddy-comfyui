"""计划状态机 —— 这个项目的灵魂。

核心不变式
----------
1. 只有 `proposed` 的计划可以变成 `approved`，而 approve 必须持有 token。
2. token 只在 WebSocket 事件里出现，HTTP 响应里永远没有。
3. 只有 `approved` / `applying` 的计划可以执行步骤。
4. 每个步骤执行前都会被记录 cursor，重复执行同一 index 会被拒绝（防重放）。
5. 任何绕过路径（AI 自己 approve、跳步、重放）都返回 403 而不是"尽力而为"。

状态流转
--------
    proposed ──approve──> approved ──step──> applying ──step…──> done
        │                              │
        ├──reject──> rejected          ├──error──> failed ──retry──> applying
        └──ttl─────> expired           └──abort──> aborted
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from . import security
from .bus import bus

logger = logging.getLogger("duddy.plan")

PLAN_TTL_SEC = 1800
MAX_STEPS = 200
MAX_STEP_TITLE = 120

ALLOWED_KINDS = {
    "create_node",
    "connect",
    "disconnect",
    "set_widget",
    "move_node",
    "delete_node",
    "group",
    "note",
    "execute",
}

_VALID_TRANSITIONS: Dict[str, set] = {
    "proposed": {"approved", "rejected", "expired"},
    "approved": {"applying", "expired", "aborted"},
    "applying": {"applying", "done", "failed", "aborted"},
    "failed": {"applying", "aborted"},
    "done": set(),
    "rejected": set(),
    "aborted": set(),
    "expired": set(),
}


class PlanError(Exception):
    """业务级错误，会被路由层翻译成 4xx。extra 会并入响应体。"""

    def __init__(
        self,
        message: str,
        code: int = 400,
        kind: str = "bad_request",
        **extra: Any,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.kind = kind
        self.extra = extra


class Plan:
    def __init__(
        self,
        plan_id: str,
        title: str,
        summary: str,
        steps: List[Dict[str, Any]],
        notes: str = "",
        client_id: Optional[str] = None,
    ) -> None:
        now = round(time.time(), 3)
        self.id = plan_id
        self.title = title
        self.summary = summary
        self.notes = notes
        self.steps = steps
        self.client_id = client_id
        self.state = "proposed"
        self.cursor = 0
        self.results: List[Dict[str, Any]] = []
        self.created_at = now
        self.updated_at = now
        self.approved_at: Optional[float] = None
        self.approved_by: Optional[str] = None
        self.finished_at: Optional[float] = None
        self.reject_reason: Optional[str] = None
        self.error: Optional[str] = None

    # ----------------------------------------------------------------- 视图
    def public(self) -> Dict[str, Any]:
        """给 AI / HTTP 看的视图：绝对不含 token。"""
        return {
            "plan_id": self.id,
            "title": self.title,
            "summary": self.summary,
            "notes": self.notes,
            "state": self.state,
            "step_count": len(self.steps),
            "cursor": self.cursor,
            "steps": self.steps,
            "results": self.results,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "approved_at": self.approved_at,
            "approved_by": self.approved_by,
            "finished_at": self.finished_at,
            "reject_reason": self.reject_reason,
            "error": self.error,
            "can_apply": self.state in ("approved", "applying"),
        }

    def narration(self) -> str:
        """给人类读的纯文本摘要（前端弹窗 & MCP 回显用）。"""
        lines = [f"【{self.title}】{self.summary}"]
        if self.notes:
            lines.append(f"备注：{self.notes}")
        lines.append(f"共 {len(self.steps)} 步：")
        for i, s in enumerate(self.steps, 1):
            lines.append(f"  {i}. {s.get('title') or s.get('kind')}")
        return "\n".join(lines)

    # ------------------------------------------------------------- 状态迁移
    def transition(self, to_state: str) -> None:
        allowed = _VALID_TRANSITIONS.get(self.state)
        if allowed is None:
            raise PlanError(f"unknown state {self.state}", 500, "internal")
        if to_state not in allowed:
            raise PlanError(
                f"illegal transition {self.state} -> {to_state}",
                409,
                "illegal_transition",
            )
        self.state = to_state
        self.updated_at = round(time.time(), 3)


def _validate_steps(steps: Any) -> List[Dict[str, Any]]:
    if not isinstance(steps, list):
        raise PlanError("steps must be a list", 400, "invalid_steps")
    if not steps:
        raise PlanError("steps is empty — 空计划没有意义", 400, "invalid_steps")
    if len(steps) > MAX_STEPS:
        raise PlanError(f"too many steps (>{MAX_STEPS})", 400, "invalid_steps")

    cleaned: List[Dict[str, Any]] = []
    for idx, raw in enumerate(steps, 1):
        if not isinstance(raw, dict):
            raise PlanError(f"step #{idx} is not an object", 400, "invalid_steps")
        kind = str(raw.get("kind", "")).strip()
        if kind not in ALLOWED_KINDS:
            raise PlanError(
                f"step #{idx} kind '{kind}' not allowed; use one of {sorted(ALLOWED_KINDS)}",
                400,
                "invalid_steps",
            )
        title = str(raw.get("title") or kind)[:MAX_STEP_TITLE]
        args = raw.get("args") or {}
        if not isinstance(args, dict):
            raise PlanError(f"step #{idx} args must be an object", 400, "invalid_steps")
        cleaned.append(
            {
                "index": idx - 1,
                "kind": kind,
                "title": title,
                "why": str(raw.get("why") or ""),
                "args": args,
            }
        )
    return cleaned


class PlanManager:
    def __init__(self) -> None:
        self._plans: Dict[str, Plan] = {}
        self._lock = threading.RLock()
        self._store_dir: Optional[str] = None

    # ------------------------------------------------------------ 持久化
    def set_store_dir(self, path: Optional[str]) -> None:
        self._store_dir = path
        if path:
            try:
                os.makedirs(path, exist_ok=True)
            except Exception:
                self._store_dir = None

    def _persist(self, plan: Plan) -> None:
        if not self._store_dir:
            return
        try:
            path = os.path.join(self._store_dir, f"{plan.id}.json")
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(plan.public(), f, ensure_ascii=False, indent=1)
            os.replace(tmp, path)
        except Exception as e:  # 审计写盘失败不能影响主流程
            logger.debug("persist failed: %s", e)

    # ------------------------------------------------------------ 创建
    def create(
        self,
        title: str,
        summary: str,
        steps: Any,
        notes: str = "",
        client_id: Optional[str] = None,
    ) -> Tuple[Plan, str]:
        cleaned = _validate_steps(steps)
        plan_id = security.new_plan_id()
        plan = Plan(
            plan_id=plan_id,
            title=str(title or "未命名计划")[:120],
            summary=str(summary or "")[:800],
            steps=cleaned,
            notes=str(notes or "")[:800],
            client_id=client_id,
        )
        token = security.vault.issue(plan_id)

        with self._lock:
            self._plans[plan_id] = plan
        self._persist(plan)

        # token 只走这里 → 只到浏览器
        bus.publish(
            "duddy.plan.proposed",
            {
                "plan": plan.public(),
                "narration": plan.narration(),
                "token": token,
                "ttl_sec": PLAN_TTL_SEC,
            },
            sid=client_id,
        )
        logger.info("[duddy] plan proposed %s (%d steps)", plan_id, len(cleaned))
        return plan, token

    # ------------------------------------------------------------ 查询
    def get(self, plan_id: str) -> Plan:
        with self._lock:
            plan = self._plans.get(plan_id)
        if plan is None:
            raise PlanError(f"plan {plan_id} not found", 404, "not_found")
        self._expire_if_needed(plan)
        return plan

    def list_recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            items = sorted(self._plans.values(), key=lambda p: p.created_at, reverse=True)
        out = []
        for p in items[:limit]:
            self._expire_if_needed(p)
            out.append(
                {
                    "plan_id": p.id,
                    "title": p.title,
                    "state": p.state,
                    "step_count": len(p.steps),
                    "cursor": p.cursor,
                    "created_at": p.created_at,
                }
            )
        return out

    def _expire_if_needed(self, plan: Plan) -> None:
        if plan.state in ("done", "rejected", "aborted", "expired"):
            return
        if time.time() - plan.created_at > PLAN_TTL_SEC:
            try:
                plan.transition("expired")
                security.vault.revoke(plan.id)
                bus.publish("duddy.plan.expired", {"plan_id": plan.id})
                self._persist(plan)
            except PlanError:
                pass

    # ------------------------------------------------------------ 审批
    def approve(self, plan_id: str, token: str, by: str = "ui") -> Plan:
        plan = self.get(plan_id)
        if plan.state != "proposed":
            raise PlanError(
                f"plan is '{plan.state}', only 'proposed' can be approved",
                409,
                "wrong_state",
            )
        if not security.vault.verify(plan_id, token):
            raise PlanError(
                "invalid or expired approval token",
                403,
                "forbidden",
            )
        plan.transition("approved")
        plan.approved_at = plan.updated_at
        plan.approved_by = by
        security.vault.revoke(plan_id)
        self._persist(plan)
        bus.publish("duddy.plan.approved", {"plan_id": plan.id, "by": by})
        logger.info("[duddy] plan approved %s by=%s", plan.id, by)
        return plan

    def auto_approve(self, plan_id: str, by: str = "auto") -> Plan:
        """免审批模式：计划一创建就视为已批准，不经过弹窗等待。

        这是用户明确要求的「全权接管」。闸门代码全部保留，
        环境变量 DUDDY_REQUIRE_APPROVAL=1 即可恢复人工审批。
        """
        plan = self.get(plan_id)
        if plan.state != "proposed":
            raise PlanError(f"plan is '{plan.state}'", 409, "wrong_state")
        plan.transition("approved")
        plan.approved_at = plan.updated_at
        plan.approved_by = by
        security.vault.revoke(plan_id)
        self._persist(plan)
        bus.publish("duddy.plan.approved", {"plan_id": plan.id, "by": by, "auto": True})
        logger.info("[duddy] plan auto-approved %s", plan.id)
        return plan

    def reject(self, plan_id: str, reason: str = "", token: Optional[str] = None) -> Plan:
        plan = self.get(plan_id)
        if plan.state != "proposed":
            raise PlanError(f"plan is '{plan.state}'", 409, "wrong_state")
        if token is not None and not security.vault.verify(plan_id, token):
            raise PlanError("invalid approval token", 403, "forbidden")
        plan.transition("rejected")
        plan.reject_reason = reason or "用户拒绝"
        security.vault.revoke(plan_id)
        self._persist(plan)
        bus.publish("duddy.plan.rejected", {"plan_id": plan.id, "reason": plan.reject_reason})
        logger.info("[duddy] plan rejected %s", plan.id)
        return plan

    # ------------------------------------------------------------ 执行
    def peek_next(self, plan_id: str) -> Tuple[int, Dict[str, Any]]:
        plan = self.get(plan_id)
        if plan.state not in ("approved", "applying"):
            raise PlanError(
                f"plan is '{plan.state}'; 必须先获得人类批准才能执行（当前无 approve）",
                403,
                "not_approved",
            )
        if plan.cursor >= len(plan.steps):
            raise PlanError("plan has no remaining steps", 409, "exhausted")
        return plan.cursor, plan.steps[plan.cursor]

    def begin_step(self, plan_id: str, expected_index: Optional[int] = None) -> Tuple[Plan, int, Dict[str, Any]]:
        """锁定并返回下一个待执行步骤。返回 (plan, index, step)。"""
        plan = self.get(plan_id)
        if plan.state not in ("approved", "applying"):
            raise PlanError(
                f"plan is '{plan.state}'; 必须先获得人类批准才能执行（当前无 approve）",
                403,
                "not_approved",
            )
        if expected_index is not None and expected_index != plan.cursor:
            raise PlanError(
                f"step out of order: expected {plan.cursor}, got {expected_index}",
                409,
                "out_of_order",
            )
        if plan.cursor >= len(plan.steps):
            raise PlanError("plan has no remaining steps", 409, "exhausted")

        if plan.state == "approved":
            plan.transition("applying")
        idx = plan.cursor
        step = plan.steps[idx]
        self._persist(plan)
        return plan, idx, step

    def record_result(
        self,
        plan_id: str,
        index: int,
        ok: bool,
        detail: Any = None,
    ) -> Plan:
        plan = self.get(plan_id)
        if index != plan.cursor:
            raise PlanError(
                f"result for stale step {index} (cursor={plan.cursor})",
                409,
                "stale_result",
            )
        plan.results.append(
            {
                "index": index,
                "ok": bool(ok),
                "detail": detail,
                "ts": round(time.time(), 3),
            }
        )
        if ok:
            plan.cursor = index + 1
            if plan.cursor >= len(plan.steps):
                plan.transition("done")
                plan.finished_at = plan.updated_at
                bus.publish("duddy.plan.done", {"plan_id": plan.id})
        else:
            plan.state = "failed"
            plan.error = str(detail)[:500] if detail else "step failed"
            plan.updated_at = round(time.time(), 3)
            bus.publish("duddy.plan.failed", {"plan_id": plan.id, "index": index, "error": plan.error})
        self._persist(plan)
        return plan

    def retry(self, plan_id: str) -> Plan:
        plan = self.get(plan_id)
        if plan.state != "failed":
            raise PlanError(f"plan is '{plan.state}', not failed", 409, "wrong_state")
        plan.transition("applying")
        plan.error = None
        self._persist(plan)
        return plan

    def abort(self, plan_id: str, reason: str = "") -> Plan:
        plan = self.get(plan_id)
        if plan.state in ("done", "rejected", "aborted", "expired"):
            raise PlanError(f"plan already {plan.state}", 409, "wrong_state")
        plan.transition("aborted")
        plan.error = reason or "aborted"
        plan.finished_at = plan.updated_at
        self._persist(plan)
        bus.publish("duddy.plan.aborted", {"plan_id": plan.id, "reason": plan.error})
        return plan

    def gc(self, keep: int = 50) -> int:
        with self._lock:
            items = sorted(self._plans.values(), key=lambda p: p.created_at, reverse=True)
            drop = items[keep:]
            for p in drop:
                self._plans.pop(p.id, None)
        return len(drop)


plan_manager = PlanManager()
