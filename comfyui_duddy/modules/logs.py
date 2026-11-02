"""M4 · 运行日志实时捕获。

两条来源一起抓：
  · logging Handler —— 覆盖 ComfyUI 自身和大多数插件的日志
  · stdout 劫持   —— 覆盖节点里直接 print 的输出（很多自定义节点都这么干）

日志进 ring buffer，同时节流推送 duddy.log.line，前端侧栏/后端 MCP 都能读。
"""

from __future__ import annotations

import logging
import re
import sys
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional

from ..core.bus import bus

logger = logging.getLogger("duddy.logs")

MAX_LINES = 3000
FLUSH_INTERVAL = 0.2
MAX_LINE_LEN = 2000

#: 这些前缀说明该行已经被 logging 捕获过，避免 stdout 重复推
_DUP_HINTS = re.compile(r"^\[?(INFO|WARNING|ERROR|DEBUG|CRITICAL)\]?|^\d{4}-\d{2}-\d{2}")


class LogTap:
    def __init__(self) -> None:
        self.buffer: deque = deque(maxlen=MAX_LINES)
        self._lock = threading.RLock()
        self._seq = 0
        self._pending: List[Dict[str, Any]] = []
        self._last_flush = 0.0
        self._installed = False
        self._orig_stdout = None
        self._orig_stderr = None

    # ------------------------------------------------------------------ 安装
    def install(self) -> bool:
        if self._installed:
            return True
        try:
            handler = _Handler(self)
            handler.setLevel(logging.INFO)
            logging.getLogger().addHandler(handler)

            self._orig_stdout = sys.stdout
            self._orig_stderr = sys.stderr
            sys.stdout = _Tee(self._orig_stdout, self, "stdout")
            sys.stderr = _Tee(self._orig_stderr, self, "stderr")

            self._installed = True
            logger.info("[duddy] 日志捕获已挂载")
            return True
        except Exception as e:
            logger.warning("[duddy] 日志捕获安装失败: %s", e)
            return False

    # ------------------------------------------------------------------ 写入
    def feed(self, level: str, source: str, message: str) -> None:
        message = (message or "").rstrip("\n")
        if not message.strip():
            return
        if len(message) > MAX_LINE_LEN:
            message = message[:MAX_LINE_LEN] + " …(truncated)"
        with self._lock:
            self._seq += 1
            rec = {
                "seq": self._seq,
                "ts": round(time.time(), 3),
                "level": level,
                "source": source,
                "msg": message,
            }
            self.buffer.append(rec)
            self._pending.append(rec)
            due = time.time() - self._last_flush >= FLUSH_INTERVAL
        if due:
            self.flush()

    def flush(self) -> None:
        with self._lock:
            if not self._pending:
                return
            batch = self._pending
            self._pending = []
            self._last_flush = time.time()
        bus.publish("duddy.log.line", {"lines": batch, "count": len(batch)}, replay=False)

    # ------------------------------------------------------------------ 查询
    def tail(self, limit: int = 100, since_seq: int = 0, level: Optional[str] = None,
             contains: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            items = list(self.buffer)
        out = []
        for r in items:
            if r["seq"] <= since_seq:
                continue
            if level and r["level"] != level.upper():
                continue
            if contains and contains.lower() not in r["msg"].lower():
                continue
            out.append(r)
        return out[-limit:]

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {"total": self._seq, "buffered": len(self.buffer), "installed": self._installed}


class _Handler(logging.Handler):
    def __init__(self, tap: LogTap) -> None:
        super().__init__()
        self.tap = tap

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.tap.feed(record.levelname, record.name, self.format(record))
        except Exception:
            pass


class _Tee:
    """劫持 stdout/stderr，原样透传的同时抓一份。"""

    def __init__(self, original, tap: LogTap, stream: str) -> None:
        self._original = original
        self._tap = tap
        self._stream = stream
        self._buf = ""

    def write(self, s):  # type: ignore[no-untyped-def]
        try:
            if self._original is not None:
                self._original.write(s)
        except Exception:
            pass
        try:
            self._buf += s
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                if line.strip() and not _DUP_HINTS.search(line):
                    self._tap.feed("PRINT", self._stream, line)
        except Exception:
            pass
        return len(s) if isinstance(s, str) else 0

    def flush(self):  # type: ignore[no-untyped-def]
        try:
            if self._buf.strip():
                self._tap.feed("PRINT", self._stream, self._buf)
                self._buf = ""
            if self._original is not None:
                self._original.flush()
        except Exception:
            pass

    def __getattr__(self, name):  # type: ignore[no-untyped-def]
        return getattr(self._original, name)


log_tap = LogTap()
