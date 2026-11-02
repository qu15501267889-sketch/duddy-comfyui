# 第三遍 · 实现层（节点内部机制）

> 这一章全部是从 `widgets_values` 原始数组和连线反推的。官方文档没写，是读出来的。

---

## 1. `TextEncodeQwenImage21` 是"三输出"节点

**这是理解整个 Qwen 2.1 工作流的关键，也是最容易被忽略的一点。**

### 连线证据

```
TextEncodeQwenImage21[0] -> KSampler[1]   positive
TextEncodeQwenImage21[1] -> KSampler[2]   negative
TextEncodeQwenImage21[2] -> KSampler[3]   latent_image   ← 这里
```

全局统计里 `TextEncodeQwenImage21 -> KSampler` 出现 **43 次**，是所有边里最多的。其中相当一部分就是这三条并行输出。

### 参数数组

```python
['', '', '0']
#  [正向前缀, 负向前缀, resolution]
```

前两个空着，因为真正的提示词从外部 `STRING` 连线进来。第三个是像素预算。

### 输入侧：它同时吃 CLIP 和 VAE

```
CLIPLoader  -> TextEncodeQwenImage21   (21 次)
VAELoader   -> TextEncodeQwenImage21   (17 次)
LoadImage   -> TextEncodeQwenImage21   (21 次)
```

**VAE 也接进来**，因为它内部要做 `VAEEncode(image_1)` 产出那个 latent。

### 这解释了 denoise 之谜

既然 latent 来自原图 VAE 编码，`denoise=1` 岂不是把它全丢了？

**对，就是要丢。**

latent 在这里**只承担"画布尺寸"的职责**。原图内容是通过 conditioning 传的——`image_1` 被拼进 text encoder 的输入序列，模型"看"着它画。

| denoise | latent 里的原图 | 实际效果 |
|---|---|---|
| **1.0** | 完全丢弃，只留尺寸 | 官方路径，指令遵循最强（57 次里的绝大多数） |
| 0.7 | 保留约 30% | 更贴原图结构，但指令遵循变弱 |
| 0.3 | 保留约 70% | 几乎不改，容易糊 |

**所以两条路都成立，取决于你要什么**：
- 要"严格按指令重画" → 1.0
- 要"少改、保结构" → 0.7，同时接受指令遵循下降

集成采样器文档说"图生图可降低 denoise 保留原图"，正是因为它的 latent 里真的有原图。而官方工作流要的是重画，所以恒为 1。

---

## 2. `TextGenerate`：自动 caption 的确切配置

Outpaint 工作流里那个"给原图写描述"的节点，13 个参数：

```python
[
  "Write the text-to-image prompt that would generate this exact picture. "
  "One paragraph of 50…",   # 0  系统提示词
  300,                       # 1  max_tokens
  "on",                      # 2  采样开关
  0.3,                       # 3  temperature
  64,                        # 4  top_k
  0.95,                      # 5  top_p
  0.05,                      # 6  min_p
  1.05,                      # 7  repetition_penalty
  20260925,                  # 8  seed（固定）
  0, False, True,            # 9-11
  "auto",                    # 12 device
]
```

### 每个值的意图

| 参数 | 值 | 为什么 |
|---|---|---|
| temperature | **0.3** | 描述原图不需要创意，要准 |
| repetition_penalty | **1.05** | 略高于 1，防车轱辘话 |
| seed | **固定** | 同一张图每次描述一致，保证可复现 |
| max_tokens | **300** | 一段话足够，太长反而稀释重点 |

### 系统提示词的措辞是精髓

```
Write the text-to-image prompt that would generate this exact picture.
```

**不是** "Describe what's in this image."

**而是** "什么提示词能生成这张图。"

这个措辞差异决定了输出格式：后者输出的是"描述"，前者输出的是"提示词"。**后者需要人再加工，前者可以直接拼回去用。**

### 拼装方式

```python
StringConcatenate  ['', '', ' Scene: ']
# 参数是 [a, b, 分隔符]，a/b 从连线进来
```

最终：

```
Outpaint the image: replace the solid gray areas with a seamless
continuation of the scene, keeping the existing composition
 Scene: A storefront at dusk, warm interior light, wooden counter...
```

**用 `Scene:` 明确分区**——模型能分清哪句是"要干什么"，哪句是"现在是什么"。

---

## 3. `LoadImagePad`：扩图的填充参数

```python
['ausboss_record_store_crop.png', 250, 138, 107, 345,
 'color', '#808080', 0.5, 32, 32, 1, '', '', '']
```

| # | 值 | 含义 |
|---|---|---|
| 0 | 文件名 | 输入图 |
| 1-4 | 250, 138, 107, 345 | 上 / 左 / 下 / 右 扩展像素 |
| 5 | `color` | 填充模式 |
| 6 | **`#808080`** | 填充色 |
| 7 | 0.5 | 羽化强度 |
| 8-9 | 32, 32 | 对齐网格 |
| 10 | 1 | 批次 |

