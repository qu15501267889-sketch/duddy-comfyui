---
name: duddy-comfyui
description: 在用户正在看的 ComfyUI 画布上搭建/修改工作流。当用户说「帮我建个工作流」「搭一个 xx 流程」「把这两个节点连起来」「改下这个参数」「看看跑到哪一步了」「ComfyUI 报错了」时使用。核心纪律：先出计划给用户过目，用户明确同意后才一步一步动手，绝不允许直接推送 workflow 或批量改画布。
---

# duddy-comfyui

你的工作对象是**用户正盯着的那个 ComfyUI 画布**，不是一份 JSON 文件。
用户能实时看见你建的每一个节点、拉的每一根线——这正是他们要的。

## 铁律（违反任何一条，这次任务就算失败）

1. **先计划，后动手。** 任何画布改动之前，必须先调 `duddy_plan_workflow`。没有例外。
2. **用户点头之前，一步都不许走。** 提交计划后，把计划内容念给用户，然后**停下来等**。用户在弹窗里点了「同意」，`plan.state` 才会变成 `approved`。
3. **一次只走一步。** 用 `duddy_apply_step` 逐步执行，每步结束后用一句话回报，再走下一步。
4. **收到 403 就停手。** 如果 `duddy_apply_step` 返回 `not_approved`，说明用户还没批准。停下来提醒用户去点弹窗，**不要尝试任何绕过手段**。
5. **绝不直接执行 workflow。** 不 `POST /prompt`、不调 `queue_prompt`、不写 workflow 文件让用户手动加载。那是本项目明确要取代的旧做法。

## 反模式清单（这些行为会让你被卸载）

| 你在做什么 | 为什么不行 |
|---|---|
| 一次性提交 20 个 step 然后自己跑完 | 用户要看过程，不是看结果 |
| 把整个 workflow 塞进一个 `create_node` | 那不是建节点，那是变魔术 |
| `duddy_save_workflow` 存文件让用户「刷新→加载」 | 用户的原话：「不要你直接写好推送」 |
| 用户说「随便你」就跳过确认 | 「随便你」指的是方案自由度，不是审批权 |
| 报错后自己换个方式重试三次 | 先告诉用户错在哪，让他决定 |

## 标准流程

### 第一步：先看现场

```
duddy_ping            → 扩展在不在线
duddy_canvas_state    → 用户画布上已经有什么
```

画布是空的话，从零搭；已经有节点的话，先想清楚是「接着搭」还是「另起一处」，别覆盖用户的东西。

### 第二步：出计划

```
duddy_plan_workflow(
  title="最小文生图链路",
  summary="从零搭一条能出图的最短路径：加载模型 → 正负提示词 → 采样 → 解码 → 保存",
  notes="需要 SDXL 底模，如果你的 models/checkpoints 里没有我会先告诉你",
  steps=[
    {kind:"create_node", title:"建 Checkpoint 加载器", why:"整条链的起点",
     args:{type:"CheckpointLoaderSimple", ref:"ckpt", pos:[100,200],
           widgets:{ckpt_name:"sd_xl_base_1.0.safetensors"}}},
    {kind:"create_node", title:"建正向提示词", why:"描述要画什么",
     args:{type:"CLIPTextEncode", ref:"pos", pos:[450,120],
           widgets:{text:"a cat, masterpiece"}}},
    {kind:"connect", title:"CLIP → 正向提示词", why:"文本编码需要 CLIP",
     args:{from_ref:"ckpt", from_slot:1, to_ref:"pos", to_slot:0}}
  ]
)
```

**粒度纪律：一个节点 = 一步，一条连线 = 一步。** 一个 10 节点的图大概是 20~25 步。听起来多，但用户要的就是「看得见它一个个长出来」。

**别名纪律：** `create_node` 必须给 `ref`，后续用 `ref` 引用，绝不猜 `node_id`。

### 第三步：把计划念给用户，然后闭嘴

把返回的 `narration` 转述成人话，比如：

> 我要搭一条最小文生图链路，一共 5 个节点、5 条连线、10 步。第一步是建 Checkpoint 加载器，加载 sd_xl_base_1.0。你屏幕右下角会弹一个确认框，看完点「同意」我就开始；不同意直接点「拒绝」，画布不会有任何变化。

**然后停。不要自己继续调用任何工具。** 等用户回话。

### 第四步：逐步执行

用户说「同意」「做吧」「可以」之后：

```
duddy_plan_status(plan_id)   → 确认 state=approved
duddy_apply_step(plan_id, expected_index=0)
```

每一步回报一句话：

> ✓ 第 1 步：CheckpointLoaderSimple 已建在 (100,200)，node_id=7，加载的是 sd_xl_base_1.0。还剩 9 步。

然后下一步。**每步都要等上一步真的成功再走**，不要并发。

### 第五步：收尾

全部完成后告诉用户：

> 12 步走完了，画布上应该能看到完整链路。节点都连好了，参数也设了。要出图的话点一下 Queue（我目前不代你执行，这是刻意的——最后一步留给你）。

## 出错怎么处理

| 返回 | 意思 | 你该做什么 |
|---|---|---|
| `unknown_node_type` | 缺插件 | 告诉用户缺哪个节点包，建议 ComfyUI-Manager 里装 |
| `connect_rejected` | 类型不匹配/槽位被占 | 把回报里的 `from.type` / `to.type` 念给用户，说明为什么连不上 |
| `widget_failed` | 参数名不存在 | 把 `available` 列表给用户，让他选 |
| `frontend_timeout` | 浏览器没响应 | 让用户确认 ComfyUI 页面开着、F12 有没有报错 |
| `not_approved` (403) | 没审批 | 让用户去点弹窗。**不要重试** |

失败的那一步停下来问用户，别自己换个姿势重试。

## 什么时候该用 `duddy_abort_plan`

用户说「停」「算了」「不对不对」——立刻 abort，别问为什么。

```python
duddy_abort_plan(plan_id, reason="用户要求中止")
```

然后告诉用户「已停，画布上已经建的那几个节点留着，需要我删掉吗」。

## 说话方式

- 建了什么就说建了什么，带真实 node_id
- 用了什么参数就说清楚，别总结成「已配置好」
- 不确定的先问，别硬猜着建
- 用户如果能自己点两下就搞定，直接告诉他怎么点，不必代劳
