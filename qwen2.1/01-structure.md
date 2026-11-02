# 第一遍 · 结构层

> 数据基础：Civitai `baseModels=Qwen 2.1` + `Workflows` 全量 71 个（58 个可解析，13 个为纯 zip 或结构异常）。
> 分析脚本递归展开了所有 `definitions.subgraphs`，所以统计里的节点数**包含子图内部**。

---

## 1. 规模真相

| 指标 | 值 |
|---|---|
| 下载工作流 | 71 个模型 / 113 个文件 / 34 MB |
| 可解析 JSON | 58 个 |
| 子图总数 | **66 个** |
| 子图平均节点数 | **11.3** |
| 主画布节点范围 | 12 ~ 213 |

**66 个子图埋在 58 个工作流里，平均每个工作流 1.1 个子图。**

这说明子图不是"高级技巧"，而是**这批工作流的默认写法**。

---

## 2. 子图骨架：它们内部长什么样

### 内部节点出现频次（TOP12）

```
34×  TextEncodeQwenImage21      ← 几乎每个子图都有
33×  VAEDecode
29×  StringConcatenate          ← 提示词拼装
29×  KSampler
26×  ComfySwitchNode            ← 内部分支
23×  CLIPLoader
20×  VAELoader
20×  UNETLoader                 ← 有些子图连模型加载都封进去了
20×  QwenImage21Cache
20×  EmptyLatentImage
16×  PrimitiveStringMultiline
14×  StringFormat
```

**关键观察**：

1. **`TextEncodeQwenImage21` 出现 34 次**，比 `KSampler`（29）还多。说明它是 Qwen 2.1 的"入口节点"——没有它什么都干不了。
2. **`StringConcatenate` 29 次**——近一半的子图在做提示词拼装。这说明**自动拼提示词是常态而非特例**。
3. **`ComfySwitchNode` 26 次**——子图内部有分支逻辑。用开关在两条路径间切换，而不是写 if/else。
4. **模型加载节点也被封进子图 20 次**——说明子图不只是"算法封装"，有些是"整条链路封装"。

### 子图命名

| 名字 | 出现次数 | 含义 |
|---|---|---|
| `Text to Image (Qwen Image 2.1)` | 4 | 文生图子流程 |
| `Prompt Generator` | 4 | 提示词生成器 |
| `Write the prompt` | 3 | 同上 |
| `Image Edit (Qwen Image 2.1)` | 3 | 编辑子流程 |
| `Film Grain` | 2 | 胶片颗粒后处理 |
| `Resize` | 2 | 缩放 |
| `Swap with Qwen 2.1` | 2 | 换脸/换人 |
| `Upscale it in tiles with Qwen Turbo` | 2 | 分块放大 |
| `MODELS AND SAMPLER` | 2 | 模型+采样器打包 |

**命名规律**：动词开头 + 模型名 + 用途。这是好习惯——用户在画布上看到一个折叠节点，光看名字就知道里面是什么。

---

## 3. 连线模式：这才是最硬的证据

统计所有连线（含子图内部），按 `A -> B` 频次排序：

```
43×  TextEncodeQwenImage21  ->  KSampler
36×  GetNode                ->  TextEncodeQwenImage21
28×  VAELoader              ->  VAEDecode
26×  KSampler               ->  VAEDecode
22×  ResolutionSelector     ->  EmptyLatentImage
21×  CLIPLoader             ->  TextEncodeQwenImage21
21×  LoadImage              ->  TextEncodeQwenImage21
17×  VAELoader              ->  TextEncodeQwenImage21
16×  Character Sheet Pack   ->  Text Encode Qwen 2.1 [SML2]
15×  PrimitiveBoolean       ->  LC Bypasser
12×  GetNode                ->  KSampler
12×  Text Encode Qwen 2.1   ->  KSampler Pipe [SML2]
12×  UNETLoader             ->  AUSBOSS_NODES_LoraLoader
11×  LoadAndResizeImage     ->  SetNode
11×  GetAllActiveNode       ->  Character Sheet Pack
11×  PrimitiveBoolean       ->  ComfySwitchNode
10×  LoadAndResizeImage     ->  Fast Bypasser (rgthree)
10×  IO Checkpoint Loader   ->  Text Encode Qwen 2.1 [SML2]
10×  VAEDecode              ->  PreviewImage
```

