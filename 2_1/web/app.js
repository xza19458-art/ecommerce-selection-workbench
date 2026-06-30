"use strict";
/* D2 前端骨架：hash 路由 + fetch 封装 + 只读页。
   后端契约：所有 /api/* 返回 {ok, data, message}（见 decisions/2026-06-19-前端架构转Web.md §6）。
   写入数据 / 联网类端点（建追踪任务、触发采集）后续由 D1 补，本骨架先做只读页。 */

const content = document.getElementById("content");
const viewTitle = document.getElementById("view-title");

/* ---------- API 封装 ---------- */
async function api(path) {
  const resp = await fetch(path);
  let payload;
  try {
    payload = await resp.json();
  } catch {
    throw new Error(`后端响应格式异常（HTTP ${resp.status}）`);
  }
  if (!payload.ok) throw new Error(payload.message || `请求未成功（HTTP ${resp.status}）`);
  return payload.data;
}

async function apiSend(path, method, body) {
  const resp = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  let payload;
  try {
    payload = await resp.json();
  } catch {
    throw new Error(`后端响应格式异常（HTTP ${resp.status}）`);
  }
  if (!payload.ok) throw new Error(payload.message || `请求未成功（HTTP ${resp.status}）`);
  return payload.data;
}

/* ---------- 轻量提示 ---------- */
let _noticeTimer;
function notice(msg, kind = "ok") {
  let el = document.getElementById("notice");
  if (!el) {
    el = document.createElement("div");
    el.id = "notice";
    el.setAttribute("role", "status");
    el.setAttribute("aria-live", "polite");
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.className = `notice notice-${kind} show`;
  clearTimeout(_noticeTimer);
  _noticeTimer = setTimeout(() => { el.className = "notice"; }, 4000);
}

/* ---------- 通用 UI ---------- */
const fmt = {
  money: (v) => (v == null || v === "" ? "—" : `$${Number(v).toFixed(2)}`),
  int: (v) => (v == null || v === "" ? "—" : Number(v).toLocaleString()),
  num: (v, d = 1) => (v == null || v === "" ? "—" : Number(v).toFixed(d)),
  text: (v) => (v == null || v === "" ? "—" : String(v)),
};

function loading() {
  content.innerHTML = `<div class="state"><div class="spinner"></div>加载中…</div>`;
}
function errorState(err) {
  content.innerHTML = `<div class="state error">⚠ ${escapeHtml(err.message)}
    <div class="hint">当前页面依赖后端服务；若数据库未启动或暂无数据，可能无法加载。可先启动数据库后点右上角「刷新」。</div></div>`;
}
function emptyState(msg) {
  content.innerHTML = `<div class="state">${escapeHtml(msg)}</div>`;
}
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function scoreBadge(score) {
  const s = Number(score);
  if (Number.isNaN(s)) return `<span class="badge badge-dim">—</span>`;
  const cls = s >= 70 ? "badge-good" : s >= 45 ? "badge-warn" : "badge-bad";
  return `<span class="badge ${cls}">${s.toFixed(0)}</span>`;
}
function displayTitle(row, fallback = "—") {
  return row?.title_zh || row?.title || row?.title_original || fallback;
}
function isDeal(v) {
  return v === true || v === 1 || v === "1" || v === "是" || String(v).toLowerCase() === "true";
}
/* 行/卡片点击导航：用户正在选中文字（拖选复制正文）时不跳转，方便复制用于搜索/分析/分享。 */
function hasTextSelection() {
  const sel = window.getSelection && window.getSelection();
  return !!(sel && String(sel).trim().length);
}
window.navHash = (hash) => { if (hasTextSelection()) return; location.hash = hash; };

const productCompareSelection = new Set();
let productCompareRows = new Map();
const recommendationState = { limit: 20, offset: 0, sortBy: "total_score", sortDir: "desc", blueOnly: false };
const productState = { limit: 50, offset: 0 };
const keywordState = { limit: 50, offset: 0, keyword: "", minProducts: "" };
const keywordProductState = { keyword: "", limit: 25, offset: 0 };
const taskErrorRows = new Map();
const agentState = {
  conversationId: null,
  messages: [],
  pendingAction: null,
  sending: false,
  config: null,
};
const SIDEBAR_NARROW_QUERY = "(max-width: 860px)";

function normalizePage(payload, fallbackLimit = 50) {
  if (Array.isArray(payload)) {
    return { rows: payload, total: payload.length, limit: fallbackLimit, offset: 0 };
  }
  const rows = Array.isArray(payload?.rows) ? payload.rows : [];
  const limit = Number(payload?.limit ?? fallbackLimit) || fallbackLimit;
  const offset = Number(payload?.offset ?? 0) || 0;
  const total = Number(payload?.total ?? rows.length) || 0;
  return { ...payload, rows, total, limit, offset };
}

function pageSummary(page, label) {
  if (!page.total) return `共 0 个${label}`;
  const start = page.offset + 1;
  const end = Math.min(page.offset + page.rows.length, page.total);
  return `共 ${fmt.int(page.total)} 个${label} · 当前 ${fmt.int(start)}-${fmt.int(end)}`;
}

function renderPager(id, page, sizes = [20, 50, 100]) {
  const limit = Math.max(1, Number(page.limit) || sizes[0]);
  const total = Math.max(0, Number(page.total) || 0);
  const offset = Math.max(0, Number(page.offset) || 0);
  const pageNo = total ? Math.floor(offset / limit) + 1 : 1;
  const totalPages = Math.max(1, Math.ceil(total / limit));
  const allSizes = [...new Set([...sizes, limit])].sort((a, b) => a - b);
  return `
    <div class="pager" id="${id}">
      <button class="btn btn-sm" data-page="prev"${offset <= 0 ? " disabled" : ""}>上一页</button>
      <span>第</span>
      <input class="pager-jump" data-page-jump type="number" min="1" max="${totalPages}" value="${pageNo}" aria-label="页码" />
      <span>/ ${totalPages} 页</span>
      <button class="btn btn-sm" data-page="go">跳转</button>
      <button class="btn btn-sm" data-page="next"${offset + limit >= total ? " disabled" : ""}>下一页</button>
      <select class="sel sel-sm" data-page-size>
        ${allSizes.map((size) => `<option value="${size}"${size === limit ? " selected" : ""}>每页 ${size}</option>`).join("")}
      </select>
    </div>`;
}

function bindPager(id, state, page, loadFn) {
  const el = document.getElementById(id);
  if (!el) return;
  state.limit = page.limit;
  state.offset = page.offset;
  const total = Math.max(0, Number(page.total) || 0);
  const prev = el.querySelector('[data-page="prev"]');
  const next = el.querySelector('[data-page="next"]');
  const go = el.querySelector('[data-page="go"]');
  const jump = el.querySelector("[data-page-jump]");
  const pageSize = el.querySelector("[data-page-size]");
  const jumpToPage = () => {
    const totalPages = Math.max(1, Math.ceil(total / state.limit));
    const pageNo = Math.max(1, Math.min(Number(jump?.value) || 1, totalPages));
    state.offset = (pageNo - 1) * state.limit;
    loadFn();
  };
  if (prev) prev.onclick = () => {
    state.offset = Math.max(0, state.offset - state.limit);
    loadFn();
  };
  if (next) next.onclick = () => {
    const maxOffset = Math.max(0, (Math.ceil(total / state.limit) - 1) * state.limit);
    state.offset = Math.min(maxOffset, state.offset + state.limit);
    loadFn();
  };
  if (go) go.onclick = jumpToPage;
  if (jump) jump.onkeydown = (event) => {
    if (event.key === "Enter") jumpToPage();
  };
  if (pageSize) pageSize.onchange = () => {
    state.limit = Number(pageSize.value) || state.limit;
    state.offset = 0;
    loadFn();
  };
}

function isNarrowSidebar() {
  return window.matchMedia(SIDEBAR_NARROW_QUERY).matches;
}

function updateSidebarA11y() {
  const expanded = isNarrowSidebar()
    ? document.body.classList.contains("sidebar-open")
    : !document.body.classList.contains("sidebar-collapsed");
  const value = expanded ? "true" : "false";
  for (const id of ["sidebar-toggle", "sidebar-reopen"]) {
    const el = document.getElementById(id);
    if (el) el.setAttribute("aria-expanded", value);
  }
}

function setSidebarOpen(open) {
  if (isNarrowSidebar()) {
    document.body.classList.toggle("sidebar-open", open);
    document.body.classList.remove("sidebar-collapsed");
  } else {
    document.body.classList.toggle("sidebar-collapsed", !open);
    document.body.classList.remove("sidebar-open");
  }
  updateSidebarA11y();
}

function toggleSidebar() {
  if (isNarrowSidebar()) setSidebarOpen(!document.body.classList.contains("sidebar-open"));
  else setSidebarOpen(document.body.classList.contains("sidebar-collapsed"));
}

function syncSidebarForViewport() {
  if (isNarrowSidebar()) document.body.classList.remove("sidebar-collapsed");
  else document.body.classList.remove("sidebar-open");
  updateSidebarA11y();
}

function closeSidebarIfNarrow() {
  if (isNarrowSidebar()) setSidebarOpen(false);
}

function isTypingTarget(el) {
  if (!el) return false;
  const tag = String(el.tagName || "").toLowerCase();
  return tag === "input" || tag === "textarea" || tag === "select" || el.isContentEditable;
}

function focusFirstField() {
  const el = content.querySelector("input:not([type='checkbox']):not([type='radio']):not([disabled]), textarea:not([disabled]), select:not([disabled])");
  if (el) {
    el.focus();
    if (typeof el.select === "function" && el.tagName.toLowerCase() === "input") el.select();
  }
}

function handleGlobalShortcuts(event) {
  const key = event.key;
  const typing = isTypingTarget(document.activeElement);
  if (key === "Escape") {
    if (closeTaskLogDialog()) {
      event.preventDefault();
      return;
    }
    if (document.body.classList.contains("sidebar-open")) {
      event.preventDefault();
      setSidebarOpen(false);
      return;
    }
    if (typing && document.activeElement) document.activeElement.blur();
    return;
  }
  if (typing) return;
  if (key === "r" || key === "R") {
    event.preventDefault();
    router();
    notice("已刷新当前页", "ok");
    return;
  }
  if (key === "/") {
    event.preventDefault();
    focusFirstField();
    return;
  }
  if (key === "[" || (event.altKey && key === "ArrowLeft")) {
    event.preventDefault();
    history.back();
  }
}

/* ---------- 视图：推荐 ---------- */
async function viewRecommendations() {
  content.innerHTML = `
    <div class="table-toolbar">
      <div class="filters compact">
        <select id="rec-sort" class="sel">
          <option value="total_score">综合得分</option>
          <option value="growth_score">增长分（占位）</option>
          <option value="price">价格</option>
          <option value="rating">评分</option>
          <option value="review_count">评论数</option>
          <option value="monthly_bought">近月购买</option>
          <option value="organic_rank">自然排名</option>
        </select>
        <select id="rec-dir" class="sel">
          <option value="desc">降序</option>
          <option value="asc">升序</option>
        </select>
        <label class="check-inline">
          <input id="rec-blue" type="checkbox" />
          只看蓝海（≥70）
        </label>
      </div>
      <button class="btn btn-sm" id="rec-csv">导出当前页 CSV</button>
    </div>
    <div id="rec-meta" class="result-meta"></div>
    <div id="rec-cards"></div>
    <div id="rec-pager-wrap"></div>`;
  document.getElementById("rec-sort").value = recommendationState.sortBy;
  document.getElementById("rec-dir").value = recommendationState.sortDir;
  document.getElementById("rec-blue").checked = recommendationState.blueOnly;
  document.getElementById("rec-sort").onchange = () => {
    recommendationState.sortBy = document.getElementById("rec-sort").value;
    recommendationState.offset = 0;
    loadRecommendations();
  };
  document.getElementById("rec-dir").onchange = () => {
    recommendationState.sortDir = document.getElementById("rec-dir").value;
    recommendationState.offset = 0;
    loadRecommendations();
  };
  document.getElementById("rec-blue").onchange = () => {
    recommendationState.blueOnly = document.getElementById("rec-blue").checked;
    recommendationState.offset = 0;
    loadRecommendations();
  };
  await loadRecommendations();
}

async function loadRecommendations() {
  const box = document.getElementById("rec-cards");
  const meta = document.getElementById("rec-meta");
  const pager = document.getElementById("rec-pager-wrap");
  const csv = document.getElementById("rec-csv");
  box.innerHTML = `<div class="state"><div class="spinner"></div>加载中…</div>`;
  meta.textContent = "";
  pager.innerHTML = "";
  if (csv) {
    csv.disabled = true;
    csv.onclick = null;
  }
  const q = new URLSearchParams({
    limit: String(recommendationState.limit),
    offset: String(recommendationState.offset),
    sort_by: recommendationState.sortBy,
    sort_dir: recommendationState.sortDir,
  });
  if (recommendationState.blueOnly) q.set("min_score", "70");
  try {
    const page = normalizePage(await api(`/api/recommendations?${q.toString()}`), recommendationState.limit);
    const rows = page.rows;
    meta.textContent = pageSummary(page, "商品") + (recommendationState.blueOnly ? " · 已筛选蓝海 ≥70" : "") + " · 可按评分/价格/评论/增长等排序";
    if (!rows.length) {
      box.innerHTML = `<div class="state">暂无推荐数据。</div>`;
      pager.innerHTML = renderPager("rec-pager", page, [20, 50, 100]);
      bindPager("rec-pager", recommendationState, page, loadRecommendations);
      return;
    }
    box.innerHTML = `<div class="cards">${rows.map((r) => `
      <div class="card" onclick="navHash('#/product/${encodeURIComponent(r.asin)}')">
        <h3>${escapeHtml(displayTitle(r, r.asin))}</h3>
        <div class="row"><span>综合得分</span> <b>${scoreBadge(r.total_score)}</b></div>
        <div class="row" title="占位值，趋势第二步接入真实增长分前未计入综合得分"><span>增长分（占位）</span> <b>${fmt.num(r.growth_score, 0)}</b></div>
        <div class="row"><span>价格</span> <b>${fmt.money(r.price)}</b></div>
        <div class="row"><span>评分 / 评论</span> <b>${fmt.num(r.rating)} · ${fmt.int(r.review_count)}</b></div>
        <div class="row"><span>近月购买</span> <b>${fmt.int(r.monthly_bought)}</b></div>
      </div>`).join("")}</div>`;
    pager.innerHTML = renderPager("rec-pager", page, [20, 50, 100]);
    bindPager("rec-pager", recommendationState, page, loadRecommendations);
    if (csv) csv.disabled = false;
    document.getElementById("rec-csv").onclick = () => {
      const headers = ["ASIN", "标题", "综合得分", "增长分(占位)", "价格", "评分", "评论数", "近月购买"];
      const data = rows.map((r) => [r.asin, displayTitle(r, r.asin), r.total_score, r.growth_score, r.price, r.rating, r.review_count, r.monthly_bought]);
    exportCsv("推荐蓝海.csv", headers, data);
  };
  } catch (err) {
    box.innerHTML = `<div class="state error">⚠ ${escapeHtml(err.message)}</div>`;
  }
}

/* ---------- 视图：商品池 ---------- */
async function viewProducts() {
  content.innerHTML = `
    <div class="filters">
      <input id="f-keyword" placeholder="关键词" />
      <input id="f-min-score" type="number" placeholder="最低得分" />
      <input id="f-min-price" type="number" placeholder="最低价" />
      <input id="f-max-price" type="number" placeholder="最高价" />
      <input id="f-max-reviews" type="number" placeholder="最多评论" />
      <button class="btn" id="f-apply">筛选</button>
      <button class="btn" id="f-reset">重置</button>
    </div>
    <div class="table-toolbar">
      <div id="prod-meta" class="result-meta"></div>
      <div class="actions">
        <span id="prod-selected" class="selected-count">已选 0 个</span>
        <button class="btn btn-sm" id="prod-compare" disabled>对比选中</button>
        <button class="btn btn-sm" id="prod-clear">清空选择</button>
      </div>
    </div>
    <div id="prod-table"></div>
    <div id="prod-pager-wrap"></div>`;
  document.getElementById("f-apply").onclick = () => loadProducts(true);
  document.getElementById("f-reset").onclick = () => {
    ["f-keyword", "f-min-score", "f-min-price", "f-max-price", "f-max-reviews"]
      .forEach((id) => { document.getElementById(id).value = ""; });
    loadProducts(true);
  };
  document.getElementById("prod-compare").onclick = compareSelectedProducts;
  document.getElementById("prod-clear").onclick = () => {
    productCompareSelection.clear();
    updateProductCompareBar();
    document.querySelectorAll(".prod-check").forEach((c) => { c.checked = false; });
  };
  content.querySelectorAll(".filters input").forEach((inp) => {
    inp.addEventListener("keydown", (e) => { if (e.key === "Enter") loadProducts(true); });
  });
  await loadProducts();
}
async function loadProducts(resetPage = false) {
  if (resetPage) productState.offset = 0;
  const box = document.getElementById("prod-table");
  const meta = document.getElementById("prod-meta");
  const pager = document.getElementById("prod-pager-wrap");
  box.innerHTML = `<div class="state"><div class="spinner"></div>加载中…</div>`;
  meta.textContent = "";
  pager.innerHTML = "";
  const q = new URLSearchParams({
    limit: String(productState.limit),
    offset: String(productState.offset),
  });
  const g = (id) => document.getElementById(id).value.trim();
  if (g("f-keyword")) q.set("keyword", g("f-keyword"));
  if (g("f-min-score")) q.set("min_score", g("f-min-score"));
  if (g("f-min-price")) q.set("min_price", g("f-min-price"));
  if (g("f-max-price")) q.set("max_price", g("f-max-price"));
  if (g("f-max-reviews")) q.set("max_reviews", g("f-max-reviews"));
  try {
    const page = normalizePage(await api(`/api/products?${q.toString()}`), productState.limit);
    const rows = page.rows;
    meta.textContent = pageSummary(page, "商品") + " · 点列头排序 · 勾选 2-5 个商品后对比";
    if (!rows.length) {
      productCompareRows = new Map();
      updateProductCompareBar();
      box.innerHTML = `<div class="state">暂无匹配商品。</div>`;
      pager.innerHTML = renderPager("prod-pager", page, [20, 50, 100]);
      bindPager("prod-pager", productState, page, loadProducts);
      return;
    }
    productCompareRows = new Map(rows.map((r) => [String(r.asin || ""), r]));
    renderSortableTable(box, [
      { key: "_select", label: "", sortable: false, csv: false, align: "check", render: (r) => productCompareCheckbox(r) },
      { key: "title", label: "标题", render: (r) => escapeHtml(truncate(displayTitle(r, r.asin), 60)), sortVal: (r) => displayTitle(r, r.asin) },
      { key: "total_score", label: "得分", align: "num", numeric: true, render: (r) => scoreBadge(r.total_score), sortVal: (r) => r.total_score },
      { key: "price", label: "价格", align: "num", numeric: true, render: (r) => fmt.money(r.price), sortVal: (r) => r.price },
      { key: "rating", label: "评分", align: "num", numeric: true, render: (r) => fmt.num(r.rating), sortVal: (r) => r.rating },
      { key: "review_count", label: "评论", align: "num", numeric: true, render: (r) => fmt.int(r.review_count), sortVal: (r) => r.review_count },
      { key: "monthly_bought", label: "近月购买", align: "num", numeric: true, render: (r) => fmt.int(r.monthly_bought), sortVal: (r) => r.monthly_bought },
      { key: "organic_rank", label: "排名", align: "num", numeric: true, render: (r) => fmt.int(r.organic_rank), sortVal: (r) => r.organic_rank },
    ], rows, { rowHash: (r) => `#/product/${encodeURIComponent(r.asin)}`, defaultSort: { key: "total_score", dir: -1 }, exportName: "商品池" });
    pager.innerHTML = renderPager("prod-pager", page, [20, 50, 100]);
    bindPager("prod-pager", productState, page, loadProducts);
    updateProductCompareBar();
  } catch (err) {
    box.innerHTML = `<div class="state error">⚠ ${escapeHtml(err.message)}</div>`;
  }
}

function productCompareCheckbox(row) {
  const asin = String(row.asin || "");
  const checked = productCompareSelection.has(asin) ? " checked" : "";
  return `<input type="checkbox" class="prod-check" value="${escapeHtml(asin)}"${checked} onclick="event.stopPropagation()" onchange="productCompareToggle(this)" />`;
}

window.productCompareToggle = (input) => {
  const asin = input.value;
  if (!asin) return;
  if (input.checked) {
    if (productCompareSelection.size >= 5 && !productCompareSelection.has(asin)) {
      input.checked = false;
      notice("一次最多对比 5 个商品", "bad");
      return;
    }
    productCompareSelection.add(asin);
  } else {
    productCompareSelection.delete(asin);
  }
  updateProductCompareBar();
};

function updateProductCompareBar() {
  const count = productCompareSelection.size;
  const selected = document.getElementById("prod-selected");
  const compare = document.getElementById("prod-compare");
  if (selected) selected.textContent = `已选 ${count} 个`;
  if (compare) compare.disabled = count < 2;
}

function compareSelectedProducts() {
  const asins = [...productCompareSelection].slice(0, 5);
  if (asins.length < 2) {
    notice("请至少勾选 2 个商品进行对比", "bad");
    return;
  }
  location.hash = `#/compare/${encodeURIComponent(asins.join(","))}`;
}

/* ---------- 视图：AI 助手（Agent M2） ---------- */
function viewAgent() {
  content.innerHTML = `
    <div class="agent-shell">
      <section class="agent-main">
        <div class="agent-head">
          <div>
            <h2>AI 助手</h2>
            <div class="agent-sub">只读查询自动执行 · 写入数据 / 联网操作需二次确认</div>
          </div>
          <button class="btn btn-sm" id="agent-new">新会话</button>
        </div>
        <div id="agent-messages" class="agent-messages"></div>
        <form id="agent-form" class="agent-form">
          <textarea id="agent-input" rows="3" placeholder="输入你的选品问题（Enter 发送 · Shift+Enter 换行）"></textarea>
          <button id="agent-send" class="btn" type="submit">发送</button>
        </form>
      </section>
      <aside class="agent-side">
        <div class="agent-side-title">常用问题</div>
        <button class="chip agent-prompt" data-prompt="帮我找综合得分高、评论数相对低的商品">高分低竞争商品</button>
        <button class="chip agent-prompt" data-prompt="看看当前关键词机会里哪些更适合差异化进入">关键词机会判断</button>
        <button class="chip agent-prompt" data-prompt="总结最近任务中心和关键词追踪任务状态">任务状态摘要</button>
        <div class="agent-side-title">模型配置</div>
        <div id="agent-config" class="agent-config">
          <div class="state"><div class="spinner"></div>读取配置…</div>
        </div>
        <div class="agent-side-title">边界</div>
        <div class="agent-boundary">创建追踪、修改状态、触发采集会先暂停并等待确认；取消不会写入数据或联网。</div>
      </aside>
    </div>`;

  document.getElementById("agent-new").onclick = () => {
    agentState.conversationId = null;
    agentState.messages = [];
    agentState.pendingAction = null;
    renderAgentMessages();
    document.getElementById("agent-input").focus();
  };
  document.getElementById("agent-form").onsubmit = (event) => {
    event.preventDefault();
    sendAgentMessage();
  };
  document.getElementById("agent-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendAgentMessage();
    }
  });
  document.querySelectorAll(".agent-prompt").forEach((btn) => {
    btn.onclick = () => {
      document.getElementById("agent-input").value = btn.dataset.prompt || "";
      sendAgentMessage();
    };
  });
  loadAgentConfig();
  renderAgentMessages();
  // P2-1 轻量打磨：进入页面即聚焦输入框，省一次点击。
  const input = document.getElementById("agent-input");
  if (input) input.focus();
}

