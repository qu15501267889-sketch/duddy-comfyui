"""M5 · 文件变更监控。

ComfyUI 的嵌入式 Python 普遍没有 watchdog，装不上也不能强求。
所以这里是**零依赖轮询实现**：定时 stat 一遍关键目录，diff 出增删改。

如果环境里恰好有 watchdog，也只是更快一点，接口不变。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

from ..core.bus import bus

logger = logging.getLogger("duddy.fswatch")

INTERVAL_SEC = 2.0
MAX_FILES = 20000
MAX_PATHS_PER_EVENT = 40

IGNORE_DIRS = {
    "__pycache__", ".git", ".github", "node_modules", ".venv", "venv",
    "temp", ".cache", ".idea", ".vscode", "__pypackages__",
}
IGNORE_EXT = {
    ".pyc", ".pyo", ".pyd", ".tmp", ".temp", ".lock", ".log",
    ".swp", ".part", ".crdownload",
}

#: 只关心这些子目录（相对 ComfyUI 根）
WATCH_SUBDIRS = ["custom_nodes", "models", "user", "input", "output"]


class FsWatch:
    def __init__(self) -> None:
        self._snapshot: Dict[str, Tuple[float, int]] = {}
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._events: deque = deque(maxlen=500)
        self._roots: List[Tuple[str, str]] = []
        self._scans = 0
        self._last_scan_ms = 0.0
        self._installed = False
        self._muted_until = 0.0

    # ------------------------------------------------------------------ 安装
    def install(self, base_dir: Optional[str]) -> bool:
        if self._installed:
            return True
        if not base_dir or not os.path.isdir(base_dir):
            logger.warning("[duddy] 文件监控未启动：拿不到 ComfyUI 根目录")
            return False

        for sub in WATCH_SUBDIRS:
            p = os.path.join(base_dir, sub)
            if os.path.isdir(p):
                self._roots.append((sub, p))

        if not self._roots:
            logger.warning("[duddy] 文件监控未启动：没有可监听的目录")
            return False

        # 基线扫描：启动时不报事件，否则一上来就刷几百条
        t0 = time.time()
        self._snapshot = self._scan()
        logger.info(
            "[duddy] 文件监控已启动：%d 个目录 / %d 个文件 / 基线 %.0fms",
            len(self._roots), len(self._snapshot), (time.time() - t0) * 1000,
        )

        self._thread = threading.Thread(target=self._loop, name="duddy-fswatch", daemon=True)
        self._thread.start()
        self._installed = True
        return True

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------ 扫描
    def _scan(self) -> Dict[str, Tuple[float, int]]:
        out: Dict[str, Tuple[float, int]] = {}
        for _label, root in self._roots:
            try:
                for dirpath, dirnames, filenames in os.walk(root, topdown=True, onerror=lambda e: None):
                    dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".")]
                    for fn in filenames:
                        ext = os.path.splitext(fn)[1].lower()
                        if ext in IGNORE_EXT:
                            continue
                        p = os.path.join(dirpath, fn)
                        try:
                            st = os.stat(p)
                            out[p] = (st.st_mtime, st.st_size)
                        except OSError:
                            continue
                        if len(out) >= MAX_FILES:
                            return out
            except Exception as e:
                logger.debug("[duddy] 扫描 %s 出错: %s", root, e)
        return out

    def _loop(self) -> None:
        while not self._stop.wait(INTERVAL_SEC):
            try:
                self.poll_once()
            except Exception as e:
                logger.debug("[duddy] 文件轮询异常: %s", e)

    def poll_once(self) -> int:
        t0 = time.time()
        new = self._scan()
        self._last_scan_ms = round((time.time() - t0) * 1000, 1)
        self._scans += 1

        with self._lock:
            old = self._snapshot
            self._snapshot = new

        created, modified = [], []
        for p, meta in new.items():
            prev = old.get(p)
            if prev is None:
                created.append(p)
            elif prev != meta:
                modified.append(p)
        deleted = [p for p in old if p not in new]

        if not (created or modified or deleted):
            return 0
        if time.time() < self._muted_until:
            return 0

        evt = {
            "created": created[:MAX_PATHS_PER_EVENT],
            "modified": modified[:MAX_PATHS_PER_EVENT],
            "deleted": deleted[:MAX_PATHS_PER_EVENT],
            "counts": {"created": len(created), "modified": len(modified), "deleted": len(deleted)},
            "ts": round(time.time(), 3),
        }
        with self._lock:
            self._events.append(evt)
        bus.publish("duddy.fs.change", evt, replay=False)
        return sum(evt["counts"].values())

    # ------------------------------------------------------------------ 查询
    def recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._events)[-limit:]

    def mute(self, seconds: float) -> None:
        """临时静音（例如我们自己正在装节点，避免刷屏）。"""
        self._muted_until = time.time() + seconds

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            total = len(self._snapshot)
        return {
            "installed": self._installed,
            "watched_dirs": [r[0] for r in self._roots],
            "files": total,
            "scans": self._scans,
            "last_scan_ms": self._last_scan_ms,
            "interval_s": INTERVAL_SEC,
            "recent_events": len(self._events),
        }


fswatch = FsWatch()
