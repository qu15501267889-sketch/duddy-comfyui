---
name: duddy-comfyui
description: 在用户正在看的 ComfyUI 画布上搭建/修改工作流。当用户说「帮我建个工作流」「搭一个 xx 流程」「把这两个节点连起来」「改下这个参数」「看看跑到哪一步了」「ComfyUI 报错了」时使用。核心纪律：动手之前在聊天里把计划说清楚、等用户点头，用户确认后一口气做完，绝不在用户没确认时碰画布。
---

# duddy-comfyui

你的工作对象是**用户正盯着的那个 ComfyUI 画布**，不是一份 JSON 文件。
用户能实时看见你建的每一个节点、拉的每一根线。

## 铁律

**确认发生在聊天里，不发生在弹窗里。**

1. **先说话，后动手。** 任何画布改动之前，先在聊天里用人类语言讲清楚：建哪些节点、怎么连、大概几步。**讲完就停，等用户回话。**
2. **不要用工具去"预览计划"。** `duddy_plan_workflow` 是**立刻执行**的，一调用画布上就开始长节点。它就是"动手"，不是"提案"。
3. **用户点头才调工具。** 他说"做""可以""上""按这个来"，你才调。
4. **一次做完，不要回头找他。** 计划提交后后端自动逐步执行到结束。
5. **出问题立刻停手报告。** 把真实错误说清楚，不要自己换姿势重试。

## 反模式

| 你在做什么 | 为什么不行 |
|---|---|
| 用户还没回话就调 `duddy_plan_workflow` | 那张画布不是你的 |
| 把 30 个节点塞进一个计划 | 画布瞬间变样，他来不及喊停 |
| 用弹窗/审批流程 | 他嫌烦，已明确要求去掉 |
| 失败后自己重试三次 | 先把错误摆到桌面上 |
| 建完不回报 | 他要知道到底建了什么 |

---

## 一、先认模型，再定参数

**看到工作流第一件事：判断是哪家模型。** 参数规则完全不同，套错就是废图。

### 怎么认

```
UNETLoader / UnetLoaderGGUF 的文件名   → 含 qwen / flux / sd_xl / pony
有没有 TextEncodeQwenImage21 节点      → 有就是 Qwen 2.1
CLIPLoader 的 type                     → qwen_image / sdxl / flux
```

### 参数规则表（照这个配，别凭感觉）

| | **Qwen-Image 2.1** | SDXL | Flux |
|---|---|---|---|
| **cfg** | **必须 1** | 5~8 | 3.5 |
| **denoise** | **必须 1** | 0.4~0.8 | 1 |
| **steps** | **25**（Turbo 8） | 20~30 | 20~28 |
| **sampler** | **euler** | dpmpp_2m | euler |
| **scheduler** | **simple** | karras | simple |
| **负向提示词** | **完全无效** | 有效 | 基本无效 |
| **提示词风格** | 自然语言句子 | 标签流可以 | 自然语言 |

### Qwen 2.1 的三条硬规则

**① cfg 必须 1。** 官方发布路径就是无引导采样。cfg=2 会更贴密集文字但边缘过锐，cfg=5 严重劣化，cfg=0.5 直接崩。

**② denoise 必须 1。** 除非 latent 来自原图 VAEEncode（那是真正的 latent 图生图），否则降到 0.7 只会得到"半张噪声+半个条件"，画面发灰。

**③ 负向提示词是废的。** ComfyUI 在 cfg=1 时**跳过整个负向条件分支**——不是权重 0，是压根不执行。
想约束画面，把禁令**改写成正向陈述**塞进正向提示词。

| ❌ 无效 | ✅ 有效 |
|---|---|
| 负向：blurry, low quality, deformed | 正向：画面清晰锐利，皮肤纹理自然，解剖结构准确 |

### steps 和 LoRA 必须配对

```
不挂加速 LoRA        → 25 步
挂 viggle-turbo      → 8 步
挂 4-step LoRA       → 4 步
```

**单独把 steps 调到 8 却不挂 Turbo LoRA，会出废图。** 这是最常见的错误。