function renderAgentMessages() {
  const box = document.getElementById("agent-messages");
  if (!box) return;
  if (!agentState.messages.length) {
    box.innerHTML = `
      <div class="agent-empty">
        <b>从一个具体问题开始</b>
        <span>例如商品筛选、ASIN 趋势、关键词机会、评论痛点或任务状态。</span>
      </div>`;
    return;
  }
  box.innerHTML = agentState.messages.map((message) => {
    const toolBlock = renderAgentToolCalls(message.toolCalls || []);
    const actionBlock = message.pendingAction ? renderAgentPendingAction(message.pendingAction) : "";
    const cls = `agent-msg agent-msg-${message.role}`;
    const label = message.role === "user" ? "你" : message.role === "error" ? "错误" : "助手";
    return `
      <div class="${cls}">
        <div class="agent-msg-label">${label}</div>
        <div class="agent-bubble">${message.pending ? `<span class="mini-spinner"></span>` : ""}${escapeHtml(message.content)}</div>
        ${toolBlock}
        ${actionBlock}
      </div>`;
  }).join("");
  box.scrollTop = box.scrollHeight;
}

function renderAgentToolCalls(toolCalls) {
  if (!toolCalls.length) return "";
  return `
    <div class="agent-tools">
      ${toolCalls.map((call) => `
        <div class="agent-tool ${call.ok === false ? "agent-tool-bad" : ""}">
          <span>${escapeHtml(agentToolLabel(call.name))}</span>
          <code>${escapeHtml(formatAgentToolInput(call.input))}</code>
          ${call.message ? `<em>${escapeHtml(call.message)}</em>` : ""}
        </div>`).join("")}
    </div>`;
}

function agentToolLabel(name) {
  const labels = {
    query_recommendations: "推荐榜",
    query_products: "商品池",
    query_product_detail: "商品详情",
    query_product_trend: "趋势",
    query_keyword_opportunities: "关键词机会",
    query_review_insights: "评论洞察",
    query_tracking_tasks: "追踪任务",
    query_tasks: "任务中心",
    create_keyword_tracking: "创建追踪",
    set_keyword_tracking_status: "修改追踪状态",
    trigger_collection: "触发采集",
  };
  return labels[name] || name || "工具";
}

function formatAgentToolInput(input) {
  const entries = Object.entries(input || {}).filter(([, value]) => value != null && value !== "");
  if (!entries.length) return "默认参数";
  return entries.map(([key, value]) => `${key}=${value}`).join(" · ");
}

async function sendAgentMessage() {
  if (agentState.sending) return;
  if (agentState.pendingAction) {
    notice("请先处理当前待确认操作", "bad");
    return;
  }
  const input = document.getElementById("agent-input");
  const send = document.getElementById("agent-send");
  const text = (input.value || "").trim();
  if (!text) {
    input.focus();
    return;
  }
  input.value = "";
  agentState.messages.push({ role: "user", content: text });
  const pending = { role: "assistant", content: "分析中…", pending: true, toolCalls: [] };
  agentState.messages.push(pending);
  agentState.sending = true;
  if (send) send.disabled = true;
  renderAgentMessages();
  try {
    const data = await apiSend("/api/agent/chat", "POST", {
      conversation_id: agentState.conversationId,
      message: text,
      confirm: null,
    });
    applyAgentResponse(data, pending);
  } catch (err) {
    pending.role = "error";
    pending.content = err.message || "请求未成功。";
    pending.pending = false;
    pending.toolCalls = [];
  } finally {
    agentState.sending = false;
    if (send) send.disabled = false;
    renderAgentMessages();
    input.focus();
  }
}

function applyAgentResponse(data, message) {
  agentState.conversationId = data.conversation_id || agentState.conversationId;
  agentState.pendingAction = data.pending_action || null;
  message.content = data.reply || "暂无返回内容。";
  message.pending = false;
  message.toolCalls = data.tool_calls || [];
  message.pendingAction = data.pending_action || null;
}

function renderAgentPendingAction(action) {
  return `
    <div class="agent-action">
      <div>
        <b>${escapeHtml(agentToolLabel(action.tool))}</b>
        <code>${escapeHtml(formatAgentToolInput(action.input || {}))}</code>
      </div>
      <div class="agent-action-buttons">
        <button class="btn btn-sm btn-warn" onclick="confirmAgentAction(true)">确认执行</button>
        <button class="btn btn-sm" onclick="confirmAgentAction(false)">取消</button>
      </div>
    </div>`;
}

window.confirmAgentAction = async (approved) => {
  if (agentState.sending) return;
  const action = agentState.pendingAction;
  if (!action) return;
  agentState.pendingAction = null;
  agentState.messages.forEach((message) => {
    if (message.pendingAction?.tool_call_id === action.tool_call_id) {
      message.pendingAction = null;
    }
  });
  const label = approved ? "确认执行" : "取消执行";
  agentState.messages.push({ role: "user", content: `${label}：${agentToolLabel(action.tool)}` });
  const pending = { role: "assistant", content: approved ? "执行并整理结果中…" : "取消并整理回复中…", pending: true, toolCalls: [] };
  agentState.messages.push(pending);
  agentState.sending = true;
  renderAgentMessages();
  try {
    const data = await apiSend("/api/agent/chat", "POST", {
      conversation_id: agentState.conversationId,
      message: null,
      confirm: {
        tool_call_id: action.tool_call_id,
        approved: !!approved,
      },
    });
    applyAgentResponse(data, pending);
  } catch (err) {
    pending.role = "error";
    pending.content = err.message || "确认操作未成功。";
    pending.pending = false;
    pending.toolCalls = [];
    pending.pendingAction = action;
    agentState.pendingAction = action;
  } finally {
    agentState.sending = false;
    renderAgentMessages();
  }
};

async function loadAgentConfig() {
  const box = document.getElementById("agent-config");
  if (!box) return;
  box.innerHTML = `<div class="state"><div class="spinner"></div>读取配置…</div>`;
  try {
    const config = await api("/api/agent/config");
    agentState.config = config;
    renderAgentConfig(config);
  } catch (err) {
    box.innerHTML = `<div class="agent-config-status agent-config-bad">${escapeHtml(err.message)}</div>`;
  }
}

