# 03 · 逐个拆解

数据全部来自实际下载的工作流 JSON，节点号对应画布上的 `id`。

---

## 案例一：Tiled Upscale | Up to 8 MP（id 2988629）

**规模**：主画布 19 节点 / 13 连线；子图内 11 节点 / 19 连线
**用途**：把手机照片、老存档、糊 JPEG 放大到 4~8 MP，靠重绘而不是拉伸

### 主画布

```
参数层
  #1  WorkflowNote        说明卡片
  #2  ImageCropRotatePad  原图（可直接裁切）
  #3  TiledUpscale        目标百万像素
  #4  Text                请求（已经在框里写好，不用改）
  #5  Seed                种子
  #6  WorkflowSwitches    Speed（Fast 开关）
  #7  Integer             步数
  #8  CLIPLoader          qwen3vl_8b_int8
  #9  VAELoader           qwen_image_2.1_vae_bf16
  #10 UNETLoader          Qwen 2.1（turbo/int8 等）
  #11 LoraLoader          Restore LoRA

算法层
  #23 (子图) "Redraw it in tiles with Qwen"

后处理
  #12 Compare  #13 SaveImage  #14 RunTimer
  #24~27 Callout ×4
```

### 子图内部（关键）

```
TextEncodeQwenImage21  "One tile and the request"
QwenImage21Cache       "Reference cache"
KSampler               "CFG 1, euler"
VAEDecode
TiledUpscaleStitch     "Join the tiles"
Reroute ×3
Callout ×3
```

### 输入端口顺序（隐式契约）

| 槽 | 来源 | 含义 |
|---|---|---|
| 0 | TiledUpscale[0] | 图块 |
| 1 | TiledUpscale[1] | 掩码/权重 |
| 2 | ImageCropRotatePad[0] | 原图 |
| 3 | Text[0] | 提示词 |
| 4 | Seed[0] | 种子 |
| 5 | CLIPLoader[0] | CLIP |
| 6 | VAELoader[0] | VAE |
| 7 | LoraLoader[0] | 挂好 LoRA 的模型 |

### 可学之处

1. **把复杂度压进子图**：用户看到的只有 8 个参数节点
2. **接缝处理在子图里**：`TiledUpscaleStitch` 负责重叠带融合，外面完全无感
3. **提示词已经预填**：`"The request Qwen gets is already in the text box. It works as it is."` —— 用户不用写提示词，这是产品化思路

---

## 案例二：Person Swap | Head or Full Body（id 2980685）

**规模**：28 节点 / 24 连线，2 个子图
**用途**：把人物换到另一张图的场景里，支持「只换头」和「整个身体」两种模式

### 结构特点

```
#2  LoadImage "Scene picture"        ← 场景
#3  LoadImage "Character 1"          ← 要换进去的人
#4  Text     "Who to replace"        ← 换谁
#5  PrimitiveBoolean "Swap the whole body"  ← 关键开关
#10 LoraLoader "Body swap LoRA"
#11 LoraLoader "Head swap LoRA"
#15 (子图 99445e5e-...)  全身边换
#16 (子图 0d42daac-...)  只换头
#51 ShowText "Request sent to Qwen"  ← 显示最终提示词
```

### 核心设计：一个布尔值分两条链

```
Text[0] ────────────────┐
                        ├──→ 子图 99445e5e (全身边)
PrimitiveBoolean[0] ────┘

                          → StringConcatenate → 子图 0d42daac (只换头)
```

**两个子图并行摆着，靠开关决定哪条生效。** 这比在一个子图里塞 if/else 清晰得多，也方便用户理解。

### 两个 LoRA 分开挂

Body swap 和 Head swap 是两个独立 LoRA，各自挂在不同链路上。**不能同时挂**——会互相干扰。用开关二选一是正确解法。

### 可学之处

- **多方案并存**：不要试图用一个流程兼容两种需求，直接摆两条链 + 开关
- **ShowText 是必备**：提示词是拼出来的，不显示就看不出问题
- **输入区分离**：场景图、角色图、描述各占一个节点，位置固定

---

## 案例三：Outpaint | Auto Prompt（id 2964810）

**规模**：23 节点 / 27 连线，无子图（逻辑全摊开）
**用途**：向外扩展画布，补全原图之外的区域

### 完整流程

