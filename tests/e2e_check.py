#!/usr/bin/env python3
"""端到端验证：用一个假前端跑完整条链路。

它冒充浏览器：
  1. 连 ComfyUI 的 /ws
  2. 报到 /duddy/client/hello
  3. 监听 duddy.plan.proposed 拿到 token
  4. 点「同意」（POST approve）
  5. 收到 duddy.canvas.op 就回报 applied（不真的建节点，只回 ok）

跑通说明：ws 定向投递 ✓  token 只走 ws ✓  闸门 ✓  逐步执行 ✓

需要 aiohttp（ComfyUI 自带环境就有）：
    D:\\AI\\ComfyUI_windows_portable\\python_embeded\\python.exe tests/e2e_check.py
"""

from __future__ import annotations

import asyncio
import json
import sys

import aiohttp

BASE = "http://127.0.0.1:8188"
WS = "ws://127.0.0.1:8188/ws"
CID = "duddy-e2e-probe"

G, Y, R, B, X = "\033[32m", "\033[33m", "\033[31m", "\033[1m", "\033[0m"


def ok(m):
    print(f"  {G}✓{X} {m}")


def bad(m):
    print(f"  {R}✗{X} {m}")


async def main() -> int:
    fails = []
    print(f"{B}端到端验证{X}  假前端 clientId={CID}")

    async with aiohttp.ClientSession() as s:
        # 0) 先问模式
        async with s.get(f"{BASE}/duddy/ping") as r:
            ping = await r.json()
        need_approval = bool(ping.get("data", {}).get("require_approval"))
        mode = "manual（需要批准）" if need_approval else "auto（全权接管）"
        print(f"  模式: {mode}")

        # 1) 报到
        async with s.post(f"{BASE}/duddy/client/hello", json={"client_id": CID}) as r:
            if r.status != 200:
                bad(f"hello 失败 {r.status}")
                return 1
        ok("报到成功")

        async with s.ws_connect(f"{WS}?clientId={CID}") as ws:
            box = {"token": None, "plan_id": None, "ops": [], "applied": 0}

            async def ws_loop():
                async for msg in ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue
                    try:
                        m = json.loads(msg.data)
                    except Exception:
                        continue
                    t = m.get("type")
                    # send_sync 外层还会包一层 {type, data}，我们塞的内容在 data.data
                    d = m.get("data") or {}
                    if isinstance(d, dict) and isinstance(d.get("data"), dict):
                        d = d["data"]
                    if t == "duddy.plan.proposed":
                        box["token"] = d.get("token")
                        box["plan_id"] = (d.get("plan") or {}).get("plan_id")
                        print(f"  {Y}←{X} plan.proposed  token={'有' if box['token'] else '没有!'}")
                    elif t == "duddy.canvas.op":
                        idx = d.get("step_index")
                        box["ops"].append(idx)
                        print(f"  {Y}←{X} canvas.op #{idx}  {d.get('op', {}).get('kind')}")
                        await asyncio.sleep(0.05)
                        async with s.post(
                            f"{BASE}/duddy/canvas/applied",
                            json={
                                "plan_id": d.get("plan_id"),
                                "step_index": idx,
                                "ok": True,
                                "detail": {"fake_frontend": True},
                                "client_id": CID,
                            },
                        ):
                            box["applied"] += 1
                    elif t in ("duddy.plan.approved", "duddy.plan.done", "duddy.plan.rejected"):
                        print(f"  {Y}←{X} {t}")

            task = asyncio.create_task(ws_loop())
            await asyncio.sleep(0.4)
            ok("WebSocket 已连接")

            # 2) 造一个 3 步计划
            plan_body = {
                "title": "端到端自检",
                "summary": "由 e2e_check.py 自动批准，验证闸门与逐步执行",
                "client_id": CID,  # 绑死假前端，别往用户画布上乱建
                "steps": [
                    {"kind": "create_node", "title": "A", "args": {"type": "Note", "ref": "a", "pos": [2400, 1800]}},
                    {"kind": "create_node", "title": "B", "args": {"type": "Note", "ref": "b", "pos": [2700, 1800]}},
                    {
                        "kind": "connect",
                        "title": "A→B",
                        "args": {"from_ref": "a", "from_slot": 0, "to_ref": "b", "to_slot": 0},
                    },
                ],
            }
            async with s.post(f"{BASE}/duddy/plan", json=plan_body) as r:
                data = await r.json()
            if not data.get("ok"):
                bad(f"建计划失败: {data}")
                return 1
            pid = data["data"]["plan_id"]
            ok(f"计划已建 {pid}")

            if "token" in json.dumps(data.get("data", {})):
                bad("HTTP 响应里出现了 token —— 安全漏洞")
                fails.append("token_leak")
            else:
                ok("HTTP 响应不含 token")

            if need_approval:
                # 3) 等 ws 拿到 token
                for _ in range(30):
                    if box["token"]:
                        break
                    await asyncio.sleep(0.1)
                if not box["token"]:
                    bad("ws 没收到 token，弹窗链路断了")
                    task.cancel()
                    return 1
                ok("token 已通过 ws 定向下发")

                # 4) 未批准先试一次，必须被拦
                async with s.post(f"{BASE}/duddy/plan/{pid}/step", json={}) as r:
                    st = r.status
                if st == 403:
                    ok("未批准时 apply 被 403 拦下")
                else:
                    bad(f"未批准竟然没拦住！HTTP {st}")
                    fails.append("gate")

                # 5) 批准 → 后端应接手自动执行
                async with s.post(
                    f"{BASE}/duddy/plan/{pid}/approve",
                    json={"token": box["token"], "by": "e2e", "client_id": CID},
                ) as r:
                    body = await r.json()
                if not body.get("ok"):
                    bad(f"批准失败: {body}")
                    task.cancel()
                    return 1
                ok(f"批准成功 → auto_started={body.get('auto_started')}")
            else:
                ok(f"免审批模式，auto_started={data['data'].get('auto_started')}")

            # 6) 等后台一口气跑完（两种模式现在都由 auto_apply 推进）
            final = {}
            for _ in range(80):
                await asyncio.sleep(0.4)
                async with s.get(f"{BASE}/duddy/plan/{pid}") as r:
                    final = (await r.json())["data"]
                if final.get("state") in ("done", "failed", "aborted"):
                    break

            if final.get("state") == "done" and final.get("cursor") == 3:
                ok(f"自动执行完成：state=done · cursor=3/3")
            else:
                bad(f"终态异常：state={final.get('state')} cursor={final.get('cursor')}")
                fails.append("final")

            # 7) 重复执行检测：假前端收到几次 op 就回报几次
            ok(f"前端回报 {box['applied']} 次（应为 3）")
            if box["applied"] != 3:
                bad("步骤被重复执行了")
                fails.append("dup_exec")

            # 8) 终态
            async with s.get(f"{BASE}/duddy/plan/{pid}") as r:
                final = (await r.json())["data"]
            if final["state"] == "done" and final["cursor"] == 3:
                ok(f"终态正确：{final['state']} · cursor={final['cursor']}/3")
            else:
                bad(f"终态异常：{final['state']} cursor={final['cursor']}")
                fails.append("final")

            # 8) 完成后不能再动
            async with s.post(f"{BASE}/duddy/plan/{pid}/step", json={}) as r:
                if r.status == 403:
                    ok("已完成的计划不能再执行（403）")
                else:
                    bad(f"完成后竟还能执行 HTTP {r.status}")
                    fails.append("replay")

            task.cancel()

    print("\n" + "─" * 56)
    if fails:
        print(f"{R}{B}失败：{X}{', '.join(fails)}")
        return 1
    print(f"{G}{B}端到端全通。{X}闸门、定向投递、逐步执行、终态都正确。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
