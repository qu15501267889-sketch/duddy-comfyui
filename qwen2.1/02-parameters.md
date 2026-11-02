# 第二遍 · 参数层

> 全部数字来自 58 个可解析工作流里所有 `KSampler` / `TextEncodeQwenImage21` / `LoraLoader` / `*Loader` 节点的**实际 widget 值**，子图内部的也算。

---

## 1. 五个参数，构成 Qwen 2.1 的"标准姿势"

```
cfg      = 1
denoise  = 1
steps    = 25（或 8）
sampler  = euler
scheduler= simple
```

这五个值同时出现的频率，远超任何其他组合。下面逐个给数据。

---

## 2. CFG：52 : 5 的压倒性

| 值 | 次数 | 占比 | 判断 |
|---|---|---|---|
| **1 / 1.0** | **52** | 90% | ✅ 官方路径 |
| 7.5 | 3 | 5% | ❌ SD 习惯残留 |
| 8 | 2 | 3% | ❌ 同上 |
| 2 | 1 | 2% | ⚠️ 可接受（想用负向词） |
| 4 | 1 | — | ⚠️ 边缘 |

### cfg > 3 的六个位置（缺陷清单）

```
cfg=8    Character Dataset Builder [2719177]
cfg=7.5  ComfyUI Workflows [2876940]      ×3
cfg=8    Lonecat's Simple Speed Boost Workflows
cfg=4    Qwen Image 2.1 Studio
```

**为什么这是错的**：

- Qwen-Image-2.1 官方发布路径就是 `cfg=1`（无引导采样）
- `cfg=2` 会更贴密集文本，但**边缘过度锐化**
- `cfg=5` 官方原话 "degrades quality badly"
- `cfg=0.5` 直接崩图

这六个工作流的作者大概是从 SD 工作流改过来，没意识到 Qwen 不是那个玩法。

---

## 3. steps：三大流派，界限分明

| 值 | 次数 | 流派 | 场景 |
|---|---|---|---|
| **25** | **20** | 全模型标准 | 官方模板起步值 |
| **8** | **15** | Turbo | 配蒸馏 LoRA |
| 20 | 7 | 折中 | 省点时间 |
| 4 | 4 | 极限 | 配 4-step LoRA |
| 12 | 4 | 12步 Turbo | 中间档 |
| 35 | 2 | 细节控 | 手部/文字 |

**规律：steps 和 LoRA 强绑定。**

- 用 `viggle-turbo` → 8 步
- 用 `p_qwen_image_2.1_8step` → 8 步
- 用 4-step LoRA → 4 步
- 不挂加速 LoRA → 25 步

**单独把 steps 调到 8 而不挂 Turbo LoRA，会出废图。** 这是个常见误区。

---

## 4. denoise：57 : 3

| 值 | 次数 |
|---|---|
| **1 / 1.0** | **57** |
| 0.35 | 2 |
| 0.2 | 1 |

**几乎恒为 1。**

那 3 个非 1 的值得单独看——它们大概率是**真正的 latent 图生图**（用 VAEEncode 原图当 init），不是 Qwen 的条件式编辑。

**如果你看到有人把 Qwen 工作流的 denoise 调到 0.5，问他一句"你的 latent 是哪来的"。** 如果 latent 是空的或来自 `EmptyLatentImage`，降 denoise 只会得到半张噪声。

---

## 5. resolution：两种哲学

Qwen 的 `TextEncodeQwenImage21` 有个 `resolution` 参数，含义是**像素预算**（不是宽高）。

| 值 | 次数 | 含义 |
|---|---|---|
| **1024** | **38** | 约 1 MP，标准尺寸 |
| **0** | **23** | 不缩放，跟随原图 |
| 1088 / 704 / 992 / 1536 | 1-2 | 微调 |

### 1024 派（38 次）
主动把参考图缩到约 1 MP。
- 好处：速度快、显存稳、结果可预测
- 代价：原图细节被压缩

### 0 派（23 次）
保持原图尺寸不缩放。
- 好处：最大保真
- 代价：原图多大就跑多大。一张 4 MP 手机照会直接吃满显存

**官方文档提醒**：resolution 设 0 时，一张 3000×4000 的照片会变成 3008×4000 的画布（约 12 MP），成本从 0.3 s/it 涨到 6 s/it。

**8 GB 卡建议用 1024，别用 0。**

---

## 6. 采样器 / 调度器

```
euler          46 次 (79%)     ← 绝对主流
er_sde          5 次
res_2s          3 次
euler_ancestral 2 次

simple         45 次 (78%)     ← 绝对主流
beta           10 次
normal          2 次
```

