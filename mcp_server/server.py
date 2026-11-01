#!/usr/bin/env python3
"""duddy-comfyui MCP server — 零依赖实现。

不依赖 `mcp` pip 包，只用标准库手写 JSON-RPC over stdio。
理由：ComfyUI 的嵌入式 Python 往往没有 pip / 没装 mcp，
要求用户先配环境就谈不上「开箱即用」。

传输：stdio（MCP 标准）
配置：
    COMFY_URL   默认 http://127.0.0.1:8188

工具清单（M1）：
    duddy_ping            连不通的时候先调这个
    duddy_plan_workflow   提交计划（只规划，不动手）
    duddy_plan_status     查计划状态
    duddy_apply_step      执行下一步（必须已获批）
    duddy_list_plans      最近计划
    duddy_abort_plan      中止
    duddy_canvas_state    当前画布快照
    duddy_events          最近事件流（调试）

刻意不提供 duddy_approve —— 审批权只属于人类，见 README「为什么没有 approve 工具」。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

SERVER_NAME = "duddy-comfyui"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"

BASE = os.environ.get("COMFY_URL", "http://127.0.0.1:8188").rstrip("/")
HTTP_TIMEOUT = float(os.environ.get("DUDDY_HTTP_TIMEOUT", "45"))


def log(msg: str) -> None:
    """日志只能走 stderr，stdout 是协议通道。"""
    sys.stderr.write(f"[duddy-mcp] {msg}\n")
    sys.stderr.flush()


# --------------------------------------------------------------------- HTTP


def call(method: str, path: str, body: Optional[Dict[str, Any]] = None,
         timeout: Optional[float] = None) -> Tuple[int, Any]:
    url = BASE + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout or HTTP_TIMEOUT) as r:
            raw = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        try:
            parsed = json.loads(raw) if raw else {}
        except Exception:
            parsed = {"ok": False, "error": raw or f"HTTP {e.code}"}
        return e.code, parsed
    except urllib.error.URLError as e:
        return 0, {
            "ok": False,
            "error": f"无法连接 ComfyUI ({BASE}): {e.reason}",
            "hint": "确认 ComfyUI 在运行，且 duddy-comfyui 已装进 custom_nodes 并重启过",
        }
    except Exception as e:
        return 0, {"ok": False, "error": f"{type(e).__name__}: {e}"}


def call_wait(method: str, path: str, body: Optional[Dict[str, Any]] = None,
              timeout: float = 120.0) -> Tuple[int, Any]:
    return call(method, path, body, timeout=timeout)


# ------------------------------------------------------------------ 工具定义

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "duddy_ping",
        "description": (
            "检查 duddy 扩展是否在线。第一次接触 ComfyUI 时先调这个，"
            "能立刻区分「ComfyUI 没开」和「扩展没装」两种故障。"
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "duddy_plan_workflow",
        "description": (
            "【第一步·只规划，绝不动手】把你想在画布上做的事写成一个分步计划，"
            "推送给用户的 ComfyUI 界面，等待用户点击「同意」。\n\n"
            "硬性要求：\n"
            "  · 每个 step 只做一件事，粒度要细（一个节点一步、一条连线一步）\n"
            "  · create_node 必须给 ref 别名，后续 connect/set_widget 用别名引用，不要猜 node_id\n"
            "  · connect 用 from_ref/from_slot → to_ref/to_slot\n"
            "  · 每步给 title（人类可读）和 why（为什么需要这一步）\n\n"
            "调用后你会拿到 plan_id 和 state=proposed。"
            "此时**什么都不会发生**，直到用户在弹窗里点同意。\n"
            "直接把 narration 字段念给用户听，然后停下来等。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "计划标题，一句话，人类可读"},
                "summary": {"type": "string", "description": "这个计划要达成什么，2-3 句"},
                "notes": {"type": "string", "description": "风险提示/前置条件，可空"},
                "steps": {
                    "type": "array",
                    "description": "按执行顺序排列的步骤",
                    "items": {
                        "type": "object",
                        "properties": {
                            "kind": {
                                "type": "string",
                                "enum": [
                                    "create_node", "connect", "disconnect", "set_widget",
                                    "move_node", "delete_node", "note",
                                ],
                            },
                            "title": {"type": "string"},
                            "why": {"type": "string"},
                            "args": {"type": "object"},
                        },
                        "required": ["kind", "args"],
                    },
                },
            },
            "required": ["title", "summary", "steps"],
        },
    },
    {
        "name": "duddy_plan_status",
        "description": (
            "查某个计划的状态与进度。执行每一步之前都应该先看一眼，"
            "确认 state 是 approved 或 applying，以及 cursor 指向第几步。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"plan_id": {"type": "string"}},
            "required": ["plan_id"],
        },
    },
    {
        "name": "duddy_apply_step",
        "description": (
            "【第二步·逐个动手】执行计划的下一步，一次只走一步。\n\n"
            "前置条件：用户已经在 ComfyUI 弹窗里点了「同意」，plan.state == approved/applying。\n"
            "如果返回 403 not_approved，说明用户还没批准。此时你必须停下来，"
            "告诉用户「请在弹出的窗口里点同意」，然后等待。\n"
            "严禁尝试其它方式绕过（直接 POST /prompt、写 workflow 文件、批量推送都不行）。\n\n"
            "返回里会告诉你这一步建了/连了什么、真实 node_id、剩余步数。"
            "把结果用一句话回报给用户，再决定要不要继续下一步。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string"},
                "expected_index": {
                    "type": "integer",
                    "description": "你期望执行的步骤序号（从 0 开始）。填了可以防止跳步，建议总填",
                },
            },
            "required": ["plan_id"],
        },
    },
    {
        "name": "duddy_list_plans",
        "description": "列出最近的计划及其状态。用户问「刚才那个计划呢」时用。",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 10}},
        },
    },
    {
        "name": "duddy_abort_plan",
        "description": "中止一个尚未完成或已失败的流程。用户说「停」时立刻调。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["plan_id"],
        },
    },
    {
        "name": "duddy_canvas_state",
        "description": (
            "读取用户当前画布的快照（节点数、连线数、完整 graph）。"
            "规划前先看一眼现有画布，避免重复建节点或位置重叠。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_graph": {
                    "type": "boolean",
                    "default": False,
                    "description": "是否返回完整 graph JSON（可能很大）",
                }
            },
        },
    },
    {
        "name": "duddy_events",
        "description": "最近的事件流，排查「为什么没反应」时用（弹窗没弹、前端没回报等）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prefix": {"type": "string", "default": "duddy."},
                "limit": {"type": "integer", "default": 30},
            },
        },
    },
]


# ------------------------------------------------------------------ 工具实现


def _txt(payload: Any, is_error: bool = False) -> Dict[str, Any]:
    if isinstance(payload, str):
        text = payload
    else:
        text = json.dumps(payload, ensure_ascii=False, indent=1)
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def tool_ping(args: Dict[str, Any]) -> Dict[str, Any]:
    status, data = call("GET", "/duddy/ping", timeout=8)
    if status == 200 and isinstance(data, dict) and data.get("ok"):
        return _txt({"connected": True, "comfy_url": BASE, **data.get("data", {})})
    return _txt(
        {
            "connected": False,
            "comfy_url": BASE,
            "http_status": status,
            "response": data,
            "checklist": [
                "ComfyUI 是否在运行？",
                "duddy-comfyui 是否在 custom_nodes 下？",
                "装完之后是否重启过 ComfyUI？",
                "路径前缀是否被反代改写？",
            ],
        },
        is_error=True,
    )


def tool_plan_workflow(args: Dict[str, Any]) -> Dict[str, Any]:
    steps = args.get("steps")
    if not isinstance(steps, list) or not steps:
        return _txt({"error": "steps 不能为空"}, is_error=True)

    status, data = call(
        "POST",
        "/duddy/plan",
        {
            "title": args.get("title") or "未命名计划",
            "summary": args.get("summary") or "",
            "notes": args.get("notes") or "",
            "steps": steps,
        },
    )
    if status == 200 and data.get("ok"):
        d = data.get("data", {})
        return _txt(
            {
                "plan_id": d.get("plan_id"),
                "state": d.get("state"),
                "step_count": d.get("step_count"),
                "narration": data.get("narration"),
                "notice": data.get("notice"),
                "next": (
                    "计划已推送到用户的 ComfyUI 界面，现在什么都没发生。"
                    "把 narration 念给用户，然后停下等用户在弹窗里点「同意」。"
                    "用户同意后，用 duddy_apply_step 一次一步地执行。"
                ),
            }
        )
    return _txt(data, is_error=True)


def tool_plan_status(args: Dict[str, Any]) -> Dict[str, Any]:
    pid = args.get("plan_id")
    if not pid:
        return _txt({"error": "plan_id 必填"}, is_error=True)
    status, data = call("GET", f"/duddy/plan/{urllib.parse.quote(str(pid))}")
    if status == 200 and data.get("ok"):
        d = data["data"]
        return _txt(
            {
                "plan_id": d["plan_id"],
                "state": d["state"],
                "cursor": d["cursor"],
                "step_count": d["step_count"],
                "can_apply": d["can_apply"],
                "approved_by": d.get("approved_by"),
                "error": d.get("error"),
                "reject_reason": d.get("reject_reason"),
                "next_step": (
                    d["steps"][d["cursor"]] if d["cursor"] < d["step_count"] else None
                ),
            }
        )
    return _txt(data, is_error=True)


def tool_apply_step(args: Dict[str, Any]) -> Dict[str, Any]:
    pid = args.get("plan_id")
    if not pid:
        return _txt({"error": "plan_id 必填"}, is_error=True)
    body: Dict[str, Any] = {}
    if args.get("expected_index") is not None:
        body["expected_index"] = int(args["expected_index"])
    status, data = call_wait(
        "POST", f"/duddy/plan/{urllib.parse.quote(str(pid))}/step", body, timeout=30
    )
    if status == 200 and data.get("ok"):
        d = data["data"]
        return _txt(
            {
                "step_index": d["step_index"],
                "kind": d["step"]["kind"],
                "title": d["step"]["title"],
                "result": d["detail"],
                "cursor": d["cursor"],
                "remaining": d["remaining"],
                "plan_state": d["plan_state"],
            }
        )
    if status == 403:
        return _txt(
            {
                "blocked": True,
                "reason": data.get("error"),
                "what_to_do": (
                    "用户还没有在 ComfyUI 弹窗里点「同意」。"
                    "停下来，请用户去点。不要尝试任何绕过方式。"
                ),
            },
            is_error=True,
        )
    return _txt(data, is_error=True)


def tool_list_plans(args: Dict[str, Any]) -> Dict[str, Any]:
    limit = int(args.get("limit") or 10)
    status, data = call("GET", f"/duddy/plans?limit={limit}")
    return _txt(data.get("data", data) if status == 200 else data, is_error=status != 200)


def tool_abort_plan(args: Dict[str, Any]) -> Dict[str, Any]:
    pid = args.get("plan_id")
    if not pid:
        return _txt({"error": "plan_id 必填"}, is_error=True)
    status, data = call(
        "POST",
        f"/duddy/plan/{urllib.parse.quote(str(pid))}/abort",
        {"reason": args.get("reason") or "用户中止"},
    )
    return _txt(data, is_error=status != 200)


def tool_canvas_state(args: Dict[str, Any]) -> Dict[str, Any]:
    status, data = call("GET", "/duddy/canvas/state")
    if status != 200:
        return _txt(data, is_error=True)
    d = data.get("data", {})
    if not args.get("include_graph"):
        return _txt(
            {
                "summary": d.get("summary"),
                "hint": "需要完整图形时传 include_graph=true",
            }
        )
    graph = d.get("graph") or {}
    nodes = graph.get("nodes") or []
    return _txt(
        {
            "summary": d.get("summary"),
            "nodes": [
                {
                    "id": n.get("id"),
                    "type": n.get("type"),
                    "title": n.get("title"),
                    "pos": n.get("pos"),
                }
                for n in nodes
            ],
            "links": graph.get("links") or [],
        }
    )


def tool_events(args: Dict[str, Any]) -> Dict[str, Any]:
    prefix = urllib.parse.quote(str(args.get("prefix") or "duddy."))
    limit = int(args.get("limit") or 30)
    status, data = call("GET", f"/duddy/events?prefix={prefix}&limit={limit}")
    return _txt(data, is_error=status != 200)


HANDLERS = {
    "duddy_ping": tool_ping,
    "duddy_plan_workflow": tool_plan_workflow,
    "duddy_plan_status": tool_plan_status,
    "duddy_apply_step": tool_apply_step,
    "duddy_list_plans": tool_list_plans,
    "duddy_abort_plan": tool_abort_plan,
    "duddy_canvas_state": tool_canvas_state,
    "duddy_events": tool_events,
}


# ------------------------------------------------------------------ JSON-RPC


def handle(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    method = msg.get("method")
    msg_id = msg.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": (
                    "duddy-comfyui：在用户画布上操作 ComfyUI 工作流。\n"
                    "铁律：任何画布改动都必须先 duddy_plan_workflow 让用户批准，"
                    "再用 duddy_apply_step 一次一步执行。绝不直接提交 workflow 或批量推送。"
                ),
            },
        }

    if method in ("notifications/initialized", "initialized"):
        return None

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        fn = HANDLERS.get(name)
        if fn is None:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32602, "message": f"unknown tool: {name}"},
            }
        try:
            result = fn(args)
            return {"jsonrpc": "2.0", "id": msg_id, "result": result}
        except Exception as e:
            log(f"tool {name} crashed: {e}")
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": f"ERROR {type(e).__name__}: {e}"}],
                    "isError": True,
                },
            }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    if msg_id is None:
        return None
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


def serve() -> None:
    log(f"ready · comfy={BASE}")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            log(f"bad json: {line[:200]}")
            continue
        resp = handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


# ---------------------------------------------------------------------- CLI


def cli(argv: List[str]) -> int:
    if not argv:
        print("usage: server.py <tool> [json-args]   |   --serve")
        return 2
    name = argv[0]
    args = json.loads(argv[1]) if len(argv) > 1 else {}
    fn = HANDLERS.get(name)
    if not fn:
        print(f"unknown tool: {name}\navailable: {', '.join(HANDLERS)}")
        return 2
    print(json.dumps(fn(args), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if "--serve" in sys.argv or len(sys.argv) == 1:
        serve()
    else:
        raise SystemExit(cli(sys.argv[1:]))
