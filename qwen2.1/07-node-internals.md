# 07 · 节点内部机制（从参数值反推）

这一节是从 `widgets_values` 的原始数组反推出来的节点行为。**没有文档支撑，全是读出来的。**

---

## 一、`TextEncodeQwenImage21` 是"三输出"节点

这是最颠覆认知的一点。之前以为它只输出 conditioning：

```
TextEncodeQwenImage21[0] -> KSampler[1]  positive
TextEncodeQwenImage21[1] -> KSampler[2]  negative
TextEncodeQwenImage21[2] -> KSampler[3]  latent_image   ← 关键
```

**它内部完成了 VAE 编码，直接吐出 latent。**

参数数组：`['', '', '0']` → `[正向前缀, 负向前缀, resolution]`
- 前两个是空的，因为真正的提示词从外部连线进来（`STRING` 输入）
- `resolution = 0` 表示不缩放

### 这解释了一个长期困惑

既然 latent 来自原图编码，那 `denoise=1` 岂不是把它全丢了？

**答案是：对，就是要丢。**

latent 在这里**只承担"画布尺寸"这一个职责**。原图的内容是通过 conditioning 传的——`image_1` 被拼进 text encoder 的输入序列，模型"看"着它画。

所以：

| denoise | latent 里的原图内容 | 结果 |
|---|---|---|
| 1.0 | 完全丢弃，只用它定尺寸 | 官方路径，指令遵循最强 |
| 0.7 | 保留 30% 的 latent 特征 | 更贴原图结构，但指令遵循变弱 |
| 0.3 | 保留 70% | 几乎不改，容易糊 |

**这就是为什么集成采样器文档说"图生图可降低 denoise 保留原图"** ——它有真实的 latent 可保留。而官方工作流几乎都用 1.0，因为它们要的是"重画"不是"微调"。

---

## 二、`TextGenerate`：自动 caption 的确切配置

Outpaint 里那个自动描述原图的节点，参数数组 13 项：

```python
[
  "Write the text-to-image prompt that would generate this exact picture. "
  "One paragraph of 50…",   # 系统提示词
  300,      # max_tokens
  "on",     # 采样开
  0.3,      # temperature —— 很低，要稳定不要创意
  64,       # top_k
  0.95,     # top_p
  0.05,     # min_p
  1.05,     # repetition_penalty —— 略高于 1，防重复
  20260925, # seed 固定
  0,
  False,
  True,
  "auto",   # device
]
```

**配置思路非常明确**：
- `temperature 0.3` —— 描述原图不需要创意，要准
- `repetition_penalty 1.05` —— 略施压力防车轱辘话
- `seed 固定` —— 同一张图每次描述一致，保证可复现
- `max_tokens 300` —— 一段话足够，太长反而稀释

**系统提示词的设计**："Write the text-to-image prompt that would generate this exact picture" ——
不是问"图里有什么"，而是问"什么提示词能生成这张图"。**这个措辞差异很关键**：后者输出的描述天然就是提示词格式，可以直接拼回去用。

---

## 三、`StringConcatenate` 的分隔符是显式参数

```python
StringConcatenate  ['', '', ' Scene: ']
```

参数是 `[a, b, 分隔符]`。a/b 从连线进来，中间插 `" Scene: "`。

最终效果：

```
Outpaint the image: replace the solid gray areas with a seamless
continuation of the scene, keeping the existing composition Scene:
A storefront at dusk, warm interior light, wooden counter, glass jars...
```

**指令 + 场景描述，用 `Scene:` 明确分区。** 模型能分清哪句是"要干什么"，哪句是"现在是什么"。

---

## 四、`LoadImagePad` 的 11 个参数

```python
['ausboss_record_store_crop.png', 250, 138, 107, 345,
 'color', '#808080', 0.5, 32, 32, 1, '', '', '']
```

拆解：

| # | 值 | 含义 |
|---|---|---|
| 0 | 文件名 | 输入图 |
| 1-4 | 250, 138, 107, 345 | 上/左/下/右的扩展像素 |
| 5 | `color` | 填充模式 |
| 6 | `#808080` | 填充色（中灰） |
| 7 | 0.5 | 羽化/混合强度 |
| 8-9 | 32, 32 | 对齐网格 |
| 10 | 1 | 批次 |

**为什么填充中灰 `#808080`？**
因为扩散模型对中灰（0.5 亮度）最不敏感——它是"中性"的，不会给生成区域带偏色。用纯白或纯黑会污染新生成的区域。

**为什么四边不对称（250/138/107/345）？**
因为原图不在画布正中——作者是往右上方扩得多，左下方扩得少。这是手工拖出来的。

---

## 五、LoRA 的 JSON 存储格式

`AUSBOSS_NODES_LoraLoader` 的 widget 存的是一个 JSON 字符串：

```json
[
  {
    "name": "qwen-image-2.1-outpaint.safetensors",
    "strength": 1,
    "enabled": true,
    "triggers": "..."
  }
]
```

后面还跟着 `', '`（分隔符）和 `'error'`（状态位）。

**`triggers` 字段**说明这个 Loader 支持自动注入触发词——挂上 LoRA 时，触发词自动加进提示词。这比手动记触发词可靠得多。

`Person Swap` 里两个 Loader 分别存：
- `bfs_body_swap_v1.0_qwen_2.1.safetensors`
- `bfs_head_v1.1_qwen_2.1.safetensors`

配合布尔开关二选一。

---

## 六、`TiledUpscale` 的 5 个参数

```python
['4', '2', '128', '32', '8']
```

推测（按功能推断，非官方文档）：

| # | 值 | 含义 |
|---|---|---|
| 0 | 4 | 目标百万像素 |
| 1 | 2 | 单块百万像素（4 MP 切成 2 块） |
| 2 | 128 | 重叠带像素（消除接缝的关键） |
| 3 | 32 | 对齐网格 |
| 4 | 8 | ？可能是最大块数或倍率上限 |

**重叠带 128 像素**是接缝看不出来的核心。相邻两块共享 128px 的内容，拼接时做渐变融合。

---

## 七、`QwenImage21Cache` 的 dtype

```python
['auto', 'default']
```

- `auto` = 自动决定缓存放哪（GPU/CPU）
- `default` = 缓存精度跟随模型

官方建议：显存紧张时把 dtype 改 `int8`（缓存减半，精度接近 bf16）。8GB 卡上值得改。

---

## 八、从这些参数看作者的工程素养

对比两个细节：

**Outpaint 作者**：
- `TextGenerate` temperature 0.3、seed 固定、repetition_penalty 1.05
- `StringConcatenate` 用 `" Scene: "` 显式分区
- `LoadImagePad` 填充中灰

**这些都是"调过很多次才定下来的值"。** 每一个都能说清楚为什么。

**大部分其他工作流**：
- 负向词抄了 SD 模板（cfg=1 下无效）
- cfg=7.5/8（Qwen 上会过锐）
- 没有任何自动描述，全靠用户手写提示词

**差距不在节点，在"有没有人认真测过"。**

---

## 九、可以直接抄进自己工作流的四条

1. **`TextEncodeQwenImage21` 的 latent 只用来定尺寸** —— 别指望它保留原图内容
2. **自动 caption 用 temperature 0.3 + 固定 seed** —— 要稳定不要创意
3. **扩图填充用 `#808080` 中灰** —— 不污染生成区域
4. **LoRA Loader 优先选带 `triggers` 字段的** —— 触发词自动注入，不用背
