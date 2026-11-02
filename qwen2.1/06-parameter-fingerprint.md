# 06 · 参数指纹与提示词工程

这一章不猜，全部来自 58 个可解析工作流的**实际参数值**统计（含子图展开）。

---

## 一、参数指纹：这个生态的"平均脸"

### CFG

| 值 | 出现次数 | 判断 |
|---|---|---|
| **1 / 1.0** | **52** | 正确答案，占 90% |
| 7.5 | 3 | ❌ SD 系习惯，Qwen 上会导致过锐 |
| 8 | 2 | ❌ 同上 |
| 2 | 1 | 可接受（想用负向词时） |
| 25 / 30 / 4 / 3 | 各 1 | 大概率是解析错位，或特殊节点 |

**结论：cfg=1 是这个生态的绝对共识。** 那 5 个用 7.5/8 的工作流，作者大概是从 SD 工作流改过来的，没意识到 Qwen 不是那个玩法。

> 顺带：cfg 越高，ComfyUI 越会执行负向条件分支，**图会变锐但也会变脏**。官方文档明说 cfg=5 "degrades quality badly"。

### steps

| 值 | 次数 | 场景 |
|---|---|---|
| **25** | **20** | 官方模板起步值，全模型的标准 |
| **8** | **15** | Turbo LoRA / 蒸馏模型 |
| 20 | 7 | 折中 |
| 4 | 4 | 极限压缩（配专门的 4-step LoRA） |
| 12 | 4 | 12-step Turbo LoRA |
| 35 | 2 | 追求细节（手部） |

**三大流派**：25 步（质量）、8 步（速度）、4 步（秒出）。

### denoise

| 值 | 次数 |
|---|---|
| **1 / 1.0** | **57** |
| 0.35 | 2 |
| 0.2 | 1 |

**57 : 3，压倒性。** 这直接印证了「条件式重绘，latent 是空的，降 denoise 只会糊」。

### resolution（Qwen 编码器的像素预算）

| 值 | 次数 | 含义 |
|---|---|---|
| **1024** | **38** | 约 1 MP，标准 |
| **0** | **23** | 不缩放，跟随原图 |
| 1088 / 704 / 992 / 1536 | 各 1-2 | 微调 |

**两种哲学**：`1024` = 主动统一到 1MP（快、省显存）；`0` = 忠实原图尺寸（保真但慢）。

### 采样器 / 调度器

```
euler  46 次   ← 绝对主流
er_sde  5 次
res_2s  3 次

simple  45 次  ← 绝对主流
beta    10 次
```

**euler + simple 是这个生态的默认答案**，和官方模板一致。少数用 `beta` 的是在做锐化风格。

### auraflow_shift

出现 `1024 / 0.5 / 0.69 / 5 / 1344` 等一堆值——**这个字段统计不可靠**，因为 `ModelSampling*` 节点的 widget 结构各不相同，很容易把别的参数算进来。**结论：不要从这个统计里下判断，按官方推荐 3.0 用。**

---

## 二、LoRA 生态：六件套

统计出的 LoRA 使用频次：

| LoRA | 次数 | 强度 | 作用 |
|---|---|---|---|
| `qwen-image-2.1-consistency` | 4 | 1.0 | **保持主体一致**（角色跨图不变） |
| `viggle-turbo-v0.3-6step-r128` | 4 | 1.0 | 8 步 Turbo 加速 |
| `qwen-image-2.1-restore` | 3 | 0.5~1.0 | **保真锚点**，把结果拉回原图 |
| `qwen-image-2.1-outfit-swap` | 2 | 1.0 | 换装 |
| `qwen-image-2.1-outpaint` | 2 | 1.0 | 扩图专用 |
| `p_qwen_image_2.1_8step_v0.1` | 2 | 1.0 | 另一个 8 步加速 |
| `bfs_body_swap_v1.0` | 1 | 1.0 | 全身替换 |
| `bfs_head_v1.1` | 1 | 1.0 | 只换头 |

**规律**：
1. **强度几乎全是 1.0**，唯一例外是 restore 在糊图场景降到 0.5
2. **一个任务一个 LoRA**，靠开关切换，不叠加
3. **consistency 和 restore 是两类不同的东西**：
   - `consistency` = 让**不同图之间**的人物像同一个人（角色一致性）
   - `restore` = 让**同一张图**重绘后别跑偏（保真）

---

## 三、模型文件：三种量化格式并存

统计到的 UNET/CLIP 文件名：

```
qwen_image_2.1_int8_convrot.safetensors     ← INT8（主流）
qwen\rayQwen21_turboV13Int8convrot...       ← 第三方 INT8 turbo
qwen_image_2.1_turbo_bf16.safetensors       ← BF16（大）
qwen3vl_8b_int8_convrot.safetensors         ← 文本编码器 INT8（9.35GB）
qwen3vl_8b_nvfp4.safetensors                ← NVFP4（新，更省）
qwen3VL8BHereticINT8W4A8_int8.safetensors   ← 社区量化
```