function renderAgentConfig(config) {
  const box = document.getElementById("agent-config");
  if (!box) return;
  const providers = config.providers || [];
  const providerOptions = providers.map((item) =>
    `<option value="${escapeHtml(item.value)}"${item.value === config.provider ? " selected" : ""}>${escapeHtml(item.label)}</option>`
  ).join("");
  const keyStatus = config.api_key_configured
    ? `已保存：${config.api_key_preview || "已隐藏"}`
    : "未保存 API Key";
  const statusClass = config.valid ? "agent-config-ok" : "agent-config-bad";
  const statusText = config.valid
    ? `${config.provider_label || config.provider} · ${config.model || "未填模型"} · ${keyStatus}`
    : (config.error || "配置未完成");
  box.innerHTML = `
    <form id="agent-config-form" class="agent-config-form">
      <label>模型接口
        <select id="agent-provider" class="sel">${providerOptions}</select>
      </label>
      <label>接口地址（Base URL）
        <input id="agent-base-url" value="${escapeHtml(config.base_url || "")}" />
      </label>
      <label>模型
        <input id="agent-model" value="${escapeHtml(config.model || "")}" />
      </label>
      <label>API Key
        <input id="agent-api-key" type="password" placeholder="${escapeHtml(keyStatus)}" autocomplete="off" />
      </label>
      <label class="agent-check">
        <input id="agent-tools-enabled" type="checkbox"${config.supports_tool_calls ? " checked" : ""} />
        允许调用工具
      </label>
      <div class="agent-config-grid">
        <label>温度
          <input id="agent-temperature" type="number" min="0" max="2" step="0.1" value="${escapeHtml(config.temperature ?? 0.2)}" />
        </label>
        <label>最大输出
          <input id="agent-max-tokens" type="number" min="128" max="8192" step="128" value="${escapeHtml(config.max_tokens ?? 2400)}" />
        </label>
        <label>超时
          <input id="agent-timeout" type="number" min="5" max="300" step="5" value="${escapeHtml(config.timeout_seconds ?? 60)}" />
        </label>
      </div>
      <div class="actions">
        <button class="btn btn-sm" id="agent-config-save" type="submit">保存</button>
        <button class="btn btn-sm" id="agent-config-test" type="button">测试连接</button>
      </div>
      <div id="agent-config-status" class="agent-config-status ${statusClass}">${escapeHtml(statusText)}</div>
    </form>`;
  bindAgentConfigForm(config);
}

function bindAgentConfigForm(config) {
  const form = document.getElementById("agent-config-form");
  const provider = document.getElementById("agent-provider");
  const baseUrl = document.getElementById("agent-base-url");
  const model = document.getElementById("agent-model");
  if (!form || !provider || !baseUrl || !model) return;
  provider.onchange = () => {
    const item = (config.providers || []).find((p) => p.value === provider.value);
    if (!item) return;
    baseUrl.value = item.default_base_url || baseUrl.value;
    model.value = item.default_model || model.value;
  };
  form.onsubmit = (event) => {
    event.preventDefault();
    saveAgentConfig(false);
  };
  document.getElementById("agent-config-test").onclick = () => saveAgentConfig(true);
}

function collectAgentConfigForm() {
  const value = (id) => document.getElementById(id)?.value?.trim() || "";
  return {
    provider: value("agent-provider") || "openai_compatible",
    base_url: value("agent-base-url"),
    api_key: value("agent-api-key") || null,
    model: value("agent-model"),
    supports_tool_calls: !!document.getElementById("agent-tools-enabled")?.checked,
    temperature: Number(value("agent-temperature") || 0.2),
    max_tokens: Number(value("agent-max-tokens") || 2400),
    timeout_seconds: Number(value("agent-timeout") || 60),
  };
}

async function saveAgentConfig(testOnly) {
  const status = document.getElementById("agent-config-status");
  const save = document.getElementById("agent-config-save");
  const test = document.getElementById("agent-config-test");
  if (save) save.disabled = true;
  if (test) test.disabled = true;
  if (status) {
    status.className = "agent-config-status";
    status.textContent = testOnly ? "测试连接中…" : "保存中…";
  }
  try {
    const body = collectAgentConfigForm();
    const data = testOnly
      ? await apiSend("/api/agent/config/test", "POST", body)
      : await apiSend("/api/agent/config", "PUT", body);
    if (testOnly) {
      if (status) {
        status.className = "agent-config-status agent-config-ok";
        status.textContent = `测试通过：${data.reply || data.model || "已连通"}`;
      }
      notice("模型连接测试通过", "ok");
    } else {
      agentState.config = data;
      renderAgentConfig(data);
      notice("AI 助手配置已保存", "ok");
    }
  } catch (err) {
    if (status) {
      status.className = "agent-config-status agent-config-bad";
      status.textContent = err.message || "操作未成功";
    }
    notice(err.message || "AI 助手配置操作未成功", "bad");
  } finally {
    if (save) save.disabled = false;
    if (test) test.disabled = false;
  }
}

/* ---------- 视图：商品详情 + 趋势曲线 ---------- */
async function viewProductDetail(asin) {
  loading();
  const detail = await api(`/api/products/${encodeURIComponent(asin)}`);
  const p = detail.product || detail || {};
  const snaps = (detail.snapshots || []).slice().sort((a, b) =>
    String(a.snapshot_at).localeCompare(String(b.snapshot_at)));
  content.innerHTML = `
    <div class="detail-head">
      <a class="link back-link" href="#/products">← 返回商品池</a>
      <h2 style="margin:0;font-size:16px">${escapeHtml(displayTitle(p, asin))}</h2>
      ${p.image_url ? `<figure class="product-hero">
        <img class="product-hero__img" loading="lazy" alt="商品主图" title="点击放大看细节"
             src="/api/products/${encodeURIComponent(asin)}/image"
             onclick="openImageZoom('${encodeURIComponent(asin)}')"
             onerror="this.closest('.product-hero').classList.add('product-hero--failed')" />
        <figcaption class="product-hero__cap">商品图 · 点击放大看细节</figcaption>
      </figure>` : ""}
      <div class="meta">ASIN: ${escapeHtml(asin)} · 综合得分 ${scoreBadge(p.total_score)}
        · 首次采集 ${fmt.text(p.first_seen_at)} · 最近采集 ${fmt.text(p.last_seen_at)}</div>
    </div>
    <div class="panel">
      <h2>选品建议</h2>
      <div id="advice"><div class="state"><div class="spinner"></div>评估中…</div></div>
    </div>
    <div class="panel">
      <h2>评论痛点</h2>
      ${renderReviewInsight(detail.review_insight)}
    </div>
    <div class="panel">
      <h2>趋势置信度</h2>
      <div id="trend-conf"><div class="state"><div class="spinner"></div>评估中…</div></div>
    </div>
    <div class="panel">
      <h2>趋势曲线（Keepa 式）</h2>
      ${snaps.length >= 2
        ? `<div id="trend-chart" class="chart"></div>`
        : `<div class="state">仅有 ${snaps.length} 个快照，样本不足，暂无法绘制趋势（需 ≥2 个采集时间点）。</div>`}
    </div>
    <div class="panel">
      <h2>历史快照（${snaps.length}）</h2>
      ${snaps.length ? snapTable(snaps) : `<div class="state">暂无快照。</div>`}
    </div>`;
  if (snaps.length >= 2) renderTrendChart(snaps);
  fillTrendConfidence(asin);
  fillProductAdvice(asin);
}

/* 选品建议面板：透出 controller.get_product_advice（与 GUI 共享逻辑）。 */
async function fillProductAdvice(asin) {
  const box = document.getElementById("advice");
  if (!box) return;
  try {
    const a = await api(`/api/products/${encodeURIComponent(asin)}/advice`);
    box.innerHTML = [
      `<div class="advice-row"><span>推荐结论</span><p>${escapeHtml(a.conclusion)}</p></div>`,
      `<div class="advice-row"><span>风险提示</span><p>${escapeHtml(a.risk)}</p></div>`,
      `<div class="advice-row"><span>进入策略</span><p>${escapeHtml(a.entry_strategy)}</p></div>`,
    ].join("");
  } catch (err) {
    box.innerHTML = `<div class="state error">选品建议暂不可用：${escapeHtml(err.message)}</div>`;
  }
}

/* 评论痛点：渲染 get_product_history 已带的 review_insight（洞察 + 低分样本）。 */
function renderReviewInsight(rd) {
  if (!rd || rd.status === "empty") {
    return `<div class="state">${escapeHtml((rd && rd.message) || "暂未采集评论内容，无法形成评论痛点分析。")}</div>`;
  }
  const ins = rd.insight || {};
  const samples = rd.low_rating_reviews || [];
  const parts = [];
  if (ins && Object.keys(ins).length) {
    parts.push(`<div class="row"><span>评论样本 / 低分 / 均分</span> <b>${fmt.int(ins.review_count)} · ${fmt.int(ins.negative_count)} · ${fmt.num(ins.avg_rating)}</b></div>`);
    const pain = formatReviewPoints(ins.pain_points);
    if (pain) parts.push(`<div class="advice-row"><span>痛点主题</span><p>${escapeHtml(pain)}</p></div>`);
    if (ins.risk_summary) parts.push(`<div class="advice-row"><span>评论风险</span><p>${escapeHtml(ins.risk_summary)}</p></div>`);
    if (ins.opportunity_summary) parts.push(`<div class="advice-row"><span>改良机会</span><p>${escapeHtml(ins.opportunity_summary)}</p></div>`);
  } else {
    parts.push(`<div class="state">已采集少量评论，但尚未生成痛点摘要。</div>`);
  }
  if (samples.length) {
    const rows = samples.slice(0, 5).map((s) =>
      `<div class="sample"><b>${fmt.num(s.rating)}★</b> ${escapeHtml(truncate(s.title || s.body || "—", 90))}</div>`).join("");
    parts.push(`<h3 style="margin:14px 0 6px;font-size:13px">低分样本（前 ${Math.min(samples.length, 5)} 条）</h3>${rows}`);
  }
  return parts.join("");
}

function formatReviewPoints(points) {
  if (!points) return "";
  const arr = Array.isArray(points) ? points : Object.values(points);
  return arr.map((item) => {
    if (typeof item === "string") return item;
    const label = item.theme || item.name || item.label || "未命名主题";
    const count = item.count != null ? `(${item.count})` : "";
    return `${label}${count}`;
  }).filter(Boolean).join("、");
}

/* 趋势置信度面板：复用服务端 assess_product_trend（单一真源），诚实展示，
   growth_score 明确标注"未计入综合得分"，与 Tkinter 端 A1 口径一致。 */
async function fillTrendConfidence(asin) {
  const box = document.getElementById("trend-conf");
  if (!box) return;
  try {
    const a = await api(`/api/products/${encodeURIComponent(asin)}/trend`);
    const parts = [
      `<div>${confBadge(a.confidence)} <span style="color:var(--text-dim)">样本 ${a.sample_size} 个快照 · 跨度约 ${Number(a.span_days).toFixed(0)} 天</span></div>`,
      `<div style="margin-top:10px;line-height:1.65">${escapeHtml(a.summary)}</div>`,
    ];
    if (a.confidence !== "无法判断") {
      parts.push(`<div style="margin-top:10px;color:var(--text-dim)">趋势分（占位·未计入综合得分，趋势第二步接入真实值）：<b style="color:var(--text)">${Number(a.growth_score).toFixed(0)}</b></div>`);
    }
    box.innerHTML = parts.join("");
  } catch (err) {
    box.innerHTML = `<div class="state error">趋势置信度暂不可用：${escapeHtml(err.message)}</div>`;
  }
}
function confBadge(c) {
  const cls = c === "高" ? "badge-good" : c === "中" ? "badge-warn" : "badge-dim";
  return `<span class="badge ${cls}">置信度 ${escapeHtml(c)}</span>`;
}

/* 左轴可切换的指标（自然排名固定在右轴，避免不同量纲挤在一起）。 */
const TREND_LEFT_METRICS = [
  { key: "price", label: "价格", color: "#4c8dff" },
  { key: "rating", label: "评分", color: "#3fb950" },
  { key: "review_count", label: "评论数", color: "#bc8cff" },
  { key: "monthly_bought", label: "近月购买", color: "#56d4dd" },
];

function renderTrendChart(snaps) {
  const chartEl = document.getElementById("trend-chart");
  if (!chartEl) return;
  let bar = document.getElementById("trend-metric-bar");
  if (!bar) {
    bar = document.createElement("div");
    bar.id = "trend-metric-bar";
    bar.className = "metric-bar";
    chartEl.parentNode.insertBefore(bar, chartEl);
  }
  const chart = echarts.init(chartEl, "dark");
  const x = snaps.map((s) => String(s.snapshot_at));
  let leftKey = "price";

  const fmtLeft = (key, v) => {
    if (v == null) return "—";
    if (key === "price") return fmt.money(v);
    if (key === "rating") return fmt.num(v, 1);
    return fmt.int(v);
  };

  function draw() {
    const left = TREND_LEFT_METRICS.find((m) => m.key === leftKey);
    bar.innerHTML = TREND_LEFT_METRICS.map((m) =>
      `<button class="chip${m.key === leftKey ? " active" : ""}" data-key="${m.key}">${m.label}</button>`
    ).join("");
    bar.querySelectorAll(".chip").forEach((b) => {
      b.onclick = () => { leftKey = b.dataset.key; draw(); };
    });
    chart.setOption({
      backgroundColor: "transparent",
      tooltip: {
        trigger: "axis",
        // 带单位的中文 tooltip：价格 $、评分 1 位小数、排名整数、其余整数。
        formatter: (params) => {
          if (!params || !params.length) return "";
          const lines = params.map((p) => {
            const isRank = String(p.seriesName).indexOf("自然排名") === 0;
            const val = isRank ? fmt.int(p.value) : fmtLeft(left.key, p.value);
            return `${p.marker}${escapeHtml(String(p.seriesName))}: ${val}`;
          });
          return [escapeHtml(String(params[0].axisValue))].concat(lines).join("<br/>");
        },
      },
      legend: { textStyle: { color: "#9aa7b4" } },
      grid: { left: 56, right: 56, top: 36, bottom: 70 },
      dataZoom: [{ type: "slider", height: 16, bottom: 20, borderColor: "#29313c", textStyle: { color: "#9aa7b4" } }],
      xAxis: { type: "category", data: x, axisLabel: { color: "#9aa7b4" } },
      yAxis: [
        { type: "value", name: left.label, axisLabel: { color: "#9aa7b4" }, splitLine: { lineStyle: { color: "#29313c" } } },
        { type: "value", name: "排名", inverse: true, axisLabel: { color: "#9aa7b4" }, splitLine: { show: false } },
      ],
      series: [
        { name: left.label, type: "line", yAxisIndex: 0, smooth: true, showSymbol: true, connectNulls: true, itemStyle: { color: left.color }, data: snaps.map((s) => numOrNull(s[left.key])) },
        { name: "自然排名（越低越好）", type: "line", yAxisIndex: 1, smooth: true, showSymbol: true, connectNulls: true, itemStyle: { color: "#d29922" }, data: snaps.map((s) => numOrNull(s.organic_rank)) },
      ],
    }, true);
  }

  draw();
  window.addEventListener("resize", () => chart.resize(), { once: true });
}

function snapTable(snaps) {
  return tableHtml(
    ["采集时间", "价格", "评分", "评论", "近月购买", "排名", "促销"],
    snaps.map((s) => ({
      cells: [
        fmt.text(s.snapshot_at),
        `<span class="num">${fmt.money(s.price)}</span>`,
        `<span class="num">${fmt.num(s.rating)}</span>`,
        `<span class="num">${fmt.int(s.review_count)}</span>`,
        `<span class="num">${fmt.int(s.monthly_bought)}</span>`,
        `<span class="num">${fmt.int(s.organic_rank)}</span>`,
        isDeal(s.is_deal) ? `<span class="badge badge-warn">促销</span>` : "—",
      ],
    }))
  );
}