### 从连线反推出的标准管线

**文生图 / 编辑的核心骨架只有一条**：

```
LoadImage ─────┐
CLIPLoader ────┼──→ TextEncodeQwenImage21 ──→ KSampler ──→ VAEDecode ──→ PreviewImage
VAELoader ─────┘         (同时收 CLIP + VAE)
```

三个输入（图 / CLIP / VAE）全灌进编码器，编码器把 positive、negative、latent 一起吐给 KSampler。

**这就是 Qwen 2.1 的"标准姿势"。** 所有变体都是在这条骨架上加东西。

### 三条值得注意的边

**① `VAELoader -> TextEncodeQwenImage21`（17 次）**
VAE 不只给 VAEDecode 用，还要给编码器——因为编码器内部要做 `VAEEncode(image_1)` 产出 latent。

**② `ResolutionSelector -> EmptyLatentImage`（22 次）**
想控制出图尺寸时，用官方的 `ResolutionSelector`（比例 + 百万像素）喂给 `EmptyLatentImage`，而不是手填宽高。

**③ `PrimitiveBoolean -> ComfySwitchNode`（11 次）**
布尔开关直接驱动切换节点。这是"一个开关切两条链路"的标准实现，比 Bypasser 更精细（Bypasser 是整组禁用，Switch 是选一路）。

---

## 4. 骨架聚类：58 个工作流其实只有几族

用「节点类型集合的 Jaccard 相似度 > 0.62」做并查集，58 个工作流聚成 **48 组**。

### 最明显的同族（4 个）

```
Qwen Image 2.1 Edit + Realign | Stays in Place
Qwen Image 2.1 Generate + Edit | Full Model, Auto Prompt
Qwen Image 2.1 Outpaint | Auto Prompt
Qwen Image 2.1 Rotate + Outpaint
```

这四个是同一个人（AusBoss）用同一套骨架换功能——**代码复用做到了工作流级别**。

### 第二大族（3 个）

```
Qwen Image 2.1 turbo int8 workflow | Edit
Qwen Image 2.1 turbo int8 workflow | Text to image
qwen 2.1 8 steps with prompt enchancer
```

Turbo + int8 的组合族。

### 其余 46 组大多是独苗

说明**大部分工作流是各写各的**，没有形成事实标准。真正的"标准"只存在于 AusBoss 一家的作品里。

---

## 5. 复杂度分布：封装能力的直接体现

| 工作流 | 主画布 | 含子图 | 封装率 |
|---|---|---|---|
| Character Dataset Creation | 213 | 230 | 7% |
| Workflow for your ART | 173 | 173 | 0% |
| Lonecats Fast Workflow | 153 | 153 | 0% |
| **ComfyUI Workflows** | **12** | **141** | **91%** |
| Qwen Image 2.1 Studio | 84 | 139 | 40% |
| Character Sheet Builder | 25 | 96 | 74% |

**`ComfyUI Workflows` 主画布只有 12 个节点，展开后有 141 个。**

同样量级的功能，有人摊成 213 个节点（打开就懵），有人封成 12 个（清爽）。

**封装率 = 工程素养。** 这不是审美问题——213 个节点的画布，改一个参数要在里面翻三分钟。

---

## 6. 结构层的三条结论

1. **子图是默认写法**，不是高级技巧。66 个子图 / 58 个工作流。
2. **核心骨架只有一条**：`LoadImage + CLIP + VAE → TextEncodeQwenImage21 → KSampler → VAEDecode`。所有工作流都是它的变体。
3. **封装率差异极大**（0% ~ 91%），这是"能不能给别人用"的分水岭。

---

## 附：分析复现方式

```bash
# 元数据
python .tmp/fetch_civitai.py
# 下载（需 CIVITAI_TOKEN）
python .tmp/download_parallel.py
# 结构解析
python .tmp/analyze_workflows.py
# 五维深挖（子图骨架/连线/语料/聚类/缺陷）
python .tmp/deep5.py
```

产出：
- `_index/models_raw.json` 原始清单
- `_index/analysis.json` 逐文件结构
- `_index/prompts.json` 794 条提示词语料（790 KB）
- `_index/clusters.json` 聚类结果
