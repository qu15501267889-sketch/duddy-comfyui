"""duddy-comfyui —— 让 AI 在你的画布上「先规划、经你批准、再逐个动手」。

装载契约
--------
ComfyUI 加载自定义节点时会执行本文件。此处只做三件事：
  1. 声明 WEB_DIRECTORY（前端扩展会被自动注入到 /extensions/）
  2. 挂载 /duddy/* 路由
  3. 初始化守卫与持久化目录

本包不注册任何推理节点（NODE_CLASS_MAPPINGS 为空），
它是一层「控制平面」，不参与出图。
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("duddy")

__version__ = "0.1.0"

NODE_CLASS_MAPPINGS: dict = {}
NODE_DISPLAY_NAME_MAPPINGS: dict = {}
WEB_DIRECTORY = "./web"

_HERE = os.path.dirname(os.path.abspath(__file__))

_ready = False
_error: str = ""


def _init() -> None:
    global _ready, _error
    if _ready:
        return
    try:
        from .core import security
        from .core.bus import bus  # noqa: F401
        from .core.plan import plan_manager
        from .core.state import state
        from .api.routes import register_routes

        roots = security.init_guard()

        # 计划审计落盘：ComfyUI/user/default/duddy/plans
        store = None
        base = roots.get("user")
        if base:
            store = os.path.join(base, "default", "duddy", "plans")
        plan_manager.set_store_dir(store)
        if store:
            security.guard.add_root(os.path.join(base, "default", "duddy"))

        if not register_routes():
            _error = "routes registration failed"
            logger.error("[duddy] %s", _error)
            return

        state.modules["plan"] = "ready"
        _ready = True
        logger.info(
            "[duddy] v%s ready · roots=%s · store=%s",
            __version__,
            ",".join(sorted(roots.keys())) or "none",
            store or "memory-only",
        )
    except Exception as e:  # 绝不能因为本包把 ComfyUI 拖挂
        _error = f"{type(e).__name__}: {e}"
        logger.exception("[duddy] init failed: %s", _error)


_init()


def get_status() -> dict:
    return {"version": __version__, "ready": _ready, "error": _error}


__all__ = [
    "NODE_CLASS_MAPPINGS",
    "NODE_DISPLAY_NAME_MAPPINGS",
    "WEB_DIRECTORY",
    "get_status",
    "__version__",
]