/* ---------- 视图：商品对比（阶段1 单元⑤；纯前端，复用商品/趋势端点） ---------- */
async function viewCompare(asinsCsv) {
  content.innerHTML = `
    <div class="filters">
      <a class="btn" href="#/products">返回商品池</a>
      <input id="cmp-input" placeholder="输入 ASIN，逗号或空格分隔（最多 5 个）" style="width:360px" />
      <button class="btn" id="cmp-go">对比</button>
      <button class="btn" id="cmp-clear">清空</button>
    </div>
    <div class="result-meta">可从商品池勾选后进入，也可手动输入 ASIN；并排对比关键指标，每行最优值标绿。</div>
    <div id="cmp-body"></div>`;
  const input = document.getElementById("cmp-input");
  if (asinsCsv) input.value = parseAsins(asinsCsv).join(", ");
  const submit = () => {
    const list = parseAsins(input.value);
    location.hash = list.length ? `#/compare/${encodeURIComponent(list.join(","))}` : "#/compare";
  };
  document.getElementById("cmp-go").onclick = submit;
  document.getElementById("cmp-clear").onclick = () => { location.hash = "#/compare"; };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); });

  const asins = parseAsins(asinsCsv);
  const body = document.getElementById("cmp-body");
  if (asins.length < 2) {
    body.innerHTML = `<div class="state">请在商品池勾选，或输入 2-5 个 ASIN 开始对比。</div>`;
    return;
  }
  body.innerHTML = `<div class="state"><div class="spinner"></div>加载中…</div>`;
  try {
    const items = await Promise.all(asins.map(async (asin) => {
      const detail = await api(`/api/products/${encodeURIComponent(asin)}`);
      const product = (detail && (detail.product || detail)) || {};
      const latest = latestSnapshot(detail);
      const p = { ...latest, ...product };
      let trend = null;
      try { trend = await api(`/api/products/${encodeURIComponent(asin)}/trend`); } catch { /* 趋势可选，缺失不阻断 */ }
      return { asin, p, trend };
    }));
    body.innerHTML = renderCompareTable(items);
  } catch (err) {
    body.innerHTML = `<div class="state error">⚠ ${escapeHtml(err.message)}</div>`;
  }
}
function parseAsins(s) {
  return [...new Set(String(s || "").split(/[,\s]+/).map((x) => x.trim()).filter(Boolean))].slice(0, 5);
}
function latestSnapshot(detail) {
  const snapshots = Array.isArray(detail?.snapshots) ? detail.snapshots.slice() : [];
  snapshots.sort((a, b) => String(a.snapshot_at || "").localeCompare(String(b.snapshot_at || "")));
  return snapshots[snapshots.length - 1] || {};
}
function renderCompareTable(items) {
  const metrics = [
    { label: "综合得分", get: (it) => numOrNull(it.p.total_score), render: (it) => scoreBadge(it.p.total_score), best: "max" },
    { label: "价格", get: (it) => numOrNull(it.p.price), render: (it) => fmt.money(it.p.price), best: "min" },
    { label: "评分", get: (it) => numOrNull(it.p.rating), render: (it) => fmt.num(it.p.rating), best: "max" },
    { label: "评论数（越低竞争越小）", get: (it) => numOrNull(it.p.review_count), render: (it) => fmt.int(it.p.review_count), best: "min" },
    { label: "近月购买", get: (it) => numOrNull(it.p.monthly_bought), render: (it) => fmt.int(it.p.monthly_bought), best: "max" },
    { label: "自然排名", get: (it) => numOrNull(it.p.organic_rank), render: (it) => fmt.int(it.p.organic_rank), best: "min" },
    { label: "趋势置信度", render: (it) => (it.trend ? `${escapeHtml(it.trend.confidence)}（样本 ${it.trend.sample_size}）` : "—") },
    { label: "最近采集", render: (it) => fmt.text(it.p.snapshot_at || it.p.last_seen_at) },
  ];
  const head = `<th>指标</th>` + items.map((it) =>
    `<th><a class="link" href="#/product/${encodeURIComponent(it.asin)}">${escapeHtml(truncate(displayTitle(it.p, it.asin), 28))}</a></th>`
  ).join("");
  const body = metrics.map((m) => {
    let bestIdx = -1;
    if (m.best) {
      const valid = items.map((it, i) => [m.get(it), i]).filter(([v]) => v != null);
      if (valid.length) {
        const pick = m.best === "max" ? Math.max(...valid.map(([v]) => v)) : Math.min(...valid.map(([v]) => v));
        const hit = valid.find(([v]) => v === pick);
        bestIdx = hit ? hit[1] : -1;
      }
    }
    const cells = items.map((it, i) => `<td class="num${i === bestIdx ? " best" : ""}">${m.render(it)}</td>`).join("");
    return `<tr><td>${m.label}</td>${cells}</tr>`;
  }).join("");
  return wrapTable(`<table class="compare"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`);
}

/* ---------- 视图：关键词机会 ---------- */
async function viewKeywords() {
  content.innerHTML = `
    <div class="filters">
      <input id="kw-filter" placeholder="关键词过滤" />
      <input id="kw-min-products" type="number" placeholder="最少商品数" />
      <button class="btn" id="kw-apply">筛选</button>
      <button class="btn" id="kw-reset">重置</button>
    </div>
    <div id="kw-meta" class="result-meta"></div>
    <div id="kw-table"></div>
    <div id="kw-pager-wrap"></div>
    <div id="kw-detail"></div>`;
  document.getElementById("kw-filter").value = keywordState.keyword;
  document.getElementById("kw-min-products").value = keywordState.minProducts;
  document.getElementById("kw-apply").onclick = () => loadKeywords(true);
  document.getElementById("kw-reset").onclick = () => {
    keywordState.keyword = "";
    keywordState.minProducts = "";
    document.getElementById("kw-filter").value = "";
    document.getElementById("kw-min-products").value = "";
    document.getElementById("kw-detail").innerHTML = "";
    loadKeywords(true);
  };
  content.querySelectorAll(".filters input").forEach((inp) => {
    inp.addEventListener("keydown", (e) => { if (e.key === "Enter") loadKeywords(true); });
  });
  await loadKeywords();
}

async function loadKeywords(resetPage = false) {
  if (resetPage) {
    keywordState.offset = 0;
    keywordState.keyword = document.getElementById("kw-filter").value.trim();
    keywordState.minProducts = document.getElementById("kw-min-products").value.trim();
    document.getElementById("kw-detail").innerHTML = "";
  }
  const table = document.getElementById("kw-table");
  const meta = document.getElementById("kw-meta");
  const pager = document.getElementById("kw-pager-wrap");
  table.innerHTML = `<div class="state"><div class="spinner"></div>加载中…</div>`;
  meta.textContent = "";
  pager.innerHTML = "";
  const q = new URLSearchParams({
    limit: String(keywordState.limit),
    offset: String(keywordState.offset),
  });
  if (keywordState.keyword) q.set("keyword", keywordState.keyword);
  if (keywordState.minProducts) q.set("min_products", keywordState.minProducts);
  try {
    const page = normalizePage(await api(`/api/keywords/opportunities?${q.toString()}`), keywordState.limit);
    const rows = page.rows;
    meta.textContent = pageSummary(page, "关键词") + " · 点行展开详情与该词下商品 · 机会分 ≥70 标「蓝海」";
    if (!rows.length) {
      table.innerHTML = `<div class="state">暂无关键词机会数据。</div>`;
      pager.innerHTML = renderPager("kw-pager", page, [20, 50, 100]);
      bindPager("kw-pager", keywordState, page, loadKeywords);
      return;
    }
  renderSortableTable(document.getElementById("kw-table"), [
    { key: "keyword", label: "关键词", render: (r) => escapeHtml(r.keyword), sortVal: (r) => r.keyword },
    { key: "opportunity_score", label: "机会分", align: "num", numeric: true, sortVal: (r) => r.opportunity_score,
      render: (r) => scoreBadge(r.opportunity_score) + (Number(r.opportunity_score) >= 70 ? ' <span class="badge badge-good">蓝海</span>' : "") },
    { key: "product_count", label: "商品数", align: "num", numeric: true, render: (r) => fmt.int(r.product_count), sortVal: (r) => r.product_count },
    { key: "avg_monthly_bought", label: "需求", align: "num", numeric: true, render: (r) => fmt.int(r.avg_monthly_bought), sortVal: (r) => r.avg_monthly_bought },
    { key: "avg_review_count", label: "竞争", align: "num", numeric: true, render: (r) => fmt.int(r.avg_review_count), sortVal: (r) => r.avg_review_count },
    { key: "avg_price", label: "价格带", align: "num", numeric: true, render: (r) => fmt.money(r.avg_price), sortVal: (r) => r.avg_price },
    { key: "avg_organic_rank", label: "自然排名", align: "num", numeric: true, render: (r) => fmt.num(r.avg_organic_rank, 0), sortVal: (r) => r.avg_organic_rank },
  ], rows, { defaultSort: { key: "opportunity_score", dir: -1 }, exportName: "关键词机会", onRowClick: showKeywordDetail });
    pager.innerHTML = renderPager("kw-pager", page, [20, 50, 100]);
    bindPager("kw-pager", keywordState, page, loadKeywords);
  } catch (err) {
    table.innerHTML = `<div class="state error">⚠ ${escapeHtml(err.message)}</div>`;
  }
}

/* 关键词机会详情展开：机会原因 / 风险警告 / 进入策略 + 该词下商品下钻。 */
function showKeywordDetail(r) {
  const box = document.getElementById("kw-detail");
  if (!box) return;
  keywordProductState.keyword = r.keyword || "";
  keywordProductState.offset = 0;
  const level = r.opportunity_level ? ` <span class="badge badge-dim">${escapeHtml(r.opportunity_level)}</span>` : "";
  box.innerHTML = `
    <div class="panel">
      <h2>关键词「${escapeHtml(r.keyword)}」机会详情${level}</h2>
      <div class="advice-row"><span>机会原因</span><p>${escapeHtml(r.opportunity_reason || "—")}</p></div>
      <div class="advice-row"><span>风险警告</span><p>${escapeHtml(r.risk_warnings || "—")}</p></div>
      <div class="advice-row"><span>进入策略</span><p>${escapeHtml(r.entry_strategy || "—")}</p></div>
      <h3 style="margin:14px 0 8px;font-size:13px">维度细分</h3>
      <div class="kv">
        <div><span>机会分</span>${scoreBadge(r.opportunity_score)}</div>
        <div><span>商品数</span>${fmt.int(r.product_count)}</div>
        <div><span>需求分</span>${fmt.num(r.avg_demand_score, 0)}</div>
        <div><span>竞争分</span>${fmt.num(r.avg_competition_score, 0)}</div>
        <div><span>评分分</span>${fmt.num(r.avg_rating_score, 0)}</div>
        <div><span>价格分</span>${fmt.num(r.avg_price_score, 0)}</div>
        <div><span>排名分</span>${fmt.num(r.avg_rank_score, 0)}</div>
        <div><span>价格区间</span>${fmt.money(r.min_price)}–${fmt.money(r.max_price)}</div>
        <div><span>前10名</span>${fmt.int(r.top10_count)}</div>
        <div><span>广告位</span>${fmt.int(r.sponsored_count)}</div>
        <div><span>最近快照</span>${fmt.text(r.latest_snapshot_at)}</div>
      </div>
    </div>
    <div class="panel">
      <div class="table-toolbar">
        <h2 style="margin:0;font-size:14px">该词下商品</h2>
      </div>
      <div id="kw-products-meta" class="result-meta"></div>
      <div id="kw-products"></div>
      <div id="kw-products-pager-wrap"></div>
    </div>`;
  box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  loadKeywordProducts();
}

async function loadKeywordProducts() {
  const keyword = keywordProductState.keyword;
  const box = document.getElementById("kw-products");
  const meta = document.getElementById("kw-products-meta");
  const pager = document.getElementById("kw-products-pager-wrap");
  if (!box || !keyword) return;
  box.innerHTML = `<div class="state"><div class="spinner"></div>加载中…</div>`;
  meta.textContent = "";
  pager.innerHTML = "";
  const q = new URLSearchParams({
    keyword,
    keyword_exact: "true",
    limit: String(keywordProductState.limit),
    offset: String(keywordProductState.offset),
  });
  try {
    const page = normalizePage(await api(`/api/products?${q.toString()}`), keywordProductState.limit);
    const rows = page.rows;
    meta.textContent = pageSummary(page, "商品") + ` · 关键词：${keyword}`;
    if (!rows.length) {
      box.innerHTML = `<div class="state">该关键词下暂无商品。</div>`;
      pager.innerHTML = renderPager("kw-products-pager", page, [10, 25, 50]);
      bindPager("kw-products-pager", keywordProductState, page, loadKeywordProducts);
      return;
    }
    renderSortableTable(box, [
      { key: "title", label: "标题", render: (item) => escapeHtml(truncate(displayTitle(item, item.asin), 68)), sortVal: (item) => displayTitle(item, item.asin) },
      { key: "total_score", label: "得分", align: "num", numeric: true, render: (item) => scoreBadge(item.total_score), sortVal: (item) => item.total_score },
      { key: "price", label: "价格", align: "num", numeric: true, render: (item) => fmt.money(item.price), sortVal: (item) => item.price },
      { key: "rating", label: "评分", align: "num", numeric: true, render: (item) => fmt.num(item.rating), sortVal: (item) => item.rating },
      { key: "review_count", label: "评论", align: "num", numeric: true, render: (item) => fmt.int(item.review_count), sortVal: (item) => item.review_count },
      { key: "monthly_bought", label: "近月购买", align: "num", numeric: true, render: (item) => fmt.int(item.monthly_bought), sortVal: (item) => item.monthly_bought },
      { key: "organic_rank", label: "排名", align: "num", numeric: true, render: (item) => fmt.int(item.organic_rank), sortVal: (item) => item.organic_rank },
    ], rows, { rowHash: (item) => `#/product/${encodeURIComponent(item.asin)}`, defaultSort: { key: "total_score", dir: -1 }, exportName: `关键词-${keyword}-商品` });
    pager.innerHTML = renderPager("kw-products-pager", page, [10, 25, 50]);
    bindPager("kw-products-pager", keywordProductState, page, loadKeywordProducts);
  } catch (err) {
    box.innerHTML = `<div class="state error">⚠ ${escapeHtml(err.message)}</div>`;
  }
}

