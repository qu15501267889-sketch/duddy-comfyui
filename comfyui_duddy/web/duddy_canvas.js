/**
 * duddy-comfyui 前端扩展
 *
 * 职责：
 *   1. 向后端报到（拿 clientId），让后端知道该把画布指令投给谁
 *   2. 接收 duddy.plan.proposed → 弹窗让人拍板（token 只在这里）
 *   3. 接收 duddy.canvas.op → 执行 litegraph 操作，每一步单独回报
 *   4. 让节点「长出来」看得见
 *
 * 绝不自己发起任何画布改动，所有改动都来自已批准的 plan。
 */

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const VERSION = "0.1.0";
const LOG = (...a) => console.log("%c[duddy]", "color:#4a9eff", ...a);
const WARN = (...a) => console.warn("%c[duddy]", "color:#ffb020", ...a);

const D = {
  clientId: null,
  refs: new Map(),
  queue: [],
  active: null,
  busy: false,
  lastCanvasPush: 0,
  lastSeq: 0,
  pollMode: false,
  pollTimer: null,
};

/* ------------------------------------------------------ 事件分发基础设施

ComfyUI 前端 1.5x 的 api.js 收到 ws 消息时这样判断：

    if (this._registered.has(type)) dispatchEvent(...)
    else if (!reportedUnknownMessageTypes.has(type)) throw Error('Unknown message type')

而 `_registered` 只由 addCustomEventListener() 填充，
addEventListener() 不会加。用错方法的结果是：第一次抛错并记入
reportedUnknownMessageTypes，之后同类型消息被**静默丢弃**——
表现就是"接不上、没反应、也不报错"。

所以必须用 api.addCustomEventListener。拿不到就退化成 HTTP 轮询。
*/

const KNOWN_EVENTS = [
  "duddy.plan.proposed",
  "duddy.plan.approved",
  "duddy.plan.rejected",
  "duddy.plan.done",
  "duddy.plan.failed",
  "duddy.plan.aborted",
  "duddy.plan.expired",
  "duddy.canvas.op",
  "duddy.canvas.applied",
  "duddy.run.progress",
  "duddy.log.line",
  "duddy.fs.change",
];

const handlers = new Map();

function on(type, fn) {
  if (!handlers.has(type)) handlers.set(type, []);
  handlers.get(type).push(fn);
}

function dispatch(type, data, seq) {
  if (seq) {
    if (seq <= D.lastSeq) return; // 去重：ws 与轮询可能都收到同一条
    D.lastSeq = seq;
  }
  const list = handlers.get(type);
  if (!list || !list.length) return;
  for (const fn of list) {
    try {
      fn(data || {});
    } catch (e) {
      WARN("handler error", type, e);
    }
  }
}

function attachWsEvents() {
  if (typeof api.addCustomEventListener !== "function") {
    WARN("api.addCustomEventListener 不存在，无法接收自定义 ws 事件");
    return false;
  }
  let ok = 0;
  KNOWN_EVENTS.forEach((t) => {
    try {
      api.addCustomEventListener(t, (e) => {
        // 注意：seq 在外层 payload 上，不能先剥壳再取，否则去重失效
        const outer = (e && e.detail) || {};
        const payload = outer.data || outer;
        dispatch(t, payload, outer.seq);
      });
      ok += 1;
    } catch (err) {
      WARN("注册自定义事件失败", t, err);
    }
  });
  LOG(`已注册 ${ok}/${KNOWN_EVENTS.length} 个自定义事件`);
  return ok > 0;
}

/** 把 lastSeq 对齐到服务器当前最新，避免页面刷新后轮询重放历史画布操作。 */
async function alignSeq() {
  try {
    const r = await api.fetchApi("/duddy/events?limit=1");
    const j = await r.json();
    const list = j.data || [];
    if (list.length) {
      const newest = list[list.length - 1].seq;
      if (newest > D.lastSeq) D.lastSeq = newest;
    }
    LOG("seq 对齐到", D.lastSeq);
  } catch (e) {
    WARN("seq 对齐失败", e);
  }
}

