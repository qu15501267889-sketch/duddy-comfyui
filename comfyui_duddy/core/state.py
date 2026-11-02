"""跨模块共享状态。进程内单例，重启即重置。"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

#: 超过这么久没动静的客户端视为已断开。
#: 浏览器关掉标签页时 ComfyUI 不会通知插件，只能靠活跃度判断。
CLIENT_TTL_SEC = 90


class DuddYState:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.boot_ts = time.time()
        self.clients: Dict[str, Dict[str, Any]] = {}
        self.last_client_id: Optional[str] = None
        self.canvas: Optional[Dict[str, Any]] = None
        self.canvas_ts: float = 0.0
        self.canvas_reporter: Optional[str] = None
        self.run: Optional[Dict[str, Any]] = None
        self.modules: Dict[str, str] = {}

    # ------------------------------------------------------------ 客户端注册
    def hello(self, client_id: str, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        with self._lock:
            rec = self.clients.get(client_id, {})
            rec.update(meta or {})
            rec["client_id"] = client_id
            rec["connected_at"] = rec.get("connected_at") or round(time.time(), 3)
            rec["last_seen"] = round(time.time(), 3)
            self.clients[client_id] = rec
            self.last_client_id = client_id
            self._gc_locked()
            return rec

    def touch(self, client_id: Optional[str]) -> None:
        if not client_id:
            return
        with self._lock:
            if client_id in self.clients:
                self.clients[client_id]["last_seen"] = round(time.time(), 3)

    def _gc_locked(self) -> int:
        now = time.time()
        dead = [
            cid
            for cid, rec in self.clients.items()
            if now - float(rec.get("last_seen") or 0) > CLIENT_TTL_SEC
        ]
        for cid in dead:
            self.clients.pop(cid, None)
            if self.last_client_id == cid:
                self.last_client_id = None
        return len(dead)

    def alive_clients(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            self._gc_locked()
            return dict(self.clients)

    def target_client(self, explicit: Optional[str] = None) -> Optional[str]:
        """决定画布指令发给谁：优先显式指定，否则取最近活跃的那个活客户端。"""
        with self._lock:
            self._gc_locked()
            if explicit and explicit in self.clients:
                return explicit
            alive = sorted(
                self.clients.items(),
                key=lambda kv: float(kv[1].get("last_seen") or 0),
                reverse=True,
            )
            if alive:
                return alive[0][0]
            return None

    # ------------------------------------------------------------ 画布快照
    def set_canvas(self, graph: Dict[str, Any], reporter: Optional[str] = None) -> None:
        with self._lock:
            self.canvas = {
                "node_count": len(graph.get("nodes", []) or []),
                "link_count": len(graph.get("links", []) or []),
                "graph": graph,
            }
            self.canvas_ts = time.time()
            self.canvas_reporter = reporter

    def canvas_summary(self) -> Dict[str, Any]:
        with self._lock:
            if not self.canvas:
                return {"available": False}
            return {
                "available": True,
                "node_count": self.canvas["node_count"],
                "link_count": self.canvas["link_count"],
                "age_s": round(time.time() - self.canvas_ts, 1),
                "reporter": self.canvas_reporter,
            }

    def canvas_graph(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self.canvas.get("graph") if self.canvas else None

    # ------------------------------------------------------------ 运行快照
    def set_run(self, run: Optional[Dict[str, Any]]) -> None:
        with self._lock:
            self.run = run

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            self._gc_locked()
            return {
                "uptime_s": round(time.time() - self.boot_ts, 1),
                "clients": list(self.clients.keys()),
                "last_client_id": self.last_client_id,
                "target_client": self.target_client(),
                "client_ttl_s": CLIENT_TTL_SEC,
                "canvas": self.canvas_summary(),
                "run": self.run,
                "modules": dict(self.modules),
            }


state = DuddYState()
