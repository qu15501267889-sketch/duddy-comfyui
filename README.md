# duddy-comfyui

**让 AI 在你的 ComfyUI 画布上干活，但每一笔都要你点头。**

不是「AI 生成一份 workflow JSON 然后 POST 过去」，而是：AI 先提交一份**分步计划**，你在 ComfyUI 界面右下角的弹窗里看清楚每一步要建什么节点、连哪根线，点「同意」之后，它才**一步一个节点**地在你的画布上长出来。你全程看得见，随时能喊停。

```
你在聊天里说「搭个文生图链路」
   ↓
AI 在聊天里讲计划：建哪些节点、怎么连、大概几步   ← 此时画布纹丝不动
   ↓
你说「做」
   ↓
duddy_plan_workflow   →  后端自动逐步执行到底
   ↓
画布上节点一个接一个长出来，右下角播报「第 3/12 步」+ 一个停止按钮
```

**确认发生在聊天里，不发生在弹窗里。** 弹窗只是播报牌，不需要你点任何东西。

## 两种模式

| 模式 | 怎么开 | 行为 |
|---|---|---|
| **auto（默认）** | 什么都不用做 | 计划一提交就自动跑完，画布实时可见，随时可停 |
| **manual** | `DUDDY_REQUIRE_APPROVAL=1` | 恢复审批闸门：必须等前端点「同意」，token 才有效 |

auto 模式下 `duddy_plan_workflow` **就是执行动作**，不是提案。
所以"先问后做"全靠技能层约束（`skills/duddy-comfyui/SKILL.md` 里写死了），而不是代码闸门。
manual 模式下才有代码级强制——这是用 0 和 1 换来的自由度。

---

## 为什么要有这东西

现有的 ComfyUI MCP 方案（`mcp-comfy-ui-builder`、`comfyui-mcp`、`comfymcp` 等）几乎都是同一个模式：

```
LLM 生成完整 API-format JSON  →  POST /prompt  →  直接开跑
```

问题在于：**你的画布上什么都没发生**。出图了，但你不知道它建了什么、连了什么线、参数对不对。想改？只能重来。这对学习、调试、复用都是灾难。

duddy 换个路子：**不碰 `/prompt`，只碰画布**。所有操作都通过 litegraph 在前端执行，等价于「有人替你拖节点、拉线」，你能看见过程。

---

## 核心机制：为什么 AI 绕不过去

这是本项目的设计核心，也是它和别的 MCP 最大的区别。

### 三根钉子

**钉子一：token 只走 WebSocket，不进 HTTP 响应**

```
POST /duddy/plan          → 返回 plan_id，**不含 token**
   ↓
后端 send_sync("duddy.plan.proposed", {plan, token}, sid=<浏览器>)
   ↓
只有那个浏览器标签页收到 token
```

AI 客户端用的是 stdio + HTTP，不持有浏览器那条 WebSocket。它拿不到 token，就调不动 `POST /duddy/plan/{id}/approve`。

**钉子二：MCP 层刻意不提供 approve 工具**

你去 `mcp_server/server.py` 里翻，只有 `duddy_apply_step`，没有 `duddy_approve_plan`。不是忘了写——是**不给 AI 这个能力**。

**钉子三：状态机在服务端强制**

`core/plan.py` 里，`begin_step()` 第一件事就是检查：

```python
if plan.state not in ("approved", "applying"):
    raise PlanError("必须先获得人类批准才能执行", 403, "not_approved")
```

跳步（`expected_index` 不符）、重放（重复回报同一 index）、非法跃迁，全部 403/409。没有「尽力而为」。

### 诚实说明边界

这三根钉子防的是**「AI 偷懒跳过审批流程」**——这是真实且高频的问题，实测有效。

它**不防**「AI 恶意攻击你自己的电脑」。本机上任何 Python 进程本来就是完全权限，可以 rm 你的硬盘、改你的 ComfyUI 源码。任何声称能防住这点的设计都是骗人。要看代码，别只看承诺。

---

## 安装

### 一键装

```bash
python install.py
```

