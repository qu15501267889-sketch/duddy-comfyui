"""M3 · 执行进度追踪。

不需要前端配合：直接 hook PromptServer.send_sync，
把 ComfyUI 自己发给浏览器的执行事件截流一份，翻译成人类可读的进度。

这样即使浏览器关着、或者你切到别的标签页，后端也知道跑到哪一步了。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

from ..core.bus import bus
from ..core.state import state

logger = logging.getLogger("duddy.trace")

#: 多久没动静就认为这一次运行已经结束（ComfyUI 出错时可能不发结束事件）
IDLE_TIMEOUT = 90.0


class Trace:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.run: Optional[Dict[str, Any]] = None
        self._installed = False
        self._node_started: Dict[str, float] = {}
        self._last_event_at = 0.0

    # ------------------------------------------------------------------ 安装
    def install(self) -> bool:
        if self._installed:
            return True
        try:
            from server import PromptServer  # type: ignore

            original = PromptServer.send_sync

            def patched(self_, event, data, sid=None):  # type: ignore[no-untyped-def]
                try:
                    self.observe(event, data)
                except Exception:
                    pass
                if sid:
                    return original(self_, event, data, sid)
                return original(self_, event, data)

            PromptServer.send_sync = patched  # type: ignore[method-assign]
            self._installed = True
            logger.info("[duddy] 执行进度追踪已挂载")
            return True
        except Exception as e:
            logger.warning("[duddy] 进度追踪安装失败: %s", e)
            return False

    # ------------------------------------------------------------------ 观察
    def observe(self, event: str, data: Any) -> None:
        if not isinstance(data, dict):
            return
        with self._lock:
            self._last_event_at = time.time()
            pid = str(data.get("prompt_id") or "")

            if event == "execution_start":
                self.run = {
                    "prompt_id": pid,
                    "started_at": time.time(),
                    "status": "running",
                    "current_node": None,
                    "current_node_type": None,
                    "step": 0,
                    "total_steps": None,
                    "completed": [],
                    "cached": [],
                    "error": None,
                    "finished_at": None,
                    "eta_s": None,
                }
                self._node_started.clear()
                self._emit("duddy.run.start", self._summary())

            elif event == "executing":
                node = data.get("node")
                if node is None:
                    # node 为 None 表示这一批执行完成
                    if self.run and self.run["status"] == "running":
                        self.run["status"] = "done"
                        self.run["finished_at"] = time.time()
                        self.run["eta_s"] = 0
                        self._emit("duddy.run.done", self._summary())
                    return
                node = str(node)
                if self.run is None:
                    self.run = {
                        "prompt_id": pid, "started_at": time.time(), "status": "running",
                        "current_node": None, "current_node_type": None,
                        "step": 0, "total_steps": None,
                        "completed": [], "cached": [], "error": None,
                        "finished_at": None, "eta_s": None,
                    }
                self.run["current_node"] = node
                self.run["current_node_type"] = self._type_of(node)
                self.run["step"] = 0
                self.run["total_steps"] = None
                self._node_started[node] = time.time()
                self._emit("duddy.run.node", self._summary())

            elif event == "progress":
                if self.run is None:
                    return
                self.run["step"] = int(data.get("value") or 0)
                self.run["total_steps"] = int(data.get("max") or 0) or None
                if data.get("node") is not None:
                    self.run["current_node"] = str(data.get("node"))
                    self.run["current_node_type"] = self._type_of(str(data.get("node")))
                self.run["eta_s"] = self._estimate_eta()
                self._emit("duddy.run.progress", self._summary(), throttle=True)

            elif event == "executed":
                if self.run is None:
                    return
                node = data.get("node")
                if node is not None and str(node) not in self.run["completed"]:
                    self.run["completed"].append(str(node))
                self.run["eta_s"] = self._estimate_eta()
                self._emit("duddy.run.progress", self._summary(), throttle=True)

            elif event == "execution_cached":
                if self.run is None:
                    return
                nodes = data.get("nodes") or []
                self.run["cached"] = [str(n) for n in nodes]
                self._emit("duddy.run.progress", self._summary(), throttle=True)

            elif event == "execution_error":
                if self.run is None:
                    self.run = {"prompt_id": pid, "started_at": time.time(),
                                "status": "error", "current_node": None,
                                "current_node_type": None, "step": 0,
                                "total_steps": None, "completed": [], "cached": [],
                                "error": None, "finished_at": None, "eta_s": None}
                self.run["status"] = "error"
                self.run["finished_at"] = time.time()
                self.run["error"] = {
                    "node": str(data.get("node_id") or ""),
                    "node_type": data.get("node_type"),
                    "message": str(data.get("exception_message") or "")[:600],
                    "type": data.get("exception_type"),
                }
                self._emit("duddy.run.error", self._summary())

            elif event == "execution_success":
                if self.run is not None:
                    self.run["status"] = "done"
                    self.run["finished_at"] = time.time()
                    self.run["eta_s"] = 0
                    self._emit("duddy.run.done", self._summary())

            elif event == "execution_interrupted":
                if self.run is not None:
                    self.run["status"] = "interrupted"
                    self.run["finished_at"] = time.time()
                    self._emit("duddy.run.error", self._summary())

            state.set_run(self._summary())

    # ------------------------------------------------------------------ 辅助
    def _type_of(self, node_id: str) -> Optional[str]:
        g = state.canvas_graph() or {}
        for n in g.get("nodes") or []:
            if str(n.get("id")) == str(node_id):
                return n.get("type")
        return None

    def _estimate_eta(self) -> Optional[float]:
        """剩余时间估算。

        优先用「当前节点内的步数进度」，这是最准的：
        8 秒/步、走完 12/20 步 → 还剩 8×8=64 秒。
        之前的实现要求「至少完成一个节点」才给数，导致第一个节点全程是 None，
        而那恰恰是用户最想知道剩余时间的时候。
        """
        if not self.run:
            return None

        st = int(self.run.get("step") or 0)
        tot = int(self.run.get("total_steps") or 0)
        node = self.run.get("current_node")
        started = self._node_started.get(str(node)) if node is not None else None

        graph = state.canvas_graph() or {}
        total_nodes = len(graph.get("nodes") or [])
        done_nodes = len(self.run.get("completed") or [])

        if st > 0 and tot > 0 and started:
            spent_in_node = time.time() - started
            per_step = spent_in_node / st
            remain_in_node = per_step * (tot - st)
            # 后续还有多少节点没法精确知道，用已完成节点的平均耗时粗估
            tail = 0.0
            if done_nodes > 0 and total_nodes > done_nodes + 1:
                avg_per_node = (time.time() - self.run["started_at"]) / done_nodes
                tail = avg_per_node * (total_nodes - done_nodes - 1)
            return round(remain_in_node + tail, 1)

        # 退化路径：没有步数信息时，用已完成节点的平均耗时
        if done_nodes == 0:
            return None
        elapsed = time.time() - self.run["started_at"]
        per_node = elapsed / done_nodes
        if total_nodes > done_nodes:
            return round(per_node * (total_nodes - done_nodes - 1), 1)
        return None

    def _summary(self) -> Dict[str, Any]:
        if not self.run:
            return {"status": "idle"}
        r = dict(self.run)
        r["elapsed_s"] = round((r.get("finished_at") or time.time()) - r["started_at"], 1)
        r["completed_count"] = len(r["completed"])
        return r

    def _emit(self, event: str, payload: Dict[str, Any], throttle: bool = False) -> None:
        if throttle:
            now = time.time()
            if now - getattr(self, "_last_emit", 0) < 0.25:
                return
            self._last_emit = now
        bus.publish(event, payload)

    # ------------------------------------------------------------------ 查询
    def status(self) -> Dict[str, Any]:
        with self._lock:
            if self.run is None:
                return {"status": "idle", "hint": "还没有执行过任何任务"}
            if self.run["status"] == "running":
                if time.time() - self._last_event_at > IDLE_TIMEOUT:
                    self.run["status"] = "stale"
                else:
                    # 查询时现算，保证 ETA 和耗时是最新的（事件之间有间隔）
                    self.run["eta_s"] = self._estimate_eta()
            return self._summary()


trace = Trace()
