"""跨模块共享状态。进程内单例，重启即重置。"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional


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
            return rec

    def touch(self, client_id: str) -> None:
        with self._lock:
            if client_id in self.clients:
                self.clients[client_id]["last_seen"] = round(time.time(), 3)

    def target_client(self, explicit: Optional[str] = None) -> Optional[str]:
        """决定画布指令发给谁。单人场景下取最近活跃的那个。"""
        with self._lock:
            if explicit and explicit in self.clients:
                return explicit
            if self.last_client_id:
                return self.last_client_id
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
            return {
                "uptime_s": round(time.time() - self.boot_ts, 1),
                "clients": list(self.clients.keys()),
                "last_client_id": self.last_client_id,
                "canvas": self.canvas_summary(),
                "run": self.run,
                "modules": dict(self.modules),
            }


state = DuddYState()