### 为什么填充中灰 `#808080`

扩散模型对**中灰（0.5 亮度）**最不敏感——它是"中性"的，不会给新生成区域带偏色。

**用纯白或纯黑会污染生成区域**：白色会让新区域偏亮，黑色会让它偏暗。

### 为什么四边不对称

```
上 250 / 左 138 / 下 107 / 右 345
```

因为原图不在画布正中——作者往右上扩得多，左下扩得少。这是手工拖出来的。

---

## 4. `TiledUpscale`：分块重绘的五个数

```python
['4', '2', '128', '32', '8']
```

| # | 值 | 推测含义 |
|---|---|---|
| 0 | 4 | 目标百万像素 |
| 1 | 2 | 单块百万像素（4 MP 切两块） |
| 2 | **128** | **重叠带像素** |
| 3 | 32 | 对齐网格 |
| 4 | 8 | 最大块数 / 倍率上限 |

**重叠带 128 px 是接缝看不出来的核心。** 相邻两块共享 128 像素内容，拼接时做渐变融合。

### 阈值规律

- ≤ 2 MP：一遍过，不分块
- \> 2 MP：按约 2 MP 一块切
- 4 MP → 2 块
- 8 MP → 4 块

### 小图的坑

作者原话：

> A tiny picture redrawn at 8 megapixels gets a made-up face.

小图最多按自身像素的 8 倍重绘，再往上就是**编造细节**，人脸会变成另一个人。

---

## 5. `StitchInpaint`：把原图贴回去

节点标题：

> `Stitch Inpaint 🆎 · original pixels back, seams matched`

**逻辑**：模型重绘整个画布（原图 + 扩展区），但**原图那部分一个像素都不该变**。所以重绘完成后，把原图区域的像素贴回去，只在边界羽化。

### 为什么这招比"提示词约束"可靠

指望模型"保持原样"是赌博。贴回像素是确定性操作。

**这是整个生态里最实用的一招**——扩图、放大、局部编辑全都用它。

---

## 6. `QwenImage21Cache`：缓存的两个参数

```python
['auto', 'default']
#  [device, dtype]
```

| 参数 | 值 | 含义 |
|---|---|---|
| device | auto | 自动决定缓存放 GPU 还是 CPU |
| dtype | default | 跟随模型精度 |

官方建议（显存紧张时）：

- `int8`：缓存减半，精度接近 bf16 ✅
- `int4`：再减半，但每步误差约翻倍 ⚠️

**8 GB 卡应该把 dtype 改成 int8。** 这是白捡的显存。

---

## 7. `ComfySwitchNode`：子图内部的分支

子图内部出现 **26 次**。

```
PrimitiveBoolean -> ComfySwitchNode   (11 次)
```

**它是"选一路"，Bypasser 是"禁用一组"。** 两者用途不同：

| 节点 | 行为 | 场景 |
|---|---|---|
| ComfySwitchNode | 在 N 个输入里选 1 个输出 | 全身边换 / 只换头 |
| Fast Groups Bypasser | 整组节点变灰跳过 | 关掉后处理链 |

`Person Swap` 用的是前者（两个子图二选一），`Outpaint` 用的是后者（关掉可选的后处理）。

---

## 8. LoRA Loader 的 JSON 结构

```json
[{
  "name": "qwen-image-2.1-outpaint.safetensors",
  "strength": 1,
  "enabled": true,
  "triggers": "..."
}]
```

后面还跟 `', '`（分隔符）和 `'error'`（状态位）。

**`triggers` 字段 = 触发词自动注入。** 挂上 LoRA 时提示词自动补，不用手动记。

---

## 9. 从参数反推作者的调试深度

对比两个细节：

**Outpaint 作者**：
- TextGenerate temperature **0.3**（不是默认的 1.0）
- repetition_penalty **1.05**（不是默认 1.0）
- seed **固定**（不是随机）
- 填充色 **#808080**（不是 #000000）

**这四个都不是默认值。** 说明他踩过坑、调过、记录下来了。

**大部分其他工作流**：全用默认值，负向词抄 SD 模板，cfg 抄 SD 的 7.5。

**差距不在用了什么节点，在有没有人认真测过。**

---

## 10. 可以直接抄进自己工作流的五条

1. **别指望 `TextEncodeQwenImage21` 的 latent 保留原图** —— 它是尺寸载体
2. **自动 caption 用 temperature 0.3 + 固定 seed** —— 要稳定不要创意
3. **扩图填充用 `#808080`** —— 不污染生成区域
4. **重绘后用 Stitch 把原图贴回** —— 比提示词约束可靠
5. **LoRA Loader 优先选带 `triggers` 字段的** —— 触发词自动注入
