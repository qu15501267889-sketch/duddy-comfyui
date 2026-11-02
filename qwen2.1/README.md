# Qwen Image 2.1 工作流研究

对 Civitai 上 **71 个 Qwen 2.1 相关 ComfyUI 工作流**的结构分析。

## 数据来源

- 站点：civitai.com
- 筛选：`baseModels=Qwen 2.1`，类型 `Workflows`
- 排序：Highest Rated
- 抓取时间：2026-11-02
- 原始清单：`D:\AI\学习工作流\_index\models_raw.json`
- 工作流文件：`D:\AI\学习工作流\<模型名> [id]\`（每个目录含 README + 工作流 json/zip）
- 结构分析结果：`D:\AI\学习工作流\_index\analysis.json`

同期还有 **21 个 Qwen 2.1 Checkpoint**（共 505 GB），本研究者只下工作流，不碰权重。

## 核心结论（先看这个，后面都是细节）

**1. 这群工作流有一个高度一致的架构范式。**
绝大多数出自同一作者（AusBoss）。他的做法不是"画一张大图"，而是：

```
参数节点（Text / Seed / Integer / Boolean / 图片）
        ↓
   【子图 subgraph】← 真正的算法藏在这里，可折叠、可复用
        ↓
   后处理节点（Stitch / Compare / Save）
```

**外部只暴露"用户该改的东西"，内部逻辑封成子图。** 这是把工作流当产品做的思路，不是当草稿。

**2. 子图（Subgraph）是 1.5x 版本最重要的新特性。**
Tiled Upscale 的外部只有 19 个节点，看着简单；点开那个 UUID 节点，里面还有 11 个节点、19 条连线。不展开子图，等于没看懂。

**3. Qwen 2.1 的编辑不是"图生图"，是"条件式重绘"。**
原图走 `TextEncodeQwenImage21` 的 `image_1` 槽位变成条件，**不进 latent**。所以：
- `denoise` 基本恒为 1
- `cfg` 恒为 1（官方路径，此时负向提示词完全失效）
- 想"少改"靠的是提示词和 LoRA，不是降 denoise

**4. "把原图贴回去"是这系列工作流的通用手法。**
扩图、放大、局部编辑都用了同一招：重绘完整画面 → 再用 `StitchInpaint` / `TiledUpscaleStitch` 把**不需要改的区域换回原始像素**。这样既保证接缝自然，又保证主体不变。

**5. 自动 caption 解决了"扩图后场景漂移"的老问题。**
Outpaint 工作流里有个 `TextGenerate` 节点，先让模型给原图写一段描述，再拼成
`指令 + " Scene: " + 描述` 喂回去。让新生成的区域知道"这是个什么场景"。

## 文档结构

| 文件 | 内容 |
|---|---|
| [01-architecture.md](01-architecture.md) | 共性架构范式：子图封装、参数外置、UI 层设计 |
| [02-techniques.md](02-techniques.md) | 技术要点：tile 重绘、stitch 贴回、caption、LoRA 策略、参数取值 |
| [03-cases.md](03-cases.md) | 逐个工作流拆解（Tiled Upscale / Person Swap / Outpaint / Multi-Image Edit） |
| [04-lessons-for-duddy.md](04-lessons-for-duddy.md) | 这些经验怎么反哺 duddy-comfyui 本身 |
| [05-ecosystem-patterns.md](05-ecosystem-patterns.md) | 非 AusBoss 体系的工程手法：无线连接、组旁路、参数流派 |
| [06-parameter-fingerprint.md](06-parameter-fingerprint.md) | 58 个工作流的实际参数统计：cfg/steps/denoise/LoRA/提示词 |
| [07-node-internals.md](07-node-internals.md) | 节点内部机制反推：三输出编码器、自动 caption 配置、填充色 |

## 工作流清单（按类型）

### 放大 / 超分
- Qwen Image 2.1 Tiled Upscale | Up to 8 MP（2988629）

### 换人 / 换装
- Qwen Image 2.1 Person Swap | Head or Full Body（2980685）
- Qwen Image 2.1 Outfit Swap | Virtual Try-On（2983126）

### 扩图 / 旋转补全
- Qwen Image 2.1 Outpaint | Auto Prompt（2964810）
- Qwen Image 2.1 Rotate + Outpaint（2966883）

### 抠图 / 去背景
- Qwen Image 2.1 Cut Out + Remove | Transparent PNG（2987326）

### 角色设定图
- [Qwen Image 2.1] Character Design Sheet Maker（2960750）
- Qwen 2.1 Character Sheet Builder - No LLM（2966369）
- Qwen Image 2.1 Character Reference Sheet Generator（2960890）
- Qwen Image 2.1 Character Sheet Designer（2986253）

### 通用生成 / 编辑
- Qwen Image 2.1 Generate + Edit | Full Model, Auto Prompt（2962616）
- Qwen Image 2.1 Edit + Realign | Stays in Place（2969862）
- Qwen Image 2.1 Inpaint | Click to Mask（3000390）
- Qwen Image 2.1 Turbo Generate + Edit | 8 Steps（2994247）
- Qwen Image 2.1 Turbo Multi-Image Edit | 8 Steps（3000002）
- Moody Qwen Image 2.1 Edit Workflow（3003715）