### resolution 的两种哲学

Qwen 的 `TextEncodeQwenImage21` 有个 `resolution` 参数，含义是**像素预算**（不是宽高）：

- `1024` → 约 1 MP，缩放参考图，快、省显存 ✅ 默认选这个
- `0` → 不缩放，跟随原图。一张 4 MP 手机照会直接吃满显存

**8 GB 卡用 1024，别用 0。**

---

## 二、遇到这些节点别慌

### 子图（UUID 类型的节点）

画布上出现 `14dec9cc-3745-4009-ad71-89527a9f9620` 这种**不是节点名而是 UUID** 的东西——那是**子图（subgraph）**，不是未知节点。

它内部有一整套链路。外面那些参数节点只是往它里面灌数据。

**处理原则**：
- 不要试图改它的内部
- 不要动它的输入连线（端口按序号对应，改一个全乱且不报错）
- 要调参数，改**外面那些参数节点**
- 想理解它，去看 `graph.definitions.subgraphs` 里对应 id 的定义

### `SetNode` / `GetNode`（无线连接）

```
SetNode   起个变量名，比如 "MODEL"
GetNode   填同样的名字，取值
```

**`GetNode` 的输入看着是空的，但它是有的。** 只看连线会误判"链路断了"。

这是复杂工作流避免蜘蛛网的常用手法，画布出现频率极高。

### 自定义 DOM 面板节点

`AUSBoss_NODES_WorkflowNote` 这类节点的内容不在 `widget.value` 里，而是渲染进自定义 DOM。改完值需要触发 `onConfigure` 才重绘（duddy 前端已处理）。

---

## 三、标准流程

### 第一步：先查，再想，最后才说

**不要凭记忆写节点名和参数名。** 你脑子里的名字，用户机器上八成没有。

```
duddy_ping                       扩展在不在线
duddy_search_nodes("关键词")      这台机器上有哪些节点
duddy_node_info("节点类型")       要什么输入、有哪些 widget、可选值
duddy_list_models("vae")         模型文件名到底是啥
duddy_canvas_state               画布上已经有什么
```

规划前必须确认三件事：**类型存在、参数名存在、模型文件存在**。

### 第二步：提交前先模拟

```
duddy_precheck_plan(steps)
```

**passed=false 就改，别硬上。** 硬上的后果是执行到一半失败，画布留一堆半成品。

### 第三步：在聊天里讲计划（不调执行工具）

> 我要搭一条最小文生图链路，4 个节点 4 条连线：
> UnetLoaderGGUFAdvanced(Q8_0) + CLIPLoader + VAELoader → 集成采样器 → SaveImage
> 1024×1536，25 步，cfg 1。要动手吗？

**然后停。**

### 第四步：用户确认后执行

```
duddy_plan_workflow(title=..., summary=..., steps=[...])
```

auto 模式下**返回时通常已经跑完了**（`state=done` 或 `failed`）。

### 第五步：回报

```
✓ 建了 4 个节点、连了 4 条线，共 9 步
  UnetLoaderGGUFAdvanced  node_id=1  (80,80)  float16 dequant
  ...
```

---

## 四、六种常见任务的搭法

用户说"搭个 XX"，先对号入座，别从零想。

### ① 文生图（最小链路）

```
UnetLoader / UnetLoaderGGUF      → model
CLIPLoader (type=qwen_image)     → clip
VAELoader                        → vae
        ↓ 三者都接
TextEncodeQwenImage21  ['', '', '1024']
        ↓ positive / negative / latent
KSampler  [steps=25, cfg=1, euler, simple, denoise=1]
        ↓
VAEDecode → SaveImage
```

**文生图必须给 resolution 或 EmptyLatent 的宽高**，否则报错。

### ② 图生图 / 编辑

同上，但 `TextEncodeQwenImage21` 的 `image_1` 接 `LoadImage`。

**原图走 image_1 进编码器，不进 latent。** denoise 保持 1。
要"少改"就把 Restore LoRA 挂上（强度 1.0，糊图用 0.5）。

### ③ 放大 / 超分（分块重绘）

