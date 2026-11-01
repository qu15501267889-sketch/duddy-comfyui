"""计划状态机测试。不依赖 ComfyUI 运行时，纯逻辑验证。

跑法：
    python -m pytest tests/test_plan.py -q
或者不装 pytest：
    python tests/test_plan.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from comfyui_duddy.core.plan import PlanError, plan_manager  # noqa: E402


def _steps():
    return [
        {
            "kind": "create_node",
            "title": "建 Checkpoint 加载器",
            "why": "整条链路的起点",
            "args": {"type": "CheckpointLoaderSimple", "ref": "ckpt", "pos": [100, 100]},
        },
        {
            "kind": "create_node",
            "title": "建正向提示词",
            "why": "描述要画什么",
            "args": {"type": "CLIPTextEncode", "ref": "pos"},
        },
        {
            "kind": "connect",
            "title": "连 CLIP → 正向提示词",
            "why": "文本编码需要 CLIP",
            "args": {"from_ref": "ckpt", "from_slot": 1, "to_ref": "pos", "to_slot": 0},
        },
    ]


def expect_raises(fn, code, label):
    try:
        fn()
    except PlanError as e:
        assert e.code == code, f"{label}: 期望 {code}，实际 {e.code} ({e})"
        print(f"  ✓ {label} → {code} {e.kind}")
        return
    raise AssertionError(f"{label}: 期望抛 PlanError({code})，但没有抛")


def main():
    print("=" * 62)
    print("duddy-comfyui · 计划状态机验证")
    print("=" * 62)

    # 1. 创建
    plan, token = plan_manager.create(
        title="最小文生图链路",
        summary="验证闸门是否真的拦得住",
        steps=_steps(),
    )
    assert plan.state == "proposed", plan.state
    assert len(plan.steps) == 3
    assert token and len(token) > 20
    print(f"1 ✓ 创建 plan {plan.id} · state={plan.state} · steps={len(plan.steps)}")
    assert "token" not in plan.public(), "public() 绝不能包含 token"
    print("2 ✓ public() 视图不含 token")

    # 2. 未批准时不能执行
    expect_raises(lambda: plan_manager.begin_step(plan.id), 403, "未批准就 apply")

    # 3. 错 token 不能批准
    expect_raises(lambda: plan_manager.approve(plan.id, "wrong-token"), 403, "错误 token 批准")

    # 4. 空 token 不能批准
    expect_raises(lambda: plan_manager.approve(plan.id, ""), 403, "空 token 批准")

    # 5. 正确 token 批准
    approved = plan_manager.approve(plan.id, token)
    assert approved.state == "approved", approved.state
    print("3 ✓ 正确 token 批准 → state=approved")

    # 6. token 一次性：重复使用应失效
    expect_raises(lambda: plan_manager.approve(plan.id, token), 409, "重复使用同一 token")

    # 7. 逐步骤执行
    p, idx, step = plan_manager.begin_step(plan.id, expected_index=0)
    assert idx == 0 and p.state == "applying", (idx, p.state)
    print("4 ✓ 第 0 步 → state=applying")

    plan_manager.record_result(plan.id, 0, True, {"node_id": 7})
    assert plan_manager.get(plan.id).cursor == 1
    print("5 ✓ 第 0 步完成 → cursor=1")

    # 8. 跳步必须被拒
    expect_raises(lambda: plan_manager.begin_step(plan.id, expected_index=2), 409, "跳步")

    # 9. 重复回报同一 index 必须被拒
    expect_raises(lambda: plan_manager.record_result(plan.id, 0, True), 409, "重复回报旧步骤")

    # 10. 失败 → failed
    plan_manager.begin_step(plan.id, expected_index=1)
    plan_manager.record_result(plan.id, 1, False, {"reason": "connect_rejected"})
    assert plan_manager.get(plan.id).state == "failed"
    print("6 ✓ 步骤失败 → state=failed")

    # 11. 重试后继续
    plan_manager.retry(plan.id)
    plan_manager.begin_step(plan.id, expected_index=1)
    plan_manager.record_result(plan.id, 1, True, {"ok": 1})
    plan_manager.begin_step(plan.id, expected_index=2)
    final = plan_manager.record_result(plan.id, 2, True, {"ok": 1})
    assert final.state == "done", final.state
    print("7 ✓ 重试 → 全部完成 → state=done")

    # 12. 完成后不能再执行
    expect_raises(lambda: plan_manager.begin_step(plan.id), 403, "已完成计划再执行")

    # 13. 非法 kind
    try:
        plan_manager.create(title="x", summary="y", steps=[{"kind": "hack_gpu", "args": {}}])
        raise AssertionError("非法 kind 应该被拒")
    except PlanError as e:
        assert e.code == 400
        print(f"  ✓ 非法 kind → {e.code} {e.kind}")

    # 14. 空 steps
    try:
        plan_manager.create(title="x", summary="y", steps=[])
        raise AssertionError("空 steps 应该被拒")
    except PlanError as e:
        assert e.code == 400
        print(f"  ✓ 空 steps → {e.code} {e.kind}")

    # 15. 拒绝流程
    p2, t2 = plan_manager.create(title="会被拒绝的计划", summary="", steps=_steps())
    rejected = plan_manager.reject(p2.id, "用户不想建", token=t2)
    assert rejected.state == "rejected"
    expect_raises(lambda: plan_manager.begin_step(p2.id), 403, "被拒计划再执行")
    print("8 ✓ 拒绝流程正确")

    print("=" * 62)
    print("全部通过：闸门会拦、跳步会拦、重放会拦、token 一次性")
    print("=" * 62)


if __name__ == "__main__":
    main()
