# 02 · 技术要点

节点标题里藏着作者的全部经验。这一节把散落在各个工作流里的技术点抽出来。

---

## 一、Qwen 2.1 的编辑是「条件式重绘」，不是「图生图」

这是最容易搞错的地方。看 Outpaint 里那个节点的标题：

> `Text Encode Qwen Image 2.1 · padded canvas = image_1, resolution 0`

意思是：**原图/画布走 `TextEncodeQwenImage21` 的 `image_1` 槽位，作为条件进文本编码器，而不是 VAE 编码成 latent 塞进采样器。**

由此推出三条铁律：

| 参数 | 值 | 原因 |
|---|---|---|
| `cfg` | **1** | 官方发布路径。cfg=1 时 ComfyUI 跳过负向条件分支，**负向提示词完全无效** |
| `denoise` | **1** | latent 是空的（或纯噪声），降 denoise 只会得到「半张噪声 + 半个条件」，画面发灰 |
| `steps` | **25**（Turbo 8） | 官方模板起步值；Qwen 自家 pipeline 用 40 步 |

**想「少改」怎么办？** 靠两样东西：提示词里的约束语句 + Restore LoRA。**不是靠调 denoise。**

cfg 的其他取值代价（来自官方文档）：
- `cfg=2` 更贴密集文本（小字、数字），代价是边缘过锐
- `cfg=5` 严重劣化
- `cfg=0.5` 直接崩图

---

## 二、分块重绘（Tile Upscale）

**问题**：8 GB 卡跑 4 MP 的图会 OOM，或者慢到无法接受。

**解法**（Tiled Upscale 工作流的核心，藏在子图 `Redraw it in tiles with Qwen` 里）：

```
原图
  ↓ ImageCropRotatePad（裁成目标比例的整数倍）
  ↓ TiledUpscale  ← 切成约 2 MP 一块，输出 [图块, 权重/掩码]
  ↓ TextEncodeQwenImage21（每块 + 请求 一起编码）
  ↓ KSampler（cfg 1, euler）
  ↓ VAEDecode
  ↓ TiledUpscaleStitch  ← 拼回去
```

**关键设计：分块之间共享一条重叠带并且渐变融合。** 作者原话是「我在结果里找过接缝，没找到」。

**阈值**：约 2 MP 以内一遍过；4 MP 两块；8 MP 四块。

**小图的坑**：小图最多按自身像素的 8 倍重绘，再往上就变成"编造细节"，人脸会变成另一个人。作者明确写了「a tiny picture redrawn at 8 megapixels gets a made-up face」。

---

## 三、把原图贴回去（Stitch）

这是这系列工作流出现频率最高、也最容易被忽略的一招。

**Outpaint 里的节点标题**：

> `Stitch Inpaint 🆎 · original pixels back, seams matched`

**逻辑**：模型重绘的是整个画布（原图 + 扩展区），但原图那部分**一个像素都不该变**。所以重绘完成后，用 Stitch 节点把原图区域的像素**贴回**去，只在边界做羽化融合。

好处：
- 原图绝对不变（不用信模型的「保持原样」）
- 扩展区和原图接缝自然
- 提示词可以放心写「重绘整个场景」，不用担心它乱改主体

**Tiled Upscale 用的是同一个思路**（`TiledUpscaleStitch`），只不过贴回的是分块而不是原图。

---

## 四、自动 caption：解决「扩图后场景漂移」

Outpaint 工作流里有个不起眼的节点：

> `Text Generate · caption the source`

以及

> `Prompt · instruction + " Scene: " + description`

**流程**：
1. 先让模型（同一个 Qwen3-VL 编码器）给原图写一段描述 → "一张室内照片，米色墙面，木地板，右侧有窗"
2. 拼成：`扩展画面，补全右侧 → Scene: 一张室内照片，米色墙面…`
3. 编码时连同画布一起喂进去

**为什么要这么做？** 因为扩图时新区域是纯噪声，模型不知道"墙该往哪延伸、地板是什么材质"。给它一句场景描述，新生成的部分才会和原图同源。

这个技巧可以直接迁移到任何「局部生成」场景：**先用模型理解整体，再让它只负责局部**。

---

## 五、LoRA 策略

这系列工作流对 LoRA 的使用很有章法：

### 按用途拆成多个 Loader

Person Swap 里有两个独立的 LoRA 节点：

```
AUSBOSS_NODES_LoraLoader "LoRA Loader - Body swap LoRA"
AUSBOSS_NODES_LoraLoader "LoRA Loader - Head swap LoRA"
```

配合 `PrimitiveBoolean "Swap the whole body"`，**用开关决定走哪条链**。而不是把两个 LoRA 永远堆在一起（那样会互相干扰）。

### Restore LoRA：保真锚点

几乎所有编辑类工作流都挂了：

> `LoRA Loader - Restore LoRA`

它的作用是**把重绘结果拉回原图**。Outpaint 的说明里提到一个关键参数：

> `Blurry photo? Set the Restore row to 0.5. At 1 it stays true to the blur and the result comes back soft.`

**这是个反直觉的点**：Restore LoRA 强度 1.0 会「忠于原图的模糊」，所以处理糊图反而要降到 0.5。

### Turbo LoRA：8 步出图

> `Fast needs the Viggle Turbo LoRA`
> `On: 8 steps with the Viggle Turbo LoRA. Off: 25 steps, about three times as long.`

用的是 `Qwen-Image-2.1-viggle-turbo-v0.3-6step-lora-r128`，配 8 步。**Fast 开关就是「挂不挂这个 LoRA + 步数改不改」的联动。**

---

## 六、QwenImage21Cache

节点标题：`Reference cache · automatic`

作用：把文本和参考图的编码结果缓存在显存/内存里，多步采样时不重复计算。

官方说明（ComfyUI 文档）：
- `int8`：缓存减半，精度接近 bf16
- `int4`：再减半，但每步误差约翻倍

**对 8-16 GB 卡做多参考图编辑特别有价值。**

---

## 七、Alpha 通道

> `Split Image with Alpha · Qwen 2.1 decodes RGBA`

Qwen 2.1 的 VAE 是四通道的，**原生支持透明背景**。所以抠图/去背景任务不需要后处理抠像，模型直接解码出 RGBA。

---

## 八、参数速查表

| 参数 | 全模型 | Turbo | 备注 |
|---|---|---|---|
| steps | 25 | 8 | 官方 pipeline 40 步，25 是模板折中 |
| cfg | 1 | 1 | 恒定。此时负向词无效 |
| sampler | euler | euler | 高级采样器可更少步数 |
| scheduler | simple | simple | |
| denoise | 1 | 1 | 条件式重绘，不要降 |
| resolution | 0 或 1024 | 同 | Qwen-Edit 的「像素预算」，非宽高 |
| auraflow_shift | 3 | 3 | 采样移位，平衡速度与质量 |

**分辨率是「百万像素预算」，不是宽高。** 1024 表示约 1 MP，2048 表示约 4 MP。宽高比由模型按参考图自行保持。这是 Qwen-Image-2.1 和 SD 系最大的参数习惯差异。
