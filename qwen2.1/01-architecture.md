# 01 · 共性架构范式

拆完这几十个工作流，会发现它们在结构上高度同构。这一节讲**骨架**，具体算法在 02。

## 三层结构

```
┌─ 参数层 ───────────────────────────────────────────┐
│  AUSBOSS_NODES_Text        用户输入的提示词/指令     │
│  AUSBOSS_NODES_Seed        随机种子                 │
│  AUSBOSS_NODES_Integer     步数等数值               │
│  PrimitiveBoolean          "全身还是只换头"这类开关  │
│  AUSBOSS_NODES_WorkflowSwitches  一组功能开关        │
│  LoadImage / ImageCropRotatePad  输入图片            │
│  CLIPLoader / VAELoader / UNETLoader / LoraLoader   │
└────────────────────┬───────────────────────────────┘
                     ↓ 全部灌进子图
┌─ 算法层（子图 subgraph，折叠状态） ────────────────┐
│  UUID 形式的节点，双击才展开                        │
│  内部：TextEncodeQwenImage21 → KSampler → VAEDecode │
│        + Cache + Stitch                            │
└────────────────────┬───────────────────────────────┘
                     ↓
┌─ 后处理层 ─────────────────────────────────────────┐
│  AUSBOSS_NODES_Compare     前后对比 A/B             │
│  AUSBOSS_NODES_SaveImage   保存                     │
│  AUSBOSS_NODES_RunTimer    计时                     │
│  AUSBOSS_NODES_ShowText    显示最终发给模型的提示词  │
└────────────────────────────────────────────────────┘
```

**为什么要这样分？**

用户在画布上想改的永远只有第一层——提示词、图、步数、开关。算法层是作者的知识产权，折叠起来既省地方，也防止用户乱动。这跟写库的时候把实现藏进函数是一个道理。

## 子图封装：1.5x 最值得学的特性

### 它是怎么存的

工作流 JSON 顶层的 `definitions.subgraphs` 里，每个子图有独立的 `id / name / nodes / links / inputNode / outputNode`：

```json
{
  "definitions": {
    "subgraphs": [
      {
        "id": "14dec9cc-3745-4009-ad71-89527a9f9620",
        "name": "Redraw it in tiles with Qwen",
        "inputNode": -10,
        "outputNode": -20,
        "nodes": [ ... 11 个节点 ... ],
        "links": [ ... 19 条连线 ... ]
      }
    ]
  }
}
```

主画布上那个「看起来像个普通节点」的 UUID 节点，就是它的实例。

### 为什么这很重要（对工具而言）

**如果不展开子图，你会严重低估工作流的复杂度。**

Tiled Upscale 表面上 19 节点 / 13 连线，看起来很轻。展开后光一个子图就 11 节点 / 19 连线，而且**真正干活的逻辑 100% 在里面**——外面的 8 个节点只是往子图里灌参数。

所以任何分析工具（包括 duddy 自己）遇到 UUID 形式的节点类型，必须：
1. 认出它是子图，不是「未知节点类型」
2. 去 `definitions.subgraphs` 里查对应 id
3. 递归展开后再统计

否则分析结论全是错的。

### 子图的输入端口约定

从连线可以看到，子图实例的输入槽是**按顺序**接收的：

```
AUSBOSS_NODES_TiledUpscale[0]  -> subgraph[0]   # 图像块
AUSBOSS_NODES_TiledUpscale[1]  -> subgraph[1]   # 掩码/权重
AUSBOSS_NODES_ImageCropRotatePad[0] -> subgraph[2]  # 原图
AUSBOSS_NODES_Text[0]          -> subgraph[3]   # 提示词
AUSBOSS_NODES_Seed[0]          -> subgraph[4]   # 种子
CLIPLoader[0]                  -> subgraph[5]   # CLIP
VAELoader[0]                   -> subgraph[6]   # VAE
AUSBOSS_NODES_LoraLoader[0]    -> subgraph[7]   # 模型（已挂 LoRA）
subgraph[0]                    -> Compare[1]    # 输出图像
```

**没有命名，只有序号。** 这意味着子图的端口顺序是隐式契约，改一个就全乱。这也是为什么这类工作流很难被程序自动重组——顺序信息只存在于作者的脑子里和 JSON 数组的排列里。

## 参数外置的四个固定角色

### 1. 提示词 → `AUSBOSS_NODES_Text`

不是直接写在采样器的 widget 里，而是单独一个节点连进去。好处：
- 画布上位置自由，字体大，用户一眼看到
- 可以接 `StringConcatenate` 做自动拼接（见 Outpaint）
- 可以接 `ShowText` 显示最终结果

### 2. 数值 → `AUSBOSS_NODES_Integer`

步数这种参数单独拎出来。配合 `WorkflowSwitches` 使用——Fast 模式开的时候，Integer 的值被强制成 8 步。

### 3. 开关 → `PrimitiveBoolean` / `WorkflowSwitches`

Person Swap 里有一句关键设计：

```
PrimitiveBoolean "Boolean - Swap the whole body"  ->  subgraph[1]
```

**一个布尔值决定走两条完全不同的路径**（全身换 / 只换头）。子图内部根据这个值选择不同的 LoRA 组合。

### 4. 图片 → `LoadImage` / `AUSBOSS_NODES_ImageCropRotatePad`

后者是前者的超集：能在节点内直接裁剪、旋转、加边框。**省掉一堆预处理节点**，也让「输入区」在画布上只占一个格子。

## UI 层：把工作流当产品做

这几个节点不参与计算，但决定了一个工作流「好不好用」：

| 节点 | 作用 | 为什么值得学 |
|---|---|---|
| `AUSBOSS_NODES_WorkflowNote` | 画布上的说明卡片（JSON 存储，支持 Markdown） | 作者的支持渠道、依赖模型清单、使用步骤全在里面 |
| `AUSBOSS_NODES_Callout` | 贴在画布上的标注文字 | 直接指着某个节点说「这里改这个」 |
| `AUSBOSS_NODES_Compare` | 前后对比 A/B | 出图后立刻能判断改得好不好 |
| `AUSBOSS_NODES_RunTimer` | 计时 | 让用户知道这套流程的真实成本 |
| `AUSBOSS_NODES_ShowText` | 显示最终发给模型的提示词 | **调试神器**——提示词是拼出来的，不显示根本不知道发了什么 |
| `MarkdownNote` | 原生 Markdown 说明 | 不依赖第三方包 |

`ShowText` 这个设计特别值得抄：**当你用 StringConcatenate 自动拼提示词时，用户必须能看到拼出来的最终结果**，否则出了问题完全无从调试。

## 版本演进留下的痕迹

同一个模型往往有多个版本（v1.0 / v1.1 / v2.0），对比能看到作者在改什么：

- **Tiled Upscale**：v1.0 19 节点 → v1.1 20 节点，多了一个节点（大概是修拼接缝的）
- **Turbo Generate + Edit**：v1.0 22 节点 → v1.2 23 节点
- **Turbo Multi-Image Edit**：v1.0 24 节点 → v1.2 34 节点（多图输入从 2 张扩到 4 张）

节点数增长通常意味着「加了兼容分支」，而不是「算法变了」。这也是为什么作者要把逻辑封进子图——主画布不会被版本迭代搞得越来越乱。