/* ------------------------------------------------------------------ 工具 */

function LG() {
  return (
    window.LiteGraph ||
    (window.comfyAPI && window.comfyAPI.LiteGraph && window.comfyAPI.LiteGraph.LiteGraph) ||
    null
  );
}

function GRAPH() {
  return app.rootGraph || app.graph;
}

async function post(path, body) {
  const r = await api.fetchApi(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  let data = null;
  try {
    data = await r.json();
  } catch (e) {
    data = { ok: false, error: "non-json response" };
  }
  return { status: r.status, data };
}

/* -------------------------------------------------------------- 画布操作 */

function resolveRef(ref, nodeId) {
  if (ref && D.refs.has(String(ref))) return D.refs.get(String(ref));
  if (nodeId != null) {
    const g = GRAPH();
    return g.getNodeById ? g.getNodeById(Number(nodeId)) : g._nodes_by_id?.[Number(nodeId)];
  }
  return null;
}

function applyWidgets(node, widgets) {
  const applied = [];
  const failed = [];
  if (!widgets || typeof widgets !== "object") return { applied, failed };
  for (const [name, value] of Object.entries(widgets)) {
    const ws = node.widgets || [];
    const w = ws.find((x) => x.name === name);
    if (!w) {
      failed.push({ widget: name, reason: "no such widget", available: ws.map((x) => x.name) });
      continue;
    }
    try {
      w.value = value;
      if (typeof w.callback === "function") w.callback(value, app.canvas, node);
      if (Array.isArray(node.widgets_values)) {
        const i = ws.indexOf(w);
        if (i >= 0) node.widgets_values[i] = value;
      }
      applied.push(name);
    } catch (e) {
      failed.push({ widget: name, reason: String(e) });
    }
  }
  return { applied, failed };
}

function flashNode(node, color) {
  if (!node) return;
  const oldBg = node.bgcolor;
  const oldBox = node.boxcolor;
  node.bgcolor = color || "#2d5f8a";
  node.boxcolor = "#4a9eff";
  const g = GRAPH();
  g.setDirtyCanvas(true, true);
  let n = 0;
  const timer = setInterval(() => {
    n += 1;
    node.bgcolor = n % 2 ? "#4a9eff" : oldBg;
    g.setDirtyCanvas(true, true);
    if (n >= 4) {
      clearInterval(timer);
      node.bgcolor = oldBg;
      node.boxcolor = oldBox;
      g.setDirtyCanvas(true, true);
    }
  }, 130);
}

function executeOp(op) {
  const kind = op.kind;
  const a = op.args || {};
  const g = GRAPH();
  const L = LG();

  if (!g || !L) return { ok: false, detail: { reason: "graph_not_ready" } };

  try {
    switch (kind) {
      case "create_node": {
        const type = String(a.type || "");
        const node = L.createNode(type);
        if (!node) {
          return {
            ok: false,
            detail: {
              reason: "unknown_node_type",
              type,
              hint: "该节点类型未注册，可能缺少对应自定义节点插件",
            },
          };
        }
        node.pos = Array.isArray(a.pos) ? [a.pos[0], a.pos[1]] : [200 + D.refs.size * 40, 200];
        g.add(node);
        const wres = applyWidgets(node, a.widgets);
        if (a.title) node.title = String(a.title);
        if (a.color) node.color = String(a.color);
        const ref = String(a.ref || type);
        D.refs.set(ref, node);
        flashNode(node, "#2d5f8a");
        g.setDirtyCanvas(true, true);
        return {
          ok: true,
          detail: {
            ref,
            node_id: node.id,
            type,
            title: node.title,
            widgets_applied: wres.applied,
            widgets_failed: wres.failed,
            pos: node.pos,
          },
        };
      }

      case "connect": {
        const from = resolveRef(a.from_ref, a.from_node_id);
        const to = resolveRef(a.to_ref, a.to_node_id);
        if (!from) return { ok: false, detail: { reason: "from_node_missing", ref: a.from_ref } };
        if (!to) return { ok: false, detail: { reason: "to_node_missing", ref: a.to_ref } };
        const fs = Number(a.from_slot ?? 0);
        const ts = Number(a.to_slot ?? 0);
        const link = from.connect(fs, to, ts);
        g.setDirtyCanvas(true, true);
        if (!link) {
          return {
            ok: false,
            detail: {
              reason: "connect_rejected",
              hint: "类型不匹配 / 槽位不存在 / 该输入已被占用",
              from: { node: from.id, title: from.title, slot: fs, type: (from.outputs?.[fs] || {}).type },
              to: { node: to.id, title: to.title, slot: ts, type: (to.inputs?.[ts] || {}).type },
            },
          };
        }
        return {
          ok: true,
          detail: {
            from: { node: from.id, title: from.title, slot: fs },
            to: { node: to.id, title: to.title, slot: ts },
          },
        };
      }

      case "set_widget": {
        const node = resolveRef(a.ref, a.node_id);
        if (!node) return { ok: false, detail: { reason: "node_missing", ref: a.ref } };
        const res = applyWidgets(node, { [a.widget]: a.value });
        g.setDirtyCanvas(true, true);
        if (res.failed.length) return { ok: false, detail: { reason: "widget_failed", ...res } };
        return { ok: true, detail: { node_id: node.id, ...res } };
      }

      case "move_node": {
        const node = resolveRef(a.ref, a.node_id);
        if (!node) return { ok: false, detail: { reason: "node_missing", ref: a.ref } };
        node.pos = [Number(a.pos[0]), Number(a.pos[1])];
        g.setDirtyCanvas(true, true);
        return { ok: true, detail: { node_id: node.id, pos: node.pos } };
      }

      case "delete_node": {
        const node = resolveRef(a.ref, a.node_id);
        if (!node) return { ok: false, detail: { reason: "node_missing", ref: a.ref } };
        const id = node.id;
        g.remove(node);
        if (a.ref) D.refs.delete(String(a.ref));
        g.setDirtyCanvas(true, true);
        return { ok: true, detail: { node_id: id } };
      }

      case "note":
        return { ok: true, detail: { skipped: "note" } };

      case "group":
        return { ok: true, detail: { skipped: "group not implemented yet" } };

      case "execute":
        return {
          ok: false,
          detail: { reason: "not_implemented", hint: "execute 在 M3 开放，当前请手动点 Queue" },
        };

      default:
        return { ok: false, detail: { reason: "unknown_kind", kind } };
    }
  } catch (e) {
    return { ok: false, detail: { reason: "exception", message: String(e && e.stack ? e.stack : e) } };
  }
}

/* ---------------------------------------------------------- 执行操作流水 */

/** 已执行过的 (plan_id, step_index)。哪怕传输层出任何岔子，
 *  同一步也绝不会在画布上执行第二次——这是最后一道硬保护。 */
const executed = new Set();

async function runStep(payload) {
  const { plan_id, step_index, total, op, dry_run } = payload;
  const key = `${plan_id}#${step_index}`;
  if (executed.has(key)) {
    WARN("重复的 canvas.op，已忽略", key);
    return;
  }
  executed.add(key);
  D.busy = true;
  setPill(`第 ${step_index + 1}/${total} 步 · ${op.title || op.kind}`);

  let res;
  if (dry_run) {
    res = { ok: true, detail: { dry_run: true } };
  } else {
    res = executeOp(op);
    await new Promise((r) => setTimeout(r, op.kind === "create_node" ? 260 : 90));
  }

  await post("/duddy/canvas/applied", {
    plan_id,
    step_index,
    ok: res.ok,
    detail: res.detail,
    client_id: D.clientId,
  });

  pushCanvasState();
  D.busy = false;

  if (res.ok) {
    setPill(`✓ ${op.title || op.kind}`, 1400);
  } else {
    setPill(`✗ ${op.title || op.kind} 失败`, 4000, true);
    WARN("step failed", step_index, res.detail);
  }
  updateActiveProgress(step_index + 1, total);
}

/* ------------------------------------------------------------------ 弹窗 */

let modalEl = null;

function ensureModal() {
  if (modalEl) return modalEl;
  const el = document.createElement("div");
  el.id = "duddy-modal";
  el.style.cssText = [
    "position:fixed",
    "right:18px",
    "bottom:18px",
    "width:390px",
    "max-height:72vh",
    "display:none",
    "flex-direction:column",
    "background:#1e1e24",
    "color:#e8e8ea",
    "border:1px solid #3a3a44",
    "border-radius:12px",
    "box-shadow:0 12px 40px rgba(0,0,0,.55)",
    "font-family:system-ui,-apple-system,'Segoe UI',sans-serif",
    "font-size:13px",
    "z-index:99999",
    "overflow:hidden",
  ].join(";");
  document.body.appendChild(el);
  modalEl = el;
  return el;
}

function setPill(text, autoHideMs, isError) {
  let pill = document.getElementById("duddy-pill");
  if (!pill) {
    pill = document.createElement("div");
    pill.id = "duddy-pill";
    pill.style.cssText = [
      "position:fixed",
      "left:50%",
      "transform:translateX(-50%)",
      "top:14px",
      "padding:7px 16px",
      "border-radius:999px",
      "background:#1e1e24",
      "color:#e8e8ea",
      "border:1px solid #3a3a44",
      "font-family:system-ui,sans-serif",
      "font-size:12.5px",
      "z-index:100000",
      "pointer-events:none",
      "opacity:0",
      "transition:opacity .18s",
    ].join(";");
    document.body.appendChild(pill);
  }
  pill.textContent = text;
  pill.style.borderColor = isError ? "#a33" : "#3a3a44";
  pill.style.color = isError ? "#ff9b9b" : "#e8e8ea";
  pill.style.opacity = "1";
  if (pill._t) clearTimeout(pill._t);
  if (autoHideMs) {
    pill._t = setTimeout(() => {
      pill.style.opacity = "0";
    }, autoHideMs);
  }
}

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

/** ComfyUI 的 send_sync 会在外面再包一层 {type, data}，
 *  所以我们自己塞的 {v, seq, ts, type, data} 位于 detail.data。 */
function payloadOf(e) {
  const d = (e && e.detail) || {};
  return d.data || d;
}

function closeModal() {
  if (modalEl) modalEl.style.display = "none";
  D.active = null;
}

function showPlan(evt) {
  const plan = evt.plan;
  const token = evt.token;
  if (!plan || !token) return;

  const el = ensureModal();
  const steps = plan.steps || [];
  const creates = steps.filter((s) => s.kind === "create_node").length;
  const connects = steps.filter((s) => s.kind === "connect").length;

  D.active = { plan, token, approved: false };

  const rows = steps
    .map(
      (s, i) => `
      <div style="display:flex;gap:8px;padding:5px 0;border-bottom:1px solid #2a2a32">
        <div style="color:#7a7a86;min-width:18px;text-align:right">${i + 1}</div>
        <div style="flex:1">
          <div style="color:#e8e8ea">${esc(s.title || s.kind)}</div>
          ${s.why ? `<div style="color:#8a8a96;font-size:11.5px;margin-top:1px">${esc(s.why)}</div>` : ""}
        </div>
      </div>`
    )
    .join("");

  el.innerHTML = `
    <div style="padding:12px 14px;border-bottom:1px solid #3a3a44;display:flex;align-items:flex-start;gap:8px">
      <div style="flex:1">
        <div style="font-size:11px;letter-spacing:.06em;color:#4a9eff;text-transform:uppercase">等待你的批准</div>
        <div style="font-size:14.5px;font-weight:600;margin-top:3px">${esc(plan.title)}</div>
        <div style="color:#9a9aa6;margin-top:3px;font-size:12px">${esc(plan.summary)}</div>
      </div>
      <button id="duddy-x" style="background:none;border:none;color:#7a7a86;font-size:18px;cursor:pointer;line-height:1">×</button>
    </div>
    <div id="duddy-modal-body" style="padding:10px 14px;overflow:auto;flex:1">
      <div style="display:flex;gap:14px;margin-bottom:8px;color:#9a9aa6;font-size:12px">
        <span>新建 <b style="color:#4a9eff">${creates}</b> 节点</span>
        <span>连线 <b style="color:#4a9eff">${connects}</b> 条</span>
        <span>共 <b style="color:#4a9eff">${steps.length}</b> 步</span>
      </div>
      <div style="max-height:38vh;overflow:auto;border-top:1px solid #2a2a32">${rows}</div>
      ${plan.notes ? `<div style="margin-top:8px;color:#8a8a96;font-size:11.5px">${esc(plan.notes)}</div>` : ""}
      <div id="duddy-progress" style="display:none;margin-top:10px">
        <div style="height:4px;background:#2a2a32;border-radius:2px;overflow:hidden">
          <div id="duddy-bar" style="height:100%;width:0%;background:#4a9eff;transition:width .25s"></div>
        </div>
        <div id="duddy-progress-text" style="color:#8a8a96;font-size:11.5px;margin-top:5px"></div>
      </div>
    </div>
    <div id="duddy-actions" style="padding:10px 14px;border-top:1px solid #3a3a44;display:flex;flex-direction:column;gap:8px">
      <div id="duddy-approve-hint" style="color:#ff9b9b;font-size:11.5px;line-height:1.4"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end">
        <button id="duddy-reject" style="padding:7px 14px;border-radius:7px;background:#2a2a32;color:#c8c8d0;border:1px solid #3a3a44;cursor:pointer;font-size:12.5px">拒绝</button>
        <button id="duddy-approve" style="padding:7px 16px;border-radius:7px;background:#2f6fed;color:#fff;border:none;cursor:pointer;font-size:12.5px;font-weight:600">同意并执行</button>
      </div>
    </div>
  `;
  el.style.display = "flex";

  el.querySelector("#duddy-x").onclick = closeModal;
  el.querySelector("#duddy-reject").onclick = async () => {
    await post(`/duddy/plan/${plan.plan_id}/reject`, {
      reason: "用户在弹窗中点了拒绝",
      token: (D.active && D.active.token) || token,
      client_id: D.clientId,
    });
    setPill("已拒绝，AI 不会动你的画布", 2200);
    closeModal();
  };
  el.querySelector("#duddy-approve").onclick = async () => {
    const btn = el.querySelector("#duddy-approve");
    const hint = el.querySelector("#duddy-approve-hint");
    btn.disabled = true;
    btn.textContent = "批准中…";
    if (hint) hint.textContent = "";
    const r = await post(`/duddy/plan/${plan.plan_id}/approve`, {
      // 用 D.active.token 而不是闭包里的 token：
      // 同一计划被重推时会换 token，闭包捕获的会过期
      token: (D.active && D.active.token) || token,
      by: "ui",
      client_id: D.clientId,
    });
    if (r.data && r.data.ok) {
      D.active.approved = true;
      btn.textContent = "已批准 ✓";
      btn.style.background = "#2a7a4a";
      el.querySelector("#duddy-actions").style.display = "none";
      el.querySelector("#duddy-progress").style.display = "block";
      setPill("已批准，等待 AI 开始执行…", 2500);
    } else {
      btn.disabled = false;
      btn.textContent = "批准失败，重试";
      btn.style.background = "#a33";
      const why =
        (r.data && (r.data.error || r.data.kind)) || `HTTP ${r.status}`;
      if (hint) hint.textContent = `失败原因：${why}`;
      WARN("approve failed", r);
    }
  };
}

function updateActiveProgress(done, total) {
  if (!modalEl || !D.active) return;
  const bar = modalEl.querySelector("#duddy-bar");
  const txt = modalEl.querySelector("#duddy-progress-text");
  if (!bar || !txt) return;
  bar.style.width = `${Math.round((done / total) * 100)}%`;
  txt.textContent = `${done} / ${total} 步已完成`;
  if (done >= total) {
    setTimeout(() => {
      txt.textContent = "全部完成 ✓";
      setTimeout(closeModal, 1800);
    }, 300);
  }
}

/* -------------------------------------------------------------- 状态上报 */

async function pushCanvasState() {
  const now = Date.now();
  if (now - D.lastCanvasPush < 1500) return;
  D.lastCanvasPush = now;
  try {
    const g = GRAPH();
    if (!g || typeof g.serialize !== "function") return;
    await post("/duddy/canvas/state", { graph: g.serialize(), client_id: D.clientId });
  } catch (e) {
    /* 上报失败不影响主流程 */
  }
}

/* -------------------------------------------------------------- 扩展注册 */

async function sayHello(reason) {
  D.clientId = api.clientId || D.clientId || null;
  const r = await post("/duddy/client/hello", {
    client_id: D.clientId,
    ua: navigator.userAgent,
    page: location.pathname,
  }).catch(() => null);
  if (r && r.data && r.data.ok) {
    LOG(`hello ok (${reason}) clientId=${D.clientId}`);
  } else {
    WARN(`hello failed (${reason})`, r);
  }
  return r;
}

function startPolling(intervalMs) {
  if (D.pollTimer) return;
  D.pollMode = true;
  const iv = intervalMs || 500;
  LOG(`HTTP 事件轮询启动（${iv}ms）`);
  D.pollTimer = setInterval(async () => {
    try {
      const r = await api.fetchApi(`/duddy/events?since=${D.lastSeq}&limit=50`);
      const j = await r.json();
      for (const evt of j.data || []) {
        dispatch(evt.type, evt.data, evt.seq);
      }
    } catch (e) {
      /* 网络抖动无所谓，下一轮再试 */
    }
  }, iv);
}

app.registerExtension({
  name: "duddy.control",
  async setup() {
    D.clientId = api.clientId || null;
    LOG("extension setup, clientId=", D.clientId);

    // ① 先挂传输层（必须用 addCustomEventListener）
    const wsOk = attachWsEvents();

    // ② 业务处理与传输方式解耦，先全部注册好
    on("duddy.plan.proposed", (d) => {
      const pid = (d.plan || {}).plan_id;
      LOG("plan proposed", pid);
      // 同一计划的重复推送（刷新重推 / 轮询重放）：只刷新 token，不重建弹窗
      if (D.active && D.active.plan.plan_id === pid) {
        D.active.token = d.token;
        LOG("同一计划重推，token 已更新");
        return;
      }
      if (D.active && !D.active.approved) {
        D.queue.push(d);
      } else {
        showPlan(d);
      }
    });
    on("duddy.plan.approved", () => setPill("AI 已获授权，开始动手", 1800));
    on("duddy.plan.rejected", () => setPill("计划已拒绝", 1800));
    on("duddy.canvas.op", (d) => {
      LOG("op received", d.step_index, d.op && d.op.kind);
      runStep(d);
    });
    on("duddy.plan.done", () => setPill("计划执行完毕 ✓", 2600));
    on("duddy.plan.failed", () => setPill("计划执行失败", 3200, true));

    // ③ 传输层在 hello + alignSeq 之后才启动（见 ⑥）

    // ComfyUI 重启后 ws 会重连，但 setup() 不会重跑 —— 必须主动重新报到
    api.addEventListener("reconnected", () => {
      LOG("ws reconnected — 重新报到");
      sayHello("reconnected");
    });
    api.addEventListener("reconnecting", () => {
      setPill("与 ComfyUI 的连接断开，重连中…", 0, true);
    });

    setInterval(pushCanvasState, 8000);
    setTimeout(pushCanvasState, 2500);

    // ④ 最后才报到：hello 会让后端把未决计划重推一遍
    await sayHello("setup");

    // ⑤ 报到完把 seq 对齐，避免轮询把刷新前的历史 op 重放出来
    await alignSeq();

    // ⑥ 传输层兜底
    if (wsOk) {
      startPolling(2000); // 慢兜底：ws 丢包 2 秒内也能补上
    } else {
      startPolling(500); // ws 不可用，靠轮询扛
    }
  },

  async nodeCreated(node) {
    /* 预留：可以在节点创建时叠加视觉标记 */
    return node;
  },
});

LOG(`duddy-comfyui v${VERSION} loaded`);
