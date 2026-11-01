#!/usr/bin/env python3
"""安装后自检。零依赖。

跑法：
    python verify.py

它会依次检查：路由通不通 → 前端连没连 → 计划能不能建 → 闸门拦不拦得住。
每一关卡住都会告诉你下一步该干什么。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("COMFY_URL", "http://127.0.0.1:8188").rstrip("/")
TIMEOUT = 8.0

G = "\033[32m"
Y = "\033[33m"
R = "\033[31m"
B = "\033[1m"
X = "\033[0m"


def call(method, path, body=None, timeout=TIMEOUT):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        try:
            return e.code, json.loads(raw) if raw else {}
        except Exception:
            return e.code, {"error": raw}
    except Exception as e:
        return 0, {"error": f"{type(e).__name__}: {e}"}


def step(n, label):
    print(f"\n{B}[{n}]{X} {label}")


def good(msg):
    print(f"    {G}✓{X} {msg}")


def bad(msg):
    print(f"    {R}✗{X} {msg}")


def warn(msg):
    print(f"    {Y}!{X} {msg}")


def main():
    print(f"{B}duddy-comfyui 自检{X}  →  {BASE}")
    failures = []

    # ---------------------------------------------------------------- 1
    step(1, "扩展路由是否挂载")
    st, data = call("GET", "/duddy/ping")
    if st == 200 and data.get("ok"):
        d = data["data"]
        good(f"在线 · uptime {d.get('uptime_s')}s · 事件总线已发出 {d['bus']['published']} 条")
    else:
        bad(f"连不上（HTTP {st}）")
        print(
            "\n    最可能的原因，按顺序排除：\n"
            "      a) ComfyUI 还没重启（自定义节点只在启动时扫描）\n"
            "      b) custom_nodes 下没有 duddy-comfyui\n"
            "      c) 控制台没打印 [duddy] v0.1.0 ready\n"
        )
        print(f"    返回: {str(data)[:300]}")
        return 1

    # ---------------------------------------------------------------- 2
    step(2, "前端是否已连接")
    st, data = call("GET", "/duddy/health")
    clients = (data.get("data", {}).get("state", {}) or {}).get("clients") or []
    if clients:
        good(f"{len(clients)} 个前端在线: {', '.join(c[:12] + '…' for c in clients)}")
    else:
        warn("没有前端连接。请打开 ComfyUI 页面并 Ctrl+Shift+R 硬刷新")
        warn("刷新后 F12 控制台应出现 [duddy] extension setup")
        failures.append("no_frontend")

    # ---------------------------------------------------------------- 3
    step(3, "能否创建计划")
    st, data = call(
        "POST",
        "/duddy/plan",
        {
            "title": "自检计划",
            "summary": "由 verify.py 生成，用来确认闸门有效",
            "notes": "这个计划不应该被执行",
            "steps": [
                {
                    "kind": "create_node",
                    "title": "自检占位节点",
                    "why": "验证流程",
                    "args": {"type": "Note", "ref": "selfcheck", "pos": [80, 80]},
                }
            ],
        },
    )
    plan_id = None
    if st == 200 and data.get("ok"):
        plan_id = data["data"]["plan_id"]
        good(f"计划已建 {plan_id} · state={data['data']['state']}")
        if data["data"]["state"] != "proposed":
            bad(f"状态异常：{data['data']['state']}")
            failures.append("bad_state")
        if "token" in json.dumps(data.get("data", {})):
            bad("响应里出现了 token —— 这是安全漏洞，立刻停下")
            failures.append("token_leak")
        else:
            good("响应里不含 token（符合预期）")
    else:
        bad(f"创建失败 HTTP {st}: {str(data)[:200]}")
        failures.append("plan_create")

    # ---------------------------------------------------------------- 4
    step(4, "闸门：未批准时能否执行")
    if plan_id:
        st, data = call("POST", f"/duddy/plan/{plan_id}/step", {})
        if st == 403:
            good(f"被拦住了（403 {data.get('kind')}）—— 闸门有效")
        else:
            bad(f"竟然没拦住！HTTP {st}，这是严重问题")
            failures.append("gate_broken")

        # 清掉自检计划，别污染用户的计划列表
        call("POST", f"/duddy/plan/{plan_id}/abort", {"reason": "自检清理"})

    # ---------------------------------------------------------------- 汇总
    print("\n" + "─" * 58)
    if not failures:
        print(f"{G}{B}全绿。{X}去浏览器右下角看看有没有弹窗，")
        print("点「拒绝」把自检计划清掉，然后就能正常用了。")
        return 0
    if failures == ["no_frontend"]:
        print(f"{Y}{B}扩展本身没问题，只差前端。{X}打开 ComfyUI 页面硬刷新即可。")
        return 0
    print(f"{R}{B}有 {len(failures)} 项没过：{X}{', '.join(failures)}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