/* ---------- 视图：评论痛点 ---------- */
async function viewReviews() {
  loading();
  const rows = await api("/api/reviews/insights?limit=100");
  if (!rows || !rows.length) return emptyState("暂无评论洞察数据。");
  content.innerHTML = `<div class="result-meta">共 ${rows.length} 条 · 点列头排序</div><div id="rv-table"></div>`;
  renderSortableTable(document.getElementById("rv-table"), [
    { key: "title", label: "商品 / ASIN", render: (r) => escapeHtml(truncate(displayTitle(r, r.asin || r.keyword || "—"), 80)), sortVal: (r) => displayTitle(r, r.asin || r.keyword || "") },
    { key: "neg", label: "低分占比", align: "num", numeric: true, render: (r) => formatNegativeRate(r), sortVal: (r) => negativeRateValue(r) },
    { key: "pain", label: "主要痛点", render: (r) => escapeHtml(truncate(formatPainPoints(r.top_pain_points || r.pain_points), 60)), csv: (r) => formatPainPoints(r.top_pain_points || r.pain_points) },
    { key: "opp", label: "改良机会", render: (r) => escapeHtml(truncate(r.opportunity_summary || r.improvement_opportunities || "—", 60)), csv: (r) => r.opportunity_summary || r.improvement_opportunities || "" },
  ], rows, { defaultSort: { key: "neg", dir: -1 }, exportName: "评论洞察" });
}
function negativeRateValue(row) {
  if (row.negative_rate != null) return Number(row.negative_rate);
  if (row.low_star_ratio != null) return Number(row.low_star_ratio) * 100;
  return null;
}
function formatNegativeRate(row) {
  const v = negativeRateValue(row);
  return v == null ? "—" : `${v.toFixed(0)}%`;
}
function formatPainPoints(value) {
  if (!value || (Array.isArray(value) && !value.length)) return "—";
  if (Array.isArray(value)) {
    return value.map((item) => {
      if (typeof item === "string") return item;
      return item.theme ? `${item.theme}${item.count ? `(${item.count})` : ""}` : JSON.stringify(item);
    }).join("、");
  }
  return String(value);
}

/* ---------- 视图：任务中心 ---------- */
async function viewTasks() {
  loading();
  const rawRows = await api("/api/tasks?limit=100");
  const rows = (rawRows || []).map((row, index) => ({ ...row, _taskRowId: String(row.id ?? index) }));
  if (!rows || !rows.length) return emptyState("暂无采集或写入任务记录。");
  taskErrorRows.clear();
  rows.forEach((row) => taskErrorRows.set(row._taskRowId, row));
  content.innerHTML = `<div class="result-meta">共 ${rows.length} 条 · 点列头排序</div><div id="task-table"></div>`;
  renderSortableTable(document.getElementById("task-table"), [
    { key: "time", label: "时间", render: (r) => fmt.text(r.created_at || r.started_at), sortVal: (r) => r.created_at || r.started_at || "" },
    { key: "type", label: "类型", render: (r) => escapeHtml(taskDisplayType(r)), sortVal: (r) => taskDisplayType(r) },
    { key: "keyword", label: "关键词", render: (r) => escapeHtml(r.keyword || "—"), sortVal: (r) => r.keyword || "" },
    { key: "status", label: "状态", render: (r) => statusBadge(r.status), sortVal: (r) => r.status || "" },
    { key: "valid_count", label: "有效数", align: "num", numeric: true, render: (r) => taskMetric(r, "valid"), sortVal: (r) => taskMetricValue(r, "valid") },
    { key: "ingested_count", label: "入库数", align: "num", numeric: true, render: (r) => taskMetric(r, "ingested"), sortVal: (r) => taskMetricValue(r, "ingested") },
    { key: "error", label: "错误日志", sortable: false, render: renderTaskErrorCell, csv: taskErrorText },
  ], rows, {
    defaultSort: { key: "time", dir: -1 },
    exportName: "任务记录",
    onDraw: bindTaskErrorButtons,
  });
}

function taskDisplayType(row) {
  const explicit = row.job_type || row.type;
  if (explicit === "入库" || explicit === "爬取") return explicit;
  if (String(row.url || "").startsWith("local_html_import:")) return "入库";
  if (row.pages == null || Number(row.total_inserted || 0) > 0) return "入库";
  return "爬取";
}

function isImportTask(row) {
  return taskDisplayType(row) === "入库";
}

function taskMetricValue(row, kind) {
  if (!isImportTask(row)) return null;
  if (kind === "valid") return row.valid_count ?? row.total_valid;
  return row.ingested_count ?? row.total_inserted;
}

function taskMetric(row, kind) {
  return isImportTask(row) ? fmt.int(taskMetricValue(row, kind)) : "—";
}

function taskErrorText(row) {
  return row?.error_message || row?.error || row?.failure_reason || "";
}

function renderTaskErrorCell(row) {
  if (!taskErrorText(row)) return "—";
  return `<button type="button" class="btn btn-sm task-log-btn" data-task-error="${escapeHtml(row._taskRowId)}">查看日志</button>`;
}

function bindTaskErrorButtons(container) {
  container.querySelectorAll("[data-task-error]").forEach((button) => {
    button.onclick = (event) => {
      event.stopPropagation();
      showTaskError(button.dataset.taskError);
    };
  });
}

function showTaskError(taskRowId) {
  const row = taskErrorRows.get(String(taskRowId));
  const text = taskErrorText(row);
  if (!text) {
    notice("这条任务没有错误日志", "bad");
    return;
  }
  const title = `任务 #${row.id ?? taskRowId} 报错日志`;
  openTaskLogDialog(title, text);
}

function openTaskLogDialog(title, text) {
  closeTaskLogDialog();
  const modal = document.createElement("div");
  modal.id = "task-log-dialog";
  modal.className = "modal-backdrop";
  modal.innerHTML = `
    <section class="modal-panel" role="dialog" aria-modal="true" aria-label="${escapeHtml(title)}">
      <div class="modal-head">
        <h2>${escapeHtml(title)}</h2>
        <button type="button" class="btn btn-sm" data-close-log>关闭</button>
      </div>
      <pre class="log-pre" tabindex="0">${escapeHtml(text)}</pre>
    </section>`;
  modal.addEventListener("click", (event) => {
    if (event.target === modal) closeTaskLogDialog();
  });
  document.body.appendChild(modal);
  const closeButton = modal.querySelector("[data-close-log]");
  if (closeButton) closeButton.onclick = closeTaskLogDialog;
  const pre = modal.querySelector(".log-pre");
  if (pre) pre.focus();
}

function closeTaskLogDialog() {
  const modal = document.getElementById("task-log-dialog");
  if (!modal) return false;
  modal.remove();
  return true;
}

/* 商品图点击放大看细节：高清大图（large=1）；滚轮以光标为中心缩放、放大后可拖动平移。 */
window.openImageZoom = (asin) => {
  closeImageZoom();
  const box = document.createElement("div");
  box.id = "img-zoom";
  box.className = "img-zoom-backdrop";
  box.innerHTML = `
    <button type="button" class="img-zoom-close" aria-label="关闭" title="关闭（Esc）">✕</button>
    <div class="img-zoom-loading"><div class="spinner"></div>加载高清图…</div>
    <div class="img-zoom-hint">滚轮缩放 · 拖动平移 · Esc 关闭</div>
    <img class="img-zoom-img" alt="商品大图" style="display:none" src="/api/products/${asin}/image?large=1" />`;
  document.body.appendChild(box);
  const img = box.querySelector(".img-zoom-img");
  const loading = box.querySelector(".img-zoom-loading");
  img.onload = () => { loading.style.display = "none"; img.style.display = ""; };
  img.onerror = () => {
    if (!img.dataset.fb) { img.dataset.fb = "1"; img.src = `/api/products/${asin}/image`; return; }
    loading.innerHTML = "图片加载失败";
  };

  let scale = 1, tx = 0, ty = 0, dragging = false, didDrag = false, sx = 0, sy = 0;
  const apply = () => {
    img.style.transform = `translate(${tx}px, ${ty}px) scale(${scale})`;
    img.style.cursor = scale > 1 ? (dragging ? "grabbing" : "grab") : "zoom-in";
  };
  box.addEventListener("wheel", (e) => {
    e.preventDefault();
    const cx = window.innerWidth / 2, cy = window.innerHeight / 2; // 图居中，视口中心≈图中心
    const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
    const next = Math.min(6, Math.max(1, scale * factor));
    // 以光标为锚点缩放：缩放前后光标下的图上点保持不动
    const px = (e.clientX - cx - tx) / scale;
    const py = (e.clientY - cy - ty) / scale;
    tx = e.clientX - cx - next * px;
    ty = e.clientY - cy - next * py;
    scale = next;
    if (scale === 1) { tx = 0; ty = 0; }
    apply();
  }, { passive: false });
  box.addEventListener("mousedown", (e) => {
    didDrag = false;
    if (scale <= 1 || e.target !== img) return; // 仅放大后、在图上才拖动
    e.preventDefault();
    dragging = true; sx = e.clientX - tx; sy = e.clientY - ty; apply();
  });
  box.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    didDrag = true; tx = e.clientX - sx; ty = e.clientY - sy; apply();
  });
  box.addEventListener("mouseup", () => { dragging = false; apply(); });
  box.addEventListener("click", (e) => { if (e.target === box && !didDrag) closeImageZoom(); });
  box.querySelector(".img-zoom-close").onclick = closeImageZoom;
  document.addEventListener("keydown", imgZoomEsc);
  apply();
};
function imgZoomEsc(e) { if (e.key === "Escape") closeImageZoom(); }
function closeImageZoom() {
  const box = document.getElementById("img-zoom");
  if (box) box.remove();
  document.removeEventListener("keydown", imgZoomEsc);
}
function statusBadge(s) {
  const v = String(s || "").toLowerCase();
  const cls = /(完成|成功|已保存|done|success|ok|completed)/.test(v) ? "badge-good"
    : /(失败|异常|停止|blocked|fail|error)/.test(v) ? "badge-bad"
    : /(运行|处理中|pending|progress|running)/.test(v) ? "badge-warn" : "badge-dim";
  return `<span class="badge ${cls}">${escapeHtml(s || "—")}</span>`;
}

/* ---------- 采集队列状态（手动采集·构建队列） ---------- */
let crawlQueue = { name: "", items: [] }; // items: { keyword, pages, status, reason, collected_at }
let crawlQueuePaused = false;
let crawlQueueRunning = false;
let savedQueueNames = [];
let queueAutoLoaded = false; // 首次进入自动载入最近队列，只做一次

function nextQueueName() {
  let n = 1;
  while (savedQueueNames.includes("队列" + n)) n++;
  return "队列" + n;
}
function newCrawlQueue() { crawlQueue = { name: nextQueueName(), items: [] }; }

async function loadSavedQueues() {
  try {
    const list = await api("/api/crawl/queues");
    savedQueueNames = (list || []).map((q) => q.name);
    return list || [];
  } catch (e) { savedQueueNames = []; return []; }
}

/* ---------- 视图：手动采集（GUI 采集入口 Web 化，含采集队列） ---------- */
function viewCrawl() {
  if (!crawlQueue.name) newCrawlQueue();
  content.innerHTML = `
    <div class="panel">
      <h2>手动采集</h2>
      <div class="filters" style="margin:0">
        <input id="cr-keyword" placeholder="采集关键词" style="width:260px" />
        <input id="cr-pages" type="number" min="1" max="7" value="1" placeholder="页数" style="width:90px" />
        <button class="btn btn-warn" id="cr-run">开始采集</button>
        <button class="btn btn-warn" id="cr-queue-add" style="display:none">加入队列</button>
        <button class="btn" id="cr-open-amazon">预开启 Amazon 页面</button>
        <label class="check-inline"><input type="checkbox" id="cr-queue-mode" /> 队列模式</label>
      </div>
      <p style="color:var(--text-dim);font-size:12.5px;margin:10px 0 0;line-height:1.6">
        会打开浏览器访问 Amazon 搜索页并保存 HTML 到 <code>2_1/html/&lt;关键词&gt;/</code>；不自动写入数据库。遇到登录页、验证码、空页或有效商品为 0 会停止。
      </p>
    </div>
    <div id="cr-queue" class="panel" style="display:none">
      <h2>采集队列</h2>
      <div class="queue-bar">
        <select id="q-select" class="sel sel-sm"></select>
        <input id="q-name" class="pager-jump" style="width:150px" placeholder="队列名" />
        <button class="btn btn-sm" id="q-save">保存队列</button>
        <button class="btn btn-sm btn-bad" id="q-del">删除队列</button>
        <span class="queue-spacer"></span>
        <span id="q-progress" class="selected-count">0 / 0</span>
        <button class="btn btn-sm btn-warn" id="q-start">开始</button>
        <button class="btn btn-sm" id="q-pause" disabled>暂停</button>
      </div>
      <p style="color:var(--text-dim);font-size:12.5px;margin:8px 0 12px;line-height:1.6">
        按顺序逐词联网采集并<b>自动入库</b>（队尾统一同步仓库）、复用浏览器会话。暂停会在<b>当前词采完后</b>停下；遇风控/未采到目标自动暂停并标记。<b>采集中请勿关闭或切走本页</b>。
      </p>
      <div id="q-list"></div>
    </div>
    <div id="cr-result"></div>`;
  const modeBox = document.getElementById("cr-queue-mode");
  modeBox.onchange = () => setQueueMode(modeBox.checked);
  document.getElementById("cr-run").onclick = runManualCrawl;
  document.getElementById("cr-queue-add").onclick = addToQueue;
  document.getElementById("cr-open-amazon").onclick = openAmazonForCrawl;
  document.getElementById("cr-keyword").addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    if (document.getElementById("cr-queue-mode").checked) addToQueue();
    else runManualCrawl();
  });
  document.getElementById("q-select").onchange = onQueueSelect;
  document.getElementById("q-save").onclick = saveCurrentQueue;
  document.getElementById("q-del").onclick = deleteCurrentQueue;
  document.getElementById("q-start").onclick = runQueue;
  document.getElementById("q-pause").onclick = pauseQueue;
  document.getElementById("q-name").value = crawlQueue.name;
  renderQueueList();
  setQueueControls();
  refreshQueueSelect();
}

function setQueueMode(on) {
  document.getElementById("cr-queue").style.display = on ? "" : "none";
  document.getElementById("cr-run").style.display = on ? "none" : "";
  document.getElementById("cr-queue-add").style.display = on ? "" : "none";
  if (on) refreshQueueSelect();
}

async function refreshQueueSelect() {
  const sel = document.getElementById("q-select");
  if (!sel) return;
  const list = await loadSavedQueues();
  // 首次进入且当前队列为空：自动载入最近保存的队列（按 updated_at），省一步手选。
  if (!queueAutoLoaded && !crawlQueue.items.length && list.length) {
    queueAutoLoaded = true;
    const recent = list.slice().sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")))[0];
    crawlQueue = {
      name: recent.name,
      items: recent.items.map((it) => ({ keyword: it.keyword, pages: it.pages, status: "待采", reason: "", collected_at: it.collected_at || "" })),
    };
    const nameEl = document.getElementById("q-name");
    if (nameEl) nameEl.value = crawlQueue.name;
    renderQueueList();
    setQueueControls();
  }
  // 新建空队列若自动命名撞上已保存同名：改名。否则下拉会"看着选中却没载入"，且保存会覆盖已存队列。
  if (!crawlQueue.items.length && savedQueueNames.includes(crawlQueue.name)) {
    crawlQueue.name = nextQueueName();
    const nameEl = document.getElementById("q-name");
    if (nameEl) nameEl.value = crawlQueue.name;
  }
  sel.innerHTML = ['<option value="__new__">＋ 新建队列</option>']
    .concat(list.map((q) => `<option value="${escapeHtml(q.name)}">${escapeHtml(q.name)}（${q.items.length}）</option>`))
    .join("");
  // 仅当当前是"已载入、非空、已保存"的队列才高亮它；空队列一律显示"新建队列"，避免假选中。
  sel.value = (crawlQueue.items.length && savedQueueNames.includes(crawlQueue.name)) ? crawlQueue.name : "__new__";
}