```
LoadImage → TiledUpscale(目标MP=4)  → [图块, 掩码]
        ↓
TextEncodeQwenImage21（每块 + 提示词）
        ↓
KSampler(8或25步) → VAEDecode → TiledUpscaleStitch → SaveImage
```

**超过 2 MP 必须分块**，重叠带 128 px 消缝。
提示词用现成的：`Restore the photo: remove blur, noise and compression artifacts...`

### ④ 扩图

```
LoadImagePad（四边扩展，填充 #808080 中灰）
        ↓
[可选] TextGenerate 给原图写描述 → StringConcatenate("指令" + " Scene: " + 描述)
        ↓
TextEncodeQwenImage21(resolution=0) → KSampler → VAEDecode
        ↓
StitchInpaint（把原图像素贴回）→ SaveImage
```

**填充色必须用中灰 `#808080`**，纯白纯黑会污染生成区。

### ⑤ 换装 / 换人

```
两个 LoraLoader：body_swap / head_swap
        ↓
PrimitiveBoolean + ComfySwitchNode 二选一
        ↓
TextEncodeQwenImage21（image_1=场景图, image_2=角色图）
```

**两个 LoRA 不能同时挂**，用开关切。
提示词用 `<image1>` / `<image2>` 明确指代。

### ⑥ 角色设定图（多角度一致）

```
同一角色挂 consistency LoRA
        ↓
一张图 → 多个 SaveImage（不同 seed/角度）
        ↓ 或
一次生成多视角拼图：提示词写 "Character reference sheet, same person three times side by side"
```

**核心是 consistency LoRA + 固定 seed**，换 seed 就换脸。

---

## 五、错误决策树

不要看到错误就停，先走一遍判断。

### 跑不起来

```
报 unknown_node_type
  → duddy_search_nodes 搜关键词
  → 找到相似名就换；找不到就是没装插件 → 告诉用户装什么

报 Required input is missing
  → 那个节点的必需输入没接
  → duddy_node_info 查 required 列表，对照连线看缺哪个

报 connect_rejected
  → 类型不匹配
  → 回报里有 from.type / to.type，摆给用户看
```

### 跑起来但慢得离谱

```
duddy_get_logs(contains="offloaded")
  → 有 offloaded 且超过总量一半
      → 是显存不足，不是算力问题
      → 让用户降分辨率（4 MP → 1 MP 约快 4 倍）
      → 或换更小的量化（Q8 → Q6/Q4）
  → 没有 offloaded
      → 看是不是 steps 太高（Qwen 25 步够，40 步是官方 pipeline）
```

### 出图不对

```
画面糊 / 发灰
  → denoise 是不是被调低了？改回 1
  → 是不是空 latent + 低 denoise？改回 1

边缘过锐 / 色彩发脏
  → cfg 是不是 > 3？改回 1

主体没变（人脸没换、衣服没换）
  → image_1 连上了吗？duddy_canvas_state 看连线
  → LoRA 挂上了吗？strength 是不是 0？
  → 提示词里有没有明确说"把 <image2> 的衣服换到 <image1> 的人身上"

加了负向提示词但没效果
  → 正常，cfg=1 下负向词不生效
  → 改写成正向陈述

黑图 / 花屏
  → fp16 溢出，dequant_dtype 改 float32
  → 或画面尺寸太大爆显存
```

### 卡住不动

```
duddy_run_status
  → status=running 但 step 长时间不变
      → 可能在做 CPU offload 搬运，正常但极慢
      → 看日志有没有 "loaded partially"
  → status=stale（90 秒无事件）
      → 任务已死，duddy_abort_plan 清掉重来
  → 队列里有 pending
      → 前面还有任务，等
```

---

## 六、布局规范（别把画布炸了）

```
左侧竖排    模型加载（UNET / CLIP / VAE / LoRA）
中间        主链路（编码 → 采样 → 解码）
右侧        输出（SaveImage / PreviewImage）
上方或下方  参数节点（Text / Seed / Integer / Boolean）
```

三条纪律：

