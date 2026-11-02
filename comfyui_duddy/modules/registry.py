"""节点 / 模型知识库 + 计划预检。

解决两个要命的问题：

1. **别人机器上没有这个节点** —— 计划里写死 `QwenImageIntegratedKSampler`，
   没装这个包的人会执行到一半才失败。现在规划阶段就拦住。
2. **参数名/参数值靠猜** —— widget 名写错、模型文件名不存在，
   以前要等前端 applyWidgets 失败才知道。现在提交前就报。

数据来源全部是 ComfyUI 自己的运行时注册表（nodes.NODE_CLASS_MAPPINGS
和 folder_paths），不维护静态清单，所以永远和当前安装一致。
"""

from __future__ import annotations

import difflib
import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("duddy.registry")

#: 这些 widget 类型的可选值来自 models 目录，做存在性校验
_MODEL_FOLDERS = {
    "ckpt_name": "checkpoints",
    "unet_name": ["diffusion_models", "unet"],
    "clip_name": ["text_encoders", "clip"],
    "vae_name": "vae",
    "lora_name": "loras",
    "control_net_name": "controlnet",
}


class NodeRegistry:
    def __init__(self) -> None:
        self._nodes: Dict[str, Dict[str, Any]] = {}
        self._loaded_at: float = 0.0
        self._lock = threading.RLock()
        self._model_cache: Dict[str, Tuple[float, List[str]]] = {}

    # ------------------------------------------------------------------ 装载
    def load(self, force: bool = False) -> int:
        with self._lock:
            if self._nodes and not force and time.time() - self._loaded_at < 300:
                return len(self._nodes)
            try:
                import nodes as comfy_nodes  # type: ignore

                mapping = getattr(comfy_nodes, "NODE_CLASS_MAPPINGS", {}) or {}
                fresh: Dict[str, Dict[str, Any]] = {}
                for type_name, cls in mapping.items():
                    try:
                        required = {}
                        optional = {}
                        it = cls.INPUT_TYPES() if hasattr(cls, "INPUT_TYPES") else {}
                        required = it.get("required", {}) or {}
                        optional = it.get("optional", {}) or {}
                        fresh[type_name] = {
                            "type": type_name,
                            "display_name": getattr(cls, "DESCRIPTION", "")[:200] or type_name,
                            "category": str(getattr(cls, "CATEGORY", "")),
                            "required": {k: _spec(v) for k, v in required.items()},
                            "optional": {k: _spec(v) for k, v in optional.items()},
                            "outputs": list(getattr(cls, "RETURN_TYPES", ()) or ()),
                            "output_names": list(getattr(cls, "RETURN_NAMES", ()) or ()),
                        }
                    except Exception:
                        fresh[type_name] = {"type": type_name, "required": {}, "optional": {}, "outputs": []}
                self._nodes = fresh
                self._loaded_at = time.time()
                logger.info("[duddy] 节点知识库已载入 %d 个类型", len(fresh))
                return len(fresh)
            except Exception as e:
                logger.warning("[duddy] 载入节点知识库失败: %s", e)
                return 0

    # ------------------------------------------------------------------ 查询
    def has(self, type_name: str) -> bool:
        self.load()
        return type_name in self._nodes

    def get(self, type_name: str) -> Optional[Dict[str, Any]]:
        self.load()
        return self._nodes.get(type_name)

    def search(self, keyword: str, limit: int = 20) -> List[Dict[str, Any]]:
        self.load()
        kw = (keyword or "").lower()
        if not kw:
            return []
        hits = []
        for name, info in self._nodes.items():
            score = 0
            if kw in name.lower():
                score += 10
            if kw in info.get("category", "").lower():
                score += 3
            if kw in info.get("display_name", "").lower():
                score += 2
            if score:
                hits.append((score, name, info))
        hits.sort(key=lambda x: (-x[0], x[1]))
        return [
            {
                "type": n,
                "category": i.get("category"),
                "required": list(i.get("required", {}).keys()),
                "optional": list(i.get("optional", {}).keys()),
                "outputs": i.get("outputs", []),
            }
            for _, n, i in hits[:limit]
        ]

    def suggest(self, type_name: str, n: int = 5) -> List[str]:
        self.load()
        return difflib.get_close_matches(type_name, list(self._nodes.keys()), n=n, cutoff=0.5)

    def widgets_of(self, type_name: str) -> Dict[str, Any]:
        """返回该节点可用的 widget/输入名 -> 规格。"""
        info = self.get(type_name) or {}
        out = dict(info.get("required") or {})
        out.update(info.get("optional") or {})
        return out

    # ------------------------------------------------------------------ 模型
    def model_list(self, folder_key: str) -> List[str]:
        """按 folder_paths 的 folder key 列模型文件名（带 20s 缓存）。"""
        now = time.time()
        hit = self._model_cache.get(folder_key)
        if hit and now - hit[0] < 20:
            return hit[1]
        try:
            import folder_paths  # type: ignore

            files = list(folder_paths.get_filename_list(folder_key) or [])
        except Exception:
            files = []
        self._model_cache[folder_key] = (now, files)
        return files

    def _resolve_folder(self, widget_name: str) -> Optional[str]:
        """把 widget 名映射到 folder_paths 的 key。"""
        candidates = _MODEL_FOLDERS.get(widget_name)
        if not candidates:
            return None
        if isinstance(candidates, str):
            candidates = [candidates]
        try:
            import folder_paths  # type: ignore

            known = set(getattr(folder_paths, "folder_names_and_paths", {}).keys())
        except Exception:
            known = set()
        for c in candidates:
            if c in known:
                return c
        return candidates[0] if candidates else None