async function onQueueSelect(e) {
  if (crawlQueueRunning) { notice("队列采集中，请先暂停", "bad"); refreshQueueSelect(); return; }
  const value = e.target.value;
  if (value === "__new__") {
    newCrawlQueue();
  } else {
    const list = await api("/api/crawl/queues");
    const q = (list || []).find((x) => x.name === value);
    if (q) crawlQueue = { name: q.name, items: q.items.map((it) => ({ keyword: it.keyword, pages: it.pages, status: "待采", reason: "", collected_at: it.collected_at || "" })) };
  }
  document.getElementById("q-name").value = crawlQueue.name;
  renderQueueList();
  setQueueControls();
}

function addToQueue() {
  const kwEl = document.getElementById("cr-keyword");
  const keyword = kwEl.value.trim();
  const pages = Math.max(1, Math.min(7, Number(document.getElementById("cr-pages").value) || 1));
  if (!keyword) { notice("请填写采集关键词", "bad"); return; }
  if (crawlQueue.items.some((it) => it.keyword.toLowerCase() === keyword.toLowerCase())) {
    notice("该关键词已在队列中", "bad"); return;
  }
  crawlQueue.items.push({ keyword, pages, status: "待采", reason: "", collected_at: "" });
  kwEl.value = "";
  renderQueueList();
  setQueueControls();
}

window.queueRemove = (idx) => {
  if (crawlQueueRunning) { notice("队列采集中，暂不能移除", "bad"); return; }
  crawlQueue.items.splice(idx, 1);
  renderQueueList();
  setQueueControls();
};

function queueStatusBadge(status) {
  const map = { "完成": "badge-good", "待采": "badge-dim", "采集中": "badge-warn", "被拦": "badge-bad", "未采到": "badge-warn", "失败": "badge-bad" };
  return `<span class="badge ${map[status] || "badge-dim"}">${escapeHtml(status || "待采")}</span>`;
}

function renderQueueList() {
  const box = document.getElementById("q-list");
  if (!box) return;
  if (!crawlQueue.items.length) {
    box.innerHTML = `<div class="state">队列为空：上方输入关键词点「加入队列」。</div>`;
    updateQueueProgress();
    return;
  }
  const rows = crawlQueue.items.map((it, i) => `
    <tr>
      <td>${escapeHtml(it.keyword)}</td>
      <td class="num">${it.pages}</td>
      <td>${queueStatusBadge(it.status)}${it.reason ? ` <span style="color:var(--text-dim);font-size:12px">${escapeHtml(it.reason)}</span>` : ""}</td>
      <td>${it.collected_at ? escapeHtml(fmt.text(it.collected_at)) : '<span style="color:var(--text-dim)">未采集</span>'}</td>
      <td><button class="btn btn-sm btn-bad" onclick="queueRemove(${i})" ${crawlQueueRunning ? "disabled" : ""}>移除</button></td>
    </tr>`).join("");
  box.innerHTML = `<div class="table-wrap"><table>
    <thead><tr><th>关键词</th><th class="num">页数</th><th>状态</th><th>上次采集</th><th>操作</th></tr></thead>
    <tbody>${rows}</tbody></table></div>`;
  updateQueueProgress();
}

function updateQueueProgress() {
  const el = document.getElementById("q-progress");
  if (!el) return;
  const done = crawlQueue.items.filter((it) => it.status === "完成").length;
  el.textContent = `${done} / ${crawlQueue.items.length}`;
}

function setQueueControls() {
  const start = document.getElementById("q-start");
  const pause = document.getElementById("q-pause");
  if (!start || !pause) return;
  start.disabled = crawlQueueRunning;
  pause.disabled = !crawlQueueRunning;
  start.textContent = crawlQueue.items.some((it) => it.status === "完成") ? "继续" : "开始";
}

function pauseQueue() {
  crawlQueuePaused = true;
  notice("将在当前关键词采完后暂停…", "ok");
  document.getElementById("q-pause").disabled = true;
}

async function runQueue() {
  if (crawlQueueRunning) return;
  const pending = crawlQueue.items.filter((it) => it.status !== "完成");
  if (!pending.length) { notice("队列没有待采任务", "bad"); return; }
  if (!confirm(
    `开始队列连续联网采集？\n\n会逐词打开/复用浏览器抓 Amazon 并自动入库，遇风控/未采到会自动暂停。共 ${pending.length} 个待采。\n\n采集中请勿关闭或切走本页。`
  )) return;
  crawlQueueRunning = true;
  crawlQueuePaused = false;
  setQueueControls();
  renderQueueList();
  let importedTotal = 0;
  for (const it of crawlQueue.items) {
    if (crawlQueuePaused) break;
    if (it.status === "完成") continue;
    it.status = "采集中";
    it.reason = "";
    renderQueueList();
    try {
      const r = await apiSend("/api/crawl/run-import", "POST", { keyword: it.keyword, pages: it.pages });
      it.status = r.outcome || "完成";
      it.reason = r["原因"] || "";
      if (it.status === "完成") {
        it.collected_at = r["采集时间"] || new Date().toISOString().slice(0, 19).replace("T", " ");
      }
      importedTotal += Number(r["入库商品数"]) || 0;
    } catch (err) {
      it.status = "失败";
      it.reason = err.message;
    }
    renderQueueList();
    if (it.status !== "完成") crawlQueuePaused = true; // 任一非完成 → 自动暂停
    if (crawlQueuePaused) break;
  }
  crawlQueueRunning = false;
  setQueueControls();
  renderQueueList();
  if (importedTotal > 0) {
    try { await apiSend("/api/warehouse/sync", "POST"); }
    catch (e) { notice("队尾仓库同步失败：" + e.message, "bad"); }
  }
  const remain = crawlQueue.items.filter((it) => it.status !== "完成").length;
  if (crawlQueuePaused) notice(`队列已暂停（剩 ${remain} 个待采），累计入库 ${importedTotal}`, "bad");
  else notice(`队列完成，累计入库 ${importedTotal}`, "ok");
}

async function saveCurrentQueue() {
  const name = document.getElementById("q-name").value.trim();
  if (!name) { notice("请填写队列名", "bad"); return; }
  if (!crawlQueue.items.length) { notice("空队列无需保存", "bad"); return; }
  try {
    const saved = await apiSend("/api/crawl/queues", "POST", {
      name,
      items: crawlQueue.items.map((it) => ({ keyword: it.keyword, pages: it.pages, collected_at: it.collected_at || "" })),
    });
    crawlQueue.name = saved.name;
    notice("队列已保存", "ok");
    await refreshQueueSelect();
  } catch (err) { notice(err.message, "bad"); }
}

async function deleteCurrentQueue() {
  const name = crawlQueue.name;
  if (!savedQueueNames.includes(name)) { notice("该队列尚未保存，无需删除", "bad"); return; }
  if (!confirm(`删除已保存队列「${name}」？（不影响已采数据）`)) return;
  try {
    await apiSend(`/api/crawl/queues/${encodeURIComponent(name)}`, "DELETE");
    notice("已删除", "ok");
    newCrawlQueue();
    document.getElementById("q-name").value = crawlQueue.name;
    renderQueueList();
    setQueueControls();
    await refreshQueueSelect();
  } catch (err) { notice(err.message, "bad"); }
}

async function openAmazonForCrawl() {
  const box = document.getElementById("cr-result");
  const btn = document.getElementById("cr-open-amazon");
  if (!confirm("预开启 Amazon 页面？\n\n会打开或复用浏览器访问 Amazon 首页。")) return;
  btn.disabled = true;
  box.innerHTML = `<div class="state"><div class="spinner"></div>正在打开 Amazon 页面…</div>`;
  try {
    const result = await apiSend("/api/crawl/open-amazon", "POST");
    box.innerHTML = `
      <div class="panel">
        <h3>Amazon 页面已打开</h3>
        <div class="result-list">
          <div class="row"><span>状态</span> <b>${escapeHtml(result["状态"] || "已打开")}</b></div>
          <div class="row"><span>标题</span> <b>${escapeHtml(result["标题"] || "Amazon")}</b></div>
          <div class="row"><span>URL</span> <b>${escapeHtml(result["URL"] || "")}</b></div>
        </div>
      </div>`;
    notice("Amazon 页面已打开", "ok");
  } catch (err) {
    box.innerHTML = `<div class="state error">⚠ ${escapeHtml(err.message)}</div>`;
    notice(err.message, "bad");
  } finally {
    btn.disabled = false;
  }
}

async function runManualCrawl() {
  const keyword = document.getElementById("cr-keyword").value.trim();
  const pages = Number(document.getElementById("cr-pages").value) || 1;
  const box = document.getElementById("cr-result");
  const btn = document.getElementById("cr-run");
  if (!keyword) { notice("请填写采集关键词", "bad"); return; }
  if (pages < 1 || pages > 7) { notice("采集页数需在 1-7 页之间", "bad"); return; }
  if (!confirm(
    `立即联网采集「${keyword}」${pages} 页？\n\n会打开浏览器访问 Amazon 搜索页并保存 HTML；不自动写入数据库。遇到验证码、登录页或空页会停止。`
  )) return;
  btn.disabled = true;
  box.innerHTML = `<div class="state"><div class="spinner"></div>采集中…（浏览器会自动打开，请勿刷新页面）</div>`;
  try {
    const result = await apiSend("/api/crawl/run", "POST", { keyword, pages });
    box.innerHTML = renderCrawlResult(result);
    notice(result["状态"] === "完成" ? "采集完成" : "采集已停止，请查看原因", result["状态"] === "完成" ? "ok" : "bad");
  } catch (err) {
    box.innerHTML = `<div class="state error">⚠ ${escapeHtml(err.message)}</div>`;
    notice(err.message, "bad");
  } finally {
    btn.disabled = false;
  }
}

function renderCrawlResult(result) {
  const pages = (result && result["页面"]) || [];
  const status = result && result["状态"];
  const summary = `
    <div class="row"><span>状态</span> <b>${statusBadge(status)}</b></div>
    <div class="row"><span>关键词</span> <b>${escapeHtml(result["关键词"] || "—")}</b></div>
    <div class="row"><span>保存页数</span> <b>${fmt.int(result["保存页数"])} / ${fmt.int(result["请求页数"])}</b></div>
    <div class="row"><span>保存目录</span> <b>${escapeHtml(result["保存目录"] || "—")}</b></div>
    <div class="row"><span>说明</span> <b>${escapeHtml(result.message || "—")}</b></div>`;
  const rows = pages.length ? tableHtml(
    ["页码", "状态", "解析商品", "有效商品", "保存文件", "原因"],
    pages.map((p) => ({ cells: [
      fmt.int(p["页码"]),
      statusBadge(p["状态"]),
      fmt.int(p["解析商品数"]),
      fmt.int(p["有效商品数"]),
      `<code>${escapeHtml(p["保存文件"] || "—")}</code>`,
      escapeHtml(p["原因"] || "—"),
    ] }))
  ) : `<div class="state">暂无保存页面。</div>`;
  return `<div class="panel"><h2>采集结果</h2>${summary}
    <div class="actions" style="margin:12px 0"><a class="btn btn-sm" href="#/import">去本地 HTML 入库</a><a class="btn btn-sm" href="#/tasks">查看任务中心</a></div>
    ${rows}</div>`;
}

/* ---------- 视图：关键词追踪（C3） ---------- */
async function viewTracking() {
  loading();
  const tasks = await api("/api/tracking/tasks?limit=100");
  content.innerHTML = `
    <div class="panel">
      <h2>新建追踪任务</h2>
      <div class="filters" style="margin:0">
        <input id="t-keyword" placeholder="关键词（必填）" style="width:210px" />
        <input id="t-target" type="number" min="1" placeholder="目标快照数" value="3" />
        <input id="t-pages" type="number" min="1" placeholder="每轮页数" value="2" />
        <input id="t-market" placeholder="站点" value="US" style="width:80px" />
        <button class="btn" id="t-create">创建追踪</button>
      </div>
      <p style="color:var(--text-dim);font-size:12.5px;margin:10px 0 0;line-height:1.6">
        追踪按「快照时间点数」积累（口径 COUNT(DISTINCT snapshot_at)），达标自动停采。
        「执行采集」为联网操作，受限频 / 被拦即停 / 72h 边界约束，需二次确认。
      </p>
    </div>
    <div class="panel">
      <h2>追踪任务（${tasks.length}）</h2>
      <div id="t-list">${trackingTable(tasks)}</div>
    </div>`;
  document.getElementById("t-create").onclick = createTracking;
}

function trackingTable(tasks) {
  if (!tasks || !tasks.length) return `<div class="state">暂无追踪任务，先在上方创建。</div>`;
  return tableHtml(
    ["关键词", "站点", "进度", "状态", "最近采集", "最近检查", "操作"],
    tasks.map((t) => {
      const cur = t.current_snapshots ?? t.achieved_snapshots ?? 0;
      const done = Number(cur) >= Number(t.target_snapshots);
      const toggle = t.status === "active"
        ? `<button class="btn btn-sm" onclick="trackToggle(${t.id},'paused')">暂停</button>`
        : `<button class="btn btn-sm" onclick="trackToggle(${t.id},'active')">恢复</button>`;
      return { cells: [
        escapeHtml(t.keyword),
        escapeHtml(t.marketplace),
        `<span class="badge ${done ? "badge-good" : "badge-dim"}">${cur} / ${escapeHtml(t.target_snapshots)}</span>`,
        statusBadge(t.status),
        fmt.text(t.last_collected_at),
        fmt.text(t.last_checked_at),
        `<div class="actions">
          <button class="btn btn-sm" onclick="trackPreview(${t.id})">检查</button>
          <button class="btn btn-sm btn-warn" onclick="trackCollect(${t.id})">执行采集</button>
          ${toggle}
          <button class="btn btn-sm btn-bad" onclick="trackDelete(${t.id})">删除</button>
        </div>`,
      ] };
    })
  );
}

async function createTracking() {
  const kw = document.getElementById("t-keyword").value.trim();
  if (!kw) return notice("请填写关键词", "bad");
  try {
    await apiSend("/api/tracking/tasks", "POST", {
      keyword: kw,
      target_snapshots: Number(document.getElementById("t-target").value) || 3,
      pages_per_keyword: Number(document.getElementById("t-pages").value) || 2,
      marketplace: document.getElementById("t-market").value.trim() || "US",
    });
    notice("追踪任务已创建", "ok");
    viewTracking();
  } catch (err) { notice(err.message, "bad"); }
}

window.trackToggle = async (id, status) => {
  try {
    await apiSend(`/api/tracking/tasks/${id}/status`, "POST", { status });
    notice(status === "paused" ? "已暂停" : "已恢复", "ok");
    viewTracking();
  } catch (err) { notice(err.message, "bad"); }
};

window.trackDelete = async (id) => {
  if (!confirm("确认删除该追踪任务？历史快照数据不受影响。")) return;
  try {
    await apiSend(`/api/tracking/tasks/${id}`, "DELETE");
    notice("已删除", "ok");
    viewTracking();
  } catch (err) { notice(err.message, "bad"); }
};

window.trackPreview = async (id) => {
  try {
    const r = await apiSend("/api/tracking/check", "POST", { execute: false, task_id: id });
    notice("检查完成（未联网）：" + summarizeCheck(r), "ok");
  } catch (err) { notice(err.message, "bad"); }
};

