"""HTTP 路由。AI 走这里，前端也走这里，但权限不同。

权限模型
--------
- AI（MCP）能调：ping / health / plan / plans / step / retry / abort / events
- AI 不能调：approve / reject（需要 token，token 只在 ws 事件里）

路由在 ComfyUI 启动时挂载，前缀统一 /duddy。
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, Optional

from aiohttp import web

from ..core import security
from ..core.bus import bus
from ..core.plan import PlanError, plan_manager
from ..core.state import state

logger = logging.getLogger("duddy.routes")

P = "/duddy"
WAIT_FRONTEND_SEC = 12.0
POLL_SEC = 0.12

#: 是否需要人工审批。
#:   0（默认）= 全权接管：计划一创建就自动执行，弹窗只是通知 + 停止按钮
#:   1        = 恢复闸门：必须等用户在弹窗里点「同意」，且 token 才有效
REQUIRE_APPROVAL = os.environ.get("DUDDY_REQUIRE_APPROVAL", "0").lower() in (
    "1",
    "true",
    "yes",
    "on",
)

#: 自动执行时每步之间的间隔，留出前端动画时间
AUTO_STEP_DELAY = float(os.environ.get("DUDDY_STEP_DELAY", "0.12"))
MAX_AUTO_STEPS = 300

# (plan_id, step_index) -> 前端回报
_pending: Dict[tuple, Dict[str, Any]] = {}
# 正在自动执行的计划（防止 AI 手动 apply 与后台任务打架）
_auto_running: set = set()
_auto_tasks: Dict[str, Any] = {}
_registered = False


def _ok(data: Any = None, **extra) -> web.Response:
    body = {"ok": True}
    if data is not None:
        body["data"] = data
    body.update(extra)
    return web.json_response(body)


def _err(message: str, code: int = 400, kind: str = "bad_request", **extra) -> web.Response:
    body = {"ok": False, "error": message, "kind": kind}
    body.update(extra)
    return web.json_response(body, status=code)


async def _body(request: web.Request) -> Dict[str, Any]:
    if not request.can_read_body:
        return {}
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _guard(fn):
    """把 PlanError 翻译成 4xx，其它异常翻译成 500 但不泄露堆栈。"""

    async def wrapper(request: web.Request):
        try:
            return await fn(request)
        except PlanError as e:
            return _err(str(e), e.code, e.kind, **e.extra)
        except web.HTTPException:
            raise
        except Exception as e:
            logger.exception("[duddy] route error in %s", fn.__name__)
            return _err(f"{type(e).__name__}: {e}", 500, "internal")

    wrapper.__name__ = fn.__name__
    return wrapper


# ------------------------------------------------------------------ 基础


@_guard
async def ping(request: web.Request) -> web.Response:
    return _ok(
        {
            "name": "duddy-comfyui",
            "protocol": 1,
            "uptime_s": round(time.time() - state.boot_ts, 1),
            "plans": len(plan_manager.list_recent(limit=200)),
            "clients": len(state.clients),
            "require_approval": REQUIRE_APPROVAL,
            "auto_apply": not REQUIRE_APPROVAL,
            "bus": bus.stats(),
        }
    )


@_guard
async def health(request: web.Request) -> web.Response:
    return _ok(
        {
            "state": state.snapshot(),
            "bus": bus.stats(),
            "guard": security.guard.snapshot(),
            "vault_size": len(security.vault),
        }
    )


@_guard
async def capabilities(request: web.Request) -> web.Response:
    return _ok(
        {
            "canvas_ops": [
                "create_node",
                "connect",
                "disconnect",
                "set_widget",
                "move_node",
                "delete_node",
                "group",
                "note",
                "execute",
            ],
            "requires_approval": REQUIRE_APPROVAL,
            "approval_channel": "frontend_modal" if REQUIRE_APPROVAL else "auto",
            "single_step_apply": True,
            "trace": False,
            "logs": False,
            "fswatch": False,
            "note": "trace/logs/fswatch 在 M3-M5 才上线，当前为 false 是诚实的",
        }
    )


# ------------------------------------------------------------ 前端注册/回报


@_guard
async def client_hello(request: web.Request) -> web.Response:
    body = await _body(request)
    client_id = str(body.get("client_id") or "").strip()
    if not client_id:
        return _err("client_id required", 400, "bad_request")
    rec = state.hello(client_id, {"ua": body.get("ua"), "page": body.get("page")})
    # 新前端上线，把还在等审批的计划重推一遍（刷新页面不丢）
    for p in plan_manager.list_recent(limit=10):
        if p["state"] == "proposed":
            try:
                plan = plan_manager.get(p["plan_id"])
                token = security.vault.issue(plan.id)
                bus.publish(
                    "duddy.plan.proposed",
                    {
                        "plan": plan.public(),
                        "narration": plan.narration(),
                        "token": token,
                        "ttl_sec": 1800,
                        "resent": True,
                    },
                    sid=client_id,
                )
            except Exception:
                pass
    return _ok(rec)


@_guard
async def canvas_state_post(request: web.Request) -> web.Response:
    body = await _body(request)
    graph = body.get("graph") or {}
    if not isinstance(graph, dict):
        return _err("graph must be an object", 400, "bad_request")
    cid = body.get("client_id")
    state.touch(cid)  # 上报本身就是心跳
    state.set_canvas(graph, reporter=cid)
    return _ok(state.canvas_summary())


@_guard
async def canvas_state_get(request: web.Request) -> web.Response:
    return _ok({"summary": state.canvas_summary(), "graph": state.canvas_graph()})


@_guard
async def canvas_applied(request: web.Request) -> web.Response:
    """前端执行完一个画布操作后的回报。"""
    body = await _body(request)
    plan_id = str(body.get("plan_id") or "")
    idx = body.get("step_index")
    if idx is None or not plan_id:
        return _err("plan_id & step_index required", 400, "bad_request")
    key = (plan_id, int(idx))
    rec = _pending.get(key)
    if rec is None:
        return _ok({"accepted": False, "reason": "no pending step (可能已超时或被丢弃)"})
    rec["done"] = True
    rec["ok"] = bool(body.get("ok"))
    rec["detail"] = body.get("detail")
    rec["ts"] = time.time()
    bus.publish(
        "duddy.canvas.applied",
        {"plan_id": plan_id, "step_index": int(idx), "ok": rec["ok"], "detail": rec["detail"]},
    )
    return _ok({"accepted": True})


# ------------------------------------------------------------------ 执行内核


async def do_step(
    plan_id: str,
    expected_index: Optional[int] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """执行一步。HTTP 路由与后台自动执行共用同一实现，避免两套逻辑分叉。"""
    plan, idx, step = plan_manager.begin_step(plan_id, expected_index)

    sid = state.target_client(plan.client_id)
    if not sid:
        plan_manager.record_result(
            plan_id, idx, False,
            {"reason": "no_frontend", "hint": "没有浏览器连接，无法操作画布"},
        )
        raise PlanError(
            "没有前端连接。请先在浏览器打开 ComfyUI 页面（画布必须在线才能建节点）。",
            409,
            "no_frontend",
        )

    key = (plan_id, idx)
    _pending[key] = {"done": False, "ok": False, "detail": None, "ts": time.time()}

    bus.publish(
        "duddy.canvas.op",
        {
            "plan_id": plan_id,
            "step_index": idx,
            "total": len(plan.steps),
            "op": step,
            "dry_run": dry_run,
        },
        sid=sid,
    )

    deadline = time.time() + WAIT_FRONTEND_SEC
    rec = _pending.get(key)
    while time.time() < deadline:
        if rec and rec.get("done"):
            break
        await asyncio.sleep(POLL_SEC)
        rec = _pending.get(key)
    rec = _pending.pop(key, None)

    if not rec or not rec.get("done"):
        plan_manager.record_result(
            plan_id, idx, False,
            {"reason": "frontend_timeout", "hint": f"前端 {WAIT_FRONTEND_SEC:.0f}s 内没有回报"},
        )
        raise PlanError(
            f"前端未在 {WAIT_FRONTEND_SEC:.0f} 秒内确认执行结果。检查浏览器控制台。",
            504,
            "frontend_timeout",
            step_index=idx,
        )

    updated = plan_manager.record_result(plan_id, idx, bool(rec["ok"]), rec["detail"])
    return {
        "step_index": idx,
        "step": step,
        "ok": bool(rec["ok"]),
        "detail": rec["detail"],
        "cursor": updated.cursor,
        "step_count": len(updated.steps),
        "plan_state": updated.state,
        "remaining": len(updated.steps) - updated.cursor,
    }


async def _auto_apply(plan_id: str) -> None:
    """后台把整个计划一口气跑完，不需要 AI 在埸、也不需要用户再点任何东西。"""
    logger.info("[duddy] auto-apply 开始 %s", plan_id)
    try:
        for _ in range(MAX_AUTO_STEPS):
            plan = plan_manager.get(plan_id)
            if plan.state not in ("approved", "applying"):
                break
            if plan.cursor >= len(plan.steps):
                break
            try:
                res = await do_step(plan_id)
            except PlanError as e:
                logger.warning("[duddy] auto-apply 停止：%s (%s)", e, e.kind)
                break
            if not res["ok"]:
                logger.warning("[duddy] auto-apply 步骤失败：%s", res["detail"])
                break
            await asyncio.sleep(AUTO_STEP_DELAY)
    except asyncio.CancelledError:
        logger.info("[duddy] auto-apply 被取消 %s", plan_id)
        raise
    except Exception as e:
        logger.exception("[duddy] auto-apply 崩溃：%s", e)
    finally:
        _auto_running.discard(plan_id)
        _auto_tasks.pop(plan_id, None)
        logger.info("[duddy] auto-apply 结束 %s", plan_id)


def kick_auto(plan_id: str) -> bool:
    """启动后台自动执行。没有前端连接时返回 False（画布指令无处可去）。"""
    if plan_id in _auto_running:
        return True
    if not state.target_client():
        logger.warning("[duddy] 没有前端连接，无法自动执行 %s", plan_id)
        return False
    _auto_running.add(plan_id)
    _auto_tasks[plan_id] = asyncio.create_task(_auto_apply(plan_id))
    return True


# ------------------------------------------------------------------ 计划


@_guard
async def plan_create(request: web.Request) -> web.Response:
    body = await _body(request)
    client_id = state.target_client(body.get("client_id"))
    plan, _token = plan_manager.create(
        title=body.get("title") or "未命名计划",
        summary=body.get("summary") or "",
        steps=body.get("steps"),
        notes=body.get("notes") or "",
        client_id=client_id,
    )

    auto_started = False
    if not REQUIRE_APPROVAL:
        # 全权接管模式：创建即批准，立刻开跑
        try:
            plan_manager.auto_approve(plan.id, by="auto")
            auto_started = kick_auto(plan.id)
        except PlanError as e:
            logger.warning("[duddy] auto approve failed: %s", e)

    notice = None
    if not state.alive_clients():
        notice = (
            "当前没有任何前端连接，画布指令无处可去。"
            "请先在浏览器打开 ComfyUI 页面，然后把计划重新提交一次。"
        )

    payload = plan.public()
    payload["approval_mode"] = "manual" if REQUIRE_APPROVAL else "auto"
    payload["auto_started"] = auto_started
    # 返回体里绝不带 token
    return _ok(payload, narration=plan.narration(), notice=notice)


@_guard
async def plan_list(request: web.Request) -> web.Response:
    return _ok(plan_manager.list_recent(limit=int(request.query.get("limit", 20))))


@_guard
async def plan_get(request: web.Request) -> web.Response:
    plan = plan_manager.get(request.match_info["plan_id"])
    return _ok(plan.public(), narration=plan.narration())


@_guard
async def plan_approve(request: web.Request) -> web.Response:
    """只有浏览器前端能成功调用——token 只存在于 ws 事件里。"""
    body = await _body(request)
    token = str(body.get("token") or "")
    if not token:
        return _err(
            "缺少审批 token。approve 只能由 ComfyUI 前端在用户点击「同意」时发起。",
            403,
            "forbidden",
        )
    by = str(body.get("by") or "ui")
    plan = plan_manager.approve(request.match_info["plan_id"], token, by=by)
    # 手动模式下用户点了同意，也应该由后端一口气跑完，不用再等 AI 回来
    started = kick_auto(plan.id)
    return _ok(plan.public(), auto_started=started)


@_guard
async def plan_reject(request: web.Request) -> web.Response:
    body = await _body(request)
    plan = plan_manager.reject(
        request.match_info["plan_id"],
        reason=str(body.get("reason") or ""),
        token=body.get("token"),
    )
    return _ok(plan.public())


@_guard
async def plan_step(request: web.Request) -> web.Response:
    body = await _body(request)
    plan_id = request.match_info["plan_id"]
    if plan_id in _auto_running:
        return _err(
            "该计划正在自动执行中，不需要手动触发。要停下来请调 abort。",
            409,
            "auto_running",
        )
    expected = body.get("expected_index")
    result = await do_step(
        plan_id,
        None if expected is None else int(expected),
        bool(body.get("dry_run")),
    )
    if not result["ok"]:
        return _err(
            f"第 {result['step_index'] + 1} 步执行失败：{result['detail']}",
            422,
            "step_failed",
            **result,
        )
    return _ok(result)


@_guard
async def plan_retry(request: web.Request) -> web.Response:
    return _ok(plan_manager.retry(request.match_info["plan_id"]).public())


@_guard
async def plan_abort(request: web.Request) -> web.Response:
    body = await _body(request)
    plan_id = request.match_info["plan_id"]
    task = _auto_tasks.pop(plan_id, None)
    if task is not None:
        task.cancel()
        _auto_running.discard(plan_id)
    return _ok(
        plan_manager.abort(plan_id, str(body.get("reason") or "")).public()
    )


@_guard
async def events(request: web.Request) -> web.Response:
    since = int(request.query.get("since", 0))
    prefix = request.query.get("prefix") or None
    limit = int(request.query.get("limit", 100))
    return _ok(bus.recent(prefix=prefix, limit=limit, since_seq=since))


# ------------------------------------------------------------------ 挂载


def build_routes():
    from aiohttp import web as _w

    r = _w.RouteTableDef()
    r.get(P + "/ping")(ping)
    r.get(P + "/health")(health)
    r.get(P + "/capabilities")(capabilities)

    r.post(P + "/client/hello")(client_hello)
    r.post(P + "/canvas/state")(canvas_state_post)
    r.get(P + "/canvas/state")(canvas_state_get)
    r.post(P + "/canvas/applied")(canvas_applied)

    r.post(P + "/plan")(plan_create)
    r.get(P + "/plans")(plan_list)
    r.get(P + "/plan/{plan_id}")(plan_get)
    r.post(P + "/plan/{plan_id}/approve")(plan_approve)
    r.post(P + "/plan/{plan_id}/reject")(plan_reject)
    r.post(P + "/plan/{plan_id}/step")(plan_step)
    r.post(P + "/plan/{plan_id}/retry")(plan_retry)
    r.post(P + "/plan/{plan_id}/abort")(plan_abort)

    r.get(P + "/events")(events)
    return r


def _attach_all(shared) -> int:
    """把一张 RouteTableDef 里的路由搬到 ComfyUI 的共享 routes 上。"""
    n = 0
    for r in build_routes():
        getattr(shared, r.method.lower())(r.path)(r.handler)
        n += 1
    return n


def register_routes() -> bool:
    """挂载路由。

    坑：这版 ComfyUI 的 PromptServer.add_routes() 是**无参**内部方法
    （它负责把 self.routes 交给 aiohttp），不能当「添加路由」用——
    传参进去会 TypeError。正确姿势是把路由定义到共享的
    PromptServer.instance.routes（RouteTableDef）上，由 ComfyUI 统一注册。
    """
    global _registered
    if _registered:
        return True
    try:
        from server import PromptServer  # type: ignore

        inst = getattr(PromptServer, "instance", None)
        if inst is not None and getattr(inst, "routes", None) is not None:
            n = _attach_all(inst.routes)
            _registered = True
            logger.info("[duddy] %d 条路由已挂到共享 routes（前缀 %s）", n, P)
            return True

        logger.warning("[duddy] PromptServer 尚未就绪，挂到构造钩子上等待")
        original_init = PromptServer.__init__

        def _patched_init(self, *a, **kw):  # type: ignore[no-untyped-def]
            original_init(self, *a, **kw)
            try:
                if not globals().get("_registered") and getattr(self, "routes", None) is not None:
                    n = _attach_all(self.routes)
                    globals()["_registered"] = True
                    logger.info("[duddy] %d 条路由已挂载（延迟生效）", n)
            except Exception as e:
                logger.error("[duddy] deferred route registration failed: %s", e)

        PromptServer.__init__ = _patched_init  # type: ignore[method-assign]
        return True
    except Exception as e:
        logger.exception("[duddy] failed to register routes: %s", e)
        return False