它会：
1. 从运行中的 ComfyUI 的 `/system_stats` 读 `argv`，精确定位安装路径（比你猜路径靠谱）
2. 在 `custom_nodes/` 下建一个目录联接/软链（Windows 用 Junction，不用管理员权限，改代码即时生效）
3. 往 `~/.workbuddy/mcp.json` / Claude Desktop 配置里登记 MCP
4. 告诉你下一步

参数：

```bash
python install.py --dry-run              # 只看不动
python install.py --comfy "D:/AI/ComfyUI_windows_portable"
python install.py --no-mcp               # 只装 ComfyUI 侧
python install.py --force                # 覆盖旧链接
```

### 手动装

```bash
# 1. 把 comfyui_duddy 放进 custom_nodes
#    Windows: mklink /J "ComfyUI\custom_nodes\duddy-comfyui" "本仓库\comfyui_duddy"
#    Linux/mac: ln -s "$PWD/comfyui_duddy" "ComfyUI/custom_nodes/duddy-comfyui"

# 2. 重启 ComfyUI，刷新浏览器

# 3. 验证
curl http://127.0.0.1:8188/duddy/ping
```

返回 `{"ok": true, ...}` 就通了。

### 接 MCP 客户端

**WorkBuddy / Claude Desktop**（install.py 自动写，手动的话）：

```json
{
  "mcpServers": {
    "duddy": {
      "command": "D:/AI/ComfyUI_windows_portable/python_embeded/python.exe",
      "args": ["D:/github/wuddy/duddy-comfyui/mcp_server/server.py"],
      "env": { "COMFY_URL": "http://127.0.0.1:8188" }
    }
  }
}
```

**Hermes**（`~/.hermes/config.yaml`）：

```yaml
mcp_servers:
  duddy:
    command: 'D:/AI/ComfyUI_windows_portable/python_embeded/python.exe'
    args: ['D:/github/wuddy/duddy-comfyui/mcp_server/server.py']
    env:
      COMFY_URL: 'http://127.0.0.1:8188'
```

> MCP server 是**零依赖**的（只用标准库手写 JSON-RPC），所以直接用 ComfyUI 自带的嵌入式 Python 就行，不用另外配环境。

---

## 工具清单

| 工具 | 干什么 | 谁能调 |
|---|---|---|
| `duddy_ping` | 检查扩展在不在线 | AI |
| `duddy_plan_workflow` | 提交计划（只规划，不动手） | AI |
| `duddy_plan_status` | 查状态、进度、下一步 | AI |
| `duddy_apply_step` | **执行下一步**（必须已获批） | AI |
| `duddy_list_plans` | 最近计划列表 | AI |
| `duddy_abort_plan` | 中止 | AI |
| `duddy_canvas_state` | 读当前画布快照 | AI |
| `duddy_events` | 事件流（排障） | AI |
| `POST /duddy/plan/{id}/approve` | **审批** | 只有浏览器前端 |

---

## 计划怎么写

`steps` 里每一项是**一个**操作。别贪，一个节点一步、一条线一步——这样你在画布上才能看见它一个个长出来。

```json
{
  "title": "最小文生图链路",
  "summary": "从零搭一条能出图的最短路径",
  "steps": [
    {
      "kind": "create_node",
      "title": "建 Checkpoint 加载器",
      "why": "整条链的起点",
      "args": { "type": "CheckpointLoaderSimple", "ref": "ckpt", "pos": [100, 200] }
    },
    {
      "kind": "create_node",
      "title": "建正向提示词",
      "why": "描述要画什么",
      "args": { "type": "CLIPTextEncode", "ref": "pos", "pos": [450, 120] }
    },
    {
      "kind": "connect",
      "title": "CLIP → 正向提示词",
      "why": "文本编码需要 CLIP",
      "args": { "from_ref": "ckpt", "from_slot": 1, "to_ref": "pos", "to_slot": 0 }
    }
  ]
}
```

### 关键：用别名（ref）而不是 node_id

规划时节点还不存在，AI 不可能知道 ComfyUI 会分配什么 id。所以：

