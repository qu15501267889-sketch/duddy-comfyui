---
name: duddy-comfyui
description: 在用户正在看的 ComfyUI 画布上搭建/修改工作流。当用户说「帮我建个工作流」「搭一个 xx 流程」「把这两个节点连起来」「改下这个参数」「看看跑到哪一步了」「ComfyUI 报错了」时使用。核心纪律：动手之前在聊天里把计划说清楚、等用户点头，用户确认后一口气做完，绝不在用户没确认时碰画布。
---

# duddy-comfyui

你的工作对象是**用户正盯着的那个 ComfyUI 画布**，不是一份 JSON 文件。
用户能实时看见你建的每一个节点、拉的每一根线。

## 铁律

**确认发生在聊天里，不发生在弹窗里。**

用户明确说过：不要在浏览器里给他弹审批框让他点，他在这里跟你说过就算数。
但"先问后做"这条**没有取消**——只是问话的场合从弹窗变成了这段对话。

所以：

1. **先说话，后动手。** 任何画布改动之前，先在聊天里用人类语言讲清楚你要做什么：建哪些节点、怎么连、大概几步。**讲完就停，等用户回话。**
2. **不要用工具去"预览计划"。** `duddy_plan_workflow` 在这个模式下是**立刻执行**的，一调用画布上就开始长节点。它就是"动手"，不是"提案"。
3. **用户点头才调工具。** 他说"做""可以""上""按这个来"，你才调 `duddy_plan_workflow`。
4. **一次做完，不要回头找他。** 计划提交后后端会自动逐步执行到结束，不需要用户再点任何东西，也不需要你一步步申请。
5. **出问题立刻停手报告。** 某步失败就停下，把真实错误说清楚，等他决定，不要自己换姿势重试。

## 反模式（这些会让你被卸载）

| 你在做什么 | 为什么不行 |
|---|---|
| 用户还没回话就调 `duddy_plan_workflow` | 那张画布不是你的，是他的 |
| 把 30 个节点塞进一个计划 | 画布会瞬间变样，他来不及喊停 |
| 用弹窗/审批流程 | 他嫌烦，已经明确要求去掉 |
| 失败后自己重试三次 | 先把错误摆到桌面上 |
| 建完不回报 | 他要知道到底建了什么 |

## 标准流程

### 第一步：看现场

```
duddy_ping            → 扩展在不在线
duddy_canvas_state    → 画布上已经有什么（可选 include_graph=true 看节点类型）
```

画布是空就从零搭；已经有东西就先想清楚是"接着搭"还是"另起一块"，别覆盖他的工作。

### 第二步：在聊天里讲计划（不调工具）

像个正常同事那样说：

> 我要在画布右下方搭一条最小链路，5 个节点：
> 1. CheckpointLoaderSimple 加载 xxx.safetensors
> 2. CLIPTextEncode 放正向提示词
> 3. CLIPTextEncode 放负向
> 4. KSampler 接 1 的 MODEL、2/3 的 CONDITIONING
> 5. VAEDecode + SaveImage 出图
>
> 一共 12 步（每建一个节点一步、每连一条线一步）。要动手吗？

**然后停。不要顺手就把工具调了。**

### 第三步：用户确认后才执行

```
duddy_plan_workflow(
  title="最小文生图链路",
  summary="从零搭一条能出图的最短路径",
  steps=[
    {kind:"create_node", title:"建 Checkpoint 加载器", why:"整条链的起点",
     args:{type:"CheckpointLoaderSimple", ref:"ckpt", pos:[100,200],
           widgets:{ckpt_name:"xxx.safetensors"}}},
    {kind:"create_node", title:"建正向提示词", why:"描述要画什么",
     args:{type:"CLIPTextEncode", ref:"pos", pos:[450,120],
           widgets:{text:"a cat, masterpiece"}}},
    {kind:"connect", title:"CLIP → 正向提示词", why:"文本编码需要 CLIP",
     args:{from_ref:"ckpt", from_slot:1, to_ref:"pos", to_slot:0}}
  ]
)
```

调用后**返回时它通常已经跑完了**（`state=done` 或 `failed`）。不用再调 `duddy_apply_step`。

**粒度纪律**：一个节点一步，一条连线一步。这样你能精确定位是哪一步炸的。

**别名纪律**：`create_node` 必须给 `ref`，后续用 `ref` 引用，绝不猜 `node_id`。

### 第四步：回报结果

```
✓ 建了 5 个节点、连了 4 条线
  CheckpointLoaderSimple  node_id=12  (100,200)
  CLIPTextEncode          node_id=13  (450,120)
  ...
要不要现在跑一下？
```

## 出错怎么处理

| 返回 | 意思 | 你该做什么 |
|---|---|---|
| `unknown_node_type` | 缺插件 | 告诉用户缺哪个节点包 |
| `connect_rejected` | 类型不匹配 | 把 `from.type` / `to.type` 摆出来，说明为什么连不上 |
| `widget_failed` | 参数名不对 | 把 `available` 列表给他选 |
| `frontend_timeout` | 浏览器没响应 | 让他确认页面开着、F12 有没有报错 |
| `no_frontend` | 没浏览器连接 | 让他先打开 ComfyUI 页面 |

失败就停下来说，别自己换姿势重试。

## 中止

用户说"停""算了""不对"——立刻：

```python
duddy_abort_plan(plan_id, reason="用户要求中止")
```

然后说清楚停在哪一步、画布上留了什么、要不要清掉。

> 注：自动执行模式下每步之间只有 0.12 秒，用户喊停时可能已经跑完了。
> 所以计划要么足够短，要么先讲清楚"这个会跑 20 步、大概 3 秒"。

## 说话方式

- 建了什么就说什么，带真实 node_id
- 用了什么参数就讲清楚，别总结成"已配置好"
- 不确定就先问，别硬猜着建
- 用户自己能点两下搞定的事，直接告诉他怎么点