**`euler + simple` 就是 Qwen 2.1 的官方答案。** 少数用 `beta` 的是在做锐化风格。

`er_sde` 和 `res_2s` 是较新的采样器，出现在 Turbo 工作流里（少步数下更稳）。

---

## 7. LoRA 生态：六件套 + 两个专用

| LoRA | 次数 | 强度 | 作用 |
|---|---|---|---|
| `qwen-image-2.1-consistency` | 4 | 1.0 | 跨图角色一致 |
| `viggle-turbo-v0.3-6step-r128` | 4 | 1.0 | 8 步加速 |
| `qwen-image-2.1-restore` | 3 | 0.5~1.0 | 保真锚点 |
| `qwen-image-2.1-outfit-swap` | 2 | 1.0 | 换装 |
| `qwen-image-2.1-outpaint` | 2 | 1.0 | 扩图 |
| `p_qwen_image_2.1_8step_v0.1` | 2 | 1.0 | 另一个 8 步加速 |
| `bfs_body_swap_v1.0` | 1 | 1.0 | 全身替换 |
| `bfs_head_v1.1` | 1 | 1.0 | 只换头 |

### 三个关键判断

**① 强度几乎全是 1.0**
唯一例外是 `restore` 在糊图场景降到 0.5——因为 1.0 会"忠于原图的模糊"，结果还是软的。

**② 一个任务一个 LoRA，不叠加**
Person Swap 用两个独立 Loader + 布尔开关二选一。**同时挂两个会互相干扰。**

**③ consistency 和 restore 是两回事**
- `consistency` = 让**不同图之间**的人物像同一个人
- `restore` = 让**同一张图**重绘后别跑偏

这俩经常被混为一谈，实际解决的完全不是一个问题。

### LoRA 的隐藏能力：triggers

`AUSBOSS_NODES_LoraLoader` 的 widget 里存了 JSON：

```json
[{"name":"...", "strength":1.0, "enabled":true, "triggers":"..."}]
```

**`triggers` 字段 = 自动注入触发词。** 挂上 LoRA 时提示词自动补上，不用手动记。

选 LoRA Loader 节点时，**优先选带 triggers 支持的**。

---

## 8. 模型文件：三种量化并存

```
qwen_image_2.1_int8_convrot.safetensors      INT8，7.26 GB，主流
qwen\rayQwen21_turboV13Int8convrot...        第三方 INT8 turbo
qwen_image_2.1_turbo_bf16.safetensors        BF16，14.23 GB
qwen3vl_8b_int8_convrot.safetensors          文本编码器 INT8，9.35 GB
qwen3vl_8b_nvfp4.safetensors                 NVFP4，更新更省
qwen3VL8BHereticINT8W4A8_int8.safetensors    社区量化
```

### 显存账（8 GB 卡的现实）

```
扩散模型 INT8     7.26 GB
文本编码器 INT8   9.35 GB
VAE bf16          0.68 GB
────────────────────────
合计             17.29 GB   ← 显卡只有 8 GB
```

**必须靠 ComfyUI 的动态 offload 硬扛。** 实测日志会看到：

```
loaded partially; 1657 MB usable, 1503 MB loaded, 5864 MB offloaded
```

5.8 GB 在内存里，每步都在 PCIe 上搬。这就是为什么同样的工作流在 4090 上 0.3 s/it，在 4060 Ti 上 11 s/it。

### 路径写法

```
QwenImage2.1\qwen_image_2.1_int8_convrot.safetensors
```

带子目录——说明模型可以在 `models/diffusion_models/QwenImage2.1/` 下分类存放。

---

## 9. 参数指纹卡

如果给一个工作流打"指纹"，最有辨识度的四个值是：

```
[cfg, steps, denoise, resolution]
```

常见组合：

| 指纹 | 含义 |
|---|---|
| `1 / 25 / 1 / 1024` | 标准全模型文生图 |
| `1 / 8 / 1 / 1024` | Turbo 加速 |
| `1 / 25 / 1 / 0` | 保真编辑（不缩放） |
| `1 / 4 / 1 / 1024` | 极限速度（4-step LoRA） |
| `7.5 / 20 / 1 / 1024` | ⚠️ SD 习惯残留，画质会差 |

---

## 10. 参数层的四条铁律

1. **cfg = 1，除非你明确要用负向提示词（此时 2，不要超 3）**
2. **denoise = 1，除非你的 latent 来自 VAEEncode 原图**
3. **steps 和 LoRA 必须配对：25 无加速 / 8 配 turbo / 4 配 4-step**
4. **8 GB 卡 resolution 用 1024，不要 0**