```
素材层
  #5  LoadImagePad  "drag any edge outward"   ← 拖边扩展画布
  #23 TextGenerate  "caption the source"      ← 自动描述原图
  #28 Text "caption instruction (training-caption style)"
  #29 Text "extra detail for the new area (optional)"

提示词拼装
  #30 StringConcatenate  "Caption instruction + detail"
  #24 StringConcatenate  "instruction + ' Scene: ' + description"
  #25 ShowText           "Prompt sent to Qwen 2.1"

编码 + 采样
  #9  TextEncodeQwenImage21  "padded canvas = image_1, resolution 0"
  #26 QwenImage21Cache       "automatic"
  #14 KSampler               "Qwen 2.1: 25 steps, CFG 1"
  #15 VAEDecode
  #27 SplitImageWithAlpha    "Qwen 2.1 decodes RGBA"

后处理
  #16 StitchInpaint  "original pixels back, seams matched"
  #18 Compare #19 SaveImage #17 RunTimer
```

### 三个精妙点

**① 自动 caption（#23）**
让模型先描述原图，拼成 `扩展指令 + " Scene: " + 描述`。解决了「新区域不知道场景是什么」的问题。

**② StitchInpaint（#16）**
重绘完整个画布后，把**原图区域的像素贴回**，只在边界羽化。这样提示词可以放心写"重绘整个场景"，不用担心改坏主体。

**③ SplitImageWithAlpha（#27）**
Qwen 2.1 的 VAE 四通道，解码出 RGBA。后面保存时可以输出带透明的 PNG。

### 可学之处

- **不加子图也能做得很清楚**：这个作者选择全摊开，因为每一步都值得用户看见（尤其是 caption 和 prompt 拼接）
- **两段 StringConcatenate**：先拼细节，再拼场景描述，层次分明
- **resolution 0**：不缩放参考图，保持原分辨率

---

## 案例四：Turbo Multi-Image Edit | 8 Steps（id 3000002）

**规模**：主画布 24 节点 / 26 连线，2 个子图
**用途**：多图输入编辑（把 2 张图合成/换装），8 步快速出图

### 结构

```
#3  Text  "Your prompt"
#5  ImageCropRotatePad  "Image 1, your picture"   ← 主图
#6  ImageCropRotatePad  "Image 2"                 ← 参考图
#12 LoraLoader  "Consistency or Outfit Swap"
#2  WorkflowSwitches  "What to run"
#57 (子图 e8411ffa-...) 编辑
#59 (子图 41465cc8-...?) 放大
```

### 多图输入怎么连

```
ImageCropRotatePad(图1)[0] -> 子图[2]
ImageCropRotatePad(图1)[0] -> 子图[3]
ImageCropRotatePad(图1)[0] -> 子图[4]
```

**同一张图连了三个槽位**（image_1 / image_2 / image_3）。这是 Qwen 2.1 的多参考图机制：`image_1` 是被编辑的画布，后面的是内容参考。

从 v1.0 到 v1.2，节点数从 24 涨到 34——**多图槽位从 2 张扩到 4 张**。

### 链式子图

```
子图 e8411ffa（编辑）输出 -> TiledUpscale -> 子图 37bafbff（放大）
```

**先编辑再放大，两个子图串联。** 这是把"任务拆成两段"的典型做法：编辑阶段用低分辨率保证速度，放大阶段单独处理细节。

### 可学之处

- **多图槽位按顺序**：image_1 是画布，其余是素材
- **子图可串联**：每个子图负责一件事，组合起来就是完整管线
- **LoRA 决定任务类型**：`Consistency or Outfit Swap` —— 挂哪个 LoRA 就干哪种活

---

## 横向对比

| 工作流 | 节点 | 子图 | 关键手法 | 适合谁 |
|---|---|---|---|---|
| Tiled Upscale | 19 | 1 | 分块重绘 + 拼接 | 需要放大老照片/手机照 |
| Person Swap | 28 | 2 | 布尔开关分两条链 + 双 LoRA | 换脸/换人 |
| Outpaint | 23 | 0 | 自动 caption + 原像素贴回 | 扩图 |
| Turbo Multi-Image | 24 | 2 | 多图槽位 + 子图串联 | 多图合成/换装 |

**共同点**：
1. 参数全部外置成独立节点
2. 都挂了 Restore LoRA 保真
3. 都有 Compare 做前后对比
4. cfg 恒为 1，denoise 恒为 1
5. 都有 WorkflowNote 说明卡片