def _spec(raw: Any) -> Dict[str, Any]:
    """把 INPUT_TYPES 的一项压缩成可读规格。"""
    try:
        if not isinstance(raw, (list, tuple)) or not raw:
            return {"kind": "unknown"}
        kind = raw[0]
        meta = raw[1] if len(raw) > 1 and isinstance(raw[1], dict) else {}
        spec: Dict[str, Any] = {"kind": kind if isinstance(kind, str) else "COMBO"}
        if isinstance(kind, (list, tuple)):
            spec["kind"] = "COMBO"
            spec["options"] = [str(x) for x in list(kind)[:200]]
            spec["option_count"] = len(kind)
        for k in ("default", "min", "max", "step", "multiline", "tooltip"):
            if k in meta:
                v = meta[k]
                spec[k] = v if not isinstance(v, str) or len(v) < 200 else v[:200]
        return spec
    except Exception:
        return {"kind": "unknown"}


registry = NodeRegistry()


# ---------------------------------------------------------------- 计划预检


def precheck(steps: List[Dict[str, Any]], strict_models: bool = True) -> Dict[str, List[str]]:
    """在真正动手之前，检查这份计划在这台机器上跑不跑得通。

    返回 {"errors": [...], "warnings": [...]}。
    errors 会让计划被拒绝（避免做一半）；warnings 只提示。
    """
    registry.load()
    errors: List[str] = []
    warnings: List[str] = []
    refs: Dict[str, str] = {}  # ref -> node type

    for i, step in enumerate(steps, 1):
        kind = step.get("kind")
        args = step.get("args") or {}
        label = f"第 {i} 步（{kind}）"

        if kind == "create_node":
            t = str(args.get("type") or "")
            if not registry.has(t):
                sug = registry.suggest(t)
                hint = f"，你是不是想用 {' / '.join(sug)}？" if sug else ""
                errors.append(f"{label}：节点类型 '{t}' 在这台机器上不存在{hint}")
                continue
            refs[str(args.get("ref") or t)] = t

            specs = registry.widgets_of(t)
            for wname, wval in (args.get("widgets") or {}).items():
                if wname not in specs:
                    avail = list(specs.keys())
                    errors.append(
                        f"{label}：'{t}' 没有参数 '{wname}'。可用参数：{', '.join(avail[:12])}"
                    )
                    continue
                spec = specs.get(wname) or {}
                opts = spec.get("options")
                if isinstance(opts, list) and opts and not isinstance(wval, bool):
                    if wval in opts:
                        continue
                    folder = registry._resolve_folder(wname)
                    if folder and strict_models:
                        files = registry.model_list(folder)
                        if wval not in files:
                            near = difflib.get_close_matches(str(wval), files, n=3, cutoff=0.4)
                            hint = f"，相近的有 {near}" if near else ""
                            errors.append(
                                f"{label}：模型文件 '{wval}' 不在 {folder} 目录里{hint}"
                                f"（该目录共 {len(files)} 个文件）"
                            )
                    else:
                        errors.append(
                            f"{label}：'{wname}' 的值 '{wval}' 不在可选项里。"
                            f"前几个可选：{opts[:6]}"
                        )

            # 必需连线型输入（非 widget）没接 → 只提示
            req = (registry.get(t) or {}).get("required", {})
            linky = [
                k for k, v in req.items()
                if v.get("kind") not in ("INT", "FLOAT", "STRING", "BOOLEAN", "COMBO")
                and k not in (args.get("widgets") or {})
            ]
            if linky:
                warnings.append(f"{label}：'{t}' 的连线型必填输入 {linky} 需由后续 connect 提供")

        elif kind == "connect":
            for side in ("from", "to"):
                ref = args.get(f"{side}_ref")
                if ref and str(ref) not in refs and not args.get(f"{side}_node_id"):
                    errors.append(f"{label}：引用了未创建的别名 '{ref}'")

        elif kind in ("set_widget", "move_node", "delete_node"):
            ref = args.get("ref")
            if ref and str(ref) not in refs and not args.get("node_id"):
                errors.append(f"{label}：引用了未创建的别名 '{ref}'")

    return {"errors": errors, "warnings": warnings}