window.trackCollect = async (id) => {
  // 联网采集是危险操作：必须二次确认后才传 execute=true（Lead 硬要求）。
  if (!confirm(
    "⚠ 立即执行联网采集？\n\n会打开浏览器实际抓取 Amazon，受限频 / 被拦即停 / 72h 边界约束，可能耗时。\n\n确认继续？"
  )) return;
  notice("联网采集执行中…（串行，请稍候）", "ok");
  try {
    const r = await apiSend("/api/tracking/check", "POST", { execute: true, task_id: id });
    notice("采集完成：" + summarizeCheck(r), "ok");
    viewTracking();
  } catch (err) { notice(err.message, "bad"); }
};

function summarizeCheck(r) {
  if (!r) return "暂无返回内容";
  if (typeof r === "string") return r;
  const keys = ["checked", "due", "executed", "skipped", "collected", "completed", "message"];
  const parts = keys.filter((k) => r[k] != null && r[k] !== "").map((k) => `${k}=${r[k]}`);
  return parts.length ? parts.join(" · ") : JSON.stringify(r).slice(0, 140);
}

/* ---------- 表格辅助 ---------- */
/* 统一表格外壳：横向滚动容器（P0-2）。
   P2-3 技术债收敛：`tableHtml`=静态表、`renderSortableTable`=可排序/导出表，二者按职责分工，
   不强行合并两套不同用途的渲染；仅把三处重复的 `.table-wrap` 外壳收敛到本 helper。 */
function wrapTable(inner) {
  return `<div class="table-wrap">${inner}</div>`;
}