**注意路径写法**：`QwenImage2.1\qwen_image_2.1_int8_convrot.safetensors` —— 带子目录。说明模型可以放在 `models/diffusion_models/QwenImage2.1/` 下面分类存放。

---

## 四、提示词工程：这才是真功夫

106 条提示词样本，去重后能看出几种成熟套路。

### 套路 1：Prompt Rewriting Expert（6 次出现）

有些工作流里塞了一段**系统提示词**，用 LLM 把用户的简短请求改写成一段详细描述：

```
# Image Prompt Rewriting Expert
You turn a user's image request into one long English paragraph
that describes the finished picture as if you were looking at it...
```

**这就是"自动提示词"的实现方式**——不是模板拼接，是真的让 LLM 写。

### 套路 2：Edit Prompt Enhancer（4 次）

专门用于编辑任务的增强器，而且处理了一个很实际的问题：

```
# Edit Prompt Enhancer — General (v2)
FIRST — there are TWO separate language decisions. Do NOT conflate them.
(A) 改写后提示词中"描述性正文"的语言...
```

它在明确区分「改写正文用什么语言」和「指令用什么语言」。**这说明中文用户和英文模型的混用是个真实痛点。**

### 套路 3：占位符约定 `<image1>`

```
head_swap: start with <image1> as the base image, keeping its lighting,
environment, and background. remove the...
```

```
Character reference sheet of the person in <image1>. Show the same person
three times side by side, full body...
```

**`<image1>` 是社区自发的占位符约定**，对应 `TextEncodeQwenImage21` 的 image_1 槽位。官方文档用的是 `image 1`，社区写成 `<image1>`，两种都能被识别。

**这是个可以直接抄的写法**：提示词里用 `<image1>` 明确指代，模型知道你说的是哪张图。

### 套路 4：负向词的尴尬（重要）

统计到好几组负向词：

```
deformed, extra limbs, blurry, low quality
blurry, out of focus, low quality, pixelated, jpeg artifacts,
distorted, deformed, malformed anatomy, unnatural...
low quality, low resolution, blurry, out of focus, distorted,
deformed, bad anatomy, malformed hands, extra fingers...
```

**但 90% 的工作流 cfg=1，意味着这些负向词一个都没生效。**

这就是 SD 时代习惯的直接残留。作者们抄了负向词模板，却没意识到 Qwen 2.1 在 cfg=1 下根本不读它。

**唯一有效的用法**：把想避免的东西**改写成正向陈述**塞进正向提示词。

### 套路 5：中文提示词

样本里有几段中文长提示词（B站封面、提示词改写专家），说明：
- 中文能用，Qwen 中文能力不弱
- 但**复杂指令类**（改写器、增强器）还是英文写效果更稳

---

## 五、复杂度分布：从 12 节点到 213 节点

| 工作流 | 主画布 | 含子图 |
|---|---|---|
| Character Dataset Creation | **213** | 230 |
| Workflow for your ART | 173 | 173 |
| Lonecats Fast Workflow | 153 | 153 |
| ComfyUI Workflows | 12 | **141** |
| Qwen Image 2.1 Studio | 84 | 139 |
| Character Sheet Builder | 25 | 96 |

**两个极端都值得注意**：

- `Character Dataset Creation` 213 个节点全摊在画布上，没封装 → 打开就是地狱
- `ComfyUI Workflows` 主画布只有 **12 个节点**，展开子图有 **141 个** → 这才是正确的封装姿势

**同样复杂的功能，一个画布 213 个节点，一个只有 12 个。** 差距全在会不会用子图。

---

## 六、从这些数据里能提炼的三条硬规则

1. **cfg=1、denoise=1、euler+simple、resolution 1024 或 0** —— 这五个值构成 Qwen 2.1 的"标准姿势"，偏离任何一个都要有明确理由
2. **一个任务配一个 LoRA，用开关切换，不叠加** —— 叠加会互相干扰
3. **负向提示词在 cfg=1 下是废的** —— 想约束画面，写进正向

---

## 七、还有一件事：这些工作流里没有的东西

翻遍 58 个没找到：

- **没人用 ControlNet**（Qwen 2.1 的 ControlNet 生态还没起来）
- **没人做真正的多轮编辑**（都是一次性指令）
- **极少有人做批量**（batch_size 几乎都是 1）
- **没有 seed 复现体系**（换个种子就是另一张脸）

这说明 **Qwen 2.1 的工作流生态还在很早期**——大家还在解决"怎么把它跑起来"，没到"怎么把它用好"的阶段。

对 duddy 来说这是机会：**如果能把 ControlNet、批量、种子管理这些补上，做出来的工作流会比现在这些更完整。**
