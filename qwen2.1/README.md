# Qwen Image 2.1 工作流研究（五遍深挖）

对 Civitai 上 **71 个 Qwen 2.1 ComfyUI 工作流**的五轮分析。每一轮换一个角度，越钻越深。

## 数据底座

| 项 | 值 |
|---|---|
| 来源 | civitai.com，`baseModels=Qwen 2.1` + `Workflows`，Highest Rated |
| 抓取 | 2026-11-02 |
| 工作流 | 71 个（58 个可解析 JSON，13 个为 zip 或结构异常） |
| 文件 | 113 个 / 34 MB |
| 子图 | **66 个**（全部递归展开分析） |
| 提示词语料 | **794 条**（659 普通 / 114 系统 / 21 负向） |
| 同期 Checkpoint | 21 个 / 505 GB（未下载） |

本地路径：`D:\AI\学习工作流\`（每个模型一个目录，含 README + 工作流文件 + `_meta.json`）

---

## 五遍分别挖了什么

### [第一遍 · 结构层](01-structure.md)
子图骨架、连线模式、骨架聚类、封装率。

**最硬的发现**：
- 66 个子图 / 58 个工作流，**子图是默认写法不是高级技巧**
- 核心骨架只有一条：`LoadImage + CLIP + VAE → TextEncodeQwenImage21 → KSampler → VAEDecode`
- 封装率从 0% 到 91%，同样功能有人摊 213 个节点，有人封 12 个

### [第二遍 · 参数层](02-parameters.md)
58 个工作流的实际 widget 值统计。

**最硬的发现**：
- `cfg=1` 占 90%（52:5），剩下 5 个用 7.5/8 是 SD 习惯残留
- `denoise=1` 占 95%（57:3）
- steps 三大流派：25（20次）/ 8（15次 Turbo）/ 4（4次）
- `euler + simple` 是绝对主流（46/45）
- LoRA 六件套，强度几乎全是 1.0

### [第三遍 · 实现层](03-internals.md)
从参数数组和连线反推节点内部机制。

**最硬的发现**：
- `TextEncodeQwenImage21` 是**三输出**节点（positive/negative/latent）
- 它内部的 latent **只承担画布尺寸职责**，原图走 conditioning
- 这解释了为什么 denoise 恒为 1 还能工作
- 自动 caption 的确切配置：temperature 0.3 + 固定 seed + repetition_penalty 1.05
- 扩图填充用 `#808080` 中灰，不用纯白纯黑

### [第四遍 · 提示词层](04-prompts.md)
794 条语料的分类与深读。

**最硬的发现**：
- 114 条系统提示词里，最精致的是一条 **17709 字**的「Edit Prompt Enhancer」
- 它把语言决策拆成两套：**描述正文**跟指令语言，**图内渲染文字**走三优先级
- 21 条负向词模板在 cfg=1 下**全部失效**
- `<image1>` 是社区自发形成的占位符标准

### [第五遍 · 评判层](05-judgement.md)
缺陷清单、成熟度分级、生态空白、对 duddy 的动作项。

**最硬的发现**：
- 6 个工作流 cfg 用错，17 个没有输出节点
- 产品级工作流几乎全是 AusBoss 一个人的
- **生态空白**：没有 ControlNet、没有多轮编辑、批量几乎为零、没有种子管理
- 真正有独立设计的只有 5 个工作流，其余是"换提示词"

---

## 六条可以直接用的结论

**1. Qwen 2.1 的标准姿势**
```
cfg = 1 · denoise = 1 · euler · simple
steps = 25（无加速）或 8（配 Turbo LoRA）
resolution = 1024（要快）或 0（要保真）
```

**2. 负向提示词在 cfg=1 下是废的**
想约束画面，把禁令改写成正向陈述塞进正向提示词。

**3. Qwen 要句子不要标签**
它是 LLM 编码的（Qwen3-VL），按语义理解。`masterpiece, 8k` 这种标签流效果很差。

**4. 重绘后把原图贴回**
`StitchInpaint` / `TiledUpscaleStitch`。比指望模型"保持不变"可靠一万倍。

**5. 自动 caption 解决场景漂移**
先让模型描述原图（temperature 0.3、seed 固定），拼成 `指令 + " Scene: " + 描述`。

**6. 子图是这个生态的基本组织方式**
66 个子图埋在 58 个工作流里。不展开子图，等于没看懂任何一个工作流。

---

## 给 duddy-comfyui 的行动清单

| 优先级 | 事项 | 理由 |
|---|---|---|
| **P0** | 子图识别与递归展开 | UUID 类型当前被判成"未知节点"直接拦截 |
| **P0** | `SetNode`/`GetNode` 变量引用解析 | 否者误判链路断开 |
| **P1** | 模型参数规则表（cfg/denoise/负向词） | 解决"AI 不懂模型"的核心短板 |
| **P2** | 日志数字解读（offload/显存） | 用户需要"该改什么"而不是原始日志 |
| **P3** | 搭工作流的布局与封装规范 | 观感问题，不紧急 |

---

## 复现方式

```bash
python .tmp/fetch_civitai.py            # 拉元数据
python .tmp/download_parallel.py        # 下载（需 CIVITAI_TOKEN）
python .tmp/analyze_workflows.py        # 结构解析
python .tmp/deep5.py                    # 五维深挖
python .tmp/gen_readme.py               # 为每个工作流生成 README
```

产出文件：
- `_index/models_raw.json` — 原始清单（71 工作流 + 21 Checkpoint）
- `_index/analysis.json` — 逐文件结构
- `_index/fingerprint.json` — 参数指纹
- `_index/prompts.json` — 794 条语料（790 KB）
- `_index/clusters.json` — 骨架聚类