function tableHtml(headers, rows) {
  return wrapTable(`<table><thead><tr>${headers.map((h) => `<th>${h}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((r) => `<tr${r._click ? ` onclick="${r._click}"` : ""}>${
      r.cells.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`);
}
function truncate(s, n) { s = String(s ?? ""); return s.length > n ? s.slice(0, n) + "…" : s; }
function numOrNull(v) { const n = Number(v); return Number.isFinite(n) ? n : null; }

/* 客户端 CSV 导出：从已加载数据生成 CSV 并下载（带 BOM 供 Excel 正确识别中文）。
   纯前端，不依赖后端导出端点，导出所见即所得。 */
function exportCsv(filename, headers, rows) {
  const esc = (v) => {
    let s = v == null ? "" : String(v);
    if (/^[=+\-@\t\r]/.test(s)) s = "'" + s; // 防 CSV 公式注入（Excel 把 =+-@ 开头当公式）
    return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = [headers.map(esc).join(",")].concat(rows.map((r) => r.map(esc).join(",")));
  const blob = new Blob(["﻿" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click();
  document.body.removeChild(a); URL.revokeObjectURL(url);
}

/* 取某列用于 CSV 的原始值：优先 csv()，其次 sortVal()，再 item[key]。 */
function csvValue(col, item) {
  if (col.csv) return col.csv(item);
  if (col.sortVal) return col.sortVal(item);
  return item[col.key];
}

/* 通用可排序表格：点列头切换升/降序，数值列按数字排，可选行跳转、CSV 导出。
   columns: [{key,label,render?(item),sortVal?(item),csv?(item),numeric?,align?:'num'}]
   opts: {rowHash?(item)=>hash, defaultSort?:{key,dir}, exportName?:string} */
function renderSortableTable(container, columns, data, opts = {}) {
  const state = { key: null, dir: 1, ...(opts.defaultSort || {}) };
  function draw() {
    const rows = data.slice();
    if (state.key) {
      const col = columns.find((c) => c.key === state.key);
      if (col && col.sortable !== false) {
        rows.sort((a, b) => {
          let va = col.sortVal ? col.sortVal(a) : a[col.key];
          let vb = col.sortVal ? col.sortVal(b) : b[col.key];
          if (col.numeric) {
            va = numOrNull(va); vb = numOrNull(vb);
            va = va == null ? -Infinity : va; vb = vb == null ? -Infinity : vb;
          } else {
            va = String(va ?? "").toLowerCase(); vb = String(vb ?? "").toLowerCase();
          }
          return va < vb ? -state.dir : va > vb ? state.dir : 0;
        });
      }
    }
    const thead = columns.map((c) => {
      const arrow = state.key === c.key ? (state.dir > 0 ? " ▲" : " ▼") : "";
      const align = c.align === "num" ? " num" : c.align === "check" ? " check-cell" : "";
      if (c.sortable === false) return `<th class="${align.trim()}">${c.label}</th>`;
      return `<th data-key="${c.key}" class="sortable${align}">${c.label}${arrow}</th>`;
    }).join("");
    const tbody = rows.map((item) => {
      const tds = columns.map((c) => {
        const v = c.render ? c.render(item) : escapeHtml(item[c.key] ?? "—");
        const cls = c.align === "num" ? "num" : c.align === "check" ? "check-cell" : "";
        return `<td${cls ? ` class="${cls}"` : ""}>${v}</td>`;
      }).join("");
      const click = opts.rowHash ? ` onclick="navHash('${opts.rowHash(item)}')"` : "";
      return `<tr${click}>${tds}</tr>`;
    }).join("");
    const bar = opts.exportName
      ? `<div class="table-bar"><button class="btn btn-sm" data-csv="1">导出 CSV</button></div>` : "";
    container.innerHTML = bar + wrapTable(`<table><thead><tr>${thead}</tr></thead><tbody>${tbody}</tbody></table>`);
    container.querySelectorAll("th.sortable").forEach((th) => {
      th.onclick = () => {
        const k = th.dataset.key;
        if (state.key === k) state.dir = -state.dir;
        else { state.key = k; state.dir = 1; }
        draw();
      };
    });
    if (opts.exportName) {
      container.querySelector("[data-csv]").onclick = () => {
        const exportColumns = columns.filter((c) => c.csv !== false);
        const headers = exportColumns.map((c) => c.label);
        const csvRows = data.map((it) => exportColumns.map((c) => csvValue(c, it)));
        exportCsv(`${opts.exportName}.csv`, headers, csvRows);
      };
    }
    if (opts.onRowClick) {
      container.querySelectorAll("tbody tr").forEach((tr, i) => {
        tr.style.cursor = "pointer";
        tr.onclick = () => { if (hasTextSelection()) return; opts.onRowClick(rows[i]); };
      });
    }
    if (opts.onDraw) opts.onDraw(container, rows);
  }
  draw();
}

/* ---------- 视图：本地 HTML 入库（阶段1 单元①） ---------- */
async function viewImport() {
  loading();
  const listing = await api("/api/import/html/files");
  const files = (listing && listing.files) || [];
  content.innerHTML = `
    <div class="panel">
      <h2>本地 HTML 入库</h2>
      <p style="color:var(--text-dim);font-size:12.5px;line-height:1.6">
        从 <code>2_1/html/</code> 目录选择已保存的 Amazon 搜索结果页 HTML，先<b>预览入库</b>有效候选与过滤原因，确认后<b>写入数据库（MySQL）</b>。
        仅允许该目录下文件（白名单）；预览只解析不写入数据库，写入需二次确认。
      </p>
      ${files.length ? `
      <div class="filters" style="margin-top:10px">
        <input id="imp-keyword" placeholder="关键词（可选，标注来源）" style="width:200px" />
        <button class="btn" id="imp-preview">预览入库</button>
        <button class="btn btn-warn" id="imp-commit">确认写入</button>
      </div>
      <div id="imp-files" class="file-list file-tree">${renderHtmlFileTree(files)}</div>`
      : `<div class="state"><code>2_1/html/</code> 目录暂无 HTML 文件。先把保存的搜索页 HTML 放进该目录再来。</div>`}
    </div>
    <div id="imp-result"></div>`;
  if (!files.length) return;
  document.getElementById("imp-preview").onclick = () => runImport(false);
  document.getElementById("imp-commit").onclick = () => runImport(true);
}

function renderHtmlFileTree(files) {
  const root = createTreeNode();
  files.forEach((file, index) => addTreeFile(root, file, index));
  return renderTreeNode(root, 0);
}

function createTreeNode() {
  return { dirs: new Map(), files: [] };
}

function addTreeFile(root, file, index) {
  const parts = String(file || "").split("/").filter(Boolean);
  let node = root;
  while (parts.length > 1) {
    const dir = parts.shift();
    if (!node.dirs.has(dir)) node.dirs.set(dir, createTreeNode());
    node = node.dirs.get(dir);
  }
  node.files.push({ path: file, name: parts[0] || file, index });
}

function renderTreeNode(node, depth) {
  const folders = [...node.dirs.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([name, child]) => `
    <details class="tree-folder" open style="--depth:${depth}">
      <summary><span class="tree-folder-name">${escapeHtml(name)}</span><span class="tree-count">${countTreeFiles(child)}</span></summary>
      ${renderTreeNode(child, depth + 1)}
    </details>`).join("");
  const files = node.files.sort((a, b) => a.name.localeCompare(b.name)).map((file) => `
    <label class="file-row tree-file" style="--depth:${depth}">
      <input type="checkbox" class="imp-file" value="${escapeHtml(file.path)}"${file.index === 0 ? " checked" : ""}/>
      <span class="tree-file-name">${escapeHtml(file.name)}</span>
      ${file.path !== file.name ? `<span class="tree-path">${escapeHtml(file.path)}</span>` : ""}
    </label>`).join("");
  return folders + files;
}

function countTreeFiles(node) {
  let count = node.files.length;
  node.dirs.forEach((child) => { count += countTreeFiles(child); });
  return count;
}

async function runImport(commit) {
  const files = [...document.querySelectorAll(".imp-file:checked")].map((c) => c.value);
  const box = document.getElementById("imp-result");
  if (!files.length) { notice("请至少勾选一个 HTML 文件", "bad"); return; }
  if (commit && !confirm(`确认把 ${files.length} 个 HTML 文件解析并写入数据库（MySQL）？\n建议先「预览入库」确认有效候选与过滤情况。`)) return;
  box.innerHTML = `<div class="state"><div class="spinner"></div>${commit ? "写入中…" : "预览入库中…"}</div>`;
  const keyword = document.getElementById("imp-keyword").value.trim() || null;
  try {
    const r = await apiSend(`/api/import/html/${commit ? "commit" : "preview"}`, "POST", { files, keyword });
    box.innerHTML = renderImportResult(r, commit);
    notice(commit ? "写入完成" : "预览入库完成", "ok");
  } catch (err) {
    box.innerHTML = `<div class="state error">⚠ ${escapeHtml(err.message)}</div>`;
  }
}

function renderImportResult(r, commit) {
  return renderSummaryPanel(commit ? "写入结果" : "预览入库结果", r);
}

/* 通用：把 controller 返回的中文摘要 dict 渲染成面板（过滤原因单列、列表值用顿号拼接）。 */
function renderSummaryPanel(title, r) {
  const main = Object.entries(r || {}).filter(([k]) => k !== "过滤原因")
    .map(([k, v]) => `<div class="row"><span>${escapeHtml(k)}</span> <b>${escapeHtml(Array.isArray(v) ? (v.join("、") || "—") : v)}</b></div>`).join("");
  const reasonsObj = (r && r["过滤原因"]) || {};
  const reasons = Object.keys(reasonsObj).length
    ? `<h3 style="margin:14px 0 6px;font-size:13px">过滤原因</h3>` +
      Object.entries(reasonsObj).map(([k, v]) => `<div class="row"><span>${escapeHtml(k)}</span> <b>${escapeHtml(v)}</b></div>`).join("")
    : "";
  return `<div class="panel"><h2>${escapeHtml(title)}</h2>${main}${reasons}</div>`;
}

/* ---------- 视图：评论导入（阶段1 单元②） ---------- */
async function viewReviewImport() {
  loading();
  const listing = await api("/api/import/reviews/files");
  const imp = (listing && listing.import_files) || [];
  const htmls = (listing && listing.html_files) || [];
  content.innerHTML = `
    <div class="panel">
      <h2>导入评论文件（CSV / JSON）</h2>
      <p style="color:var(--text-dim);font-size:12.5px;line-height:1.6">
        从 <code>2_1/reviews/</code> 选择评论 CSV/JSON，先<b>预览导入</b>再写入数据库（MySQL），按内容哈希去重并刷新评论洞察。文件缺 ASIN 时可填默认 ASIN。
      </p>
      ${imp.length ? `
      <div class="filters" style="margin-top:10px">
        <select id="rv-file" class="sel">${imp.map((f) => `<option value="${escapeHtml(f)}">${escapeHtml(f)}</option>`).join("")}</select>
        <input id="rv-asin" placeholder="默认 ASIN（可选）" style="width:160px" />
        <button class="btn" id="rv-preview">预览导入</button>
        <button class="btn btn-warn" id="rv-commit">确认导入</button>
      </div>` : `<div class="state"><code>2_1/reviews/</code> 目录暂无 CSV/JSON 文件。可先在下方解析评论 HTML 生成，或放入文件。</div>`}
      <div id="rv-imp-result"></div>
    </div>
    <div class="panel">
      <h2>解析评论 HTML → 导入文件</h2>
      <p style="color:var(--text-dim);font-size:12.5px;line-height:1.6">
        把手动保存到 <code>2_1/reviews/</code> 的 Amazon 评论页 HTML <b>离线解析</b>为导入用 CSV/JSON（仅本地解析、不联网）。生成后点右上「刷新」即可在上方导入。
      </p>
      ${htmls.length ? `
      <div class="filters" style="margin-top:10px">
        <input id="rh-asin" placeholder="默认 ASIN（可选）" style="width:160px" />
        <select id="rh-fmt" class="sel"><option value="csv">CSV</option><option value="json">JSON</option></select>
        <button class="btn" id="rh-parse">解析</button>
      </div>
      <div class="file-list">${htmls.map((f, i) => `<label class="file-row"><input type="checkbox" class="rh-file" value="${escapeHtml(f)}"${i === 0 ? " checked" : ""}/> ${escapeHtml(f)}</label>`).join("")}</div>`
      : `<div class="state"><code>2_1/reviews/</code> 目录暂无评论 HTML。</div>`}
      <div id="rv-html-result"></div>
    </div>`;
  if (imp.length) {
    document.getElementById("rv-preview").onclick = () => runReviewImport(false);
    document.getElementById("rv-commit").onclick = () => runReviewImport(true);
  }
  if (htmls.length) document.getElementById("rh-parse").onclick = runReviewParse;
}

async function runReviewImport(commit) {
  const file = document.getElementById("rv-file").value;
  const box = document.getElementById("rv-imp-result");
  if (!file) { notice("请选择评论文件", "bad"); return; }
  if (commit && !confirm(`确认把「${file}」的评论写入数据库（MySQL）？建议先预览导入，确认有效候选与过滤情况。`)) return;
  box.innerHTML = `<div class="state"><div class="spinner"></div>${commit ? "导入中…" : "预览导入中…"}</div>`;
  const default_asin = document.getElementById("rv-asin").value.trim() || null;
  try {
    const r = await apiSend(`/api/import/reviews/${commit ? "commit" : "preview"}`, "POST", { file, default_asin });
    box.innerHTML = renderSummaryPanel(commit ? "导入结果" : "预览导入结果", r);
    notice(commit ? "评论已导入" : "预览导入完成", "ok");
  } catch (err) { box.innerHTML = `<div class="state error">⚠ ${escapeHtml(err.message)}</div>`; }
}

async function runReviewParse() {
  const files = [...document.querySelectorAll(".rh-file:checked")].map((c) => c.value);
  const box = document.getElementById("rv-html-result");
  if (!files.length) { notice("请勾选评论 HTML", "bad"); return; }
  box.innerHTML = `<div class="state"><div class="spinner"></div>解析中…</div>`;
  const default_asin = document.getElementById("rh-asin").value.trim() || null;
  const output_format = document.getElementById("rh-fmt").value;
  try {
    const r = await apiSend("/api/import/reviews/parse-html", "POST", { files, default_asin, output_format });
    box.innerHTML = renderSummaryPanel("解析结果", r);
    notice("解析完成，点右上「刷新」后可导入生成文件", "ok");
  } catch (err) { box.innerHTML = `<div class="state error">⚠ ${escapeHtml(err.message)}</div>`; }
}

/* ---------- 视图：分析仓库手动同步（阶段1 单元③） ---------- */
function viewWarehouseSync() {
  content.innerHTML = `
    <div class="panel">
      <h2>分析仓库手动同步</h2>
      <p style="color:var(--text-dim);font-size:12.5px;line-height:1.6">
        从 MySQL 主库把分析数据同步到本地 DuckDB/Parquet 仓库（推荐榜 / 关键词机会 / 趋势等重聚合查询优先读仓库）。
        <b>单向</b> MySQL → 仓库，<b>不反写主库</b>。数据量大时耗时较长，请勿重复点击或刷新。
      </p>
      <div class="filters" style="margin-top:10px">
        <button class="btn btn-warn" id="wh-sync">开始同步</button>
      </div>
      <div id="wh-result"></div>
    </div>`;
  document.getElementById("wh-sync").onclick = runWarehouseSync;
}

async function runWarehouseSync() {
  if (!confirm("开始把 MySQL 分析数据同步到 DuckDB/Parquet 仓库？\n这会重建分析副本（不影响 MySQL 主库），数据量大时耗时较长。")) return;
  const box = document.getElementById("wh-result");
  const btn = document.getElementById("wh-sync");
  btn.disabled = true;
  box.innerHTML = `<div class="state"><div class="spinner"></div>同步中…（请耐心等待，勿刷新）</div>`;
  try {
    const r = await apiSend("/api/warehouse/sync", "POST");
    box.innerHTML = renderWarehouseResult(r);
    notice("仓库同步完成", "ok");
  } catch (err) {
    box.innerHTML = `<div class="state error">⚠ ${escapeHtml(err.message)}</div>`;
  } finally {
    btn.disabled = false;
  }
}

function renderWarehouseResult(r) {
  const top = ["总行数", "DuckDB", "Parquet"].filter((k) => r && r[k] != null)
    .map((k) => `<div class="row"><span>${k}</span> <b>${escapeHtml(r[k])}</b></div>`).join("");
  const tables = (r && r["同步表"]) || {};
  const tableRows = Object.entries(tables)
    .map(([t, n]) => `<div class="row"><span>${escapeHtml(t)}</span> <b>${escapeHtml(n)}</b></div>`).join("");
  return `<div class="panel"><h2>同步结果</h2>${top}${tableRows ? `<h3 style="margin:14px 0 6px;font-size:13px">各表行数</h3>${tableRows}` : ""}</div>`;
}

/* ---------- 视图：设置（S4 文案复核，透出 services.settings 三层护栏） ---------- */
async function viewSettings() {
  loading();
  let data;
  try { data = await api("/api/settings"); }
  catch (err) { return errorState(err); }
  renderSettings((data && data.settings) || {});
}

const SETTINGS_FIELD_LABELS = {
  "ui.theme": "界面主题",
  "ui.language": "界面语言",
  "ui.default_page_size": "默认每页条数",
  "ui.table_density": "表格密度",
  "ui.confirm_before_write": "写入或联网前确认",
  "collection.page_delay_min_seconds": "页间最短等待",
  "collection.page_delay_max_seconds": "页间最长等待",
  "collection.pages_per_keyword": "默认采集页数",
  "collection.max_pages_per_keyword": "单关键词页数上限",
  "collection.tracking_min_interval_hours": "同一关键词自动追踪间隔",
  "collection.snapshot_expire_days": "快照过期提醒",
  "collection.max_runtime_minutes": "单轮采集最长时长",
  "analytics.opportunity_highlight_score": "蓝海高亮线",
  "analytics.custom_scoring.enabled": "个人自定义评分",
  "analytics.custom_scoring.weights.demand": "需求权重",
  "analytics.custom_scoring.weights.competition": "竞争权重",
  "analytics.custom_scoring.weights.rating": "评分权重",
  "analytics.custom_scoring.weights.price": "价格权重",
  "analytics.custom_scoring.weights.rank": "排名权重",
  "analytics.custom_scoring.weights.growth": "增长权重",
};

function settingsFieldLabel(path) {
  return SETTINGS_FIELD_LABELS[path] || path;
}

function renderSettings(s) {
  const ui = s.ui || {};
  const col = s.collection || {};
  const an = s.analytics || {};
  const w = (an.custom_scoring || {}).weights || {};
  const opt = (v, cur, label) => `<option value="${v}"${v === cur ? " selected" : ""}>${label}</option>`;
  content.innerHTML = `
    <div class="panel">
      <h2>显示与偏好 <span class="layer-tag layer-a">A 自由设置</span></h2>
      <div class="set-grid">
        <label>界面主题<select id="set-theme" class="sel">${opt("system", ui.theme, "跟随系统")}${opt("light", ui.theme, "浅色")}${opt("dark", ui.theme, "深色")}</select></label>
        <label>界面语言<select id="set-lang" class="sel" disabled>${opt("zh-CN", ui.language || "zh-CN", "简体中文")}</select></label>
        <label>默认每页条数<input id="set-page-size" type="number" min="5" max="200" value="${escapeHtml(ui.default_page_size)}" /></label>
        <label>表格密度<select id="set-density" class="sel">${opt("comfortable", ui.table_density, "宽松")}${opt("compact", ui.table_density, "紧凑")}</select></label>
        <label class="set-check"><input id="set-confirm" type="checkbox"${ui.confirm_before_write ? " checked" : ""}/> 写入数据或联网前二次确认</label>
        <label>蓝海高亮线（机会分达到此值时标记）<input id="set-highlight" type="number" min="0" max="100" value="${escapeHtml(an.opportunity_highlight_score)}" /></label>
        <label>单轮采集最长时长（分钟）<input id="set-runtime" type="number" min="1" value="${escapeHtml(col.max_runtime_minutes)}" /><span class="set-hint">自由设置，不设服务端上限</span></label>
      </div>
    </div>
    <div class="panel">
      <h2>采集安全 <span class="layer-tag layer-b">B 安全边界</span></h2>
      <p class="set-banner-b">以下设置可在安全范围内调整；保存和执行时仍以服务端校验为准。超出边界的值会自动调整，并在保存结果中说明。</p>
      <div class="set-grid">
        <label>页间最短等待（秒）<input id="set-delay-min" type="number" min="5" value="${escapeHtml(col.page_delay_min_seconds)}" /><span class="set-hint">服务端安全下限 5 秒</span></label>
        <label>页间最长等待（秒）<input id="set-delay-max" type="number" min="5" value="${escapeHtml(col.page_delay_max_seconds)}" /><span class="set-hint">不得小于最短等待</span></label>
        <label>默认采集页数<input id="set-pages" type="number" min="1" max="7" value="${escapeHtml(col.pages_per_keyword)}" /><span class="set-hint">1-7 页</span></label>
        <label>单关键词页数上限<input id="set-maxpages" type="number" min="1" max="7" value="${escapeHtml(col.max_pages_per_keyword)}" /><span class="set-hint">服务端安全上限 7 页</span></label>
        <label>同一关键词自动追踪间隔（小时）<input id="set-track-hours" type="number" min="72" value="${escapeHtml(col.tracking_min_interval_hours)}" /><span class="set-hint">服务端安全下限 72 小时</span></label>
        <label>快照过期提醒（天）<input id="set-expire-days" type="number" min="1" value="${escapeHtml(col.snapshot_expire_days)}" /><span class="set-hint">最少 1 天</span></label>
      </div>
    </div>
    <div class="panel">
      <h2>自定义评分 <span class="layer-tag layer-c">C 独立口径</span></h2>
      <p class="set-banner-c"><b>标准评分口径固定不变</b>。这里生成的是个人自定义评分，只用于本地并列参考，<b>不替换</b>商品的标准综合得分，也不能与其他用户的自定义分横向比较。</p>
      <div class="set-grid">
        <label class="set-check"><input id="set-custom-enabled" type="checkbox"${(an.custom_scoring || {}).enabled ? " checked" : ""}/> 启用个人自定义评分</label>
      </div>
      <div class="set-weights">
        ${["demand", "competition", "rating", "price", "rank", "growth"].map((k) =>
          `<label>${({demand:"需求权重",competition:"竞争权重",rating:"评分权重",price:"价格权重",rank:"排名权重",growth:"增长权重"})[k]}<input id="set-w-${k}" type="number" min="0" max="1" step="0.05" value="${escapeHtml(w[k])}" /></label>`
        ).join("")}
      </div>
      <div class="set-hint">权重范围 0-1；增长权重在趋势第二步接入真实值前建议保持 0。</div>
    </div>
    <div class="set-actions">
      <button class="btn" id="set-save">保存设置</button>
      <button class="btn" id="set-reload">放弃改动并重新载入</button>
    </div>
    <div id="set-result"></div>`;
  document.getElementById("set-save").onclick = saveSettings;
  document.getElementById("set-reload").onclick = viewSettings;
}

function collectSettingsPatch() {
  const num = (id) => { const v = document.getElementById(id).value.trim(); return v === "" ? null : Number(v); };
  const bool = (id) => document.getElementById(id).checked;
  const sel = (id) => document.getElementById(id).value;
  return {
    ui: {
      theme: sel("set-theme"),
      default_page_size: num("set-page-size"),
      table_density: sel("set-density"),
      confirm_before_write: bool("set-confirm"),
    },
    collection: {
      page_delay_min_seconds: num("set-delay-min"),
      page_delay_max_seconds: num("set-delay-max"),
      pages_per_keyword: num("set-pages"),
      max_pages_per_keyword: num("set-maxpages"),
      tracking_min_interval_hours: num("set-track-hours"),
      snapshot_expire_days: num("set-expire-days"),
      max_runtime_minutes: num("set-runtime"),
    },
    analytics: {
      opportunity_highlight_score: num("set-highlight"),
      custom_scoring: {
        enabled: bool("set-custom-enabled"),
        weights: Object.fromEntries(
          ["demand", "competition", "rating", "price", "rank", "growth"].map((k) => [k, num(`set-w-${k}`)])
        ),
      },
    },
  };
}

async function saveSettings() {
  const box = document.getElementById("set-result");
  const btn = document.getElementById("set-save");
  btn.disabled = true;
  box.innerHTML = `<div class="state"><div class="spinner"></div>保存中…</div>`;
  try {
    const r = await apiSend("/api/settings", "POST", { patch: collectSettingsPatch() });
    const changes = (r && r.changes) || [];
    renderSettings(r.settings || {});
    const after = document.getElementById("set-result");
    if (changes.length) {
      after.innerHTML = `<div class="panel"><h2>已保存（${changes.length} 项已按安全边界调整）</h2>${
        changes.map((c) => `<div class="row"><span>${escapeHtml(settingsFieldLabel(c.path))}</span> <b>${escapeHtml(c.original)} → ${escapeHtml(c.clamped)}</b></div><div class="set-hint">${escapeHtml(c.reason)}</div>`).join("")
      }</div>`;
      notice(`已保存；${changes.length} 项已按安全边界调整`, "ok");
    } else {
      notice("设置已保存", "ok");
    }
  } catch (err) {
    box.innerHTML = `<div class="state error">⚠ ${escapeHtml(err.message)}</div>`;
    notice(err.message, "bad");
  } finally {
    const b = document.getElementById("set-save"); if (b) b.disabled = false;
  }
}

/* ---------- 路由 ---------- */
const routes = [
  { re: /^#\/crawl$/, title: "手动采集", run: viewCrawl },
  { re: /^#\/recommendations$/, title: "推荐 · 蓝海", run: viewRecommendations },
  { re: /^#\/products$/, title: "商品池 · 筛选", run: viewProducts },
  { re: /^#\/compare(?:\/(.+))?$/, title: "商品对比", run: (m) => viewCompare(m[1] ? decodeURIComponent(m[1]) : "") },
  { re: /^#\/product\/(.+)$/, title: "商品详情 · 趋势", run: (m) => viewProductDetail(decodeURIComponent(m[1])) },
  { re: /^#\/keywords$/, title: "关键词机会", run: viewKeywords },
  { re: /^#\/reviews$/, title: "评论痛点", run: viewReviews },
  { re: /^#\/agent$/, title: "AI 助手", run: viewAgent },
  { re: /^#\/tasks$/, title: "任务中心", run: viewTasks },
  { re: /^#\/tracking$/, title: "关键词追踪", run: viewTracking },
  { re: /^#\/import$/, title: "本地 HTML 入库", run: viewImport },
  { re: /^#\/import-reviews$/, title: "评论导入", run: viewReviewImport },
  { re: /^#\/warehouse$/, title: "仓库同步", run: viewWarehouseSync },
  { re: /^#\/settings$/, title: "设置", run: viewSettings },
];

async function router() {
  const hash = location.hash || "#/recommendations";
  const route = routes.find((r) => r.re.test(hash)) || routes[0];
  const m = hash.match(route.re);
  viewTitle.textContent = route.title;
  document.title = `${route.title} · 选品助手`;
  document.querySelectorAll(".nav-item").forEach((a) => {
    a.classList.toggle("active", hash.startsWith(a.getAttribute("href")));
  });
  try {
    await route.run(m);
  } catch (err) {
    errorState(err);
  }
}

/* ---------- 健康指示 ---------- */
async function pingHealth() {
  const dot = document.getElementById("api-status");
  const txt = document.getElementById("api-status-text");
  try {
    await api("/api/health");
    dot.className = "dot dot-ok"; txt.textContent = "API 正常";
  } catch {
    dot.className = "dot dot-bad"; txt.textContent = "API 不可达";
  }
}

let shellControlsReady = false;
function initShellControls() {
  if (shellControlsReady) return;
  shellControlsReady = true;
  const toggle = document.getElementById("sidebar-toggle");
  const reopen = document.getElementById("sidebar-reopen");
  const backdrop = document.getElementById("sidebar-backdrop");
  if (toggle) toggle.onclick = toggleSidebar;
  if (reopen) reopen.onclick = toggleSidebar;
  if (backdrop) backdrop.onclick = () => setSidebarOpen(false);
  document.querySelectorAll(".nav-item").forEach((a) => {
    a.addEventListener("click", closeSidebarIfNarrow);
  });
  window.addEventListener("resize", syncSidebarForViewport);
  document.addEventListener("keydown", handleGlobalShortcuts);
  syncSidebarForViewport();
}

document.getElementById("refresh-btn").onclick = router;
document.getElementById("back-btn").onclick = () => history.back();
window.addEventListener("hashchange", router);
window.addEventListener("DOMContentLoaded", () => { initShellControls(); pingHealth(); router(); });
if (document.readyState !== "loading") { initShellControls(); pingHealth(); router(); }