- `create_node` 必须给 `ref`（自己起名，如 `ckpt`）
- `connect` / `set_widget` / `move_node` 用 `from_ref` / `to_ref` / `ref` 引用
- 前端执行时维护 `ref → 真实 node` 的映射

这样 AI 只描述「谁连谁」，不会猜错 id，也不会因为顺序变化而全盘崩。

### 支持的 kind

| kind | 必填参数 | 说明 |
|---|---|---|
| `create_node` | `type` | 可选 `ref` `pos` `widgets` `title` `color` |
| `connect` | `from_ref`/`from_slot`/`to_ref`/`to_slot` | 槽位从 0 开始 |
| `set_widget` | `ref` `widget` `value` | 改参数 |
| `move_node` | `ref` `pos` | 摆位置 |
| `delete_node` | `ref` | 删掉 |
| `note` | — | 纯注释，跳过 |

---

## HTTP API

所有路由在 `/duddy` 前缀下。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/duddy/ping` | 存活检查 |
| GET | `/duddy/health` | 详细状态（客户端、画布、事件总线） |
| GET | `/duddy/capabilities` | 能力表 |
| POST | `/duddy/client/hello` | 前端报到 |
| POST | `/duddy/plan` | 创建计划 |
| GET | `/duddy/plan/{id}` | 计划详情 |
| POST | `/duddy/plan/{id}/approve` | 审批（**需 token**） |
| POST | `/duddy/plan/{id}/reject` | 拒绝 |
| POST | `/duddy/plan/{id}/step` | 执行下一步 |
| POST | `/duddy/plan/{id}/abort` | 中止 |
| POST | `/duddy/canvas/state` | 前端上报画布 |
| POST | `/duddy/canvas/applied` | 前端回报执行结果 |
| GET | `/duddy/events` | 事件流 |

WebSocket 事件（ComfyUI 原生 `/ws`）：

| 事件 | 方向 | 携带 |
|---|---|---|
| `duddy.plan.proposed` | 后端 → 前端 | plan + **token** |
| `duddy.plan.approved` | 后端 → 前端 | plan_id |
| `duddy.canvas.op` | 后端 → 前端 | 一步操作 |
| `duddy.canvas.applied` | 后端 → 前端 | 执行结果 |
| `duddy.plan.done` / `failed` | 后端 → 前端 | plan_id |

---

## 排障

**`/duddy/ping` 连不上**
1. ComfyUI 重启过吗？（自定义节点只在启动时扫描）
2. `custom_nodes/duddy-comfyui` 存在吗？
3. 控制台有没有 `[duddy] v0.1.0 ready`？

**弹窗不出来**
- F12 控制台看有没有 `[duddy] extension setup`
- 没有 → 前端扩展没加载，硬刷新（Ctrl+Shift+R）
- 有但没有弹窗 → `duddy_events` 看 `duddy.plan.proposed` 发出去了没

**apply 一直 403**
- 这是**正常拦截**。说明你还没点「同意」。
- 去 ComfyUI 页面右下角找弹窗。

**节点建不出来**
- 前端回报 `unknown_node_type` 说明缺插件，去 ComfyUI-Manager 装
- `connect_rejected` 说明类型不匹配，看回报里的 `from.type` / `to.type`

---

## 路线图

- [x] **M0** 骨架 + WebSocket 事件总线 + 安装器
- [x] **M1** plan / approve / apply 强制闸门 + 节点逐条长出
- [ ] **M2** 画布能力补全（delete/batch/group）+ 位置自动排布
- [ ] **M3** 执行进度追踪（跑到第几步、哪个节点、ETA）
- [ ] **M4** 实时日志流（侧栏滚动、按级别过滤）
- [ ] **M5** 文件监控（custom_nodes / models 增删改即时感知）
- [ ] **M6** 节点知识库（从 `/object_info` 自动同步，规划不瞎编）
- [ ] **M7** 技能包 + 跨客户端一键接入
- [ ] **M8** 文档 + 发布

---

## 协议版本

`duddy/1`。事件里都带 `v`、`seq`、`ts`、`type`、`data`。

## License

MIT。拿去改，改成你自己的也行。
