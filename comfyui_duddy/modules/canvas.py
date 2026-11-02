"""画布操作模型：定义 step 的合法形状 + 计划期静态校验。

节点引用用「别名（ref）」而不是真实 node_id —— 因为规划阶段节点还没创建，
AI 不可能知道 ComfyUI 分配的 id。前端执行时维护 ref -> node 的映射。

一个典型计划：
    1. create_node  ref=ckpt  type=CheckpointLoaderSimple
    2. create_node  ref=pos   type=CLIPTextEncode
    3. connect      from_ref=ckpt from_slot=1  to_ref=pos to_slot=0
    ...

这样 AI 只描述「谁连谁」，不猜 id，也不会写错。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

REQUIRED_ARGS: Dict[str, List[str]] = {
    "create_node": ["type"],
    "connect": ["from_slot", "to_slot"],
    "disconnect": [],
    "set_widget": ["widget"],
    "set_title": ["title"],
    "move_node": ["pos"],
    "delete_node": [],
    "group": [],
    "note": [],
    "execute": [],
}


def _ref_of(step: Dict[str, Any]) -> Optional[str]:
    args = step.get("args") or {}
    ref = args.get("ref")
    if ref:
        return str(ref)
    if step.get("kind") == "create_node":
        return str(args.get("type") or "")
    return None


def validate_plan(steps: List[Dict[str, Any]]) -> List[str]:
    """静态校验整个计划，返回问题列表（空表示通过）。

    只做「不需要运行时信息」的检查：参数齐全、别名可解析、连线两端都存在。
    真正的类型匹配（MODEL/CLIP/LATENT 对不对）要等前端执行时才知道，
    那是前端的活儿。
    """
    issues: List[str] = []
    known_refs: Set[str] = set()

    for i, step in enumerate(steps, 1):
        kind = step.get("kind", "")
        args = step.get("args") or {}

        missing = [k for k in REQUIRED_ARGS.get(kind, []) if args.get(k) in (None, "")]
        for m in missing:
            issues.append(f"第 {i} 步（{kind}）缺少参数 '{m}'")

        if kind == "create_node":
            ref = _ref_of(step)
            if ref:
                if ref in known_refs:
                    issues.append(f"第 {i} 步别名 '{ref}' 重复定义")
                known_refs.add(ref)

        elif kind == "connect":
            for side in ("from", "to"):
                ref = args.get(f"{side}_ref")
                if ref and str(ref) not in known_refs and not args.get(f"{side}_node_id"):
                    issues.append(
                        f"第 {i} 步连线引用了不存在的别名 '{ref}'（尚未在任何 create_node 中定义）"
                    )
            if not (args.get("from_ref") or args.get("from_node_id")):
                issues.append(f"第 {i} 步 connect 缺少来源节点")
            if not (args.get("to_ref") or args.get("to_node_id")):
                issues.append(f"第 {i} 步 connect 缺少目标节点")

        elif kind in ("set_widget", "move_node", "delete_node"):
            ref = args.get("ref")
            if ref and str(ref) not in known_refs and not args.get("node_id"):
                issues.append(f"第 {i} 步引用了不存在的别名 '{ref}'")

    return issues


def summarise(steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    """给人类看的统计：这个计划到底要干什么。"""
    counts: Dict[str, int] = {}
    for s in steps:
        counts[s.get("kind", "?")] = counts.get(s.get("kind", "?"), 0) + 1
    creates = counts.get("create_node", 0)
    connects = counts.get("connect", 0)
    return {
        "total_steps": len(steps),
        "creates": creates,
        "connects": connects,
        "by_kind": counts,
        "one_liner": f"将创建 {creates} 个节点、建立 {connects} 条连线，共 {len(steps)} 步",
    }


def describe_step(step: Dict[str, Any]) -> str:
    kind = step.get("kind")
    a = step.get("args") or {}
    if kind == "create_node":
        return f"创建 {a.get('type')}（别名 {a.get('ref') or a.get('type')}）"
    if kind == "connect":
        return (
            f"连线 {a.get('from_ref', a.get('from_node_id'))}[{a.get('from_slot')}]"
            f" → {a.get('to_ref', a.get('to_node_id'))}[{a.get('to_slot')}]"
        )
    if kind == "set_widget":
        return f"设置 {a.get('ref', a.get('node_id'))}.{a.get('widget')} = {a.get('value')!r}"
    if kind == "move_node":
        return f"移动 {a.get('ref')} 到 {a.get('pos')}"
    if kind == "delete_node":
        return f"删除 {a.get('ref', a.get('node_id'))}"
    return f"{kind}"