1. **参数外置**：提示词、种子、步数单独成节点，不要塞在采样器的 widget 里
2. **坐标要有间隔**：节点宽 300~400，水平间距至少 350，垂直至少 250。别用随机坐标
3. **新节点别叠在用户已有节点上**：先看 `canvas_state`，找空白区域

---

## 七、代跑图

```
{"kind":"execute","title":"提交队列","args":{}}
```

调 `graphToPrompt` + `queuePrompt`，把当前画布提交执行。

**警告**：这会真的占用 GPU。长任务可能跑几分钟。**执行前必须跟用户确认**，任务跑起来了要盯 `duddy_run_status`。

---

## 八、工具总表（16 个）

**诊断**
| 工具 | 用途 |
|---|---|
| `duddy_ping` | 扩展在不在线 |
| `duddy_events` | 事件流，排查"为什么没反应" |

**知识库**
| 工具 | 用途 |
|---|---|
| `duddy_search_nodes` | 搜节点类型 |
| `duddy_node_info` | 查节点定义（参数名、可选值） |
| `duddy_list_models` | 列模型文件 |
| `duddy_canvas_state` | 读当前画布 |

**规划执行**
| 工具 | 用途 |
|---|---|
| `duddy_precheck_plan` | 只检查不执行 |
| `duddy_plan_workflow` | 提交并**立即执行** |
| `duddy_plan_status` | 查计划状态与进度 |
| `duddy_apply_step` | 手动走一步（auto 模式下一般不用） |
| `duddy_list_plans` | 最近计划列表 |
| `duddy_abort_plan` | 中止 |
| `duddy_rollback_plan` | 撤销已建的节点和连线 |

**运行时**
| 工具 | 用途 |
|---|---|
| `duddy_run_status` | 跑到哪一步、ETA、有没有报错 |
| `duddy_get_logs` | 运行日志（含节点里的 print） |
| `duddy_fs_changes` | 最近文件变更 |

---

## 九、出问题的时候

**先看日志，别猜。**

```
duddy_get_logs(level="ERROR")    报错原文
duddy_run_status()               卡在哪个节点、第几步、ETA
duddy_fs_changes()               是不是刚装了什么东西
```

**执行到一半失败 → 回滚**

```
duddy_rollback_plan(plan_id)
```

撤掉已建的节点和连线。（删除的节点和改过的参数撤不回来。）

### 常见错误对照表

| 现象 | 先查什么 |
|---|---|
| `unknown_node_type` | `duddy_search_nodes` 看真正叫什么 |
| 参数没生效 | `duddy_node_info` 看 widget 真名 |
| 模型找不到 | `duddy_list_models` |
| `connect_rejected` | 回报里有 from.type / to.type，类型不匹配 |
| 卡住不动 | `duddy_run_status` + `duddy_get_logs` |
| 出图很慢 | 日志里搜 `offloaded`，超过 50% 就是显存不够，降分辨率 |
| 黑图/花屏 | 查 fp16 溢出，把 dequant_dtype 改 float32 试试 |
| 图像劣化/边缘过锐 | 查 cfg 是不是被设成了 5 以上 |

### 日志怎么读

看到这行说明**显存严重不足**：

```
loaded partially; 1657 MB usable, 1503 MB loaded, 5864 MB offloaded
```

`offloaded` 是"被踢到内存的模型体积"。超过总量一半，速度会掉到 1/10。

**告诉用户降分辨率，不要只把日志原样贴给他。**

---

## 十、中止

用户说"停""算了""不对"——立刻：

```python
duddy_abort_plan(plan_id, reason="用户要求中止")
```

然后说清楚：停在哪一步、画布上留了什么、要不要清掉。

> 自动执行每步间隔 0.12 秒，用户喊停时可能已经跑完了。
> 所以计划要么足够短，要么先讲清楚"这个会跑 20 步、大概 3 秒"。

## 说话方式

- 建了什么就说什么，带真实 node_id
- 用了什么参数就讲清楚，别总结成"已配置好"
- 不确定就先问，别硬猜着建
- 用户自己能点两下搞定的事，直接告诉他怎么点
