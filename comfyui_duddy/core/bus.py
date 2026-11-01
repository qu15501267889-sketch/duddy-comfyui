"""事件总线：所有模块的唯一出口。

设计要点
--------
1. 走 ComfyUI 原生 WebSocket（PromptServer.send_sync），不另开端口、不开新 ws 服务。
2. 支持定向投递：传 sid 时只推给那个浏览器标签页；不传则广播。
3. 保留 ring buffer，晚连上的客户端可以 replay 最近事件，避免「刚刷新就错过」。
4. 任何推送失败都不能让业务崩——一律吞异常并计数。

事件命名规范：duddy.<域>.<动作>
    duddy.plan.proposed / approved / rejected / done / failed / aborted
    duddy.canvas.op / applied / state
    duddy.run.progress / done / error        (M3)
    duddy.log.line                           (M4)
    duddy.fs.change                          (M5)
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional

logger = logging.getLogger("duddy.bus")

PROTOCOL_VERSION = 1


class EventBus:
    """进程内单例事件总线。线程安全，永不抛异常。"""

    def __init__(self, capacity: int = 600) -> None:
        self._buf: deque = deque(maxlen=capacity)
        self._lock = threading.RLock()
        self._seq = 0
        self.boot_ts = time.time()
        self.publish_count = 0
        self.drop_count = 0

    # ------------------------------------------------------------------ 内部
    @staticmethod
    def _server():
        """延迟导入 PromptServer，避免模块加载顺序问题。"""
        try:
            from server import PromptServer  # type: ignore

            return getattr(PromptServer, "instance", None)
        except Exception:
            return None

    def _send(self, event: str, payload: Dict[str, Any], sid: Optional[str]) -> bool:
        server = self._server()
        if server is None:
            return False
        try:
            if sid:
                server.send_sync(event, payload, sid)
            else:
                server.send_sync(event, payload)
            return True
        except TypeError:
            # 老版本 send_sync 不接受第三个参数
            try:
                server.send_sync(event, payload)
                return True
            except Exception:
                return False
        except Exception:
            return False

    # ------------------------------------------------------------------ 公开
    def publish(
        self,
        event: str,
        data: Optional[Dict[str, Any]] = None,
        sid: Optional[str] = None,
        replay: bool = True,
    ) -> int:
        with self._lock:
            self._seq += 1
            seq = self._seq
            payload = {
                "v": PROTOCOL_VERSION,
                "seq": seq,
                "ts": round(time.time(), 3),
                "type": event,
                "data": data or {},
            }
            if replay:
                self._buf.append(payload)
            self.publish_count += 1

        if not self._send(event, payload, sid):
            with self._lock:
                self.drop_count += 1
        return seq

    def recent(
        self,
        prefix: Optional[str] = None,
        limit: int = 100,
        since_seq: int = 0,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            items = list(self._buf)
        out = []
        for evt in items:
            if evt["seq"] <= since_seq:
                continue
            if prefix and not evt["type"].startswith(prefix):
                continue
            out.append(evt)
        return out[-limit:]

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "seq": self._seq,
                "buffered": len(self._buf),
                "published": self.publish_count,
                "dropped": self.drop_count,
                "uptime_s": round(time.time() - self.boot_ts, 1),
                "protocol_version": PROTOCOL_VERSION,
            }


bus = EventBus()
