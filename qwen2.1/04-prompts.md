# 第四遍 · 提示词层

> 语料：从 58 个工作流（含子图）里提取的所有 ≥30 字符字符串，共 **794 条**。
> 分类：普通提示词 659 / 系统提示词 114 / 负向词 21。
> 全量保存在 `_index/prompts.json`（790 KB）。

---

## 1. 系统提示词才是真资产

114 条系统提示词按用途分：

| 类型 | 数量 | 干什么 |
|---|---|---|
| 提示词改写器 | 11 | 把用户的一句话扩写成完整描述 |
| 提示词增强器 | 7 | 在改写基础上做质量提升 |
| 图像描述器 | 5 | 看图写描述（自动 caption） |
| 角色设定 | 13 | "You are a..." 型角色约束 |
| 其他/未分类 | 76 | 格式约束、输出规范等 |
| 节点文档（误捕） | 2 | 2 万字节点清单，不是提示词 |

**注意**：有 2 条被误捕进语料——22005 字的「# Total nodes」，其实是 LC123 插件的节点清单。**这种"把文档塞进说明节点"的做法会导致自动化工具误判**，写工具的人要注意。

---

## 2. 深度案例：一个 17709 字的增强器

这是全量语料里最精致的系统提示词。它解决的问题很多人根本想不到：

### 问题：改写提示词时，到底该用哪种语言？

场景：用户用中文下指令，要改一张**图里全是泰文招牌**的照片。

**直觉答案**：全用中文。
**正确答案**：描述正文用中文，**图里渲染的招牌文字必须用泰文**。

### 它的解法：把语言决策拆成两套

```
(A) 描述性正文的语言 —— 双引号之外的每一个词
    规则：跟用户指令的语言走
    - 中文指令 → 中文描述
    - 英文指令 → 英文描述
    - 其他语言指令 → 一律英文描述（保底）

(B) 图内渲染文字的语言 —— 双引号之内的内容
    优先级 1：用户明确指定了文字或目标语言 → 照做
    优先级 2：原图里已有文字 → 用原图的**主导语言**
    优先级 3：都没有 → 用用户指令的语言（不要强塞英文）
```

### 它还堵了两个坑

**坑一：混排**
> all rendered (quoted) text must be **monolingual** — do not mix Chinese and English inside the quotes

引号内不许中英混排，也不许自动给双语对照（除非用户要求）。

**坑二：风格绑架语言**
> **genre never overrides input language**：技术参数表/分镜/数据文档的观感靠排版和字体实现，不是靠把标签全改成英文

这一条极其重要——很多人做"科技感海报"就自动把标签写成英文，这是错的。**科技感是排版，不是语言。**

### 为什么这条值得学

它把"模糊的任务"拆成了"可执行的决策树"，而且**给出了优先级冲突时的裁决规则**。这才是工程化的提示词。

大部分人写提示词是"形容词堆砌"，这个是"决策逻辑"。

---

## 3. 负向提示词的集体失效

21 条负向词，典型内容：

```
deformed, extra limbs, blurry, low quality
blurry, out of focus, low quality, pixelated, jpeg artifacts,
distorted, deformed, malformed anatomy, unnatural proportions...
low quality, low resolution, blurry, out of focus, distorted,
deformed, bad anatomy, malformed hands, extra fingers...
```

**这些是标准的 SD 时代模板。**

但前面的参数统计告诉我们：**90% 的工作流 cfg=1，这些词一个都没生效。**

ComfyUI 在 cfg=1 时会**跳过整个负向条件分支**——不是权重为 0，是压根不执行。

### 唯一有效的替代方案

把想避免的东西**改写成正向陈述**：

```
❌ 负向：blurry, low quality, deformed
✅ 正向：画面清晰锐利，皮肤纹理自然，解剖结构准确
```

**正向描述是全程生效的，负向在 cfg=1 下是装饰品。**

---

## 4. 占位符约定：`<image1>`

社区自发形成的写法：

```
head_swap: start with <image1> as the base image, keeping its lighting,
environment, and background. remove the...

Character reference sheet of the person in <image1>. Show the same person
three times side by side, full body...

Create one an image of the clown girl on a white background...
```

**`<image1>` 明确指代第 1 张参考图**，对应 `TextEncodeQwenImage21` 的 `image_1` 槽位。

官方文档写的是 `image 1`（带空格），社区写成 `<image1>`（尖括号）。**两种都能识别**，但尖括号更清晰，不会和正文混淆。

**多图场景建议**：`<image1>` 是画布（被编辑的），`<image2>` 之后的都是素材。提示词里要明确说"用 <image2> 的衣服换到 <image1> 的人身上"。

---

## 5. 触发词模式

LoRA 的触发词写法有两种：

**① 前缀式**
```
head_swap: start with <image1> as the base image, keeping its lighting...
```
`head_swap:` 是触发词，后面跟指令。

**② 自然语言式**
```
Turn her around. she stands up. natural skin, impefect skin.
real skin texture. sharp details. Extreme detail...
```

**第一种更可靠**——触发词独立成前缀，模型不会把它当成正文。

**这也是为什么 LoRA Loader 的 `triggers` 字段有价值**：自动注入前缀，不用人记。

---

## 6. 提示词风格：长短两派

### 长段落派（AusBoss 系）

> Restore the photo: remove blur, noise and compression artifacts and recover sharp, natural detail, keeping the same composition, people and colors.

**特点**：一句话说清"做什么 + 保持什么"，无形容词堆砌，动词开头。

### 标签流派（SD 残留）

> masterpiece, best quality, 8k, ultra detailed, cinematic lighting...

**Qwen 2.1 对标签流的响应很差。** 它是 LLM 编码的（Qwen3-VL），按句子理解，不按 tag 匹配。

**正确姿势是写句子**：

```
❌ a cat, masterpiece, best quality, 8k
✅ A cat sitting on a wooden windowsill, warm afternoon light
   from the left, soft shadows, shallow depth of field
```

---

## 7. 中文能用，但复杂指令还是英文稳

语料里有几段高质量中文提示词：

```
设计一张高质量、具有强烈视觉冲击力和专业科技感的 Bilibili AI 教程视频封面，
竖版 3:4 构图，整体风格参考热门科技类视频缩略图...
```

**结论**：
- **出图描述** → 中文完全可用，Qwen 中文能力强
- **系统指令 / 改写器 / 增强器** → 英文更稳（语料里 114 条系统提示词，中文的只有 2 条）

原因很实际：**复杂的逻辑约束（优先级、例外、裁决规则）用英文表达更不容易歧义。**

---

## 8. 提示词工程的四个层次

按成熟度排序，语料里都能找到例子：

| 层次 | 特征 | 占比（估） |
|---|---|---|
| **L1 标签堆砌** | masterpiece, 8k, best quality | ~40% |
| **L2 句子描述** | 一句话说清画面 | ~35% |
| **L3 模板化** | 固定句式 + 变量（`<image1>`） | ~20% |
| **L4 决策树** | 优先级、例外、裁决规则 | ~5% |

**L4 只有那个 17709 字的增强器达到了。** 这就是为什么它值得单独研究。

---

## 9. 提示词层的四条结论

1. **负向提示词在 cfg=1 下是废的** —— 21 条负向词，一条都没生效
2. **Qwen 要句子不要标签** —— 它是 LLM 编码，按语义理解
3. **`<image1>` 是社区标准占位符** —— 多图场景必须用它指明对象
4. **系统提示词是真正的资产** —— 114 条里有 5% 达到了"决策树"级别，那才是护城河
