"use strict";
/* D2 前端骨架：hash 路由 + fetch 封装 + 只读页。
   后端契约：所有 /api/* 返回 {ok, data, message}（见 decisions/2026-06-19-前端架构转Web.md §6）。
   写入数据 / 联网类端点（建追踪任务、触发采集）后续由 D1 补，本骨架先做只读页。 */

const content = document.getElementById("content");
const viewTitle = document.getElementById("view-title");

/* ---------- API 封装 ---------- */
let viewRequestEpoch = 0;

class StaleViewError extends Error {
  constructor() {
    super("页面已切换，忽略旧请求结果");
    this.name = "StaleViewError";
  }
}

async function api(path, { allowStale = false } = {}) {
  const requestEpoch = viewRequestEpoch;
  const resp = await fetch(path);
  let payload;
  try {
    payload = await resp.json();
  } catch {
    throw new Error(`后端响应格式异常（HTTP ${resp.status}）`);
  }
  if (!allowStale && requestEpoch !== viewRequestEpoch) throw new StaleViewError();
  if (!payload.ok) throw apiRequestError(payload, resp.status);
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
  if (!payload.ok) throw apiRequestError(payload, resp.status);
  return payload.data;
}

function apiRequestError(payload, status) {
  const err = new Error(apiErrorMessage(payload, status));
  err.name = "ApiRequestError";
  err.status = status;
  err.code = payload?.code || "";
  err.data = payload?.data || null;
  return err;
}

function apiErrorMessage(payload, status) {
  if (payload && payload.message) return payload.message;
  const detail = payload && payload.detail;
  if (Array.isArray(detail) && detail.length) {
    return detail.map((item) => {
      const loc = Array.isArray(item.loc) ? item.loc.filter((x) => x !== "body").join(".") : "";
      return `${loc ? loc + "：" : ""}${item.msg || "参数错误"}`;
    }).join("；");
  }
  if (typeof detail === "string") return detail;
  return `请求未成功（HTTP ${status}）`;
}

async function openCurrentPageInBrowser() {
  const path = `${location.pathname}${location.search}${location.hash || ""}`;
  try {
    await apiSend("/api/desktop/open-web", "POST", { path });
    notice("已在系统浏览器打开当前页面", "ok");
  } catch (err) {
    try {
      window.open(location.href, "_blank", "noopener");
      notice("已尝试在浏览器打开当前页面", "ok");
    } catch {
      notice(err.message, "bad");
    }
  }
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

let browserDriverDialogFinish = null;

function closeBrowserDriverDialog(approved = false) {
  if (browserDriverDialogFinish) {
    browserDriverDialogFinish(approved);
    return true;
  }
  const modal = document.getElementById("browser-driver-dialog");
  if (modal) modal.remove();
  return !!modal;
}

function confirmBrowserDriverDownload(info = {}) {
  closeBrowserDriverDialog(false);
  const browser = info.browser || {};
  const versions = Array.isArray(info.detected_driver_versions)
    ? info.detected_driver_versions.filter(Boolean)
    : [];
  const modal = document.createElement("div");
  modal.id = "browser-driver-dialog";
  modal.className = "modal-backdrop";
  modal.innerHTML = `
    <section class="modal-panel browser-driver-modal" role="dialog" aria-modal="true" aria-label="下载匹配浏览器驱动">
      <div class="modal-head"><h2>需要匹配的浏览器驱动</h2></div>
      <div class="browser-driver-body">
        <p>已找到本机 Google Chrome，但当前没有可用的匹配驱动。是否现在下载？</p>
        <div class="browser-driver-facts">
          <div><span>Chrome 版本</span><b>${escapeHtml(browser.version || "未知")}</b></div>
          <div><span>需要的驱动</span><b>${escapeHtml(info.expected_driver || "匹配版本")}</b></div>
          ${versions.length ? `<div><span>已发现旧驱动</span><b>${escapeHtml(versions.join("、"))}</b></div>` : ""}
        </div>
        <p class="hint">驱动将从 Google Chrome for Testing 下载到当前用户缓存，仅用于控制本机 Chrome；不会替换 Chrome，也不会读取日常浏览器配置。</p>
      </div>
      <div class="browser-driver-actions">
        <button class="btn" type="button" data-driver-cancel>暂不下载</button>
        <button class="btn btn-warn" type="button" data-driver-install>下载匹配驱动</button>
      </div>
    </section>`;
  document.body.appendChild(modal);

  return new Promise((resolve) => {
    let settled = false;
    const onKeydown = (event) => {
      if (event.key === "Escape") finish(false);
    };
    const finish = (approved) => {
      if (settled) return;
      settled = true;
      document.removeEventListener("keydown", onKeydown);
      modal.remove();
      browserDriverDialogFinish = null;
      resolve(!!approved);
    };
    browserDriverDialogFinish = finish;
    modal.querySelector("[data-driver-cancel]").onclick = () => finish(false);
    modal.querySelector("[data-driver-install]").onclick = () => finish(true);
    modal.addEventListener("click", (event) => {
      if (event.target === modal) finish(false);
    });
    document.addEventListener("keydown", onKeydown);
    modal.querySelector("[data-driver-install]").focus();
  });
}

async function repairBrowserDriver(err) {
  if (err?.code !== "chrome_driver_required" || err?.data?.can_download_driver === false) {
    throw err;
  }
  const approved = await confirmBrowserDriverDownload(err.data || {});
  if (!approved) {
    const cancelled = new Error("未下载浏览器驱动，本次操作已取消。");
    cancelled.code = "chrome_driver_download_cancelled";
    throw cancelled;
  }
  notice("正在下载匹配的 ChromeDriver，请稍候…", "ok");
  const installed = await apiSend("/api/crawl/browser-driver/install", "POST", { confirmed: true });
  notice(`ChromeDriver ${installed?.driver?.version || ""} 已就绪`, "ok");
  return installed;
}

async function ensureBrowserRuntimeReady() {
  try {
    return await api("/api/crawl/browser-runtime");
  } catch (err) {
    await repairBrowserDriver(err);
    return api("/api/crawl/browser-runtime");
  }
}

async function runBrowserAction(action) {
  await ensureBrowserRuntimeReady();
  try {
    return await action();
  } catch (err) {
    if (err?.code !== "chrome_driver_required") throw err;
    await repairBrowserDriver(err);
    return action();
  }
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
  if (err?.name === "StaleViewError") return;
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

const AMAZON_MARKETPLACE_DOMAINS = {
  US: "amazon.com", CA: "amazon.ca", MX: "amazon.com.mx", BR: "amazon.com.br",
  UK: "amazon.co.uk", GB: "amazon.co.uk", DE: "amazon.de", FR: "amazon.fr",
  IT: "amazon.it", ES: "amazon.es", NL: "amazon.nl", SE: "amazon.se",
  PL: "amazon.pl", BE: "amazon.com.be", JP: "amazon.co.jp", AU: "amazon.com.au",
  IN: "amazon.in", SG: "amazon.sg", TR: "amazon.com.tr", SA: "amazon.sa",
  AE: "amazon.ae", EG: "amazon.eg",
};

function amazonProductHref(rowOrAsin) {
  const row = typeof rowOrAsin === "object" && rowOrAsin ? rowOrAsin : {};
  const asin = String(row.asin || rowOrAsin || "").trim().toUpperCase();
  const marketplace = String(row.marketplace || "US").trim().toUpperCase();
  let domain = AMAZON_MARKETPLACE_DOMAINS[marketplace] || AMAZON_MARKETPLACE_DOMAINS.US;
  try {
    const source = new URL(String(row.product_url || ""));
    const host = source.hostname.toLowerCase().replace(/^www\./, "");
    if (Object.values(AMAZON_MARKETPLACE_DOMAINS).includes(host)) domain = host;
  } catch { /* 缺少原链接时按站点生成标准链接。 */ }
  return `https://www.${domain}/dp/${encodeURIComponent(asin)}`;
}

function amazonProductLink(rowOrAsin, label, maxLength = null) {
  const row = typeof rowOrAsin === "object" && rowOrAsin ? rowOrAsin : {};
  const asin = String(row.asin || rowOrAsin || "").trim().toUpperCase();
  const fullLabel = String(label || asin || "—");
  if (!/^[A-Z0-9]{10}$/.test(asin)) return escapeHtml(maxLength ? truncate(fullLabel, maxLength) : fullLabel);
  const shown = maxLength ? truncate(fullLabel, maxLength) : fullLabel;
  const marketplace = String(row.marketplace || "US").trim().toUpperCase();
  const sourceUrl = String(row.product_url || "");
  const tooltip = `${fullLabel}\n按住 Ctrl 并双击，在系统默认浏览器打开 Amazon 商品页`;
  return `<a class="amazon-product-link" href="${escapeHtml(amazonProductHref(rowOrAsin))}"
    data-amazon-asin="${escapeHtml(asin)}" data-amazon-marketplace="${escapeHtml(marketplace)}"
    data-amazon-product-url="${escapeHtml(sourceUrl)}" title="${escapeHtml(tooltip)}"
    onclick="return window.amazonProductLinkClick(event)"
    ondblclick="window.amazonProductLinkDoubleClick(event)">${escapeHtml(shown)}</a>`;
}

window.amazonProductLinkClick = (event) => {
  event.preventDefault();
  event.stopPropagation();
  const entry = event.currentTarget.closest("[data-selectable-entry]");
  if (entry) selectInteractiveEntry(entry);
  return false;
};

window.amazonProductLinkDoubleClick = async (event) => {
  event.preventDefault();
  event.stopPropagation();
  const link = event.currentTarget;
  const entry = link.closest("[data-selectable-entry]");
  if (!event.ctrlKey) {
    if (entry) activateInteractiveEntry(entry, { ignoreTextSelection: true });
    return;
  }
  const body = {
    asin: link.dataset.amazonAsin,
    marketplace: link.dataset.amazonMarketplace || "US",
    product_url: link.dataset.amazonProductUrl || null,
  };
  try {
    await apiSend("/api/desktop/open-amazon-product", "POST", body);
    notice("已在系统默认浏览器打开 Amazon 商品页", "ok");
  } catch (err) {
    const fallback = link.href;
    if (fallback) window.open(fallback, "_blank", "noopener");
    else notice(err.message || "商品链接打开失败", "bad");
  }
};

const CLIENT_TRANSLATE_SOURCE_LANG = "en";
const CLIENT_TRANSLATE_TARGET_LANG = "zh";
const CLIENT_TRANSLATE_CACHE_KEY = "amazon2.clientTranslateCache.v1";
const CLIENT_TRANSLATE_MAX_NODES = 80;
const CLIENT_TRANSLATE_MAX_CHARS = 5000;
const CLIENT_TRANSLATE_SKIP_SELECTOR = [
  "button", "input", "textarea", "select", "option", "code", "pre",
  "script", "style", ".btn", ".chip", ".badge", ".pager", ".table-bar",
  ".filters", ".actions", ".agent-config-form", ".agent-composer",
  ".sidebar", ".topbar"
].join(",");

const clientTranslateState = {
  enabled: false,
  busy: false,
  translator: null,
  scheduled: null,
  observer: null,
  translatedNodes: new Set(),
  originalText: new WeakMap(),
  cache: loadClientTranslateCache(),
};

function loadClientTranslateCache() {
  try {
    const raw = localStorage.getItem(CLIENT_TRANSLATE_CACHE_KEY);
    const rows = raw ? JSON.parse(raw) : [];
    return new Map(Array.isArray(rows) ? rows : []);
  } catch {
    return new Map();
  }
}

function saveClientTranslateCache() {
  try {
    const rows = Array.from(clientTranslateState.cache.entries()).slice(-500);
    localStorage.setItem(CLIENT_TRANSLATE_CACHE_KEY, JSON.stringify(rows));
  } catch {
    // localStorage may be unavailable in embedded shells.
  }
}

function getClientTranslatorApi() {
  if (window.Translator && typeof window.Translator.create === "function") {
    return window.Translator;
  }
  return null;
}

async function ensureClientTranslator() {
  if (clientTranslateState.translator) return clientTranslateState.translator;
  const api = getClientTranslatorApi();
  if (!api) {
    throw new Error("当前浏览器不支持 Chrome 内置 Translator API，请用支持该 API 的桌面 Chrome 打开本页面。");
  }
  const opts = {
    sourceLanguage: CLIENT_TRANSLATE_SOURCE_LANG,
    targetLanguage: CLIENT_TRANSLATE_TARGET_LANG,
  };
  if (typeof api.availability === "function") {
    const availability = await api.availability(opts);
    if (availability === "unavailable") {
      throw new Error("当前 Chrome 暂不可用 en→zh 内置翻译模型。");
    }
    if (availability === "downloadable" || availability === "downloading") {
      notice("首次使用需要下载 Chrome 内置翻译模型，请稍等。");
    }
  }
  clientTranslateState.translator = await api.create({
    ...opts,
    monitor(monitor) {
      if (!monitor?.addEventListener) return;
      monitor.addEventListener("downloadprogress", (event) => {
        const percent = Math.round(Number(event.loaded || 0) * 100);
        notice(`Chrome 翻译模型下载中 ${percent}%`);
      });
    },
  });
  return clientTranslateState.translator;
}

function normalizeClientTranslateText(text) {
  return String(text || "").replace(/\s+/g, " ").trim();
}

function shouldClientTranslateText(text, parent) {
  const value = normalizeClientTranslateText(text);
  if (value.length < 12) return false;
  if (parent?.closest?.(CLIENT_TRANSLATE_SKIP_SELECTOR)) return false;
  if (/^https?:\/\//i.test(value)) return false;
  if (/^B[A-Z0-9]{9}$/i.test(value)) return false;
  if (!/[A-Za-z][A-Za-z'-]+/.test(value)) return false;
  const words = value.match(/[A-Za-z][A-Za-z'-]+/g) || [];
  if (words.length < 2) return false;
  return true;
}

function collectClientTranslateNodes(root = content) {
  const nodes = [];
  let chars = 0;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      if (!shouldClientTranslateText(node.nodeValue, node.parentElement)) {
        return NodeFilter.FILTER_REJECT;
      }
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  while (nodes.length < CLIENT_TRANSLATE_MAX_NODES) {
    const node = walker.nextNode();
    if (!node) break;
    const text = normalizeClientTranslateText(node.nodeValue);
    if (chars + text.length > CLIENT_TRANSLATE_MAX_CHARS) break;
    chars += text.length;
    nodes.push(node);
  }
  return nodes;
}

async function translateClientText(text) {
  const source = normalizeClientTranslateText(text);
  const key = `${CLIENT_TRANSLATE_SOURCE_LANG}|${CLIENT_TRANSLATE_TARGET_LANG}|${source}`;
  if (clientTranslateState.cache.has(key)) return clientTranslateState.cache.get(key);
  const translator = await ensureClientTranslator();
  const translated = await translator.translate(source);
  clientTranslateState.cache.set(key, translated);
  saveClientTranslateCache();
  return translated;
}

async function translateCurrentView() {
  if (!clientTranslateState.enabled || clientTranslateState.busy) return;
  clientTranslateState.busy = true;
  setClientTranslateButton("翻译中…", true);
  try {
    await ensureClientTranslator();
    const nodes = collectClientTranslateNodes();
    if (!nodes.length) {
      setClientTranslateButton("还原原文", false, true);
      return;
    }
    let changed = 0;
    for (const node of nodes) {
      if (!clientTranslateState.originalText.has(node)) {
        clientTranslateState.originalText.set(node, node.nodeValue);
      }
      const translated = await translateClientText(node.nodeValue);
      if (translated && translated !== node.nodeValue) {
        node.nodeValue = translated;
        clientTranslateState.translatedNodes.add(node);
        changed += 1;
      }
    }
    setClientTranslateButton("还原原文", false, true);
    if (changed) notice(`已前端翻译 ${changed} 段可见英文内容`);
  } catch (err) {
    clientTranslateState.enabled = false;
    stopClientTranslateObserver();
    setClientTranslateButton("译中文");
    notice(err.message || "前端翻译失败", "bad");
  } finally {
    clientTranslateState.busy = false;
  }
}

function restoreClientTranslations() {
  for (const node of clientTranslateState.translatedNodes) {
    if (node.isConnected && clientTranslateState.originalText.has(node)) {
      node.nodeValue = clientTranslateState.originalText.get(node);
    }
  }
  clientTranslateState.translatedNodes.clear();
}

function scheduleClientTranslate(delay = 160) {
  if (!clientTranslateState.enabled) return;
  clearTimeout(clientTranslateState.scheduled);
  clientTranslateState.scheduled = setTimeout(() => { translateCurrentView(); }, delay);
}

function startClientTranslateObserver() {
  if (clientTranslateState.observer) return;
  clientTranslateState.observer = new MutationObserver(() => {
    if (!clientTranslateState.busy) scheduleClientTranslate(260);
  });
  clientTranslateState.observer.observe(content, {
    childList: true,
    characterData: true,
    subtree: true,
  });
}

function stopClientTranslateObserver() {
  if (!clientTranslateState.observer) return;
  clientTranslateState.observer.disconnect();
  clientTranslateState.observer = null;
}

function setClientTranslateButton(label, disabled = false, active = false) {
  const btn = document.getElementById("client-translate-btn");
  if (!btn) return;
  btn.textContent = label;
  btn.disabled = disabled;
  btn.classList.toggle("btn-active", active);
}

function initClientTranslationControls() {
  const btn = document.getElementById("client-translate-btn");
  if (!btn) return;
  btn.onclick = async () => {
    if (clientTranslateState.enabled) {
      clientTranslateState.enabled = false;
      stopClientTranslateObserver();
      restoreClientTranslations();
      setClientTranslateButton("译中文");
      notice("已还原当前页面原文");
      return;
    }
    clientTranslateState.enabled = true;
    startClientTranslateObserver();
    await translateCurrentView();
  };
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

const INTERACTION_STATE_STORAGE_KEY = "amazon_interaction_state_v1";
let renderedRouteHash = null;
let detailEnteredFromInApp = false;

function readInteractionState() {
  try {
    const raw = sessionStorage.getItem(INTERACTION_STATE_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function writeInteractionState(state) {
  try {
    sessionStorage.setItem(INTERACTION_STATE_STORAGE_KEY, JSON.stringify(state));
  } catch {
    // 交互状态只提升体验；桌面壳禁用会话存储时不影响业务功能。
  }
}

function selectableEntryAttrs(hash, key = hash) {
  return ` data-selectable-entry="1" data-entry-key="${escapeHtml(String(key || hash || ""))}" data-nav-hash="${escapeHtml(String(hash || ""))}" tabindex="0" aria-selected="false"`;
}

function prepareSelectableEntry(element, { hash = "", key = "", activate = null } = {}) {
  if (!element) return;
  element.dataset.selectableEntry = "1";
  element.dataset.entryKey = String(key || hash || "");
  if (hash) element.dataset.navHash = String(hash);
  else delete element.dataset.navHash;
  element.tabIndex = 0;
  element.setAttribute("aria-selected", "false");
  element.title = element.title || "单击选中，双击进入";
  element._selectableActivate = typeof activate === "function" ? activate : null;
}

function selectableCommandTarget(target) {
  if (!(target instanceof Element)) return null;
  return target.closest("button,input,select,textarea,label,[contenteditable='true'],a:not(.amazon-product-link)");
}

function selectInteractiveEntry(entry, { focus = false, ignoreTextSelection = false } = {}) {
  if (!entry || (!ignoreTextSelection && hasTextSelection())) return false;
  const scope = location.hash || "#/recommendations";
  content.querySelectorAll("[data-selectable-entry].interactive-selected").forEach((item) => {
    item.classList.remove("interactive-selected");
    item.setAttribute("aria-selected", "false");
  });
  entry.classList.add("interactive-selected");
  entry.setAttribute("aria-selected", "true");
  if (focus) entry.focus({ preventScroll: true });
  const state = readInteractionState();
  state.selected = state.selected && typeof state.selected === "object" ? state.selected : {};
  state.selected[scope] = entry.dataset.entryKey || entry.dataset.navHash || "";
  writeInteractionState(state);
  return true;
}

function activateInteractiveEntry(entry, { ignoreTextSelection = false } = {}) {
  if (!entry || (!ignoreTextSelection && hasTextSelection())) return false;
  selectInteractiveEntry(entry, { ignoreTextSelection });
  if (typeof entry._selectableActivate === "function") {
    entry._selectableActivate();
    return true;
  }
  if (entry.dataset.keywordLibraryDetail) {
    window.keywordLibraryDetail?.(Number(entry.dataset.keywordLibraryDetail));
    return true;
  }
  const hash = entry.dataset.navHash;
  if (hash) {
    location.hash = hash;
    return true;
  }
  return false;
}

function restoreInteractiveSelection(root = content) {
  const state = readInteractionState();
  const selectedKey = state.selected?.[location.hash || "#/recommendations"];
  if (!selectedKey) return;
  root.querySelectorAll("[data-selectable-entry]").forEach((entry) => {
    const selected = entry.dataset.entryKey === selectedKey;
    entry.classList.toggle("interactive-selected", selected);
    entry.setAttribute("aria-selected", selected ? "true" : "false");
  });
}

function handleSelectableClick(event) {
  const entry = event.target instanceof Element ? event.target.closest("[data-selectable-entry]") : null;
  if (!entry || selectableCommandTarget(event.target)) return;
  selectInteractiveEntry(entry);
}

function handleSelectableDoubleClick(event) {
  const entry = event.target instanceof Element ? event.target.closest("[data-selectable-entry]") : null;
  if (!entry || selectableCommandTarget(event.target)) return;
  event.preventDefault();
  activateInteractiveEntry(entry, { ignoreTextSelection: true });
}

function handleSelectableKeydown(event) {
  const entry = event.target instanceof Element ? event.target.closest("[data-selectable-entry]") : null;
  if (!entry || event.target !== entry) return;
  if (event.key === " ") {
    event.preventDefault();
    selectInteractiveEntry(entry);
  } else if (event.key === "Enter") {
    event.preventDefault();
    activateInteractiveEntry(entry);
  }
}

function interactionScrollTop() {
  return window.matchMedia(SIDEBAR_NARROW_QUERY).matches ? window.scrollY : content.scrollTop;
}

function saveRouteScroll(hash) {
  if (!hash) return;
  const state = readInteractionState();
  state.scroll = state.scroll && typeof state.scroll === "object" ? state.scroll : {};
  state.scroll[hash] = interactionScrollTop();
  writeInteractionState(state);
}

function restoreRouteScroll(hash) {
  const state = readInteractionState();
  const top = Number(state.scroll?.[hash] || 0);
  requestAnimationFrame(() => requestAnimationFrame(() => {
    if (window.matchMedia(SIDEBAR_NARROW_QUERY).matches) window.scrollTo({ top, behavior: "auto" });
    else content.scrollTo({ top, behavior: "auto" });
  }));
}

function isProductDetailRoute(hash) {
  return /^#\/product\/[^/?]+/.test(String(hash || ""));
}

function rememberDetailOrigin(detailHash, originHash) {
  if (!isProductDetailRoute(detailHash) || !originHash || isProductDetailRoute(originHash)) return;
  const state = readInteractionState();
  state.detailOrigins = state.detailOrigins && typeof state.detailOrigins === "object" ? state.detailOrigins : {};
  state.detailOrigins[detailHash] = originHash;
  writeInteractionState(state);
}

function productDetailReturnTarget() {
  const hash = location.hash || "";
  const state = readInteractionState();
  const origin = state.detailOrigins?.[hash];
  return origin && !isProductDetailRoute(origin) ? origin : "#/products";
}

function routeTitleForHash(hash) {
  const route = routes.find((item) => item.re.test(hash));
  return route?.title || "上一页";
}

window.returnFromProductDetail = () => {
  const target = productDetailReturnTarget();
  if (detailEnteredFromInApp && history.length > 1) {
    history.back();
    return;
  }
  location.hash = target;
};

function productDetailHash(asin, scoreKeyword = "") {
  const base = `#/product/${encodeURIComponent(asin)}`;
  const keyword = String(scoreKeyword || "").trim();
  const encodedKeyword = encodeURIComponent(keyword).replace(/'/g, "%27");
  return keyword ? `${base}?score_keyword=${encodedKeyword}` : base;
}

const VIEW_STATE_STORAGE_KEY = "amazon_view_state_v2";
const persistedStateMeta = new WeakMap();

function readViewStateBag() {
  try {
    const raw = localStorage.getItem(VIEW_STATE_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function writeViewStateBag(bag) {
  try {
    localStorage.setItem(VIEW_STATE_STORAGE_KEY, JSON.stringify(bag));
  } catch {
    // 嵌入式桌面壳或隐私模式下可能不可写；状态保存失败不影响主流程。
  }
}

function hydrateStateValue(defaultValue, savedValue) {
  if (savedValue == null) return defaultValue;
  if (typeof defaultValue === "number") {
    const n = Number(savedValue);
    return Number.isFinite(n) ? n : defaultValue;
  }
  if (typeof defaultValue === "boolean") return savedValue === true || savedValue === "true";
  if (typeof defaultValue === "string") return String(savedValue);
  return savedValue;
}

function createPersistentState(key, defaults, fields = Object.keys(defaults)) {
  const saved = readViewStateBag()[key] || {};
  const state = { ...defaults };
  fields.forEach((field) => {
    if (Object.prototype.hasOwnProperty.call(saved, field)) {
      state[field] = hydrateStateValue(defaults[field], saved[field]);
    }
  });
  persistedStateMeta.set(state, { key, fields });
  return state;
}

function persistState(state) {
  const meta = persistedStateMeta.get(state);
  if (!meta) return;
  const bag = readViewStateBag();
  const payload = {};
  meta.fields.forEach((field) => {
    const value = state[field];
    if (value == null || typeof value === "string" || typeof value === "number" || typeof value === "boolean" || Array.isArray(value)) {
      payload[field] = value;
    }
  });
  bag[meta.key] = payload;
  writeViewStateBag(bag);
}

function bindStateInputs(ids, readFn) {
  ids.forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    const save = () => {
      if (typeof readFn === "function") readFn();
    };
    el.addEventListener("change", save);
    el.addEventListener("input", save);
  });
}

const productCompareSelection = new Set();
let productCompareRows = new Map();
const recommendationState = createPersistentState("recommendations", {
  limit: 20,
  offset: 0,
  sortBy: "total_score",
  sortDir: "desc",
  blueOnly: false,
  keyword: "",
  minScore: "",
  maxScore: "",
  minPrice: "",
  maxPrice: "",
  minRating: "",
  maxRating: "",
  minReviews: "",
  maxReviews: "",
  minBought: "",
  maxBought: "",
  minRank: "",
  maxRank: "",
  dealStatus: "all",
  sizeStatus: "all",
  moreOpen: false,
});
const productState = createPersistentState("products", {
  limit: 50,
  offset: 0,
  keyword: "",
  minScore: "",
  maxScore: "",
  minPrice: "",
  maxPrice: "",
  minRating: "",
  maxRating: "",
  minReviews: "",
  maxReviews: "",
  minBought: "",
  maxBought: "",
  minRank: "",
  maxRank: "",
  dealStatus: "all",
  sizeStatus: "all",
  moreOpen: false,
  sortBy: "total_score",
  sortDir: "desc",
});
const metricState = createPersistentState("metrics", { limit: 50, offset: 0, keyword: "" });
const metricEvidenceState = createPersistentState("metricEvidence", {
  limit: 20,
  offset: 0,
  fileStatus: "all",
  gapPriority: "all",
  projectId: "",
  projectName: "",
});
const metricEvidenceSelected = new Set();
let metricEvidenceFileRows = new Map();
let metricEvidencePayload = null;
let metricEvidenceBrowserAction = "";
let metricEvidenceCollectingAsin = "";
const keywordState = createPersistentState("keywords", { limit: 50, offset: 0, keyword: "", minProducts: "", groupMode: "tail", primaryOpen: false, sortBy: "opportunity_score", sortDir: "desc" });
const keywordProductState = createPersistentState("keywordProducts", {
  keyword: "",
  scope: "current",
  limit: 25,
  offset: 0,
  sortBy: "total_score",
  sortDir: "desc",
  currentCount: 0,
  historicalCount: 0,
  currentSnapshotAt: "",
  previousSnapshotAt: "",
});
const keywordLibraryRows = new Map();
const keywordLibrarySelected = new Set();
const keywordLibraryTreeCollapsed = new Set();
const keywordLibraryState = createPersistentState("keywordLibrary", {
  limit: 50,
  offset: 0,
  keyword: "",
  marketplace: "US",
  snapshotFilter: "all",
  trackingFilter: "all",
  sourceFilter: "all",
  viewMode: "table",
  sortBy: "latest_snapshot_at",
  sortDir: "desc",
});
const keywordWorkshopIdeaRows = new Map();
const keywordWorkshopState = createPersistentState("keywordWorkshop", {
  limit: 50,
  offset: 0,
  keyword: "",
  status: "candidate",
  source: "all",
  lastRunId: null,
  runId: null,
  runsLimit: 8,
  sortBy: "idea_score",
  sortDir: "desc",
});
keywordWorkshopState.selected = new Set();
const researchProjectState = createPersistentState("researchProjects", {
  limit: 25,
  offset: 0,
  marketplace: "US",
  status: "all",
  keyword: "",
  sortBy: "updated_at",
  sortDir: "desc",
  createOpen: false,
  draftName: "",
  draftObjective: "",
  draftStrategy: "",
});
const researchReviewQueueState = createPersistentState("researchReviewQueue", {
  limit: 25,
  offset: 0,
  marketplace: "US",
  status: "all",
  attention: "all",
  monitoring: "all",
  keyword: "",
});
let researchReviewQueueCurrent = null;
let researchObservationPlanCurrent = null;
const researchProjectDetailState = createPersistentState("researchProjectDetail", {
  projectId: null,
  asins: "",
  productRole: "candidate",
  keywords: "",
  keywordRole: "candidate",
  noteType: "observation",
  noteContent: "",
  decisionSummary: "",
});
let researchProjectCurrent = null;
const researchAssociationState = createPersistentState("researchAssociations", {
  lastProjectId: "",
  productRole: "candidate",
  keywordRole: "candidate",
  nicheRole: "candidate",
});
const researchDecisionReportState = createPersistentState("researchDecisionReport", {
  projectId: null,
  asOf: "",
});
let researchDecisionReportCurrent = null;
let researchDecisionBaselineComparison = null;
const researchReportVersionState = createPersistentState("researchReportVersions", {
  projectId: null,
  fromVersion: null,
  toVersion: null,
});
let researchReportFreezeDialogState = null;
const marketNicheState = createPersistentState("marketNiches", {
  limit: 25,
  offset: 0,
  marketplace: "US",
  status: "all",
  keyword: "",
  sortBy: "updated_at",
  sortDir: "desc",
  createOpen: false,
  draftName: "",
  draftDefinition: "",
  draftCategoryScope: "",
});
const marketNicheDetailState = createPersistentState("marketNicheDetail", {
  nicheId: null,
  editName: "",
  editDefinition: "",
  editCategoryScope: "",
  editStatus: "draft",
  keywords: "",
  keywordRole: "core",
  asins: "",
  productRole: "benchmark",
  projectId: "",
  projectRole: "candidate",
});
let marketNicheCurrent = null;
let marketNicheAvailableProjects = [];
const marketNicheRows = new Map();
const competitiveGraphState = createPersistentState("competitiveGraph", {
  nicheId: null,
  snapshotId: null,
  marketplace: "US",
  keyword: "",
  listLimit: 25,
  listOffset: 0,
  productLimit: 100,
  minShared: 1,
  focusAsin: "",
  activeView: "relations",
});
let competitiveGraphCurrent = null;
const scoringReplayState = createPersistentState("scoringReplay", {
  mode: "replay",
  limit: 50,
  offset: 0,
  marketplace: "US",
  keyword: "",
  strategy: "balanced",
  recommendation: "all",
  minConfidence: "",
  maxRisk: "",
  sortBy: "opportunity_score",
  sortDir: "desc",
  selectedKey: "",
});
let scoringReplayCurrent = null;
const domainModelState = createPersistentState("domainModels", {
  marketplace: "US",
  status: "all",
  selectedId: "",
  selectedVersionId: "",
  draftProfileId: "",
  draftJson: "",
  validationSearch: "",
  validationEvidence: "all",
  validationSignal: "all",
  validationMinDelta: "",
  validationSortBy: "abs_delta",
  validationSortDir: "desc",
  validationSampleLimit: 50,
  limit: 20,
  offset: 0,
});
let domainModelCatalog = null;
let domainModelPage = null;
let domainModelDetail = null;
let domainModelNiches = [];
let domainModelValidationPage = null;
const productDomainScoreState = createPersistentState("productDomainScore", {
  selectedByMarketplaceJson: "{}",
});
const scoringCalibrationState = createPersistentState("scoringCalibration", {
  marketplace: "US",
  keyword: "",
  strategy: "balanced",
  samplePerBucket: 2,
  exportScope: "reviewed",
  sampleSeed: "baseline",
  excludedAsinsJson: "[]",
  batchNumber: 1,
  batchHistoryJson: "[]",
  selectedBatchId: "",
  activeView: "current",
  selectedKey: "",
  selectedReviewKey: "",
  reviewJson: "{}",
  lastExportPath: "",
  lastExportAt: "",
});
let scoringCalibrationCurrent = null;
let scoringCalibrationExportApiAvailable = null;
let scoringCalibrationExportDirectory = "";
let scoringCalibrationExportedReviews = [];
let scoringCalibrationReviewArchiveWarnings = [];
let scoringCalibrationReviewArchiveFileCount = 0;
let scoringCalibrationReviewArchiveServerTotal = 0;
let scoringCalibrationReplayExpectation = null;
const crawlState = createPersistentState("crawl", { keyword: "", pages: 1, queueMode: false });
const trackingFormState = createPersistentState("trackingForm", { keyword: "", targetSnapshots: 3, pagesPerKeyword: 2, marketplace: "US" });
const htmlImportState = createPersistentState("htmlImport", { keyword: "", selectedFiles: [] });
let htmlImportPreview = null;
const reviewImportState = createPersistentState("reviewImport", {
  file: "",
  defaultAsin: "",
  htmlDefaultAsin: "",
  outputFormat: "csv",
  selectedHtmlFiles: [],
});
const taskState = createPersistentState("tasks", {
  limit: 25,
  offset: 0,
  keyword: "",
  jobType: "all",
  status: "all",
});
const taskErrorRows = new Map();
const taskRows = new Map();
const taskSelected = new Set();
const trackingTaskRows = new Map();
const trackingSelected = new Set();
const agentState = {
  conversationId: null,
  messages: [],
  pendingAction: null,
  sending: false,
  config: null,
  lastPageContext: null,
  contextSuggestions: [],
};
let agentSuggestionRegistry = [];
const AGENT_CONTEXT_STORAGE_KEY = "amazon_agent_recent_business_context";
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
  const nearbyPages = [];
  for (let pageIndex = Math.max(1, pageNo - 2); pageIndex <= Math.min(totalPages, pageNo + 2); pageIndex += 1) {
    nearbyPages.push(pageIndex);
  }
  return `
    <div class="pager" id="${id}">
      <span class="pager-summary">第 ${pageNo} / ${totalPages} 页</span>
      <div class="pager-nav" role="group" aria-label="分页导航">
        <button class="btn btn-sm btn-icon pager-icon" data-page="first" title="首页" aria-label="首页"${pageNo <= 1 ? " disabled" : ""}>«</button>
        <button class="btn btn-sm btn-icon pager-icon" data-page="prev" title="上一页" aria-label="上一页"${pageNo <= 1 ? " disabled" : ""}>‹</button>
        ${nearbyPages.map((number) => `<button class="btn btn-sm pager-number${number === pageNo ? " btn-active" : ""}" data-page-index="${number}" aria-label="第 ${number} 页"${number === pageNo ? ' aria-current="page"' : ""}>${number}</button>`).join("")}
        <button class="btn btn-sm btn-icon pager-icon" data-page="next" title="下一页" aria-label="下一页"${pageNo >= totalPages ? " disabled" : ""}>›</button>
        <button class="btn btn-sm btn-icon pager-icon" data-page="last" title="末页" aria-label="末页"${pageNo >= totalPages ? " disabled" : ""}>»</button>
      </div>
      <label class="pager-jump-label">
        <span>跳至</span>
      <input class="pager-jump" data-page-jump type="number" min="1" max="${totalPages}" value="${pageNo}" aria-label="页码" />
      </label>
      <button class="btn btn-sm" data-page="go">确定</button>
      <select class="sel sel-sm" data-page-size aria-label="每页条数">
        ${allSizes.map((size) => `<option value="${size}"${size === limit ? " selected" : ""}>每页 ${size}</option>`).join("")}
      </select>
    </div>`;
}

function scrollMainContentToTop() {
  if (window.matchMedia(SIDEBAR_NARROW_QUERY).matches) {
    window.scrollTo({ top: 0, behavior: "smooth" });
    return;
  }
  content.scrollTo({ top: 0, behavior: "smooth" });
}

function bindPager(id, state, page, loadFn) {
  const el = document.getElementById(id);
  if (!el) return;
  state.limit = page.limit;
  state.offset = page.offset;
  const total = Math.max(0, Number(page.total) || 0);
  const maxOffset = Math.max(0, (Math.ceil(total / state.limit) - 1) * state.limit);
  if (total > 0 && state.offset > maxOffset) {
    state.offset = maxOffset;
    persistState(state);
    loadFn();
    return;
  }
  persistState(state);
  const first = el.querySelector('[data-page="first"]');
  const prev = el.querySelector('[data-page="prev"]');
  const next = el.querySelector('[data-page="next"]');
  const last = el.querySelector('[data-page="last"]');
  const go = el.querySelector('[data-page="go"]');
  const jump = el.querySelector("[data-page-jump]");
  const pageSize = el.querySelector("[data-page-size]");
  const totalPages = Math.max(1, Math.ceil(total / state.limit));
  const loadPage = (pageNo) => {
    const normalizedPage = Math.max(1, Math.min(Number(pageNo) || 1, totalPages));
    state.offset = (normalizedPage - 1) * state.limit;
    persistState(state);
    scrollMainContentToTop();
    loadFn();
  };
  const jumpToPage = () => {
    const pageNo = Math.max(1, Math.min(Number(jump?.value) || 1, totalPages));
    loadPage(pageNo);
  };
  if (first) first.onclick = () => loadPage(1);
  if (prev) prev.onclick = () => {
    loadPage(Math.floor(state.offset / state.limit));
  };
  if (next) next.onclick = () => {
    loadPage(Math.floor(state.offset / state.limit) + 2);
  };
  if (last) last.onclick = () => loadPage(totalPages);
  el.querySelectorAll("[data-page-index]").forEach((button) => {
    button.onclick = () => loadPage(Number(button.dataset.pageIndex));
  });
  if (go) go.onclick = jumpToPage;
  if (jump) jump.onkeydown = (event) => {
    if (event.key === "Enter") jumpToPage();
  };
  if (pageSize) pageSize.onchange = () => {
    state.limit = Number(pageSize.value) || state.limit;
    state.offset = 0;
    persistState(state);
    scrollMainContentToTop();
    loadFn();
  };
}

const CATALOG_RANGE_FILTERS = [
  { key: "Score", slug: "score", label: "综合得分", min: 0, max: 100, step: 1 },
  { key: "Price", slug: "price", label: "价格（美元）", min: 0, step: 0.01 },
  { key: "Rating", slug: "rating", label: "评分", min: 0, max: 5, step: 0.1, advanced: true },
  { key: "Reviews", slug: "reviews", label: "评论数", min: 0, step: 1, advanced: true },
  { key: "Bought", slug: "bought", label: "近月购买量", min: 0, step: 1, advanced: true },
  { key: "Rank", slug: "rank", label: "自然序位估算", min: 1, step: 1, advanced: true },
];
const CATALOG_FILTER_STATE_FIELDS = [
  "keyword",
  ...CATALOG_RANGE_FILTERS.flatMap((item) => [`min${item.key}`, `max${item.key}`]),
  "dealStatus",
  "sizeStatus",
];

function catalogRangeFilterMarkup(prefix, state, item) {
  const minKey = `min${item.key}`;
  const maxKey = `max${item.key}`;
  const constraints = [
    item.min != null ? `min="${item.min}"` : "",
    item.max != null ? `max="${item.max}"` : "",
    `step="${item.step}"`,
  ].filter(Boolean).join(" ");
  return `
    <fieldset class="filter-range">
      <legend>${escapeHtml(item.label)}</legend>
      <div class="filter-range-inputs">
        <input id="${prefix}-min-${item.slug}" type="number" ${constraints} placeholder="最低" value="${escapeHtml(state[minKey])}" aria-label="${escapeHtml(item.label)}最低值" />
        <span aria-hidden="true">至</span>
        <input id="${prefix}-max-${item.slug}" type="number" ${constraints} placeholder="最高" value="${escapeHtml(state[maxKey])}" aria-label="${escapeHtml(item.label)}最高值" />
      </div>
    </fieldset>`;
}

function catalogFilterPanel(prefix, state, { keywordLabel = "标题 / ASIN / 关键词" } = {}) {
  const basicRanges = CATALOG_RANGE_FILTERS.filter((item) => !item.advanced)
    .map((item) => catalogRangeFilterMarkup(prefix, state, item)).join("");
  const advancedRanges = CATALOG_RANGE_FILTERS.filter((item) => item.advanced)
    .map((item) => catalogRangeFilterMarkup(prefix, state, item)).join("");
  return `
    <section class="catalog-filter-panel" aria-label="商品筛选条件">
      <div class="catalog-filter-grid catalog-filter-grid-main">
        <label class="filter-field filter-search">
          <span>${escapeHtml(keywordLabel)}</span>
          <input id="${prefix}-keyword" value="${escapeHtml(state.keyword)}" placeholder="输入后筛选" autocomplete="off" />
        </label>
        ${basicRanges}
        <div class="filter-actions">
          <button class="btn" id="${prefix}-apply" type="button">筛选</button>
          <button class="btn" id="${prefix}-reset" type="button">重置</button>
        </div>
      </div>
      <details class="catalog-filter-more" id="${prefix}-more"${state.moreOpen ? " open" : ""}>
        <summary>更多筛选 <span class="filter-active-count" id="${prefix}-active-count"></span></summary>
        <div class="catalog-filter-grid catalog-filter-grid-more">
          ${advancedRanges}
          <label class="filter-field">
            <span>促销状态</span>
            <select class="sel" id="${prefix}-deal-status">
              <option value="all">全部</option>
              <option value="deal">有促销</option>
              <option value="regular">无促销</option>
            </select>
          </label>
          <label class="filter-field">
            <span>尺寸完整性</span>
            <select class="sel" id="${prefix}-size-status">
              <option value="all">全部</option>
              <option value="known">已采集</option>
              <option value="missing">未采集</option>
            </select>
          </label>
        </div>
      </details>
    </section>`;
}

function readCatalogFiltersFromDom(prefix, state) {
  const keyword = document.getElementById(`${prefix}-keyword`);
  if (!keyword) return;
  state.keyword = keyword.value.trim();
  CATALOG_RANGE_FILTERS.forEach((item) => {
    state[`min${item.key}`] = document.getElementById(`${prefix}-min-${item.slug}`)?.value.trim() || "";
    state[`max${item.key}`] = document.getElementById(`${prefix}-max-${item.slug}`)?.value.trim() || "";
  });
  state.dealStatus = document.getElementById(`${prefix}-deal-status`)?.value || "all";
  state.sizeStatus = document.getElementById(`${prefix}-size-status`)?.value || "all";
  state.moreOpen = document.getElementById(`${prefix}-more`)?.open || false;
  persistState(state);
  updateCatalogFilterCount(prefix, state);
}

function setCatalogFilterSelectValues(prefix, state) {
  const deal = document.getElementById(`${prefix}-deal-status`);
  const size = document.getElementById(`${prefix}-size-status`);
  if (deal) deal.value = state.dealStatus;
  if (size) size.value = state.sizeStatus;
  updateCatalogFilterCount(prefix, state);
}

function resetCatalogFilters(prefix, state) {
  CATALOG_FILTER_STATE_FIELDS.forEach((field) => {
    state[field] = field === "dealStatus" || field === "sizeStatus" ? "all" : "";
  });
  state.moreOpen = false;
  state.offset = 0;
  persistState(state);
  document.getElementById(`${prefix}-keyword`).value = "";
  CATALOG_RANGE_FILTERS.forEach((item) => {
    document.getElementById(`${prefix}-min-${item.slug}`).value = "";
    document.getElementById(`${prefix}-max-${item.slug}`).value = "";
  });
  document.getElementById(`${prefix}-deal-status`).value = "all";
  document.getElementById(`${prefix}-size-status`).value = "all";
  const details = document.getElementById(`${prefix}-more`);
  if (details) details.open = false;
  updateCatalogFilterCount(prefix, state);
}

function catalogActiveFilterCount(state) {
  return CATALOG_FILTER_STATE_FIELDS.reduce((count, field) => {
    const value = state[field];
    if (field === "dealStatus" || field === "sizeStatus") return count + (value && value !== "all" ? 1 : 0);
    return count + (String(value || "").trim() ? 1 : 0);
  }, 0);
}

function updateCatalogFilterCount(prefix, state) {
  const badge = document.getElementById(`${prefix}-active-count`);
  if (!badge) return;
  const count = catalogActiveFilterCount(state);
  badge.textContent = count ? `已启用 ${count} 项` : "未启用";
  badge.classList.toggle("filter-active-count-on", count > 0);
}

function validateCatalogFilterRanges(prefix, state) {
  for (const item of CATALOG_RANGE_FILTERS) {
    const minValue = state[`min${item.key}`];
    const maxValue = state[`max${item.key}`];
    if (minValue !== "" && maxValue !== "" && Number(minValue) > Number(maxValue)) {
      notice(`${item.label}的最低值不能高于最高值`, "bad");
      document.getElementById(`${prefix}-min-${item.slug}`)?.focus();
      return false;
    }
  }
  return true;
}

function bindCatalogFilterInputs(prefix, state, applyFn, resetFn) {
  const ids = [`${prefix}-keyword`];
  CATALOG_RANGE_FILTERS.forEach((item) => {
    ids.push(`${prefix}-min-${item.slug}`, `${prefix}-max-${item.slug}`);
  });
  ids.push(`${prefix}-deal-status`, `${prefix}-size-status`);
  bindStateInputs(ids, () => {
    readCatalogFiltersFromDom(prefix, state);
    state.offset = 0;
    persistState(state);
  });
  ids.forEach((id) => {
    document.getElementById(id)?.addEventListener("keydown", (event) => {
      if (event.key === "Enter") applyFn();
    });
  });
  const details = document.getElementById(`${prefix}-more`);
  if (details) details.addEventListener("toggle", () => {
    state.moreOpen = details.open;
    persistState(state);
  });
  document.getElementById(`${prefix}-apply`).onclick = applyFn;
  document.getElementById(`${prefix}-reset`).onclick = resetFn;
}

function appendCatalogFilters(query, state) {
  const queryMap = {
    keyword: "keyword",
    minScore: "min_score",
    maxScore: "max_score",
    minPrice: "min_price",
    maxPrice: "max_price",
    minRating: "min_rating",
    maxRating: "max_rating",
    minReviews: "min_reviews",
    maxReviews: "max_reviews",
    minBought: "min_bought",
    maxBought: "max_bought",
    minRank: "min_rank",
    maxRank: "max_rank",
  };
  Object.entries(queryMap).forEach(([stateKey, queryKey]) => {
    const value = String(state[stateKey] || "").trim();
    if (value) query.set(queryKey, value);
  });
  if (state.dealStatus && state.dealStatus !== "all") query.set("deal_status", state.dealStatus);
  if (state.sizeStatus && state.sizeStatus !== "all") query.set("size_status", state.sizeStatus);
}

function catalogFilterContext(state) {
  return {
    keyword: state.keyword,
    min_score: state.minScore,
    max_score: state.maxScore,
    min_price: state.minPrice,
    max_price: state.maxPrice,
    min_rating: state.minRating,
    max_rating: state.maxRating,
    min_reviews: state.minReviews,
    max_reviews: state.maxReviews,
    min_bought: state.minBought,
    max_bought: state.maxBought,
    min_rank: state.minRank,
    max_rank: state.maxRank,
    deal_status: state.dealStatus,
    size_status: state.sizeStatus,
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
    if (closeKeywordIdeaDialog()) {
      event.preventDefault();
      return;
    }
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
    ${catalogFilterPanel("rec-f", recommendationState)}
    <div class="table-toolbar">
      <div class="filters compact">
        <label class="filter-field filter-field-inline">
          <span>排序指标</span>
          <select id="rec-sort" class="sel">
            <option value="total_score">综合得分</option>
            <option value="growth_score">增长分（占位）</option>
            <option value="price">价格</option>
            <option value="rating">评分</option>
            <option value="review_count">评论数</option>
            <option value="monthly_bought">近月购买</option>
            <option value="organic_rank">自然序位估算</option>
          </select>
        </label>
        <label class="filter-field filter-field-inline">
          <span>排序方向</span>
          <select id="rec-dir" class="sel">
            <option value="desc">降序</option>
            <option value="asc">升序</option>
          </select>
        </label>
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
  setCatalogFilterSelectValues("rec-f", recommendationState);
  document.getElementById("rec-sort").value = recommendationState.sortBy;
  document.getElementById("rec-dir").value = recommendationState.sortDir;
  document.getElementById("rec-blue").checked = recommendationState.blueOnly;
  const applyFilters = () => {
    readCatalogFiltersFromDom("rec-f", recommendationState);
    if (!validateCatalogFilterRanges("rec-f", recommendationState)) return;
    if (recommendationState.blueOnly && recommendationState.maxScore !== "" && Number(recommendationState.maxScore) < 70) {
      notice("“只看蓝海”与最高得分低于 70 冲突", "bad");
      document.getElementById("rec-f-max-score")?.focus();
      return;
    }
    recommendationState.offset = 0;
    persistState(recommendationState);
    loadRecommendations();
  };
  bindCatalogFilterInputs("rec-f", recommendationState, applyFilters, () => {
    resetCatalogFilters("rec-f", recommendationState);
    recommendationState.blueOnly = false;
    document.getElementById("rec-blue").checked = false;
    persistState(recommendationState);
    loadRecommendations();
  });
  document.getElementById("rec-sort").onchange = () => {
    recommendationState.sortBy = document.getElementById("rec-sort").value;
    recommendationState.offset = 0;
    persistState(recommendationState);
    loadRecommendations();
  };
  document.getElementById("rec-dir").onchange = () => {
    recommendationState.sortDir = document.getElementById("rec-dir").value;
    recommendationState.offset = 0;
    persistState(recommendationState);
    loadRecommendations();
  };
  document.getElementById("rec-blue").onchange = () => {
    recommendationState.blueOnly = document.getElementById("rec-blue").checked;
    recommendationState.offset = 0;
    persistState(recommendationState);
    applyFilters();
  };
  await loadRecommendations();
}

async function loadRecommendations() {
  persistState(recommendationState);
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
  appendCatalogFilters(q, recommendationState);
  if (recommendationState.blueOnly) {
    const explicitMin = Number(recommendationState.minScore);
    q.set("min_score", String(recommendationState.minScore !== "" && Number.isFinite(explicitMin) ? Math.max(70, explicitMin) : 70));
  }
  try {
    const page = normalizePage(await api(`/api/recommendations?${q.toString()}`), recommendationState.limit);
    const rows = page.rows;
    const activeCount = catalogActiveFilterCount(recommendationState) + (recommendationState.blueOnly ? 1 : 0);
    meta.textContent = pageSummary(page, "商品×关键词推荐上下文")
      + (activeCount ? ` · 已启用 ${activeCount} 项筛选` : "")
      + " · 同一 ASIN 在不同关键词下会分别展示";
    if (!rows.length) {
      box.innerHTML = `<div class="state">暂无匹配推荐。<button class="btn btn-sm state-action" id="rec-empty-reset" type="button">清空筛选</button></div>`;
      document.getElementById("rec-empty-reset").onclick = () => document.getElementById("rec-f-reset").click();
      pager.innerHTML = renderPager("rec-pager", page, [20, 50, 100]);
      bindPager("rec-pager", recommendationState, page, loadRecommendations);
      return;
    }
    box.innerHTML = `<div class="cards" role="listbox" aria-label="推荐商品">${rows.map((r) => {
      const hash = productDetailHash(r.asin, r.keyword);
      return `
      <div class="card" role="option"${selectableEntryAttrs(hash, `${r.asin}|${r.keyword || ""}`)}>
        <h3>${amazonProductLink(r, displayTitle(r, r.asin), 86)}</h3>
        <div class="row"><span>综合得分</span> <b>${scoreBadge(r.total_score)}</b></div>
        <div class="row"><span>评分关键词</span> <b>${escapeHtml(r.keyword || "旧数据未标注")}</b></div>
        <div class="row" title="旧版模型以中性值 50 按 15% 权重计入综合分；尚未使用真实趋势信号"><span>增长分（中性占位）</span> <b>${fmt.num(r.growth_score, 0)}</b></div>
        <div class="row"><span>价格</span> <b>${fmt.money(r.price)}</b></div>
        <div class="row"><span>尺寸/规格</span> <b>${escapeHtml(truncate(fmt.text(r.product_size), 42))}</b></div>
        <div class="row"><span>评分 / 评论</span> <b>${fmt.num(r.rating)} · ${fmt.int(r.review_count)}</b></div>
        <div class="row"><span>近月购买</span> <b>${fmt.int(r.monthly_bought)}</b></div>
        <div class="row"><span>自然序位估算 / 促销</span> <b>${fmt.int(r.organic_rank)} · ${isDeal(r.is_deal) ? "是" : "否"}</b></div>
      </div>`;
    }).join("")}</div>`;
    restoreInteractiveSelection(box);
    pager.innerHTML = renderPager("rec-pager", page, [20, 50, 100]);
    bindPager("rec-pager", recommendationState, page, loadRecommendations);
    if (csv) csv.disabled = false;
    document.getElementById("rec-csv").onclick = () => {
      const headers = ["ASIN", "标题", "评分关键词", "综合得分", "增长分(中性占位)", "价格", "尺寸/规格", "评分", "评论数", "近月购买"];
      const data = rows.map((r) => [r.asin, displayTitle(r, r.asin), r.keyword, r.total_score, r.growth_score, r.price, r.product_size, r.rating, r.review_count, r.monthly_bought]);
    exportCsv("推荐蓝海.csv", headers, data);
  };
  } catch (err) {
    box.innerHTML = `<div class="state error">⚠ ${escapeHtml(err.message)}</div>`;
  }
}

/* ---------- 视图：商品池 ---------- */
async function viewProducts() {
  content.innerHTML = `
    ${catalogFilterPanel("prod-f", productState)}
    <div class="table-toolbar">
      <div id="prod-meta" class="result-meta"></div>
      <div class="actions">
        <span id="prod-selected" class="selected-count">已选 0 个</span>
        <button class="btn btn-sm" id="prod-compare" disabled>对比选中</button>
        <button class="btn btn-sm" id="prod-add-project" disabled>加入研究项目</button>
        <button class="btn btn-sm" id="prod-clear">清空选择</button>
      </div>
    </div>
    <div id="prod-table"></div>
    <div id="prod-pager-wrap"></div>`;
  setCatalogFilterSelectValues("prod-f", productState);
  const applyFilters = () => {
    readCatalogFiltersFromDom("prod-f", productState);
    if (!validateCatalogFilterRanges("prod-f", productState)) return;
    loadProducts(true);
  };
  bindCatalogFilterInputs("prod-f", productState, applyFilters, () => {
    resetCatalogFilters("prod-f", productState);
    productCompareSelection.clear();
    loadProducts();
  });
  document.getElementById("prod-compare").onclick = compareSelectedProducts;
  document.getElementById("prod-add-project").onclick = addSelectedProductsToResearchProject;
  document.getElementById("prod-clear").onclick = () => {
    productCompareSelection.clear();
    updateProductCompareBar();
    document.querySelectorAll(".prod-check").forEach((c) => { c.checked = false; });
    rememberAgentBusinessContext();
  };
  await loadProducts();
}

function readProductFiltersFromDom() {
  readCatalogFiltersFromDom("prod-f", productState);
}

async function loadProducts(resetPage = false) {
  if (resetPage) {
    productState.offset = 0;
    readProductFiltersFromDom();
  }
  persistState(productState);
  const box = document.getElementById("prod-table");
  const meta = document.getElementById("prod-meta");
  const pager = document.getElementById("prod-pager-wrap");
  box.innerHTML = `<div class="state"><div class="spinner"></div>加载中…</div>`;
  meta.textContent = "";
  pager.innerHTML = "";
  const q = new URLSearchParams({
    limit: String(productState.limit),
    offset: String(productState.offset),
    sort_by: productState.sortBy,
    sort_dir: productState.sortDir,
  });
  appendCatalogFilters(q, productState);
  try {
    const page = normalizePage(await api(`/api/products?${q.toString()}`), productState.limit);
    syncRemoteSortState(productState, page);
    const rows = page.rows;
    const activeCount = catalogActiveFilterCount(productState);
    meta.textContent = pageSummary(page, "商品")
      + (activeCount ? ` · 已启用 ${activeCount} 项筛选` : "")
      + " · 列头排序作用于全部结果 · 勾选 2-5 个商品后对比";
    if (!rows.length) {
      productCompareRows = new Map();
      updateProductCompareBar();
      box.innerHTML = `<div class="state">暂无匹配商品。<button class="btn btn-sm state-action" id="prod-empty-reset" type="button">清空筛选</button></div>`;
      document.getElementById("prod-empty-reset").onclick = () => document.getElementById("prod-f-reset").click();
      pager.innerHTML = renderPager("prod-pager", page, [20, 50, 100]);
      bindPager("prod-pager", productState, page, loadProducts);
      return;
    }
    productCompareRows = new Map(rows.map((r) => [String(r.asin || ""), r]));
    renderSortableTable(box, [
      { key: "_select", label: "", sortable: false, csv: false, align: "check", render: (r) => productCompareCheckbox(r) },
      { key: "title", label: "标题", render: (r) => amazonProductLink(r, displayTitle(r, r.asin), 60), sortVal: (r) => displayTitle(r, r.asin) },
      { key: "total_score", label: "得分", align: "num", numeric: true, render: (r) => scoreBadge(r.total_score), sortVal: (r) => r.total_score },
      { key: "price", label: "价格", align: "num", numeric: true, render: (r) => fmt.money(r.price), sortVal: (r) => r.price },
      { key: "product_size", label: "尺寸/规格", render: (r) => escapeHtml(truncate(fmt.text(r.product_size), 28)), sortVal: (r) => r.product_size || "" },
      { key: "rating", label: "评分", align: "num", numeric: true, render: (r) => fmt.num(r.rating), sortVal: (r) => r.rating },
      { key: "review_count", label: "评论", align: "num", numeric: true, render: (r) => fmt.int(r.review_count), sortVal: (r) => r.review_count },
      { key: "monthly_bought", label: "近月购买", align: "num", numeric: true, render: (r) => fmt.int(r.monthly_bought), sortVal: (r) => r.monthly_bought },
      { key: "organic_rank", label: "序位估算", align: "num", numeric: true, render: (r) => fmt.int(r.organic_rank), sortVal: (r) => r.organic_rank },
      { key: "is_deal", label: "促销", render: (r) => isDeal(r.is_deal) ? "是" : "否", sortVal: (r) => isDeal(r.is_deal) ? 1 : 0 },
    ], rows, { rowHash: (r) => productDetailHash(r.asin, r.score_keyword), remoteSort: remoteSortOptions(productState, loadProducts), exportName: "商品池" });
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
  return `<input type="checkbox" class="prod-check" value="${escapeHtml(asin)}"${checked} onclick="event.stopPropagation()" onchange="window.productCompareToggle(this)" />`;
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
  rememberAgentBusinessContext();
};

function updateProductCompareBar() {
  const count = productCompareSelection.size;
  const selected = document.getElementById("prod-selected");
  const compare = document.getElementById("prod-compare");
  const addProject = document.getElementById("prod-add-project");
  if (selected) selected.textContent = `已选 ${count} 个`;
  if (compare) compare.disabled = count < 2;
  if (addProject) addProject.disabled = count < 1;
}

function compareSelectedProducts() {
  const asins = [...productCompareSelection].slice(0, 5);
  if (asins.length < 2) {
    notice("请至少勾选 2 个商品进行对比", "bad");
    return;
  }
  location.hash = `#/compare/${encodeURIComponent(asins.join(","))}`;
}

function addSelectedProductsToResearchProject() {
  const asins = [...productCompareSelection].slice(0, 5);
  if (!asins.length) return notice("请先勾选商品", "bad");
  const items = asins.map((asin) => {
    const row = productCompareRows.get(asin) || {};
    return {
      key: asin,
      label: displayTitle(row, asin),
      marketplace: row.marketplace || "US",
    };
  });
  return openResearchAssociationDialog({
    assetType: "product",
    items,
    marketplace: items[0]?.marketplace || "US",
  });
}

/* ---------- 视图：AI 助手（Agent M2） ---------- */
function viewAgent() {
  agentState.contextSuggestions = [];
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
        <div id="agent-readiness" class="agent-readiness" hidden></div>
        <div id="agent-messages" class="agent-messages"></div>
        <form id="agent-form" class="agent-form">
          <textarea id="agent-input" rows="3" placeholder="输入你的选品问题（Enter 发送 · Shift+Enter 换行）"></textarea>
          <button id="agent-send" class="btn" type="submit">发送</button>
        </form>
      </section>
      <aside class="agent-side">
        <div class="agent-side-title">常用问题</div>
        <button class="chip agent-prompt" data-prompt="帮我找综合得分高、评论数相对低的商品">高分低竞争商品</button>
        <button class="chip agent-prompt" data-prompt="先做一次应用全局体检：推荐榜、关键词机会、评论洞察、追踪任务和任务中心分别有什么重点？">全局体检</button>
        <button class="chip agent-prompt" data-prompt="看看当前关键词机会里哪些更适合差异化进入">关键词机会判断</button>
        <button class="chip agent-prompt" data-prompt="按一级关键词分组分析当前关键词机会，帮我找更值得观察的赛道">一级关键词分组</button>
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
    agentState.contextSuggestions = [];
    renderAgentMessages();
    loadAgentContextSuggestions();
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
      if (agentState.config?.valid !== true) return;
      document.getElementById("agent-input").value = btn.dataset.prompt || "";
      sendAgentMessage();
    };
  });
  syncAgentComposerAvailability(null);
  loadAgentConfig();
  renderAgentMessages();
  loadAgentContextSuggestions();
}

function renderAgentMessages() {
  const box = document.getElementById("agent-messages");
  if (!box) return;
  agentSuggestionRegistry = [];
  if (!agentState.messages.length) {
    const suggestionBlock = renderAgentActionSuggestions(agentState.contextSuggestions || []);
    const ready = agentState.config?.valid === true;
    const emptyTitle = ready ? "从一个具体问题开始" : "完成模型配置后开始";
    const emptyDescription = ready
      ? "例如商品筛选、ASIN 趋势、关键词机会、评论痛点或任务状态。"
      : "模型配置就绪前不会发送问题，也不会产生无效会话记录。";
    box.innerHTML = `
      <div class="agent-empty">
        <b>${emptyTitle}</b>
        <span>${emptyDescription}</span>
        ${suggestionBlock ? `<div class="agent-empty-actions">${suggestionBlock}</div>` : ""}
      </div>`;
    return;
  }
  box.innerHTML = agentState.messages.map((message) => {
    const toolBlock = renderAgentToolCalls(message.toolCalls || []);
    const actionBlock = message.pendingAction ? renderAgentPendingAction(message.pendingAction) : "";
    const suggestionBlock = renderAgentActionSuggestions(message.actionSuggestions || []);
    const cls = `agent-msg agent-msg-${message.role}`;
    const label = message.role === "user" ? "你" : message.role === "error" ? "错误" : "助手";
    return `
      <div class="${cls}">
        <div class="agent-msg-label">${label}</div>
        <div class="agent-bubble">${message.pending ? `<span class="mini-spinner"></span>` : ""}${escapeHtml(message.content)}</div>
        ${toolBlock}
        ${actionBlock}
        ${suggestionBlock}
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
    query_app_overview: "应用概览",
    query_recommendations: "推荐榜",
    query_products: "商品池",
    query_product_detail: "商品详情",
    query_product_metrics: "指标与估算",
    query_product_trend: "趋势",
    query_keyword_opportunities: "关键词机会",
    query_keyword_groups: "关键词分组",
    query_keyword_ideas: "关键词创意",
    query_research_projects: "研究项目",
    query_research_review_queue: "研究项目复核队列",
    query_research_decision_report: "研究项目决策报告",
    query_market_niches: "市场利基",
    query_competitive_graph: "竞品图谱",
    query_scoring_v2_replay: "评分 V2 回放",
    query_scoring_v2_calibration: "评分 V2 校准抽样",
    query_review_insights: "评论洞察",
    query_tracking_tasks: "追踪任务",
    query_tasks: "任务中心",
    open_amazon_page: "预开启 Amazon",
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

function agentInputValue(id) {
  const el = document.getElementById(id);
  return el ? String(el.value || "").trim() : "";
}

function agentCleanContext(value) {
  if (value == null || value === "" || (Array.isArray(value) && !value.length)) return undefined;
  if (Array.isArray(value)) return value.map(agentCleanContext).filter((item) => item !== undefined);
  if (typeof value === "object") {
    const obj = {};
    Object.entries(value).forEach(([key, item]) => {
      const cleaned = agentCleanContext(item);
      if (cleaned !== undefined) obj[key] = cleaned;
    });
    return Object.keys(obj).length ? obj : undefined;
  }
  return value;
}

function collectAgentVisibleSummary() {
  return Array.from(content.querySelectorAll(".result-meta, .detail-head .meta, .kw-secondary-head, .report-context-summary, .state"))
    .map((el) => String(el.textContent || "").replace(/\s+/g, " ").trim())
    .filter(Boolean)
    .slice(0, 5);
}

function collectAgentLiveContext() {
  const hash = location.hash || "#/recommendations";
  const ctx = {
    hash,
    title: viewTitle?.textContent || document.title || "",
    visible_summary: collectAgentVisibleSummary(),
  };
  const researchEvidenceContext = readResearchEvidenceContext();
  if (researchEvidenceContext) {
    ctx.research_evidence_action = {
      project_id: researchEvidenceContext.projectId,
      project_name: researchEvidenceContext.projectName,
      target: researchEvidenceContext.target,
      gap_key: researchEvidenceContext.gapKey,
      gap_label: researchEvidenceContext.gapLabel,
      default_role: researchEvidenceContext.defaultRole,
      return_hash: researchEvidenceContext.returnHash,
    };
  }
  const productMatch = hash.match(/^#\/product\/([^?]+)(?:\?score_keyword=(.*))?$/);
  const metricProductMatch = hash.match(/^#\/metrics\/(.+)$/);
  const compareMatch = hash.match(/^#\/compare(?:\/(.+))?$/);
  const researchProjectMatch = hash.match(/^#\/research-projects\/(\d+)$/);
  const researchObservationMatch = hash.match(/^#\/research-projects\/(\d+)\/observation-plan$/);
  const researchReportMatch = hash.match(/^#\/research-projects\/(\d+)\/report$/);
  const researchReportVersionMatch = hash.match(/^#\/research-projects\/(\d+)\/report-(?:versions|compare|live-compare)/);
  const marketNicheMatch = hash.match(/^#\/market-niches\/(\d+)$/);
  const competitiveGraphMatch = hash.match(/^#\/competitive-graph\/(\d+)$/);
  if (hash === "#/research-review-queue") {
    ctx.research_review_queue_filters = {
      marketplace: researchReviewQueueState.marketplace,
      status: researchReviewQueueState.status,
      attention: researchReviewQueueState.attention,
      monitoring: researchReviewQueueState.monitoring,
      keyword: researchReviewQueueState.keyword,
      limit: researchReviewQueueState.limit,
      offset: researchReviewQueueState.offset,
    };
    ctx.research_review_queue_summary = researchReviewQueueCurrent?.summary || null;
    ctx.module_hint = "研究项目复核队列为打开页面时只读计算；观察计划只保存人工复核节奏，不会后台创建任务、采集、冻结报告或变更项目状态。";
  } else if (competitiveGraphMatch) {
    ctx.current_market_niche_id = Number(competitiveGraphMatch[1]);
    ctx.competitive_graph = competitiveGraphCurrent ? {
      niche_id: competitiveGraphCurrent.niche?.id,
      niche_name: competitiveGraphCurrent.niche?.name,
      snapshot_id: competitiveGraphCurrent.snapshot?.id,
      keyword_count: competitiveGraphCurrent.summary?.keyword_count,
      observed_product_count: competitiveGraphCurrent.summary?.observed_product_count,
      focus_asin: competitiveGraphCurrent.focus?.product?.asin || null,
      source_complete: competitiveGraphCurrent.source_integrity?.complete,
    } : null;
    ctx.module_hint = "竞品图谱只代表所选利基快照的已采集页面观察；未观察到不等于没有排名，自然可见度代理不等于流量。";
  } else if (marketNicheMatch) {
    ctx.current_market_niche_id = Number(marketNicheMatch[1]);
    ctx.market_niche = marketNicheCurrent?.niche ? {
      id: marketNicheCurrent.niche.id,
      name: marketNicheCurrent.niche.name,
      status: marketNicheCurrent.niche.status,
      evidence_level: marketNicheCurrent.niche.evidence_level,
      keyword_count: marketNicheCurrent.niche.keyword_count,
      observed_product_count: marketNicheCurrent.niche.observed_product_count,
    } : null;
    ctx.module_hint = "市场利基详情页；说明成员、跨关键词去重和证据覆盖，证据等级不是机会评分。";
  } else if (researchObservationMatch) {
    ctx.current_research_project_id = Number(researchObservationMatch[1]);
    ctx.research_observation_plan = researchObservationPlanCurrent ? {
      project_id: researchObservationPlanCurrent.project?.id,
      project_name: researchObservationPlanCurrent.project?.name,
      project_status: researchObservationPlanCurrent.project?.status,
      plan: researchObservationPlanCurrent.plan,
      current_report: researchObservationPlanCurrent.current_report,
      policy: researchObservationPlanCurrent.policy,
    } : null;
    ctx.module_hint = "项目观察计划只记录用户人工检查节奏和最近所见指纹；Agent 只能解释状态，不能保存计划、完成复核、采集、冻结或推进项目。";
  } else if (researchReportMatch || researchReportVersionMatch) {
    const projectId = Number((researchReportMatch || researchReportVersionMatch)[1]);
    const detailReadiness = researchDecisionReportCurrent?.detail_evidence_readiness || {};
    ctx.current_research_project_id = projectId;
    ctx.research_decision_report = researchDecisionReportCurrent ? {
      project_id: researchDecisionReportCurrent.project?.id,
      project_name: researchDecisionReportCurrent.project?.name,
      readiness_level: researchDecisionReportCurrent.readiness?.level,
      readiness_label: researchDecisionReportCurrent.readiness?.label,
      gates_passed: researchDecisionReportCurrent.readiness?.passed_count,
      gates_total: researchDecisionReportCurrent.readiness?.total_count,
      blocking_count: researchDecisionReportCurrent.readiness?.blocking_count,
      evidence_as_of: researchDecisionReportCurrent.evidence_as_of,
      report_fingerprint: researchDecisionReportCurrent.report_fingerprint,
      detail_evidence_level: detailReadiness.summary?.level,
      detail_evidence_ready: detailReadiness.summary?.ready_total,
      detail_evidence_total: detailReadiness.summary?.product_total,
      detail_evidence_next_action: detailReadiness.summary?.next_action?.label,
      frozen_baseline: researchDecisionBaselineComparison?.baseline ? {
        version_no: researchDecisionBaselineComparison.baseline.version_no,
        baseline_label: researchDecisionBaselineComparison.baseline_label,
        comparison_scope: researchDecisionBaselineComparison.diff?.comparison_scope,
        comparison_scope_label: researchDecisionBaselineComparison.diff?.comparison_scope_label,
        method_compatible: researchDecisionBaselineComparison.diff?.method_compatible,
        material_change_count: researchDecisionBaselineComparison.diff?.summary?.material_change_count,
      } : null,
    } : null;
    ctx.module_hint = researchReportVersionMatch
      ? "当前为不可变报告历史或差异页；Agent 只能解释已冻结证据与结构化变化，不能创建版本、批准或淘汰项目。"
      : "研究项目决策报告只判断证据就绪度；Agent 必须同时说明支持、反对、缺口和来源，不能替用户批准或淘汰项目。";
  } else if (researchProjectMatch) {
    const detailReadiness = researchProjectCurrent?.detail_readiness || {};
    ctx.current_research_project_id = Number(researchProjectMatch[1]);
    ctx.research_project = researchProjectCurrent?.project ? {
      id: researchProjectCurrent.project.id,
      name: researchProjectCurrent.project.name,
      status: researchProjectCurrent.project.status,
      evidence_coverage: researchProjectCurrent.project.evidence_coverage,
      product_count: researchProjectCurrent.project.product_count,
      keyword_count: researchProjectCurrent.project.keyword_count,
      niche_count: researchProjectCurrent.niches?.length || 0,
      detail_evidence_level: detailReadiness.summary?.level,
      detail_evidence_ready: detailReadiness.summary?.ready_total,
      detail_evidence_total: detailReadiness.summary?.product_total,
      detail_evidence_next_action: detailReadiness.summary?.next_action?.label,
    } : null;
    ctx.module_hint = "研究项目详情页；最终批准或拒绝必须由用户人工决定，Agent 只解释已有证据。";
  } else if (productMatch) {
    ctx.current_asin = decodeURIComponent(productMatch[1]);
    if (productMatch[2]) ctx.current_keyword = decodeURIComponent(productMatch[2]);
  } else if (metricProductMatch) {
    ctx.current_asin = decodeURIComponent(metricProductMatch[1]);
    ctx.module_hint = "指标与估算详情页；分析时必须区分采集事实、确定性计算、经验情景和人工/官方输入。";
  } else if (compareMatch && compareMatch[1]) {
    ctx.compare_asins = parseAsins(decodeURIComponent(compareMatch[1]));
  } else if (hash === "#/recommendations") {
    ctx.recommendation_filters = {
      ...catalogFilterContext(recommendationState),
      sort_by: recommendationState.sortBy,
      sort_dir: recommendationState.sortDir,
      blue_only: recommendationState.blueOnly,
      limit: recommendationState.limit,
      offset: recommendationState.offset,
    };
  } else if (hash === "#/products") {
    ctx.product_filters = {
      ...catalogFilterContext(productState),
      sort_by: productState.sortBy,
      sort_dir: productState.sortDir,
      selected_asins: [...productCompareSelection],
      limit: productState.limit,
      offset: productState.offset,
    };
  } else if (hash === "#/metrics") {
    ctx.metric_filters = {
      keyword: metricState.keyword,
      limit: metricState.limit,
      offset: metricState.offset,
    };
    ctx.module_hint = "指标与估算商品概览；经验转化率不代表竞品真实转化率。";
  } else if (hash === "#/keywords") {
    ctx.keyword_filters = {
      keyword: keywordState.keyword,
      min_products: keywordState.minProducts,
      sort_by: keywordState.sortBy,
      sort_dir: keywordState.sortDir,
      selected_primary: document.querySelector(".kw-primary-chip.active")?.dataset.primary || "",
      selected_keyword: keywordProductState.keyword,
      selected_product_scope: keywordProductState.scope,
      current_batch_product_count: keywordProductState.currentCount,
      historical_observed_product_count: keywordProductState.historicalCount,
      current_batch_at: keywordProductState.currentSnapshotAt,
      previous_batch_at: keywordProductState.previousSnapshotAt,
      selected_product_sort_by: keywordProductState.sortBy,
      selected_product_sort_dir: keywordProductState.sortDir,
      group_mode: keywordGroupMode,
      limit: keywordState.limit,
      offset: keywordState.offset,
    };
  } else if (hash === "#/keyword-library") {
    ctx.keyword_library_filters = {
      keyword: keywordLibraryState.keyword,
      marketplace: keywordLibraryState.marketplace,
      snapshot_filter: keywordLibraryState.snapshotFilter,
      tracking_filter: keywordLibraryState.trackingFilter,
      source_filter: keywordLibraryState.sourceFilter,
      view_mode: keywordLibraryState.viewMode,
      sort_by: keywordLibraryState.sortBy,
      sort_dir: keywordLibraryState.sortDir,
      selected_keyword_ids: keywordLibrarySelectedIds(),
      selected_keywords: keywordLibrarySelectedRows(),
      limit: keywordLibraryState.limit,
      offset: keywordLibraryState.offset,
    };
  } else if (hash === "#/keyword-workshop") {
    ctx.keyword_workshop_filters = {
      keyword: keywordWorkshopState.keyword,
      status: keywordWorkshopState.status,
      source: keywordWorkshopState.source,
      run_id: keywordWorkshopState.runId,
      sort_by: keywordWorkshopState.sortBy,
      sort_dir: keywordWorkshopState.sortDir,
      selected_idea_ids: keywordWorkshopSelectedIds(),
      limit: keywordWorkshopState.limit,
      offset: keywordWorkshopState.offset,
    };
  } else if (hash === "#/research-projects") {
    ctx.research_project_filters = {
      marketplace: researchProjectState.marketplace,
      status: researchProjectState.status,
      keyword: researchProjectState.keyword,
      sort_by: researchProjectState.sortBy,
      sort_dir: researchProjectState.sortDir,
      limit: researchProjectState.limit,
      offset: researchProjectState.offset,
    };
    ctx.module_hint = "研究项目列表页，用于查看选品方向的验证阶段、证据覆盖和人工结论。";
  } else if (hash === "#/market-niches") {
    ctx.market_niche_filters = {
      marketplace: marketNicheState.marketplace,
      status: marketNicheState.status,
      keyword: marketNicheState.keyword,
      sort_by: marketNicheState.sortBy,
      sort_dir: marketNicheState.sortDir,
      limit: marketNicheState.limit,
      offset: marketNicheState.offset,
    };
    ctx.module_hint = "市场利基列表页；利基是多关键词市场对象，覆盖等级不代表进入建议。";
  } else if (hash === "#/competitive-graph") {
    ctx.competitive_graph_filters = {
      marketplace: competitiveGraphState.marketplace,
      keyword: competitiveGraphState.keyword,
      product_limit: competitiveGraphState.productLimit,
      min_shared: competitiveGraphState.minShared,
      limit: competitiveGraphState.listLimit,
      offset: competitiveGraphState.listOffset,
    };
    ctx.module_hint = "竞品图谱入口页；需要先选择一个已生成证据快照的市场利基。";
  } else if (hash === "#/model-replay") {
    if (scoringReplayState.mode === "domain") {
      ctx.domain_scoring_models = {
        marketplace: domainModelState.marketplace,
        status: domainModelState.status,
        selected_profile: domainModelDetail?.profile || null,
      };
      ctx.module_hint = "领域模型用于表达卖家判断偏好；版本化参数试算不写生产评分，自动训练与榜单接管仍冻结。";
    } else if (scoringReplayState.mode === "calibration") {
      const reviews = scoringCalibrationReviews();
      ctx.scoring_v2_calibration = {
        marketplace: scoringCalibrationState.marketplace,
        keyword: scoringCalibrationState.keyword,
        strategy: scoringCalibrationState.strategy,
        sample_per_bucket: scoringCalibrationState.samplePerBucket,
        reviewed_count: Object.values(reviews).filter((item) => item?.judgment && item.judgment !== "pending").length,
        selected_sample: scoringCalibrationCurrent?.rows?.find((row) => row.sample_key === scoringCalibrationState.selectedKey) || null,
      };
      ctx.module_hint = "评分校准抽样只用于人工复核策略分歧和证据风险；审计标记不是错误标签，Agent 不能自动调权或填写人工结论。";
    } else {
      ctx.scoring_v2_filters = {
        marketplace: scoringReplayState.marketplace,
        keyword: scoringReplayState.keyword,
        strategy: scoringReplayState.strategy,
        recommendation: scoringReplayState.recommendation,
        min_confidence: scoringReplayState.minConfidence,
        max_risk: scoringReplayState.maxRisk,
        sort_by: scoringReplayState.sortBy,
        sort_dir: scoringReplayState.sortDir,
        selected_context: scoringReplayCurrent?.rows?.find((row) => scoreReplayRowKey(row) === scoringReplayState.selectedKey) || null,
      };
      ctx.module_hint = "评分 V2 是只读影子回放：机会、风险、置信度三轴并列，不写生产评分，也不替代利润、供应链、评论和人工终审。";
    }
  } else if (hash === "#/tracking") {
    ctx.tracking_filters = {
      selected_task_ids: trackingSelectedIds(),
      selected_tasks: trackingSelectedTasks(),
    };
    ctx.module_hint = "关键词追踪任务页，可查询 active/paused/completed/error 任务。";
  } else if (hash === "#/tasks") {
    ctx.task_filters = {
      selected_task_ids: taskSelectedRowIds(),
      selected_tasks: taskSelectedRows(),
    };
    ctx.module_hint = "任务中心页，可查询最近爬取/入库任务及错误日志。";
  } else if (hash === "#/reviews") {
    ctx.module_hint = "评论痛点页，仅代表已导入或已解析评论证据。";
  } else if (hash === "#/crawl") {
    ctx.module_hint = "手动采集页；联网采集前建议预开启 Amazon 页面并由用户处理地址/登录/验证码。";
  }
  return agentCleanContext(ctx) || {};
}

function storeAgentBusinessContext(context) {
  try {
    sessionStorage.setItem(AGENT_CONTEXT_STORAGE_KEY, JSON.stringify(context || {}));
  } catch {
    // 会话存储不可用时退回内存上下文。
  }
}

function readStoredAgentBusinessContext() {
  try {
    const raw = sessionStorage.getItem(AGENT_CONTEXT_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    if (parsed && typeof parsed === "object" && parsed.hash !== "#/agent") return parsed;
  } catch {
    return null;
  }
  return null;
}

function rememberAgentBusinessContext() {
  if ((location.hash || "") === "#/agent") return;
  const context = collectAgentLiveContext();
  agentState.lastPageContext = context;
  storeAgentBusinessContext(context);
}

function getAgentClientContext() {
  const live = collectAgentLiveContext();
  const recent = agentState.lastPageContext || readStoredAgentBusinessContext();
  const context = {
    current_page: live,
    pending_action: agentState.pendingAction ? {
      tool: agentState.pendingAction.tool,
      input: agentState.pendingAction.input || {},
    } : null,
  };
  if ((live.hash || "") === "#/agent" && recent) {
    context.recent_business_page = recent;
  }
  return agentCleanContext(context) || {};
}

async function sendAgentMessage() {
  if (agentState.sending) return;
  if (agentState.config?.valid !== true) {
    notice("请先完成可用的模型配置", "bad");
    focusAgentConfig();
    return;
  }
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
  agentState.contextSuggestions = [];
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
      client_context: getAgentClientContext(),
    });
    applyAgentResponse(data, pending);
  } catch (err) {
    pending.role = "error";
    pending.content = err.message || "请求未成功。";
    pending.pending = false;
    pending.toolCalls = [];
  } finally {
    agentState.sending = false;
    syncAgentComposerAvailability(agentState.config);
    renderAgentMessages();
    input.focus();
  }
}

function applyAgentResponse(data, message) {
  agentState.conversationId = data.conversation_id || agentState.conversationId;
  agentState.pendingAction = data.pending_action || null;
  agentState.contextSuggestions = [];
  message.content = data.reply || "暂无返回内容。";
  message.pending = false;
  message.toolCalls = data.tool_calls || [];
  message.pendingAction = data.pending_action || null;
  message.actionSuggestions = data.action_suggestions || [];
}

async function loadAgentContextSuggestions() {
  if (agentState.messages.length || agentState.pendingAction) return;
  try {
    const data = await apiSend("/api/agent/suggestions", "POST", {
      client_context: getAgentClientContext(),
    });
    agentState.contextSuggestions = data.action_suggestions || [];
  } catch {
    agentState.contextSuggestions = [];
  }
  renderAgentMessages();
}

function renderAgentPendingAction(action) {
  return `
    <div class="agent-action">
      <div>
        <b>${escapeHtml(agentToolLabel(action.tool))}</b>
        <code>${escapeHtml(formatAgentToolInput(action.input || {}))}</code>
      </div>
      <div class="agent-action-buttons">
        <button class="btn btn-sm btn-warn" onclick="window.confirmAgentAction(true)">确认执行</button>
        <button class="btn btn-sm" onclick="window.confirmAgentAction(false)">取消</button>
      </div>
    </div>`;
}

function renderAgentActionSuggestions(suggestions) {
  const items = Array.isArray(suggestions) ? suggestions : [];
  if (!items.length) return "";
  return `
    <div class="agent-suggestions">
      ${items.map((item) => {
        const index = agentSuggestionRegistry.push(item) - 1;
        return `
          <button type="button" class="agent-suggestion-card" onclick="window.runAgentActionSuggestion(${index})">
            <b>${escapeHtml(item.label || "执行建议")}</b>
            <span>${escapeHtml(item.description || "需要你确认后执行。")}</span>
            ${item.risk ? `<em>${escapeHtml(item.risk)}</em>` : ""}
          </button>`;
      }).join("")}
    </div>`;
}

window.runAgentActionSuggestion = async (index) => {
  if (agentState.sending) return;
  if (agentState.pendingAction) {
    notice("请先处理当前待确认操作", "bad");
    return;
  }
  const suggestion = agentSuggestionRegistry[Number(index)];
  if (!suggestion) {
    notice("建议已过期，请重新询问 AI 助手", "bad");
    return;
  }
  const confirmText = suggestion.confirm_text || `确认执行「${suggestion.label || "建议"}」？`;
  const risk = suggestion.risk ? `\n${suggestion.risk}` : "";
  if (suggestion.requires_confirmation !== false && !confirm(`${confirmText}${risk}`)) return;

  agentState.messages.push({ role: "user", content: `执行建议：${suggestion.label || "建议"}` });
  const pending = { role: "assistant", content: "执行建议中…", pending: true, toolCalls: [], actionSuggestions: [] };
  agentState.messages.push(pending);
  agentState.sending = true;
  renderAgentMessages();
  try {
    const result = await executeAgentActionSuggestion(suggestion);
    pending.pending = false;
    pending.content = summarizeAgentSuggestionResult(suggestion, result);
    await refreshAfterAgentSuggestion(suggestion);
    notice("建议执行完成", "ok");
  } catch (err) {
    pending.role = "error";
    pending.pending = false;
    pending.content = err.message || "建议执行失败。";
    notice(pending.content, "bad");
  } finally {
    agentState.sending = false;
    renderAgentMessages();
  }
};

async function executeAgentActionSuggestion(suggestion) {
  if (suggestion.kind === "product_pool") {
    return executeProductPoolSuggestion(suggestion);
  }
  if (suggestion.kind === "tracking") {
    return executeTrackingSuggestion(suggestion);
  }
  if (suggestion.kind === "task_center") {
    return executeTaskCenterSuggestion(suggestion);
  }
  if (suggestion.kind !== "keyword_workshop") {
    throw new Error("暂不支持该建议类型。");
  }
  const ids = Array.isArray(suggestion.ids) ? suggestion.ids.map(Number).filter(Boolean) : [];
  if (!ids.length) {
    throw new Error("建议缺少候选词 ID，请重新选择候选。");
  }
  const marketplace = suggestion.marketplace || "US";
  if (suggestion.operation === "promote") {
    return apiSend("/api/keyword-workshop/ideas/promote", "POST", { ids, marketplace });
  }
  if (suggestion.operation === "create_tracking") {
    const body = {
      ids,
      marketplace,
      target_snapshots: Number(suggestion.target_snapshots || 3),
    };
    if (suggestion.pages_per_keyword) body.pages_per_keyword = Number(suggestion.pages_per_keyword);
    return apiSend("/api/keyword-workshop/ideas/create-tracking", "POST", body);
  }
  if (suggestion.operation === "set_status") {
    return apiSend("/api/keyword-workshop/ideas/status", "POST", {
      ids,
      marketplace,
      status: suggestion.status || "ignored",
    });
  }
  throw new Error("暂不支持该建议操作。");
}

function executeProductPoolSuggestion(suggestion) {
  if (suggestion.operation === "compare") {
    const asins = Array.isArray(suggestion.asins)
      ? suggestion.asins.map((item) => String(item || "").trim()).filter(Boolean).slice(0, 5)
      : [];
    if (asins.length < 2) throw new Error("至少需要 2 个商品才能打开对比。");
    location.hash = `#/compare/${encodeURIComponent(asins.join(","))}`;
    return { navigated: true, asins };
  }
  if (suggestion.operation === "view_detail") {
    const asin = String(suggestion.asin || "").trim();
    if (!asin) throw new Error("建议缺少 ASIN。");
    location.hash = `#/product/${encodeURIComponent(asin)}`;
    return { navigated: true, asin };
  }
  throw new Error("暂不支持该商品池建议。");
}

async function executeTrackingSuggestion(suggestion) {
  const taskIds = Array.isArray(suggestion.task_ids)
    ? suggestion.task_ids.map(Number).filter(Boolean).slice(0, 5)
    : [];
  if (suggestion.operation === "check") {
    if (!taskIds.length) throw new Error("建议缺少追踪任务 ID。");
    const results = [];
    for (const taskId of taskIds) {
      const result = await apiSend("/api/tracking/check", "POST", { execute: false, task_id: taskId });
      results.push({ task_id: taskId, result });
    }
    return { checked: results.length, results };
  }
  if (suggestion.operation === "set_status") {
    if (!taskIds.length) throw new Error("建议缺少追踪任务 ID。");
    const status = suggestion.status || "paused";
    const results = [];
    for (const taskId of taskIds) {
      const result = await apiSend(`/api/tracking/tasks/${encodeURIComponent(taskId)}/status`, "POST", { status });
      results.push({ task_id: taskId, result });
    }
    return { updated: results.length, status, results };
  }
  if (suggestion.operation === "collect") {
    const taskId = Number(suggestion.task_id || 0);
    if (!taskId) throw new Error("建议缺少追踪任务 ID。");
    const result = await runBrowserAction(
      () => apiSend("/api/tracking/check", "POST", { execute: true, task_id: taskId })
    );
    return { task_id: taskId, result };
  }
  throw new Error("暂不支持该追踪建议。");
}

function executeTaskCenterSuggestion(suggestion) {
  if (suggestion.operation === "show_error") {
    const rowId = String(suggestion.row_id || "");
    if (!rowId) throw new Error("建议缺少任务行 ID。");
    showTaskError(rowId);
    return { row_id: rowId };
  }
  if (suggestion.operation === "navigate") {
    const route = String(suggestion.route || "");
    if (!route.startsWith("#/")) throw new Error("建议缺少有效跳转目标。");
    location.hash = route;
    return { route };
  }
  throw new Error("暂不支持该任务中心建议。");
}

function summarizeAgentSuggestionResult(suggestion, result) {
  const label = suggestion.label || "建议";
  if (suggestion.kind === "product_pool" && suggestion.operation === "compare") {
    return `已打开商品对比页：${(result?.asins || []).join("、")}。`;
  }
  if (suggestion.kind === "product_pool" && suggestion.operation === "view_detail") {
    return `已打开商品详情页：${result?.asin || "已选商品"}。`;
  }
  if (suggestion.kind === "tracking" && suggestion.operation === "check") {
    const first = Array.isArray(result?.results) && result.results[0] ? `\n首个结果：${summarizeCheck(result.results[0].result)}` : "";
    return `已完成「${label}」：检查 ${fmt.int(result?.checked)} 个任务（未联网）。${first}`;
  }
  if (suggestion.kind === "tracking" && suggestion.operation === "set_status") {
    const statusText = result?.status === "active" ? "恢复为 active" : result?.status === "paused" ? "暂停" : result?.status;
    return `已完成「${label}」：${statusText} ${fmt.int(result?.updated)} 个追踪任务。`;
  }
  if (suggestion.kind === "tracking" && suggestion.operation === "collect") {
    return `已完成「${label}」：任务 #${fmt.int(result?.task_id)}，${summarizeCheck(result?.result)}。`;
  }
  if (suggestion.kind === "task_center" && suggestion.operation === "show_error") {
    return `已打开任务错误日志：${result?.row_id || "已选任务"}。`;
  }
  if (suggestion.kind === "task_center" && suggestion.operation === "navigate") {
    return `已跳转：${result?.route || "目标页面"}。`;
  }
  if (suggestion.operation === "promote") {
    return `已完成「${label}」：更新 ${fmt.int(result?.updated)} 个候选词。`;
  }
  if (suggestion.operation === "create_tracking") {
    const warnings = Array.isArray(result?.warnings) && result.warnings.length
      ? `\n提示：${result.warnings.slice(0, 3).join("；")}`
      : "";
    return `已完成「${label}」：创建或复用 ${fmt.int(result?.created_or_existing)} 个追踪任务。${warnings}`;
  }
  if (suggestion.operation === "set_status") {
    return `已完成「${label}」：更新 ${fmt.int(result?.updated)} 个候选词。`;
  }
  return `已完成「${label}」。`;
}

async function refreshAfterAgentSuggestion(suggestion) {
  if (suggestion.kind === "task_center") return;
  if (suggestion.kind === "tracking") {
    if (location.hash === "#/tracking") await viewTracking();
    return;
  }
  if (suggestion.kind !== "keyword_workshop") return;
  const ids = Array.isArray(suggestion.ids) ? suggestion.ids.map(Number).filter(Boolean) : [];
  ids.forEach((id) => keywordWorkshopState.selected.delete(Number(id)));
  if (location.hash === "#/keyword-workshop") {
    await loadKeywordRuns();
    await loadKeywordIdeas();
  }
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
    if (approved && ["open_amazon_page", "trigger_collection"].includes(action.tool)) {
      await ensureBrowserRuntimeReady();
    }
    const data = await apiSend("/api/agent/chat", "POST", {
      conversation_id: agentState.conversationId,
      message: null,
      confirm: {
        tool_call_id: action.tool_call_id,
        approved: !!approved,
      },
      client_context: getAgentClientContext(),
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
  syncAgentComposerAvailability(null);
  try {
    const config = await api("/api/agent/config");
    agentState.config = config;
    renderAgentConfig(config);
    renderAgentMessages();
  } catch (err) {
    agentState.config = { valid: false, error: err.message || "模型配置读取失败" };
    box.innerHTML = `<div class="agent-config-status agent-config-bad">${escapeHtml(err.message)}</div>`;
    syncAgentComposerAvailability(agentState.config);
    renderAgentMessages();
  }
}

function focusAgentConfig() {
  const box = document.getElementById("agent-config");
  if (!box) return;
  box.scrollIntoView({ behavior: "smooth", block: "center" });
  window.setTimeout(() => document.getElementById("agent-provider")?.focus({ preventScroll: true }), 250);
}

function syncAgentComposerAvailability(config = agentState.config) {
  const ready = config?.valid === true;
  const input = document.getElementById("agent-input");
  const send = document.getElementById("agent-send");
  const readiness = document.getElementById("agent-readiness");
  if (input) {
    input.disabled = !ready;
    input.placeholder = ready
      ? "输入你的选品问题（Enter 发送 · Shift+Enter 换行）"
      : (config == null ? "正在读取模型配置…" : "请先完成模型配置");
  }
  if (send) send.disabled = !ready || agentState.sending;
  document.querySelectorAll(".agent-prompt").forEach((button) => {
    button.disabled = !ready;
  });
  if (!readiness) return;
  if (ready) {
    readiness.hidden = true;
    readiness.innerHTML = "";
    return;
  }
  readiness.hidden = false;
  if (config == null) {
    readiness.className = "agent-readiness is-loading";
    readiness.textContent = "正在确认模型配置…";
    return;
  }
  readiness.className = "agent-readiness is-blocked";
  readiness.innerHTML = `
    <div><b>AI 助手尚未就绪</b><span>${escapeHtml(config.error || "请先保存可用的模型配置。")}</span></div>
    <button class="btn btn-sm" id="agent-readiness-config" type="button">定位模型配置</button>`;
  document.getElementById("agent-readiness-config").onclick = focusAgentConfig;
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
  syncAgentComposerAvailability(config);
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

/* ---------- 视图：指标与估算中心 ---------- */
const METRIC_DRAFT_STORAGE_KEY = "amazon_metric_input_drafts_v1";
const METRIC_CSV_COLUMNS = {
  "ASIN": "asin", "asin": "asin",
  "统计周期开始": "period_start", "period_start": "period_start",
  "统计周期结束": "period_end", "period_end": "period_end",
  "来源类型": "source_type", "source_type": "source_type",
  "来源说明": "source_label", "source_label": "source_label",
  "会话数": "sessions", "sessions": "sessions",
  "页面浏览量": "page_views", "page_views": "page_views",
  "订购件数": "units_ordered", "units_ordered": "units_ordered",
  "订单数": "orders", "orders": "orders",
  "订购销售额": "ordered_sales", "ordered_sales": "ordered_sales",
  "Featured Offer比例": "featured_offer_percentage", "featured_offer_percentage": "featured_offer_percentage",
  "曝光量": "impressions", "impressions": "impressions",
  "点击量": "clicks", "clicks": "clicks",
  "加购量": "cart_adds", "cart_adds": "cart_adds",
  "购买量": "purchases", "purchases": "purchases",
  "广告花费": "ad_spend", "ad_spend": "ad_spend",
  "广告点击": "ad_clicks", "ad_clicks": "ad_clicks",
  "广告订单": "ad_orders", "ad_orders": "ad_orders",
  "广告销售额": "ad_sales", "ad_sales": "ad_sales",
  "总销售额": "total_sales", "total_sales": "total_sales",
  "单件采购成本": "unit_purchase_cost", "unit_purchase_cost": "unit_purchase_cost",
  "单件运输成本": "unit_shipping_cost", "unit_shipping_cost": "unit_shipping_cost",
  "单件FBA费用": "unit_fba_fee", "unit_fba_fee": "unit_fba_fee",
  "单件佣金": "unit_referral_fee", "unit_referral_fee": "unit_referral_fee",
  "单件其他成本": "unit_other_cost", "unit_other_cost": "unit_other_cost",
  "转化率低情景": "assumed_cvr_low", "assumed_cvr_low": "assumed_cvr_low",
  "转化率中情景": "assumed_cvr_base", "assumed_cvr_base": "assumed_cvr_base",
  "转化率高情景": "assumed_cvr_high", "assumed_cvr_high": "assumed_cvr_high",
  "备注": "notes", "notes": "notes",
};
const METRIC_SOURCE_ALIASES = {
  "人工录入": "manual",
  "手动录入": "manual",
  "manual": "manual",
  "CSV": "csv",
  "csv": "csv",
  "SP-API": "sp_api",
  "sp_api": "sp_api",
  "Ads API": "ads_api",
  "ads_api": "ads_api",
  "第三方数据": "third_party",
  "第三方": "third_party",
  "third_party": "third_party",
};

async function viewMetricCenter() {
  content.innerHTML = `
    <section class="panel metric-overview-panel">
      <div class="metric-overview-toolbar">
        <div class="filters compact">
          <input id="metric-search" value="${escapeHtml(metricState.keyword)}" placeholder="ASIN / 商品标题" />
          <button class="btn" id="metric-search-btn" type="button">查询</button>
          <button class="btn" id="metric-reset-btn" type="button">重置</button>
        </div>
        <div class="actions">
          <a class="btn" href="#/metrics/evidence">证据队列</a>
          <button class="btn" id="metric-template-btn" type="button">CSV 模板</button>
          <button class="btn" id="metric-import-btn" type="button">导入 CSV</button>
          <input id="metric-csv-file" type="file" accept=".csv,text/csv" hidden />
        </div>
      </div>
      <div id="metric-import-result"></div>
      <div id="metric-products-meta" class="result-meta"></div>
      <div id="metric-products-table"><div class="state"><div class="spinner"></div>读取商品指标概览…</div></div>
      <div id="metric-products-pager"></div>
    </section>`;
  const search = document.getElementById("metric-search");
  const runSearch = () => {
    metricState.keyword = String(search?.value || "").trim();
    metricState.offset = 0;
    persistState(metricState);
    loadMetricProducts();
  };
  document.getElementById("metric-search-btn").onclick = runSearch;
  document.getElementById("metric-reset-btn").onclick = () => {
    metricState.keyword = "";
    metricState.offset = 0;
    persistState(metricState);
    if (search) search.value = "";
    loadMetricProducts();
  };
  if (search) search.onkeydown = (event) => { if (event.key === "Enter") runSearch(); };
  document.getElementById("metric-template-btn").onclick = downloadMetricCsvTemplate;
  document.getElementById("metric-import-btn").onclick = () => document.getElementById("metric-csv-file")?.click();
  document.getElementById("metric-csv-file").onchange = importMetricCsvFile;
  await loadMetricProducts();
}

async function loadMetricProducts() {
  const box = document.getElementById("metric-products-table");
  if (!box) return;
  box.innerHTML = `<div class="state"><div class="spinner"></div>读取商品指标概览…</div>`;
  const query = new URLSearchParams({ limit: metricState.limit, offset: metricState.offset });
  if (metricState.keyword) query.set("keyword", metricState.keyword);
  try {
    const page = normalizePage(await api(`/api/metrics/products?${query.toString()}`), metricState.limit);
    document.getElementById("metric-products-meta").textContent = pageSummary(page, "商品");
    if (!page.rows.length) {
      box.innerHTML = `<div class="state">暂无匹配商品。<button class="btn btn-sm state-action" id="metric-empty-reset" type="button">清空筛选</button></div>`;
      document.getElementById("metric-empty-reset").onclick = () => document.getElementById("metric-reset-btn").click();
    } else {
      box.innerHTML = tableHtml(
        ["商品", "价格", "评分 / 评论", "近月购买", "GMV 下界代理", "详情证据", "精确输入", "操作"],
        page.rows.map((row) => ({
          _hash: `#/metrics/${encodeURIComponent(row.asin)}`,
          _key: row.asin,
          cells: [
            `<b>${escapeHtml(truncate(displayTitle(row, row.asin), 58))}</b><div class="set-hint">${escapeHtml(row.asin)}</div>`,
            fmt.money(row.detail_price ?? row.price),
            `${fmt.num(row.rating)} / ${fmt.int(row.review_count)}`,
            fmt.int(row.monthly_bought),
            fmt.money(row.monthly_gmv_floor_proxy),
            row.detail_collected_at ? metricLayerBadge("采集事实") : `<span class="badge badge-dim">未采集</span>`,
            row.has_metric_input ? metricLayerBadge("人工/官方输入") : `<span class="badge badge-dim">暂无</span>`,
            `<a class="btn btn-sm" href="#/metrics/${encodeURIComponent(row.asin)}" onclick="event.stopPropagation()">查看</a>`,
          ],
        }))
      );
    }
    document.getElementById("metric-products-pager").innerHTML = renderPager("metric-pager", page, [20, 50, 100]);
    bindPager("metric-pager", metricState, page, loadMetricProducts);
  } catch (err) {
    box.innerHTML = `<div class="state error">${escapeHtml(err.message)}</div>`;
  }
}

function downloadMetricCsvTemplate() {
  const headers = Object.keys(METRIC_CSV_COLUMNS).filter((key) => !/^[a-z_]+$/.test(key));
  exportCsv("商品指标导入模板.csv", headers, []);
}

async function importMetricCsvFile(event) {
  const file = event.target.files?.[0];
  event.target.value = "";
  if (!file) return;
  const box = document.getElementById("metric-import-result");
  try {
    const rows = mapMetricCsvRows(parseCsvMatrix(await file.text()));
    if (!rows.length) throw new Error("CSV 没有可导入的数据行。");
    if (!confirm(`确认导入 ${rows.length} 行商品指标？\n\n重复的 ASIN + 周期 + 来源会更新已有记录。`)) return;
    if (box) box.innerHTML = `<div class="state"><div class="spinner"></div>正在校验并写入…</div>`;
    const result = await apiSend("/api/metrics/inputs/import", "POST", { rows });
    const rejected = Array.isArray(result.rejected) ? result.rejected : [];
    if (box) {
      box.innerHTML = `<div class="metric-import-summary">
        <span>写入 <b>${fmt.int(result.saved)}</b> 行</span>
        <span>拒绝 <b>${fmt.int(rejected.length)}</b> 行</span>
        ${rejected.length ? `<span class="metric-import-errors">${rejected.slice(0, 5).map((item) => `第 ${item.row} 行：${escapeHtml(item.message)}`).join("；")}</span>` : ""}
      </div>`;
    }
    notice(rejected.length ? `导入完成，${rejected.length} 行未通过校验` : "商品指标导入完成", rejected.length ? "bad" : "ok");
    await loadMetricProducts();
  } catch (err) {
    if (box) box.innerHTML = `<div class="state error">${escapeHtml(err.message)}</div>`;
    notice(err.message, "bad");
  }
}

function parseCsvMatrix(text) {
  const rows = [];
  let row = [];
  let field = "";
  let quoted = false;
  const source = String(text || "").replace(/^\uFEFF/, "");
  for (let index = 0; index < source.length; index += 1) {
    const char = source[index];
    if (quoted) {
      if (char === '"' && source[index + 1] === '"') {
        field += '"'; index += 1;
      } else if (char === '"') {
        quoted = false;
      } else {
        field += char;
      }
    } else if (char === '"') {
      quoted = true;
    } else if (char === ",") {
      row.push(field); field = "";
    } else if (char === "\n") {
      row.push(field); rows.push(row); row = []; field = "";
    } else if (char !== "\r") {
      field += char;
    }
  }
  if (field || row.length) { row.push(field); rows.push(row); }
  return rows;
}

function mapMetricCsvRows(matrix) {
  if (!Array.isArray(matrix) || matrix.length < 2) return [];
  const headers = matrix[0].map((value) => METRIC_CSV_COLUMNS[String(value || "").trim()] || "");
  if (!headers.includes("asin") || !headers.includes("period_start") || !headers.includes("period_end")) {
    throw new Error("CSV 必须包含 ASIN、统计周期开始、统计周期结束三列。");
  }
  return matrix.slice(1).map((cells) => {
    const row = {};
    headers.forEach((key, index) => { if (key && String(cells[index] || "").trim() !== "") row[key] = String(cells[index]).trim(); });
    row.source_type = METRIC_SOURCE_ALIASES[row.source_type] || row.source_type || "csv";
    return row;
  }).filter((row) => Object.keys(row).some((key) => key !== "source_type"));
}

async function viewMetricEvidence() {
  content.innerHTML = `
    <div class="metric-evidence-page">
    <header class="metric-page-head metric-evidence-head">
      <div>
        <a class="link back-link" href="#/metrics">← 返回指标概览</a>
        <h2>证据质量与离线重解析</h2>
        <div class="meta">本地详情 HTML · 30 天过期阈值 · 综合评分未接入</div>
      </div>
      <div class="metric-page-actions">
        <button class="btn" id="metric-evidence-open-amazon" type="button">预开启 Amazon</button>
        <button class="btn" id="metric-evidence-rescan" type="button">重新解析本地文件</button>
        <button class="btn" id="metric-evidence-preview" type="button" disabled>预览选中</button>
        <button class="btn btn-warn" id="metric-evidence-apply" type="button" disabled>回填选中</button>
      </div>
    </header>
    <div class="metric-layer-legend">
      ${metricEvidenceStatusBadge("not_collected")}${metricEvidenceStatusBadge("page_missing")}
      ${metricEvidenceStatusBadge("parser_unrecognized")}${metricEvidenceStatusBadge("stale")}${metricEvidenceStatusBadge("ready")}
    </div>
    <section class="panel metric-quality-panel">
      <div class="metric-panel-head"><h2>证据概览</h2><span id="metric-evidence-root" class="result-meta"></span></div>
      <div id="metric-evidence-summary" class="metric-summary-grid"><div class="state"><div class="spinner"></div>扫描中…</div></div>
      <div id="metric-evidence-cache-warning" class="metric-risk-note" hidden></div>
    </section>
    <section class="panel">
      <div class="metric-evidence-filebar">
        <div><h2>本地详情 HTML</h2><span id="metric-evidence-selected" class="selected-count">已选 0 个</span></div>
        <div class="actions">
          <select id="metric-evidence-file-status" class="sel sel-sm">
            ${[["all","全部状态"],["ready","可回填"],["page_missing","页面未提供"],["parser_unrecognized","解析器需适配"],["stale","样本过旧"],["invalid","不可用"]]
              .map(([value,label]) => `<option value="${value}"${metricEvidenceState.fileStatus === value ? " selected" : ""}>${label}</option>`).join("")}
          </select>
          <button class="btn btn-sm" id="metric-evidence-select-all" type="button">选择可回填</button>
          <button class="btn btn-sm" id="metric-evidence-clear" type="button">清空选择</button>
        </div>
      </div>
      <div id="metric-evidence-files"><div class="state"><div class="spinner"></div>解析本地文件…</div></div>
    </section>
    <section class="panel" id="metric-evidence-preview-panel">
      <h2>差异预览</h2>
      <div id="metric-evidence-preview-box" class="state">暂无差异预览。</div>
    </section>
    <section class="panel">
      <div class="metric-evidence-filebar">
        <div><h2>待补详情商品</h2><span id="metric-evidence-gap-meta" class="result-meta"></span></div>
        <div class="actions">
          <select id="metric-evidence-gap-priority" class="sel sel-sm" aria-label="详情补采优先级">
            ${[["all","全部优先级"],["focus","项目重点（候选/对标）"],["project","进行中项目关联"],["planned","人工计划关注"],["recent","近期活跃"],["routine","常规积压"]]
              .map(([value,label]) => `<option value="${value}"${metricEvidenceState.gapPriority === value ? " selected" : ""}>${label}</option>`).join("")}
          </select>
        </div>
      </div>
      <div id="metric-evidence-project-scope" class="metric-evidence-project-scope" hidden></div>
      <div id="metric-evidence-gap-summary" class="metric-gap-priority-summary"></div>
      <div id="metric-evidence-disposition-policy" class="metric-disposition-policy"></div>
      <div id="metric-evidence-collection-status" class="detail-collect-status"></div>
      <div id="metric-evidence-gaps"><div class="state"><div class="spinner"></div>读取证据缺口…</div></div>
      <div id="metric-evidence-gap-pager"></div>
    </section>
    </div>`;
  document.getElementById("metric-evidence-open-amazon").onclick = openAmazonForMetricEvidence;
  document.getElementById("metric-evidence-rescan").onclick = () => {
    if (!confirm("确认重新解析全部本地详情 HTML？\n\n该操作只重建本地派生缓存，不访问 Amazon、不写数据库；文件较多时需要等待。")) return;
    loadMetricEvidence({ refreshCache: true });
  };
  document.getElementById("metric-evidence-preview").onclick = () => previewMetricEvidence([...metricEvidenceSelected]);
  document.getElementById("metric-evidence-apply").onclick = applySelectedMetricEvidence;
  document.getElementById("metric-evidence-file-status").onchange = (event) => {
    metricEvidenceState.fileStatus = event.target.value;
    persistState(metricEvidenceState);
    renderMetricEvidenceFiles();
  };
  document.getElementById("metric-evidence-select-all").onclick = selectReplayableMetricEvidence;
  document.getElementById("metric-evidence-clear").onclick = () => {
    metricEvidenceSelected.clear();
    renderMetricEvidenceFiles();
  };
  document.getElementById("metric-evidence-gap-priority").onchange = (event) => {
    metricEvidenceState.gapPriority = event.target.value;
    metricEvidenceState.offset = 0;
    persistState(metricEvidenceState);
    loadMetricEvidence();
  };
  renderMetricEvidenceProjectScope();
  await loadMetricEvidence();
}

async function loadMetricEvidence({ refreshCache = false } = {}) {
  const filesBox = document.getElementById("metric-evidence-files");
  const gapsBox = document.getElementById("metric-evidence-gaps");
  const rescanButton = document.getElementById("metric-evidence-rescan");
  if (!filesBox || !gapsBox) return;
  filesBox.innerHTML = `<div class="state"><div class="spinner"></div>${refreshCache ? "正在重新解析本地文件…" : "读取本地解析缓存…"}</div>`;
  gapsBox.innerHTML = `<div class="state"><div class="spinner"></div>读取证据缺口…</div>`;
  if (rescanButton) {
    rescanButton.disabled = true;
    rescanButton.textContent = refreshCache ? "正在重新解析…" : "正在扫描…";
  }
  const query = new URLSearchParams({
    limit: metricEvidenceState.limit,
    offset: metricEvidenceState.offset,
    file_limit: "100",
    stale_days: "30",
    refresh_cache: refreshCache ? "true" : "false",
    priority: metricEvidenceState.gapPriority || "all",
  });
  const projectId = Number(metricEvidenceState.projectId || 0);
  if (Number.isInteger(projectId) && projectId > 0) query.set("project_id", String(projectId));
  try {
    metricEvidencePayload = await api(`/api/metrics/evidence?${query.toString()}`);
    metricEvidenceFileRows = new Map((metricEvidencePayload.files || []).map((row) => [String(row.path), row]));
    for (const path of [...metricEvidenceSelected]) {
      if (!metricEvidenceFileRows.get(path)?.can_apply) metricEvidenceSelected.delete(path);
    }
    renderMetricEvidenceSummary(metricEvidencePayload);
    renderMetricEvidenceFiles();
    renderMetricEvidenceProjectScope(metricEvidencePayload.gaps || {});
    renderMetricEvidenceGaps(metricEvidencePayload.gaps || {});
  } catch (err) {
    filesBox.innerHTML = `<div class="state error">${escapeHtml(err.message)}</div>`;
    gapsBox.innerHTML = `<div class="state error">${escapeHtml(err.message)}</div>`;
  } finally {
    if (rescanButton && document.body.contains(rescanButton)) {
      rescanButton.disabled = false;
      rescanButton.textContent = "重新解析本地文件";
    }
  }
}

function renderMetricEvidenceSummary(data) {
  const summary = data.summary || {};
  const cache = data.cache || {};
  const root = document.getElementById("metric-evidence-root");
  if (root) {
    const checked = Number(cache.hit_count || 0) + Number(cache.miss_count || 0);
    const seconds = Number(cache.duration_ms || 0) / 1000;
    root.textContent = `${data.root || "html/_details"} · 缓存命中 ${fmt.int(cache.hit_count)}/${fmt.int(checked)} · ${seconds.toFixed(seconds >= 10 ? 1 : 2)} 秒`;
    root.title = `${cache.manifest || "cache/detail_reparse_manifest.json"} · 分析器 ${cache.analysis_version || "—"}`;
  }
  const cacheWarning = document.getElementById("metric-evidence-cache-warning");
  if (cacheWarning) {
    cacheWarning.hidden = !cache.warning;
    cacheWarning.textContent = cache.warning || "";
  }
  const box = document.getElementById("metric-evidence-summary");
  if (!box) return;
  const items = [
    ["商品总数", summary.product_total],
    ["未采集详情", summary.not_collected_total],
    ["字段不全", summary.partial_product_total],
    ["样本过旧", summary.stale_product_total],
    ["本地 HTML", summary.local_file_count],
    ["可回填文件", summary.replayable_file_count],
    ["解析器需适配", summary.parser_unrecognized_file_count],
    ["缓存命中", summary.cache_hit_count],
    ["本次重解析", summary.cache_miss_count],
  ];
  box.innerHTML = items.map(([label,value]) => `<div><span>${label}</span><b>${fmt.int(value)}</b></div>`).join("");
}

function renderMetricEvidenceFiles() {
  const box = document.getElementById("metric-evidence-files");
  if (!box) return;
  const rows = [...metricEvidenceFileRows.values()].filter((row) =>
    metricEvidenceState.fileStatus === "all" || row.status === metricEvidenceState.fileStatus);
  if (!rows.length) {
    box.innerHTML = `<div class="state">当前筛选下没有本地详情 HTML。</div>`;
  } else {
    box.innerHTML = tableHtml(
      ["", "ASIN / 文件", "原始采集时间", "证据状态", "字段覆盖", "待写动作", "操作"],
      rows.map((row) => ({ cells: [
        `<input class="prod-check metric-evidence-check" type="checkbox" value="${escapeHtml(row.path)}"${metricEvidenceSelected.has(row.path) ? " checked" : ""}${row.can_apply ? "" : " disabled"} aria-label="选择 ${escapeHtml(row.asin || row.file_name)}" />`,
        `<b>${escapeHtml(row.asin || "—")}</b><div class="set-hint" title="${escapeHtml(row.path)}">${escapeHtml(truncate(row.file_name, 45))}</div>`,
        escapeHtml(fmt.text(row.captured_at)),
        `${metricEvidenceStatusBadge(row.status)}${row.reason ? `<div class="set-hint">${escapeHtml(row.reason)}</div>` : ""}`,
        metricEvidenceCoverageBadges(row.coverage),
        fmt.int(row.change_count),
        `<button class="btn btn-sm metric-evidence-one-preview" type="button" data-path="${escapeHtml(row.path)}">查看差异</button>`,
      ] }))
    );
    box.querySelectorAll(".metric-evidence-check").forEach((checkbox) => {
      checkbox.onchange = () => {
        if (checkbox.checked) metricEvidenceSelected.add(checkbox.value);
        else metricEvidenceSelected.delete(checkbox.value);
        updateMetricEvidenceSelection();
      };
    });
    box.querySelectorAll(".metric-evidence-one-preview").forEach((button) => {
      button.onclick = () => previewMetricEvidence([button.dataset.path]);
    });
  }
  updateMetricEvidenceSelection();
}

function metricEvidenceCoverageBadges(coverage) {
  return `<div class="metric-evidence-coverage">${Object.values(coverage || {}).map((item) => {
    const cls = item.status === "collected" ? "badge-good" : item.status === "parser_unrecognized" ? "badge-bad" : "badge-dim";
    return `<span class="badge ${cls}" title="${escapeHtml(metricEvidenceStatusLabel(item.status))}">${escapeHtml(item.label)}</span>`;
  }).join("")}</div>`;
}

function metricEvidenceStatusLabel(status) {
  return ({
    collected: "已解析", not_collected: "未采集", page_missing: "页面未提供",
    parser_unrecognized: "解析器需适配", stale: "样本过旧", ready: "可回填",
    invalid: "不可用", partial: "字段不全",
  })[status] || status || "未知";
}

function metricEvidenceStatusBadge(status) {
  const cls = ({
    collected: "badge-good", ready: "badge-good", page_missing: "badge-dim",
    parser_unrecognized: "badge-bad", stale: "badge-warn", invalid: "badge-bad",
    not_collected: "badge-dim", partial: "badge-warn",
  })[status] || "badge-dim";
  return `<span class="badge ${cls}">${escapeHtml(metricEvidenceStatusLabel(status))}</span>`;
}

function selectReplayableMetricEvidence() {
  [...metricEvidenceFileRows.values()].forEach((row) => {
    if (row.can_apply && (metricEvidenceState.fileStatus === "all" || row.status === metricEvidenceState.fileStatus)) {
      metricEvidenceSelected.add(row.path);
    }
  });
  renderMetricEvidenceFiles();
}

function updateMetricEvidenceSelection() {
  const count = metricEvidenceSelected.size;
  const label = document.getElementById("metric-evidence-selected");
  if (label) label.textContent = `已选 ${count} 个`;
  for (const id of ["metric-evidence-preview", "metric-evidence-apply"]) {
    const button = document.getElementById(id);
    if (button) button.disabled = count === 0;
  }
}

async function previewMetricEvidence(paths) {
  const box = document.getElementById("metric-evidence-preview-box");
  if (!paths.length) { notice("请先选择本地详情 HTML", "bad"); return; }
  box.innerHTML = `<div class="state"><div class="spinner"></div>读取解析结果并比较…</div>`;
  try {
    const result = await apiSend("/api/metrics/evidence/preview", "POST", { paths });
    box.innerHTML = renderMetricEvidencePreview(result.items || []);
  } catch (err) {
    box.innerHTML = `<div class="state error">${escapeHtml(err.message)}</div>`;
  }
}

function renderMetricEvidencePreview(items) {
  if (!items.length) return `<div class="state">没有可预览文件。</div>`;
  return items.map((item) => `
    <section class="metric-evidence-preview-item">
      <div class="metric-panel-head">
        <h3>${escapeHtml(item.asin || item.file_name)}</h3>
        ${metricEvidenceStatusBadge(item.status)}
      </div>
      <div class="set-hint">${escapeHtml(item.path)} · ${escapeHtml(fmt.text(item.captured_at))}</div>
      ${tableHtml(
        ["字段", "当前值", "解析值", "写入动作"],
        (item.changes || []).map((change) => ({ cells: [
          escapeHtml(change.label), metricEvidenceValue(change.current), metricEvidenceValue(change.parsed),
          `<b>${escapeHtml(metricEvidenceActionLabel(change.action))}</b>`,
        ] }))
      )}
    </section>`).join("");
}

function metricEvidenceValue(value) {
  if (value == null || value === "" || (Array.isArray(value) && !value.length)) return "—";
  if (typeof value === "object") return escapeHtml(truncate(JSON.stringify(value), 120));
  return escapeHtml(truncate(String(value), 120));
}

function metricEvidenceActionLabel(action) {
  return ({
    fill: "补空值", update: "更新当前值", snapshot: "写入历史快照", history: "合并历史关系",
    preserve_newer: "保留较新值", unchanged: "无需变更", missing: "本页缺失",
  })[action] || action || "—";
}

function metricEvidencePriorityBadge(row) {
  const tier = row?.priority_tier || "routine";
  const className = ({
    project_candidate: "metric-priority-candidate",
    project_benchmark: "badge-warn",
    project_related: "metric-priority-project",
    recent_observation: "metric-priority-recent",
    historical_project: "badge-dim",
    routine: "badge-dim",
  })[tier] || "badge-dim";
  return `<span class="badge ${className}" title="${escapeHtml(row?.priority_reason || "")}">${escapeHtml(row?.priority_label || "常规补全")}</span>`;
}

function metricEvidencePriorityContext(row) {
  const contexts = Array.isArray(row?.research_context) ? row.research_context : [];
  const projectLinks = contexts.slice(0, 2).map((context) => {
    const label = `${truncate(context.project_name || "未命名项目", 18)} · ${context.role_label || context.role || "关联"}`;
    const title = `${context.project_name || "未命名项目"} · ${context.project_status_label || context.project_status || "未知阶段"} · ${context.role_label || context.role || "关联"}`;
    return `<a class="metric-priority-project-link" href="#/research-projects/${encodeURIComponent(context.project_id)}" title="${escapeHtml(title)}">${escapeHtml(label)}</a>`;
  }).join("");
  const monitoring = row?.monitoring || {};
  const planBadge = Number(monitoring.due_plan_count || 0) > 0
    ? `<span class="badge badge-warn">人工计划到期</span>`
    : Number(monitoring.active_plan_count || 0) > 0
      ? `<span class="badge metric-priority-plan">人工计划关注</span>`
      : "";
  const more = row?.research_context_truncated ? `<span class="set-hint">另有关联项目</span>` : "";
  return `<div class="metric-priority-topline">${metricEvidencePriorityBadge(row)}${planBadge}</div>
    <div class="set-hint metric-priority-reason">${escapeHtml(truncate(row?.priority_reason || "", 92))}</div>
    ${projectLinks ? `<div class="metric-priority-projects">${projectLinks}${more}</div>` : ""}`;
}

function renderMetricEvidencePrioritySummary(rawPage) {
  const box = document.getElementById("metric-evidence-gap-summary");
  const policyBox = document.getElementById("metric-evidence-disposition-policy");
  if (!box) return;
  const summary = rawPage?.priority_summary || {};
  const items = [
    ["项目重点", summary.project_focus_total],
    ["进行中项目", summary.active_project_total],
    ["人工计划", summary.planned_total],
    ["计划已到期", summary.due_plan_total],
    ["近期活跃", summary.recent_observation_total],
  ];
  box.innerHTML = items.map(([label, value]) => `<span><b>${fmt.int(value)}</b> ${label}</span>`).join("");
  box.title = rawPage?.priority_policy?.sorting_scope || "";
  if (policyBox) {
    const disposition = rawPage?.disposition_summary || {};
    const policy = rawPage?.disposition_policy || {};
    policyBox.innerHTML = `<b>当前页处置</b>
      <span>${fmt.int(disposition.network_action_total)} 个需显式联网</span>
      <span>${fmt.int(disposition.local_action_total)} 个可先看本地证据</span>
      <span>${fmt.int(disposition.hold_total)} 个应保留缺失</span>
      <span>${fmt.int(disposition.review_total)} 个需人工核对</span>
      <small>${escapeHtml(policy.page_missing_meaning || "处置建议只提供操作引导，不会自动执行。")}</small>`;
  }
}

function metricEvidenceDispositionBadge(action) {
  const kind = action?.kind || "review";
  const className = ({
    network: "metric-disposition-network",
    local: "metric-disposition-local",
    hold: "badge-dim",
    review: "metric-disposition-review",
  })[kind] || "badge-dim";
  return `<span class="badge ${className}">${escapeHtml(action?.label || "人工核对")}</span>`;
}

function metricEvidenceDispositionContext(row) {
  const action = row?.recommended_action || {};
  const noLocalText = action.local_evidence_scan_complete
    ? "已核对本地目录，未发现匹配的详情 HTML"
    : action.local_evidence_checked
      ? "当前扫描范围未发现匹配的详情 HTML"
      : "本轮未核对本地详情 HTML";
  const local = row?.local_file_path
    ? `<div class="metric-disposition-local-file">${metricEvidenceStatusBadge(row.local_file_status)}<span title="${escapeHtml(row.local_file_path)}">${escapeHtml(truncate(row.local_file_path, 36))}</span></div>`
    : `<div class="set-hint">${noLocalText}</div>`;
  return `<div class="metric-disposition-cell">
    ${metricEvidenceDispositionBadge(action)}
    <div class="metric-disposition-reason">${escapeHtml(action.reason || "请人工核对当前证据。")}</div>
    ${action.follow_up ? `<div class="set-hint">${escapeHtml(action.follow_up)}</div>` : ""}
    ${local}
  </div>`;
}

function metricEvidenceDispositionActions(row) {
  const action = row?.recommended_action || {};
  const command = action.primary_command || "";
  let primary = "";
  if (command === "collect_detail") {
    const busy = metricEvidenceCollectingAsin === row.asin;
    primary = `<button class="btn btn-sm metric-evidence-collect-detail" type="button"
      data-asin="${escapeHtml(row.asin)}" data-action-code="${escapeHtml(action.code || "collect_gap")}"
      data-action-label="${escapeHtml(action.label || "采集详情")}" data-action-reason="${escapeHtml(action.reason || "")}"
      ${metricEvidenceBrowserAction ? " disabled" : ""}
      aria-label="${escapeHtml(action.button_label || "采集详情")} ${escapeHtml(row.asin)}">${busy ? "处理中…" : escapeHtml(action.button_label || "采集详情")}</button>`;
  } else if (command === "preview_local" && row.local_file_path) {
    primary = `<button class="btn btn-sm metric-evidence-inspect-local" type="button" data-path="${escapeHtml(row.local_file_path)}">${escapeHtml(action.button_label || "查看本地证据")}</button>`;
  } else {
    primary = `<span class="set-hint">暂无直接动作</span>`;
  }
  return `<div class="metric-disposition-actions">${primary}<a class="btn btn-sm" href="#/product/${encodeURIComponent(row.asin)}">商品详情</a><a class="btn btn-sm" href="#/metrics/${encodeURIComponent(row.asin)}">指标</a></div>`;
}

async function applySelectedMetricEvidence() {
  const paths = [...metricEvidenceSelected];
  if (!paths.length) { notice("请先选择可回填文件", "bad"); return; }
  if (!confirm(`确认回填 ${paths.length} 份本地详情 HTML？\n\n系统不会访问 Amazon；将按原始采集时间写入历史证据，旧文件不会覆盖较新当前值。`)) return;
  const button = document.getElementById("metric-evidence-apply");
  const box = document.getElementById("metric-evidence-preview-box");
  if (button) button.disabled = true;
  box.innerHTML = `<div class="state"><div class="spinner"></div>重新校验并回填…</div>`;
  try {
    const result = await apiSend("/api/metrics/evidence/apply", "POST", { paths });
    metricEvidenceSelected.clear();
    await loadMetricEvidence();
    const currentBox = document.getElementById("metric-evidence-preview-box");
    if (currentBox) currentBox.innerHTML = `<div class="metric-import-summary"><span>已回填 <b>${fmt.int(result.applied)}</b> 份</span><span>拒绝 <b>${fmt.int((result.rejected || []).length)}</b> 份</span></div>`;
    notice(`本地详情回填完成：${result.applied} 份`, (result.rejected || []).length ? "bad" : "ok");
  } catch (err) {
    box.innerHTML = `<div class="state error">${escapeHtml(err.message)}</div>`;
    notice(err.message, "bad");
  } finally {
    if (button && document.body.contains(button)) button.disabled = metricEvidenceSelected.size === 0;
  }
}

function renderMetricEvidenceGaps(rawPage) {
  const page = normalizePage(rawPage, metricEvidenceState.limit);
  const box = document.getElementById("metric-evidence-gaps");
  const meta = document.getElementById("metric-evidence-gap-meta");
  const pager = document.getElementById("metric-evidence-gap-pager");
  if (!box || !pager) return;
  const projectFilter = rawPage?.project_filter || {};
  const projectLabel = projectFilter.active
    ? ` · ${projectFilter.project_name || `项目 #${projectFilter.project_id}`}`
    : "";
  if (meta) meta.textContent = `${pageSummary(page, "待补商品")} · ${rawPage?.priority_filter_label || "全部优先级"}${projectLabel}`;
  renderMetricEvidencePrioritySummary(rawPage);
  if (!page.rows.length) {
    box.innerHTML = `<div class="state">${projectFilter.active ? "当前项目在所选优先级下没有详情证据缺口。" : "当前没有详情证据缺口。"}</div>`;
  } else {
    box.innerHTML = tableHtml(
      ["商品", "补采优先级", "状态", "证据缺口", "处置建议", "最近观察", "操作"],
      page.rows.map((row) => ({ cells: [
        `<b>${escapeHtml(truncate(displayTitle(row, row.asin), 58))}</b><div class="set-hint">${escapeHtml(row.asin)}</div>`,
        metricEvidencePriorityContext(row),
        metricEvidenceStatusBadge(row.evidence_status),
        escapeHtml((row.reasons || []).join("、") || "—"),
        metricEvidenceDispositionContext(row),
        `<div>详情：${escapeHtml(fmt.text(row.detail_collected_at))}</div><div class="set-hint">搜索：${escapeHtml(fmt.text(row.last_seen_at))}</div>`,
        metricEvidenceDispositionActions(row),
      ] }))
    );
    box.querySelectorAll(".metric-evidence-collect-detail").forEach((button) => {
      button.onclick = () => collectMetricEvidenceGap(button.dataset.asin || "", {
        code: button.dataset.actionCode || "collect_gap",
        label: button.dataset.actionLabel || "采集详情",
        reason: button.dataset.actionReason || "",
      });
    });
    box.querySelectorAll(".metric-evidence-inspect-local").forEach((button) => {
      button.onclick = () => inspectMetricEvidenceGap(button.dataset.path || "");
    });
  }
  pager.innerHTML = renderPager("metric-evidence-pager", page, [20, 50, 100]);
  bindPager("metric-evidence-pager", metricEvidenceState, page, loadMetricEvidence);
}

function renderMetricEvidenceProjectScope(rawPage = {}) {
  const box = document.getElementById("metric-evidence-project-scope");
  if (!box) return;
  const filter = rawPage?.project_filter || {};
  const stateProjectId = Number(metricEvidenceState.projectId || 0);
  const projectId = Number(filter.project_id || stateProjectId || 0);
  if (!Number.isInteger(projectId) || projectId < 1) {
    box.hidden = true;
    box.innerHTML = "";
    return;
  }
  const projectName = String(filter.project_name || metricEvidenceState.projectName || `项目 #${projectId}`);
  if (filter.active && projectName !== metricEvidenceState.projectName) {
    metricEvidenceState.projectName = projectName;
    persistState(metricEvidenceState);
  }
  box.hidden = false;
  box.innerHTML = `
    <span class="badge badge-good">项目范围</span>
    <b>${escapeHtml(projectName)}</b>
    <span>${escapeHtml(filter.project_status_label || "读取中")} · 只显示该项目成员的详情缺口</span>
    <a class="link" href="#/research-projects/${encodeURIComponent(projectId)}">返回项目</a>
    <button class="btn btn-sm" id="metric-evidence-clear-project" type="button">清除项目范围</button>`;
  document.getElementById("metric-evidence-clear-project").onclick = () => {
    metricEvidenceState.projectId = "";
    metricEvidenceState.projectName = "";
    metricEvidenceState.offset = 0;
    persistState(metricEvidenceState);
    loadMetricEvidence();
  };
}

async function inspectMetricEvidenceGap(path) {
  const normalizedPath = String(path || "").trim();
  if (!normalizedPath) return notice("当前商品没有可核对的本地详情证据", "bad");
  await previewMetricEvidence([normalizedPath]);
  document.getElementById("metric-evidence-preview-panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function openAmazonForMetricEvidence() {
  if (metricEvidenceBrowserAction) {
    notice("浏览器操作正在执行，请稍候", "bad");
    return;
  }
  if (!confirm("预开启 Amazon 页面？\n\n会打开或复用采集浏览器，供你先处理地址、登录或验证码。")) return;
  const button = document.getElementById("metric-evidence-open-amazon");
  const status = document.getElementById("metric-evidence-collection-status");
  metricEvidenceBrowserAction = "open";
  if (button) button.disabled = true;
  if (metricEvidencePayload?.gaps) renderMetricEvidenceGaps(metricEvidencePayload.gaps);
  if (status) status.innerHTML = `<span class="mini-spinner"></span>正在打开 Amazon 页面…`;
  try {
    const result = await runBrowserAction(() => apiSend("/api/crawl/open-amazon", "POST"));
    if (status) status.textContent = `Amazon 页面已打开：${result?.["标题"] || "可在采集浏览器中继续处理页面状态"}`;
    notice("Amazon 页面已打开", "ok");
  } catch (err) {
    if (status) status.textContent = err.message || "Amazon 页面打开失败";
    notice(err.message || "Amazon 页面打开失败", "bad");
  } finally {
    metricEvidenceBrowserAction = "";
    if (button && document.body.contains(button)) button.disabled = false;
    if (metricEvidencePayload?.gaps) renderMetricEvidenceGaps(metricEvidencePayload.gaps);
  }
}

async function collectMetricEvidenceGap(asin, disposition = {}) {
  const normalizedAsin = String(asin || "").trim().toUpperCase();
  if (!normalizedAsin) return;
  if (metricEvidenceBrowserAction) {
    notice("已有浏览器操作正在执行，请等待完成", "bad");
    return;
  }
  const actionLabel = disposition.label || "采集详情";
  const actionReason = disposition.reason ? `处置依据：${disposition.reason}\n\n` : "";
  if (!confirm(
    `确认执行“${actionLabel}”：ASIN ${normalizedAsin}？\n\n` + actionReason +
    "本次只访问这 1 个详情页；遇到验证码、登录页或 ASIN 不一致会立即停止，不会继续采集其他商品。"
  )) return;

  const status = document.getElementById("metric-evidence-collection-status");
  metricEvidenceBrowserAction = "collect";
  metricEvidenceCollectingAsin = normalizedAsin;
  if (metricEvidencePayload?.gaps) renderMetricEvidenceGaps(metricEvidencePayload.gaps);
  if (status) status.innerHTML = `<span class="mini-spinner"></span>正在执行${escapeHtml(actionLabel)}：${escapeHtml(normalizedAsin)}…`;
  let refreshed = false;
  try {
    const result = await runBrowserAction(
      () => apiSend(`/api/products/${encodeURIComponent(normalizedAsin)}/collect-detail`, "POST")
    );
    const missing = Array.isArray(result?.["未采集字段"]) ? result["未采集字段"] : [];
    const found = Number(result?.["已采集字段数"] || 0);
    metricEvidenceBrowserAction = "";
    metricEvidenceCollectingAsin = "";
    await loadMetricEvidence();
    refreshed = true;
    const currentStatus = document.getElementById("metric-evidence-collection-status");
    if (currentStatus) {
      currentStatus.textContent = missing.length
        ? `${normalizedAsin} 已采集 ${found} 项；${missing.join("、")}未在当前页面出现。`
        : `${normalizedAsin} 详情采集完成，证据队列已刷新。`;
    }
    notice(missing.length ? `详情采集完成；${missing.join("、")}未采集` : "商品详情采集完成", "ok");
  } catch (err) {
    if (status) status.textContent = err.message || "商品详情采集失败";
    notice(err.message || "商品详情采集失败", "bad");
  } finally {
    metricEvidenceBrowserAction = "";
    metricEvidenceCollectingAsin = "";
    if (!refreshed && metricEvidencePayload?.gaps) renderMetricEvidenceGaps(metricEvidencePayload.gaps);
  }
}

async function viewMetricProduct(asin) {
  loading();
  const data = await api(`/api/metrics/products/${encodeURIComponent(asin)}`);
  const product = data.product || {};
  const quality = data.data_quality || {};
  const latestInput = Array.isArray(data.inputs) ? data.inputs[0] : null;
  content.innerHTML = `
    <header class="metric-page-head">
      <div>
        <a class="link back-link" href="#/metrics">← 返回指标概览</a>
        <h2>${escapeHtml(truncate(displayTitle(product, asin), 100))}</h2>
        <div class="meta">${amazonProductLink(product, asin)} · 模型 ${escapeHtml(data.model?.version || "—")} · 综合评分未接入</div>
      </div>
      <div class="metric-page-actions">
        <a class="btn" href="#/product/${encodeURIComponent(asin)}">商品详情</a>
        <button class="btn" id="metric-refresh-estimates" type="button">重新计算估算</button>
      </div>
    </header>
    <div class="metric-layer-legend">
      ${metricLayerBadge("采集事实")}${metricLayerBadge("确定性计算")}${metricLayerBadge("经验估算")}${metricLayerBadge("人工/官方输入")}
    </div>
    ${renderMetricQuality(quality)}
    ${renderMetricEvidence(data)}
    <section class="panel">
      <h2>确定性指标</h2>
      ${renderMetricRows(data.deterministic_metrics, false)}
    </section>
    <section class="panel">
      <h2>经验情景与相对诊断</h2>
      <div class="metric-risk-note">经验转化率用于情景计算，不代表该竞品的真实转化率；BSR 月销量模型当前停用。</div>
      ${renderMetricEstimates(data.estimates)}
    </section>
    <section class="panel">
      <h2>人工 / 官方精确指标</h2>
      ${latestInput ? renderMetricRows(data.exact_metrics, true) : `<div class="state">暂无 Sessions、销量或广告输入，精确转化与投放指标未计算。</div>`}
    </section>
    ${renderMetricSerpContexts(data.serp_contexts)}
    ${renderMetricInputForm(asin)}
    ${renderMetricInputHistory(data.inputs)}
  `;
  document.getElementById("metric-refresh-estimates").onclick = () => refreshMetricEstimates(asin);
  bindMetricInputForm(asin);
}

function renderMetricQuality(quality) {
  const checks = Object.entries(quality.checks || {});
  return `<section class="panel metric-quality-panel">
    <div class="metric-panel-head"><h2>数据质量</h2>${scoreBadge(quality.score)}</div>
    <div class="metric-summary-grid">
      ${checks.map(([label, present]) => `<div><span>${escapeHtml(label)}</span><b class="${present ? "metric-present" : "metric-absent"}">${present ? "已有" : "缺失"}</b></div>`).join("")}
    </div>
    <div class="result-meta">最近快照距今 ${quality.latest_snapshot_age_days == null ? "—" : `${fmt.int(quality.latest_snapshot_age_days)} 天`} · 缺失项 ${fmt.int((quality.missing || []).length)}</div>
  </section>`;
}

function renderMetricEvidence(data) {
  const offer = data.latest_offer || {};
  const specs = data.physical_specs || {};
  const bsr = Array.isArray(data.latest_bsr) ? data.latest_bsr : [];
  const variants = Array.isArray(data.variants) ? data.variants : [];
  const badges = Array.isArray(offer.badges_json) ? offer.badges_json.join("、") : "";
  return `<section class="panel">
    <h2>采集事实</h2>
    <div class="metric-evidence-grid">
      <section class="metric-evidence-section">
        <h3>报价与履约</h3>
        <div class="metric-fact-list">
          ${metricFact("详情价格", fmt.money(offer.current_price))}
          ${metricFact("标价", fmt.money(offer.list_price))}
          ${metricFact("优惠", offer.discount_percent == null ? "—" : `${fmt.num(offer.discount_percent)}%`)}
          ${metricFact("库存", metricAvailability(offer.availability_status))}
          ${metricFact("卖家", escapeHtml(fmt.text(offer.featured_offer_seller)))}
          ${metricFact("发货方", escapeHtml(fmt.text(offer.ships_from)))}
          ${metricFact("履约", escapeHtml(fmt.text(offer.fulfillment_channel)))}
          ${metricFact("Prime", metricBoolean(offer.is_prime))}
          ${metricFact("页面徽标", escapeHtml(badges || "—"))}
        </div>
      </section>
      <section class="metric-evidence-section">
        <h3>物理规格</h3>
        <div class="metric-fact-list">
          ${metricFact("商品尺寸", formatMetricDimensions(specs, "item"))}
          ${metricFact("包装尺寸", formatMetricDimensions(specs, "package"))}
          ${metricFact("商品重量", formatMetricWeight(specs.item_weight_oz))}
          ${metricFact("包装重量", formatMetricWeight(specs.package_weight_oz))}
          ${metricFact("单位数量", fmt.num(specs.unit_count))}
          ${metricFact("型号", escapeHtml(fmt.text(specs.model_number)))}
          ${metricFact("父 ASIN", escapeHtml(fmt.text(specs.parent_asin)))}
        </div>
      </section>
      <section class="metric-evidence-section">
        <h3>页面内容</h3>
        <div class="metric-fact-list">
          ${metricFact("图片数", fmt.int(offer.image_count))}
          ${metricFact("视频数", fmt.int(offer.video_count))}
          ${metricFact("卖点条目", fmt.int(offer.bullet_count))}
          ${metricFact("A+ 内容", metricBoolean(offer.has_a_plus))}
          ${metricFact("其他报价", fmt.int(offer.offer_count))}
          ${metricFact("展示邮编", escapeHtml(fmt.text(offer.postal_code)))}
        </div>
      </section>
      <section class="metric-evidence-section">
        <h3>BSR 与变体</h3>
        <div class="metric-fact-list">
          ${metricFact("最新 BSR", renderBestSellerRanks(bsr.map((row) => ({ rank: row.rank_value, category_name: row.category_name, is_primary: row.is_primary }))))}
          ${metricFact("已识别变体", fmt.int(variants.length))}
          ${metricFact("详情采集", escapeHtml(fmt.text(data.product?.detail_collected_at)))}
        </div>
      </section>
    </div>
    ${variants.length ? `<div class="metric-variant-strip">${variants.slice(0, 20).map((item) => `<span><b>${escapeHtml(item.child_asin)}</b>${formatVariantAttributes(item.attributes_json)}</span>`).join("")}</div>` : ""}
  </section>`;
}

function metricFact(label, value) {
  return `<div><span>${escapeHtml(label)}</span><strong>${value == null || value === "" ? "—" : value}</strong></div>`;
}

function metricAvailability(value) {
  return ({ in_stock: "在售", out_of_stock: "缺货", limited: "库存紧张", preorder: "预售", unknown: "未知" })[value] || "—";
}

function metricBoolean(value) {
  if (value == null) return "—";
  return value === true || value === 1 ? "是" : "否";
}

function formatMetricDimensions(specs, prefix) {
  const values = [specs?.[`${prefix}_length_in`], specs?.[`${prefix}_width_in`], specs?.[`${prefix}_height_in`]];
  if (values.every((value) => value != null)) return `${values.map((value) => fmt.num(value, 2)).join(" × ")} 英寸`;
  const raw = specs?.raw_dimensions_json;
  if (raw && typeof raw === "object") {
    const match = Object.entries(raw).find(([key]) => String(key).toLowerCase().includes(prefix === "item" ? "product" : "package"));
    if (match) return escapeHtml(String(match[1]));
  }
  return "—";
}

function formatMetricWeight(value) {
  return value == null ? "—" : `${fmt.num(value, 2)} 盎司`;
}

function formatVariantAttributes(value) {
  if (!value || typeof value !== "object") return "";
  const text = Object.values(value).flat().filter(Boolean).join(" / ");
  return text ? `<small>${escapeHtml(text)}</small>` : "";
}

function renderMetricRows(rows, exact) {
  const list = Array.isArray(rows) ? rows : [];
  if (!list.length) return `<div class="state">暂无可计算指标。</div>`;
  return `<div class="metric-value-table">${tableHtml(
    ["指标", "数值", exact ? "数据层" : "置信", exact ? "公式" : "口径说明"],
    list.map((row) => ({ cells: [
      `<b>${escapeHtml(row.label)}</b>`,
      `<span class="num">${formatMetricValue(row.value, row.unit)}</span>`,
      exact ? metricLayerBadge(row.layer || "人工/官方输入") : metricConfidenceBadge(row.confidence || "中"),
      escapeHtml(row.formula || row.explanation || "—"),
    ] }))
  )}</div>`;
}

function renderMetricEstimates(rows) {
  const list = Array.isArray(rows) ? rows : [];
  if (!list.length) return `<div class="state">当前证据不足，暂无估算。</div>`;
  return tableHtml(
    ["指标", "低值", "基准", "高值", "置信度", "方法 / 限制"],
    list.map((row) => ({ cells: [
      `<b>${escapeHtml(row.label)}</b><div class="set-hint">${metricLayerBadge(row.layer || "经验估算")}</div>`,
      formatMetricValue(row.low, row.unit),
      formatMetricValue(row.base, row.unit),
      formatMetricValue(row.high, row.unit),
      `${metricConfidenceBadge(row.confidence_level)} <span class="set-hint">${fmt.num(row.confidence_score)} / 100</span>`,
      `<b>${escapeHtml(metricMethodLabel(row.method))}</b><div class="set-hint">${escapeHtml(metricEstimateEvidence(row.evidence))}</div>`,
    ] }))
  );
}

function metricEstimateEvidence(evidence) {
  if (!evidence || typeof evidence !== "object") return "—";
  if (evidence.limitation) return evidence.limitation;
  if (evidence.source) return `来源：${evidence.source}`;
  if (evidence.category) return `${evidence.category} · 样本 ${fmt.int(evidence.sample_size)}`;
  if (evidence.not_actual_cvr) return "相对诊断，不是真实转化率。";
  return "证据已记录";
}

function metricMethodLabel(method) {
  return ({
    editable_scenario_assumption: "可编辑经验情景",
    monthly_bought_floor_divided_by_cvr_scenario: "需求下界 ÷ 转化率情景",
    relative_listing_evidence_score: "页面证据相对评分",
    latest_category_bsr_percentile: "同类目 BSR 百分位",
  })[method] || method || "—";
}

function formatMetricValue(value, unit) {
  if (value == null || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return escapeHtml(String(value));
  if (unit === "ratio") return `${(number * 100).toFixed(2)}%`;
  if (unit === "%") return `${number.toFixed(2)}%`;
  if (String(unit).startsWith("currency")) return fmt.money(number);
  if (["units/month", "sessions/month", "days", "variants"].includes(unit)) return fmt.int(number);
  if (unit === "score/100") return `${number.toFixed(1)} / 100`;
  if (unit === "x") return `${number.toFixed(2)}x`;
  return `${fmt.num(number, 3)}${unit ? ` ${escapeHtml(unit)}` : ""}`;
}

function metricLayerBadge(layer) {
  const text = String(layer || "未知");
  let cls = "metric-layer-observed";
  if (text.includes("确定性")) cls = "metric-layer-derived";
  else if (text.includes("经验") || text.includes("相对诊断")) cls = "metric-layer-estimated";
  else if (text.includes("人工") || text.includes("官方")) cls = "metric-layer-input";
  return `<span class="metric-layer ${cls}">${escapeHtml(text)}</span>`;
}

function metricConfidenceBadge(level) {
  const text = String(level || "低");
  const cls = text === "高" ? "badge-good" : text === "中" ? "badge-warn" : "badge-bad";
  return `<span class="badge ${cls}">${escapeHtml(text)}</span>`;
}

function renderMetricSerpContexts(rows) {
  const list = Array.isArray(rows) ? rows : [];
  return `<section class="panel">
    <h2>关键词市场快照</h2>
    ${list.length ? tableHtml(
      ["关键词", "时间", "页数", "广告密度", "价格中位数", "评论中位数", "近月购买中位数", "CR3", "覆盖率"],
      list.map((row) => ({ cells: [
        `<b>${escapeHtml(row.keyword)}</b>`, escapeHtml(fmt.text(row.snapshot_at)), fmt.int(row.page_count),
        formatMetricValue(row.ad_density, "ratio"), fmt.money(row.price_median), fmt.int(row.review_median),
        fmt.int(row.monthly_bought_median), formatMetricValue(row.demand_cr3, "ratio"), formatMetricValue(row.data_coverage, "ratio"),
      ] }))
    ) : `<div class="state">该商品尚未关联已生成的关键词 SERP 聚合快照。</div>`}
  </section>`;
}

function renderMetricInputForm(asin) {
  const draft = loadMetricDraft(asin);
  const today = new Date();
  const end = today.toISOString().slice(0, 10);
  const start = `${end.slice(0, 8)}01`;
  const values = { period_start: start, period_end: end, source_type: "manual", assumed_cvr_low: "3", assumed_cvr_base: "8", assumed_cvr_high: "15", ...draft };
  return `<section class="panel">
    <h2>录入卖家 / 官方数据</h2>
    <form id="metric-input-form" class="metric-input-form" data-asin="${escapeHtml(asin)}">
      <div class="metric-form-grid metric-form-base">
        ${metricFormField("period_start", "统计周期开始", values, "date", "1", true)}
        ${metricFormField("period_end", "统计周期结束", values, "date", "1", true)}
        <label><span>来源类型</span><select data-metric-field="source_type" class="sel">
          ${[["manual","人工录入"],["sp_api","SP-API"],["ads_api","Ads API"],["third_party","第三方数据"]].map(([value,label]) => `<option value="${value}"${values.source_type === value ? " selected" : ""}>${label}</option>`).join("")}
        </select></label>
        ${metricFormField("source_label", "来源说明", values, "text")}
      </div>
      <details open><summary>流量与销售</summary><div class="metric-form-grid">
        ${metricFormField("sessions", "Sessions", values)}
        ${metricFormField("page_views", "页面浏览量", values)}
        ${metricFormField("units_ordered", "订购件数", values)}
        ${metricFormField("orders", "订单数", values)}
        ${metricFormField("ordered_sales", "订购销售额", values, "number", "0.01")}
        ${metricFormField("featured_offer_percentage", "Featured Offer（%）", values, "number", "0.01")}
      </div></details>
      <details><summary>漏斗与广告</summary><div class="metric-form-grid">
        ${metricFormField("impressions", "曝光量", values)}
        ${metricFormField("clicks", "点击量", values)}
        ${metricFormField("cart_adds", "加购量", values)}
        ${metricFormField("purchases", "购买量", values)}
        ${metricFormField("ad_spend", "广告花费", values, "number", "0.01")}
        ${metricFormField("ad_clicks", "广告点击", values)}
        ${metricFormField("ad_orders", "广告订单", values)}
        ${metricFormField("ad_sales", "广告销售额", values, "number", "0.01")}
        ${metricFormField("total_sales", "总销售额", values, "number", "0.01")}
      </div></details>
      <details><summary>单件成本</summary><div class="metric-form-grid">
        ${metricFormField("unit_purchase_cost", "采购成本", values, "number", "0.01")}
        ${metricFormField("unit_shipping_cost", "头程 / 运输", values, "number", "0.01")}
        ${metricFormField("unit_fba_fee", "FBA 费用", values, "number", "0.01")}
        ${metricFormField("unit_referral_fee", "Amazon 佣金", values, "number", "0.01")}
        ${metricFormField("unit_other_cost", "其他成本", values, "number", "0.01")}
      </div></details>
      <details><summary>经验转化率情景</summary><div class="metric-form-grid">
        ${metricFormField("assumed_cvr_low", "低情景（%）", values, "number", "0.01")}
        ${metricFormField("assumed_cvr_base", "中情景（%）", values, "number", "0.01")}
        ${metricFormField("assumed_cvr_high", "高情景（%）", values, "number", "0.01")}
      </div></details>
      <label class="metric-notes"><span>备注</span><textarea data-metric-field="notes" rows="3">${escapeHtml(values.notes || "")}</textarea></label>
      <div class="actions"><button class="btn" id="metric-input-save" type="submit">保存并重算</button><button class="btn" id="metric-input-clear" type="button">清空草稿</button></div>
    </form>
  </section>`;
}

function metricFormField(name, label, values, type = "number", step = "1", required = false) {
  const value = values?.[name] ?? "";
  return `<label><span>${escapeHtml(label)}</span><input data-metric-field="${name}" type="${type}" value="${escapeHtml(value)}"${type === "number" ? ` min="0" step="${step}"` : ""}${required ? " required" : ""} /></label>`;
}

function bindMetricInputForm(asin) {
  const form = document.getElementById("metric-input-form");
  if (!form) return;
  const saveDraft = () => saveMetricDraft(asin, collectMetricInputForm(form));
  form.querySelectorAll("[data-metric-field]").forEach((element) => {
    element.addEventListener("input", saveDraft);
    element.addEventListener("change", saveDraft);
  });
  document.getElementById("metric-input-clear").onclick = () => {
    clearMetricDraft(asin);
    renderMetricProductAgain(asin);
  };
  form.onsubmit = async (event) => {
    event.preventDefault();
    const body = collectMetricInputForm(form);
    if (!confirm(`确认保存 ${asin} 的指标输入？\n\n该操作会写入 MySQL，并按当前模型重新计算估算。`)) return;
    const button = document.getElementById("metric-input-save");
    if (button) button.disabled = true;
    try {
      await apiSend(`/api/metrics/products/${encodeURIComponent(asin)}/inputs`, "POST", body);
      clearMetricDraft(asin);
      notice("指标已保存并重新计算", "ok");
      await viewMetricProduct(asin);
    } catch (err) {
      notice(err.message, "bad");
    } finally {
      if (button && document.body.contains(button)) button.disabled = false;
    }
  };
}

function collectMetricInputForm(form) {
  const result = {};
  form.querySelectorAll("[data-metric-field]").forEach((element) => {
    const key = element.dataset.metricField;
    const value = String(element.value || "").trim();
    result[key] = value === "" ? null : value;
  });
  return result;
}

function readMetricDraftBag() {
  try { return JSON.parse(localStorage.getItem(METRIC_DRAFT_STORAGE_KEY) || "{}"); } catch { return {}; }
}
function loadMetricDraft(asin) { return readMetricDraftBag()[asin] || {}; }
function saveMetricDraft(asin, value) {
  try { const bag = readMetricDraftBag(); bag[asin] = value; localStorage.setItem(METRIC_DRAFT_STORAGE_KEY, JSON.stringify(bag)); } catch { /* optional */ }
}
function clearMetricDraft(asin) {
  try { const bag = readMetricDraftBag(); delete bag[asin]; localStorage.setItem(METRIC_DRAFT_STORAGE_KEY, JSON.stringify(bag)); } catch { /* optional */ }
}
function renderMetricProductAgain(asin) { viewMetricProduct(asin).catch(errorState); }

async function refreshMetricEstimates(asin) {
  if (!confirm(`确认按当前证据重新计算 ${asin} 的版本化估算？`)) return;
  const button = document.getElementById("metric-refresh-estimates");
  if (button) button.disabled = true;
  try {
    await apiSend(`/api/metrics/products/${encodeURIComponent(asin)}/refresh-estimates`, "POST");
    notice("估算已重新计算", "ok");
    await viewMetricProduct(asin);
  } catch (err) {
    notice(err.message, "bad");
  } finally {
    if (button && document.body.contains(button)) button.disabled = false;
  }
}

function renderMetricInputHistory(rows) {
  const list = Array.isArray(rows) ? rows : [];
  return `<section class="panel"><h2>输入历史（${fmt.int(list.length)}）</h2>
    ${list.length ? tableHtml(
      ["周期", "来源", "Sessions", "订购件数", "销售额", "广告花费", "CVR 情景", "更新时间"],
      list.map((row) => ({ cells: [
        `${escapeHtml(row.period_start)} → ${escapeHtml(row.period_end)}`,
        escapeHtml(metricSourceLabel(row.source_type, row.source_label)),
        fmt.int(row.sessions), fmt.int(row.units_ordered), fmt.money(row.ordered_sales), fmt.money(row.ad_spend),
        row.assumed_cvr_low == null ? "—" : `${formatMetricValue(row.assumed_cvr_low, "ratio")} / ${formatMetricValue(row.assumed_cvr_base, "ratio")} / ${formatMetricValue(row.assumed_cvr_high, "ratio")}`,
        escapeHtml(fmt.text(row.updated_at)),
      ] }))
    ) : `<div class="state">暂无人工或官方输入。</div>`}
  </section>`;
}

function metricSourceLabel(type, label) {
  const source = ({ manual: "人工录入", csv: "CSV", sp_api: "SP-API", ads_api: "Ads API", third_party: "第三方" })[type] || type || "未知";
  return label ? `${source} · ${label}` : source;
}

/* ---------- 视图：商品详情 + 趋势曲线 ---------- */
function productContextApiPath(asin, suffix = "", scoreKeyword = "") {
  const path = `/api/products/${encodeURIComponent(asin)}${suffix}`;
  const keyword = String(scoreKeyword || "").trim();
  return keyword ? `${path}?score_keyword=${encodeURIComponent(keyword)}` : path;
}

async function viewProductDetail(asin, scoreKeyword = "") {
  loading();
  const [detail, metricData] = await Promise.all([
    api(productContextApiPath(asin, "", scoreKeyword)),
    api(`/api/metrics/products/${encodeURIComponent(asin)}`).catch((err) => ({
      _error: err?.message || "指标数据暂不可用",
    })),
  ]);
  const p = detail.product || detail || {};
  const domainProfiles = await api(`/api/domain-models?marketplace=${encodeURIComponent(p.marketplace || "US")}&status=active&limit=200&offset=0`).catch((err) => ({
    rows: [],
    _error: err?.message || "领域模型暂不可用",
  }));
  const activeScoreKeyword = p.score_keyword || scoreKeyword || "";
  const searchSnaps = (detail.snapshots || []).slice().sort((a, b) =>
    String(a.snapshot_at).localeCompare(String(b.snapshot_at)));
  const captures = (detail.capture_history?.length ? detail.capture_history : searchSnaps)
    .slice()
    .sort((a, b) => String(a.snapshot_at).localeCompare(String(b.snapshot_at)));
  const latest = {
    price: latestCaptureValue(captures, "price"),
    rating: latestCaptureValue(captures, "rating"),
    review_count: latestCaptureValue(captures, "review_count"),
    monthly_bought: latestCaptureValue(captures, "monthly_bought"),
  };
  const captureSummary = detail.capture_summary || {};
  const bsrRanks = Array.isArray(detail.best_seller_ranks) ? detail.best_seller_ranks : [];
  const returnTarget = productDetailReturnTarget();
  const returnTitle = routeTitleForHash(returnTarget);
  content.innerHTML = `
    <div class="detail-floating-back">
      <button class="btn btn-sm" id="product-detail-back" type="button" title="返回${escapeHtml(returnTitle)}" aria-label="返回${escapeHtml(returnTitle)}">← 返回</button>
    </div>
    <div class="detail-head">
      <div class="product-detail-title-row">
        <h2 class="product-detail-title">${amazonProductLink(p, displayTitle(p, asin), 100)}</h2>
        <div class="product-detail-latest-capture">
          <span>最近采集</span>
          <strong>${escapeHtml(fmt.text(captureSummary.latest_capture_at || p.detail_collected_at || p.last_seen_at))}</strong>
          <small>${escapeHtml(captureSummary.latest_capture_source || (p.detail_collected_at ? "详情页" : "搜索页"))}</small>
        </div>
      </div>
      <div class="product-identity">
        ${p.image_url ? `<figure class="product-hero">
          <img class="product-hero__img" loading="lazy" alt="商品主图" title="点击放大看细节"
               src="/api/products/${encodeURIComponent(asin)}/image"
               onclick="window.openImageZoom('${encodeURIComponent(asin)}')"
               onerror="this.closest('.product-hero').classList.add('product-hero--failed');this.closest('.product-identity').classList.add('product-identity--no-image')" />
          <figcaption class="product-hero__cap">商品图 · 点击放大看细节</figcaption>
        </figure>` : `<div class="product-image-empty">暂无商品图</div>`}
        <section class="product-summary" aria-label="商品信息">
          <div class="product-summary-head">
            <h3>商品信息</h3>
            <div class="actions">
              <a class="btn btn-sm" href="#/metrics/${encodeURIComponent(asin)}">指标中心</a>
              <button class="btn btn-sm" id="add-product-to-research" type="button">加入研究项目</button>
              <button class="btn btn-sm" id="collect-product-detail" type="button">采集商品详情</button>
            </div>
          </div>
          <div class="product-facts">
            <div class="product-fact"><span>ASIN</span><strong>${amazonProductLink(p, asin)}</strong></div>
            <div class="product-fact"><span>综合得分</span><strong>${scoreBadge(p.total_score)}</strong></div>
            <div class="product-fact"><span>评分关键词</span><strong>${escapeHtml(activeScoreKeyword || "旧数据未标注")}</strong></div>
            <div class="product-fact"><span>价格</span><strong>${fmt.money(latest.price)}</strong></div>
            <div class="product-fact"><span>评分 / 评论</span><strong>${fmt.num(latest.rating)} / ${fmt.int(latest.review_count)}</strong></div>
            <div class="product-fact"><span>近月购买</span><strong>${fmt.int(latest.monthly_bought)}</strong></div>
            <div class="product-fact"><span>尺寸 / 规格</span><strong>${escapeHtml(fmt.text(p.product_size))}</strong></div>
            <div class="product-fact"><span>系统首次观察</span><strong>${escapeHtml(fmt.text(p.first_seen_at))}</strong></div>
            <div class="product-fact"><span>最近搜索采集</span><strong>${escapeHtml(fmt.text(p.last_seen_at))}</strong></div>
            <div class="product-fact"><span>Amazon 首次上架日期</span><strong>${detailCollectedValue(p.date_first_available)}</strong></div>
            <div class="product-fact"><span>详情采集时间</span><strong>${detailCollectedValue(p.detail_collected_at)}</strong></div>
            <div class="product-fact product-fact-wide"><span>商品类别</span><strong>${detailCollectedValue(p.category_path)}</strong></div>
            <div class="product-fact product-fact-wide"><span>热销榜排名</span><strong>${renderBestSellerRanks(bsrRanks)}</strong></div>
          </div>
          ${renderLegacyScoreBreakdown(p)}
          ${renderProductDomainScorePanel(domainProfiles, p.marketplace || "US")}
          <div class="detail-evidence-note">
            ${escapeHtml(detail.rank_context?.message || "自然序位缺少关键词上下文。")}
            <br>顶部指标取各字段最近一次真实观测值；具体采集时间与来源见下方历史记录，不对缺失字段做跨时点伪造。
            ${detail.snapshot_warning ? `<br><span class="warning-text">${escapeHtml(detail.snapshot_warning)}</span>` : ""}
          </div>
          <div id="detail-collect-status" class="detail-collect-status"></div>
        </section>
      </div>
    </div>
    ${renderProductDetailMetricSections(metricData, p)}
    <div class="panel">
      <h2>选品建议</h2>
      <div id="advice"><div class="state"><div class="spinner"></div>评估中…</div></div>
    </div>
    <div class="panel">
      <h2>评论痛点</h2>
      ${renderReviewInsight(detail.review_insight)}
    </div>
    <div class="panel">
      <h2>搜索快照趋势置信度</h2>
      <div id="trend-conf"><div class="state"><div class="spinner"></div>评估中…</div></div>
    </div>
    <div class="panel">
      <div class="product-history-head">
        <h2>采集趋势曲线</h2>
        <span>搜索页 ${fmt.int(captureSummary.search_capture_count ?? searchSnaps.length)} · 详情页 ${fmt.int(captureSummary.detail_capture_count ?? 0)}</span>
      </div>
      <div class="detail-evidence-note product-history-note">曲线合并搜索页商品快照与详情页报价快照；详情页未观察到的字段保持空值，自然序位仍只来自对应关键词搜索快照。</div>
      ${captures.length >= 2
        ? `<div id="trend-chart" class="chart"></div>`
        : `<div class="state">仅有 ${captures.length} 个采集时间点，样本不足，暂无法绘制趋势（需 ≥2 个时间点）。</div>`}
    </div>
    <div class="panel">
      <div class="product-history-head">
        <h2>历史采集记录（${captures.length}）</h2>
        <span>按来源分层，不混写事实表</span>
      </div>
      ${captures.length ? snapTable(captures) : `<div class="state">暂无采集记录。</div>`}
    </div>`;
  document.getElementById("product-detail-back").onclick = window.returnFromProductDetail;
  document.getElementById("add-product-to-research").onclick = () => openResearchAssociationDialog({
    assetType: "product",
    items: [{ key: asin, label: displayTitle(p, asin), marketplace: p.marketplace || "US" }],
    marketplace: p.marketplace || "US",
  });
  document.getElementById("collect-product-detail").onclick = () => collectCurrentProductDetail(asin, activeScoreKeyword);
  bindProductDomainScorePanel(asin, activeScoreKeyword, p.marketplace || "US");
  if (captures.length >= 2) renderTrendChart(captures);
  fillTrendConfidence(asin, activeScoreKeyword);
  fillProductAdvice(asin, activeScoreKeyword);
}

function latestCaptureValue(rows, key) {
  for (let index = rows.length - 1; index >= 0; index -= 1) {
    const value = rows[index]?.[key];
    if (value !== null && value !== undefined && value !== "") return value;
  }
  return null;
}

function renderProductDetailMetricSections(data, product) {
  if (data?._error) {
    return `<section class="panel">
      <h2>采集事实与确定性指标</h2>
      <div class="state error">${escapeHtml(data._error)}</div>
    </section>`;
  }
  const hasDetail = Boolean(data?.product?.detail_collected_at || product?.detail_collected_at);
  if (!hasDetail) {
    return `<section class="panel">
        <div class="metric-panel-head"><h2>采集事实</h2>${metricLayerBadge("未采集")}</div>
        <div class="state">该商品尚未完成有效详情采集。报价、履约、物理规格、页面内容、BSR 与变体将在采集成功后更新。</div>
      </section>
      <section class="panel">
        <div class="metric-panel-head"><h2>确定性指标</h2>${metricLayerBadge("等待采集")}</div>
        <div class="state">详情证据尚未建立，暂不展示容易被误解为完整结论的指标；完成商品详情采集后自动更新。</div>
      </section>`;
  }
  return `${renderMetricEvidence(data)}
    <section class="panel">
      <div class="metric-panel-head"><h2>确定性指标</h2>${metricLayerBadge("确定性计算")}</div>
      ${renderMetricRows(data?.deterministic_metrics, false)}
    </section>`;
}

function renderLegacyScoreBreakdown(product) {
  const parts = [
    ["需求", product.demand_score, "25%"],
    ["增长（中性占位）", product.growth_score, "15%"],
    ["竞争", product.competition_score, "20%"],
    ["评分质量", product.rating_score, "15%"],
    ["价格带", product.price_score, "15%"],
    ["自然序位", product.rank_score, "10%"],
  ];
  return `<section class="legacy-score-breakdown" aria-label="旧版综合得分拆解">
    <div class="legacy-score-head"><strong>旧版综合得分拆解</strong><span>六项加权组成</span></div>
    <div class="legacy-score-grid">${parts.map(([label, value, weight]) => `
      <div><span>${escapeHtml(label)}</span><b>${fmt.num(value, 0)}</b><small>${weight}</small></div>`).join("")}</div>
    <p>增长项当前固定使用中性值 50，并已按 15% 计入旧版综合分；真实趋势只在评分 V2 影子回放中使用。</p>
  </section>`;
}

function productDomainScoreSelections() {
  try {
    const parsed = JSON.parse(productDomainScoreState.selectedByMarketplaceJson || "{}");
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch (_) {
    return {};
  }
}

function selectedProductDomainProfile(marketplace, rows) {
  const list = Array.isArray(rows) ? rows : [];
  if (!list.length) return "";
  const saved = String(productDomainScoreSelections()[marketplace] || "");
  return list.some((row) => String(row.id) === saved) ? saved : String(list[0].id);
}

function renderProductDomainScorePanel(page, marketplace) {
  if (page?._error) {
    return `<section class="domain-product-score"><div class="domain-product-score-head"><strong>领域专项评分</strong><span class="badge badge-dim">暂不可用</span></div><div class="state error">${escapeHtml(page._error)}</div></section>`;
  }
  const rows = page?.rows || [];
  if (!rows.length) {
    return `<section class="domain-product-score">
      <div class="domain-product-score-head"><div><strong>领域专项评分</strong><span>暂无已启用模型</span></div><button class="btn btn-sm" type="button" id="open-domain-model-lab">创建模型</button></div>
      <div class="state">通用综合分保持不变；创建并启用领域模型后，可在这里按需并列试算。</div>
    </section>`;
  }
  const selected = selectedProductDomainProfile(marketplace, rows);
  return `<section class="domain-product-score">
    <div class="domain-product-score-head"><div><strong>领域专项评分</strong><span>独立试算，不覆盖综合分</span></div><button class="btn btn-sm" type="button" id="open-domain-model-lab">模型实验室</button></div>
    <div class="domain-product-score-controls">
      <label>调用模型<select class="sel" id="product-domain-model-select">${rows.map((row) => `<option value="${row.id}"${String(row.id) === selected ? " selected" : ""}>${escapeHtml(row.name)} · v${fmt.int(row.current_version?.version_no)}</option>`).join("")}</select></label>
      <button class="btn btn-primary btn-sm" type="button" id="product-domain-model-evaluate">调用专项模型</button>
    </div>
    <div id="product-domain-model-result" class="domain-product-score-result"><div class="state">选择模型并显式调用后显示专项评分、适用度与差异来源。</div></div>
  </section>`;
}

function bindProductDomainScorePanel(asin, keyword, marketplace) {
  const openLab = document.getElementById("open-domain-model-lab");
  if (openLab) openLab.onclick = () => window.openDomainModelLab();
  const select = document.getElementById("product-domain-model-select");
  const button = document.getElementById("product-domain-model-evaluate");
  if (!select || !button) return;
  select.onchange = () => {
    const values = productDomainScoreSelections();
    values[marketplace] = select.value;
    productDomainScoreState.selectedByMarketplaceJson = JSON.stringify(values);
    persistState(productDomainScoreState);
    const result = document.getElementById("product-domain-model-result");
    if (result) result.innerHTML = `<div class="state">模型已切换，点击“调用专项模型”重新试算。</div>`;
  };
  button.onclick = () => evaluateProductDomainScore(asin, keyword, select.value);
}

window.openDomainModelLab = function(profileId = "") {
  scoringReplayState.mode = "domain";
  if (profileId) domainModelState.selectedId = String(profileId);
  persistState(scoringReplayState);
  persistState(domainModelState);
  const alreadyOpen = location.hash === "#/model-replay";
  location.hash = "#/model-replay";
  if (alreadyOpen) route();
};

async function evaluateProductDomainScore(asin, keyword, profileId) {
  const button = document.getElementById("product-domain-model-evaluate");
  const target = document.getElementById("product-domain-model-result");
  if (!target || !profileId) return;
  if (button) button.disabled = true;
  target.innerHTML = `<div class="state"><div class="spinner"></div>正在用同一证据计算通用基线与专项模型…</div>`;
  try {
    const data = await apiSend(`/api/domain-models/${encodeURIComponent(profileId)}/evaluate-product`, "POST", {
      asin,
      keyword: String(keyword || "").trim() || null,
      version_id: null,
    });
    target.innerHTML = renderProductDomainScoreResult(data);
  } catch (err) {
    target.innerHTML = `<div class="state error">${escapeHtml(err.message || "专项评分试算失败")}</div>`;
  } finally {
    if (button && document.body.contains(button)) button.disabled = false;
  }
}

function renderProductDomainScoreResult(data) {
  const baseline = data.baseline || {};
  const custom = data.custom || {};
  const comparison = data.comparison || {};
  const applicability = data.applicability || {};
  const decision = data.decision || {};
  const eligible = Boolean(applicability.eligible);
  const applicabilityClass = eligible ? (Number(applicability.score) >= 80 ? "badge-good" : "badge-warn") : "badge-bad";
  const delta = Number(comparison.specialized_score_delta || 0);
  const drivers = (comparison.drivers || []).map((item) => `<li>${escapeHtml(item.message)}</li>`).join("");
  const contextWarning = data.context?.selection_warning ? `<div class="warning-text">${escapeHtml(data.context.selection_warning)}</div>` : "";
  return `
    <div class="domain-score-summary">
      <div><span>通用 V2 基线</span><b>${fmt.num(baseline.specialized_score, 1)}</b><small>${escapeHtml(baseline.recommendation_label || "—")}</small></div>
      <div class="domain-score-primary"><span>专项评分</span><b>${fmt.num(custom.specialized_score, 1)}</b><small>${escapeHtml(data.profile?.name || "领域模型")} · v${fmt.int(data.version?.version_no)}</small></div>
      <div><span>相对基线</span><b>${delta > 0 ? "+" : ""}${fmt.num(delta, 1)}</b><small>同一证据、同一轴组合</small></div>
      <div><span>模型适用度</span><b><span class="badge ${applicabilityClass}">${fmt.num(applicability.score, 0)} · ${escapeHtml(applicability.level || "—")}</span></b><small>要求 ≥ ${fmt.num(applicability.minimum_required, 0)}</small></div>
      <div><span>证据置信度</span><b>${fmt.num(custom.confidence_score, 1)}</b><small>${escapeHtml(custom.confidence_level || "—")}</small></div>
    </div>
    <div class="domain-score-axes">
      <span>机会 ${scoreReplayAxisBadge(custom.opportunity_score, "opportunity")}</span>
      <span>风险 ${scoreReplayAxisBadge(custom.risk_score, "risk")}</span>
      <span>置信 ${scoreReplayAxisBadge(custom.confidence_score, "confidence")}</span>
    </div>
    <div class="advice-row"><span>专项结论</span><p><b>${escapeHtml(decision.label || "—")}</b> · ${escapeHtml(decision.reason || "")}</p></div>
    <div class="advice-row"><span>适用依据</span><p>${escapeHtml(applicability.message || "—")}</p></div>
    ${contextWarning}
    ${drivers ? `<details class="domain-score-drivers"><summary>查看主要参数差异</summary><ul>${drivers}</ul></details>` : `<div class="detail-evidence-note">当前专项权重未改变该商品的专项分；建议阈值仍可能影响行动结论。</div>`}
    <div class="domain-score-footer"><span>专项分 = 机会轴 + 反向风险轴；置信度与适用度独立约束结论。</span><button class="btn btn-sm" type="button" onclick="window.openDomainModelLab('${escapeHtml(data.profile?.id || "")}')">查看模型参数</button></div>`;
}

function detailCollectedValue(value) {
  return value == null || value === ""
    ? `<span class="detail-missing">未采集</span>`
    : escapeHtml(String(value));
}

function renderBestSellerRanks(ranks) {
  if (!ranks.length) return `<span class="detail-missing">未采集</span>`;
  return `<span class="bsr-list">${ranks.map((item) =>
    `<span><b>#${fmt.int(item.rank)}</b> ${escapeHtml(item.category_name || "未命名类目")}${item.is_primary ? "（主类目）" : ""}</span>`
  ).join("")}</span>`;
}

async function collectCurrentProductDetail(asin, scoreKeyword = "") {
  if (!confirm(
    `确认采集 ASIN ${asin} 的 Amazon 商品详情？\n\n` +
    "本次只访问 1 个详情页，复用当前采集 Chrome；遇到验证码或登录页会立即停止，不会写入不可信字段。"
  )) return;
  const button = document.getElementById("collect-product-detail");
  const status = document.getElementById("detail-collect-status");
  if (button) button.disabled = true;
  if (status) status.innerHTML = `<span class="mini-spinner"></span>正在读取详情页并校验字段…`;
  try {
    const result = await runBrowserAction(
      () => apiSend(`/api/products/${encodeURIComponent(asin)}/collect-detail`, "POST")
    );
    const missing = Array.isArray(result?.["未采集字段"]) ? result["未采集字段"] : [];
    notice(missing.length ? `详情采集完成；${missing.join("、")}未采集` : "商品详情采集完成", "ok");
    await viewProductDetail(asin, scoreKeyword);
  } catch (err) {
    if (status) status.textContent = err.message || "商品详情采集失败";
    notice(err.message || "商品详情采集失败", "bad");
  } finally {
    if (button && document.body.contains(button)) button.disabled = false;
  }
}

/* 选品建议面板：透出 controller.get_product_advice（与 GUI 共享逻辑）。 */
async function fillProductAdvice(asin, scoreKeyword = "") {
  const box = document.getElementById("advice");
  if (!box) return;
  try {
    const a = await api(productContextApiPath(asin, "/advice", scoreKeyword));
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
  const parts = [`<div class="row"><span>评论证据</span><b>${reviewEvidenceBadge(rd.evidence)}</b></div>`];
  if (rd.evidence?.message) {
    parts.push(`<div class="detail-evidence-note${rd.evidence.status === "不可核验" ? " warning-text" : ""}">${escapeHtml(rd.evidence.message)}</div>`);
  }
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

function reviewEvidenceBadge(evidence) {
  const status = evidence?.status || "不可核验";
  const cls = status === "可追溯" ? "badge-good" : status === "部分可追溯" ? "badge-warn" : "badge-bad";
  const sample = evidence?.sample_count == null ? "" : ` · ${fmt.int(evidence.sample_count)} 条`;
  return `<span class="badge ${cls}" title="${escapeHtml(evidence?.message || "评论来源状态未知")}">${escapeHtml(status)}${sample}</span>`;
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

/* 趋势置信度面板：复用服务端 assess_product_trend（单一真源），诚实展示。 */
async function fillTrendConfidence(asin, scoreKeyword = "") {
  const box = document.getElementById("trend-conf");
  if (!box) return;
  try {
    const a = await api(productContextApiPath(asin, "/trend", scoreKeyword));
    const parts = [
      `<div>${confBadge(a.confidence)} <span style="color:var(--text-dim)">样本 ${a.sample_size} 个快照 · 跨度约 ${Number(a.span_days).toFixed(0)} 天</span></div>`,
      `<div style="margin-top:10px;line-height:1.65">${escapeHtml(a.summary)}</div>`,
    ];
    if (a.confidence !== "无法判断") {
      parts.push(`<div style="margin-top:10px;color:var(--text-dim)">真实趋势评估分（仅评分 V2 影子模型使用）：<b style="color:var(--text)">${Number(a.growth_score).toFixed(0)}</b></div>`);
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

/* 左轴可切换的指标（自然序位估算固定在右轴，避免不同量纲挤在一起）。 */
const TREND_LEFT_METRICS = [
  { key: "price", label: "价格", color: "#4c8dff" },
  { key: "rating", label: "评分", color: "#3fb950" },
  { key: "review_count", label: "评论数", color: "#bc8cff" },
  { key: "monthly_bought", label: "近月购买", color: "#56d4dd" },
];

function formatTrendAxisTime(value) {
  const text = String(value || "");
  const match = text.match(/^\d{4}-(\d{2}-\d{2})[ T](\d{2}:\d{2})/);
  return match ? `${match[1]} ${match[2]}` : text;
}

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
  const x = snaps.map((s) => formatTrendAxisTime(s.snapshot_at));
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
        // 带单位的中文 tooltip：价格 $、评分 1 位小数、序位估算整数、其余整数。
        formatter: (params) => {
          if (!params || !params.length) return "";
          const capture = snaps[params[0].dataIndex] || {};
          const lines = params.map((p) => {
            const isRank = String(p.seriesName).indexOf("自然序位估算") === 0;
            const val = isRank ? fmt.int(p.value) : fmtLeft(left.key, p.value);
            return `${p.marker}${escapeHtml(String(p.seriesName))}: ${val}`;
          });
          return [
            escapeHtml(String(params[0].axisValue)),
            `来源：${escapeHtml(capture.source_label || "搜索页")}`,
          ].concat(lines).join("<br/>");
        },
      },
      legend: { textStyle: { color: "#9aa7b4" } },
      grid: { left: 56, right: 56, top: 36, bottom: 70 },
      dataZoom: [{ type: "slider", height: 16, bottom: 20, borderColor: "#29313c", textStyle: { color: "#9aa7b4" } }],
      xAxis: { type: "category", data: x, axisLabel: { color: "#9aa7b4" } },
      yAxis: [
        { type: "value", name: left.label, axisLabel: { color: "#9aa7b4" }, splitLine: { lineStyle: { color: "#29313c" } } },
        { type: "value", name: "序位估算", inverse: true, axisLabel: { color: "#9aa7b4" }, splitLine: { show: false } },
      ],
      series: [
        { name: left.label, type: "line", yAxisIndex: 0, smooth: true, showSymbol: true, connectNulls: true, itemStyle: { color: left.color }, data: snaps.map((s) => numOrNull(s[left.key])) },
        { name: "自然序位估算（越低越好）", type: "line", yAxisIndex: 1, smooth: true, showSymbol: true, connectNulls: true, itemStyle: { color: "#d29922" }, data: snaps.map((s) => numOrNull(s.organic_rank)) },
      ],
    }, true);
  }

  draw();
  window.addEventListener("resize", () => chart.resize(), { once: true });
}

function snapTable(snaps) {
  return tableHtml(
    ["采集时间", "来源 / 实际观测", "价格", "评分", "评论", "近月购买", "序位估算", "促销"],
    snaps.map((s) => ({
      cells: [
        fmt.text(s.snapshot_at),
        captureSourceCell(s),
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

function captureSourceCell(snapshot) {
  const source = String(snapshot?.source_type || "search");
  const cls = source === "detail"
    ? "metric-layer-observed"
    : source === "combined"
      ? "metric-layer-derived"
      : "metric-layer-input";
  const label = snapshot?.source_label || (source === "detail" ? "详情页" : "搜索页");
  const fields = Array.isArray(snapshot?.observed_fields)
    ? snapshot.observed_fields.filter(Boolean).join("、")
    : "";
  return `<span class="metric-layer ${cls}">${escapeHtml(label)}</span>${fields ? `<div class="set-hint">${escapeHtml(fields)}</div>` : ""}`;
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
    { label: "评分关键词", render: (it) => escapeHtml(it.p.score_keyword || "旧数据未标注") },
    { label: "价格", get: (it) => numOrNull(it.p.price), render: (it) => fmt.money(it.p.price), best: "min" },
    { label: "尺寸/规格", render: (it) => escapeHtml(truncate(fmt.text(it.p.product_size), 36)) },
    { label: "评分", get: (it) => numOrNull(it.p.rating), render: (it) => fmt.num(it.p.rating), best: "max" },
    { label: "评论数（越低竞争越小）", get: (it) => numOrNull(it.p.review_count), render: (it) => fmt.int(it.p.review_count), best: "min" },
    { label: "近月购买", get: (it) => numOrNull(it.p.monthly_bought), render: (it) => fmt.int(it.p.monthly_bought), best: "max" },
    { label: "自然序位估算", get: (it) => numOrNull(it.p.organic_rank), render: (it) => fmt.int(it.p.organic_rank), best: "min" },
    { label: "趋势置信度", render: (it) => (it.trend ? `${escapeHtml(it.trend.confidence)}（样本 ${it.trend.sample_size}）` : "—") },
    { label: "最近采集", render: (it) => fmt.text(it.p.snapshot_at || it.p.last_seen_at) },
  ];
  const head = `<th>指标</th>` + items.map((it) =>
    `<th>${amazonProductLink(it.p, displayTitle(it.p, it.asin), 28)}<br><a class="link compare-detail-link" href="${escapeHtml(productDetailHash(it.asin, it.p.score_keyword))}">站内详情</a></th>`
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

/* ---------- 视图：研究项目工作区 ---------- */
const RESEARCH_STATUS_LABELS = {
  idea: "方向构想",
  collecting: "收集证据",
  validating: "验证中",
  candidate: "候选方向",
  manual_review: "人工复核",
  approved: "已批准",
  rejected: "已拒绝",
};
const RESEARCH_PRODUCT_ROLE_LABELS = {
  candidate: "候选商品",
  benchmark: "对标商品",
  competitor: "竞争商品",
  reference: "参考商品",
};
const RESEARCH_KEYWORD_ROLE_LABELS = {
  seed: "种子词",
  candidate: "候选词",
  core: "核心词",
  long_tail: "长尾词",
  reference: "参考词",
};
const RESEARCH_NOTE_TYPE_LABELS = {
  observation: "观察",
  opportunity: "机会",
  risk: "风险",
  decision: "决策依据",
};
const RESEARCH_EVIDENCE_CONTEXT_KEY = "amazon2.researchEvidenceContext.v1";
const RESEARCH_EVIDENCE_CONTEXT_MAX_AGE_MS = 6 * 60 * 60 * 1000;
let researchAssociationDialogState = null;

function researchAssociationRoleLabels(assetType) {
  if (assetType === "product") return RESEARCH_PRODUCT_ROLE_LABELS;
  if (assetType === "keyword") return RESEARCH_KEYWORD_ROLE_LABELS;
  if (assetType === "niche") return NICHE_PROJECT_ROLE_LABELS;
  return {};
}

function researchAssociationTypeLabel(assetType, count = 1) {
  const labels = { product: "商品", keyword: "关键词", niche: "市场利基" };
  const label = labels[assetType] || "资产";
  return count > 1 ? `${count} 个${label}` : label;
}

function normalizeResearchAssociationItems(assetType, items) {
  const result = [];
  const seen = new Set();
  for (const raw of items || []) {
    const source = raw && typeof raw === "object" ? raw : { key: raw };
    let key = source.key;
    if (assetType === "product") key = String(key || "").trim().toUpperCase();
    else if (assetType === "keyword") key = String(key || "").trim().replace(/\s+/g, " ");
    else if (assetType === "niche") key = Number(key);
    const identity = assetType === "keyword" ? String(key).toLocaleLowerCase() : String(key);
    if (!key || seen.has(identity)) continue;
    seen.add(identity);
    result.push({
      key,
      label: String(source.label || key),
      marketplace: String(source.marketplace || "").trim().toUpperCase(),
    });
  }
  return result;
}

function readResearchEvidenceContext() {
  try {
    const raw = sessionStorage.getItem(RESEARCH_EVIDENCE_CONTEXT_KEY);
    const value = raw ? JSON.parse(raw) : null;
    const createdAt = Number(value?.createdAt || 0);
    if (
      !value
      || !Number.isInteger(Number(value.projectId))
      || Number(value.projectId) < 1
      || !String(value.returnHash || "").match(
        /^(?:#\/research-projects\/\d+\/report|#\/research-review-queue)$/
      )
      || !createdAt
      || Date.now() - createdAt > RESEARCH_EVIDENCE_CONTEXT_MAX_AGE_MS
    ) {
      sessionStorage.removeItem(RESEARCH_EVIDENCE_CONTEXT_KEY);
      return null;
    }
    return {
      ...value,
      projectId: Number(value.projectId),
      marketplace: String(value.marketplace || "US").toUpperCase(),
    };
  } catch {
    return null;
  }
}

function writeResearchEvidenceContext(context) {
  try {
    sessionStorage.setItem(RESEARCH_EVIDENCE_CONTEXT_KEY, JSON.stringify(context));
  } catch {
    // 嵌入式壳禁用 sessionStorage 时仍可使用普通项目选择器。
  }
}

function clearResearchEvidenceContext() {
  try { sessionStorage.removeItem(RESEARCH_EVIDENCE_CONTEXT_KEY); } catch { /* optional */ }
}

function researchEvidenceTargetForGap(key) {
  if (["candidate_product", "competition_baseline"].includes(key)) return "product";
  if (key === "keyword_scope") return "keyword";
  if (["market_snapshot", "demand_coverage"].includes(key)) return "niche";
  if (["objective", "opportunity_hypothesis", "risk_record"].includes(key)) return "project";
  if (key === "trend_evidence") return "tracking";
  if (key === "financial_inputs") return "metrics";
  if (key === "stale_sources") return "tasks";
  return "project";
}

function researchEvidenceDefaultRole(key) {
  if (key === "competition_baseline") return "benchmark";
  if (key === "keyword_scope") return "core";
  if (["market_snapshot", "demand_coverage"].includes(key)) return "primary";
  return "candidate";
}

function researchEvidenceTargetForHash(hash) {
  if (/^#\/research-projects\/\d+\/report$/.test(hash)) return "report";
  if (/^#\/research-projects\/\d+\/report-(?:versions|compare)/.test(hash)) return "report";
  if (/^#\/research-projects\/\d+$/.test(hash)) return "project";
  if (/^#\/products$/.test(hash) || /^#\/product\//.test(hash) || /^#\/compare/.test(hash)) return "product";
  if (/^#\/keyword-library$/.test(hash) || /^#\/keywords$/.test(hash)) return "keyword";
  if (/^#\/market-niches(?:\/\d+)?$/.test(hash)) return "niche";
  if (/^#\/tracking$/.test(hash)) return "tracking";
  if (/^#\/metrics(?:\/.*)?$/.test(hash)) return "metrics";
  if (/^#\/tasks$/.test(hash)) return "tasks";
  return "";
}

function startResearchEvidenceAction(project, gap) {
  const route = String(gap?.route || "");
  if (!route.startsWith("#/")) return;
  writeResearchEvidenceContext({
    projectId: Number(project.id),
    projectName: String(project.name || `项目 #${project.id}`),
    marketplace: String(project.marketplace || "US").toUpperCase(),
    gapKey: String(gap.key || ""),
    gapTitle: String(gap.title || "补充项目证据"),
    nextAction: String(gap.next_action || ""),
    target: researchEvidenceTargetForGap(String(gap.key || "")),
    defaultRole: researchEvidenceDefaultRole(String(gap.key || "")),
    returnHash: `#/research-projects/${Number(project.id)}/report`,
    createdAt: Date.now(),
  });
  location.hash = route;
}

function mountResearchEvidenceContextBar() {
  const context = readResearchEvidenceContext();
  if (!context || context.target !== researchEvidenceTargetForHash(location.hash)) return;
  if (document.getElementById("research-evidence-context")) return;
  const bar = document.createElement("section");
  bar.id = "research-evidence-context";
  bar.className = "research-evidence-context";
  bar.setAttribute("aria-label", "研究项目补证上下文");
  bar.innerHTML = `
    <div>
      <span>正在补充项目证据</span>
      <b>#${fmt.int(context.projectId)} ${escapeHtml(context.projectName)}</b>
      <small>${escapeHtml(context.gapTitle)}${context.nextAction ? ` · ${escapeHtml(context.nextAction)}` : ""}</small>
    </div>
    <div class="actions">
      <button class="btn btn-sm" type="button" data-research-context-return>${escapeHtml(context.returnLabel || "返回报告")}</button>
      <button class="btn btn-sm" type="button" data-research-context-clear>结束补证</button>
    </div>`;
  content.prepend(bar);
  bar.querySelector("[data-research-context-return]").onclick = () => {
    const returnHash = context.returnHash;
    clearResearchEvidenceContext();
    location.hash = returnHash;
  };
  bar.querySelector("[data-research-context-clear]").onclick = () => {
    clearResearchEvidenceContext();
    bar.remove();
    notice("已结束本次项目补证上下文", "ok");
  };
}

function finishResearchEvidenceAssociation(projectId, assetType) {
  const context = readResearchEvidenceContext();
  if (!context || context.projectId !== Number(projectId) || context.target !== assetType) return false;
  const returnHash = context.returnHash;
  clearResearchEvidenceContext();
  location.hash = returnHash;
  return true;
}

function closeResearchAssociationDialog() {
  const state = researchAssociationDialogState;
  if (!state) {
    document.getElementById("research-association-dialog")?.remove();
    return;
  }
  document.removeEventListener("keydown", state.onKeydown);
  state.modal.remove();
  researchAssociationDialogState = null;
}

async function openResearchAssociationDialog({
  assetType,
  items,
  marketplace = "US",
  defaultRole = "",
  defaultProjectId = "",
} = {}) {
  closeResearchAssociationDialog();
  const normalizedItems = normalizeResearchAssociationItems(assetType, items);
  if (!["product", "keyword", "niche"].includes(assetType) || !normalizedItems.length) {
    return notice("没有可加入研究项目的已入库资产", "bad");
  }
  if (assetType === "niche" && normalizedItems.length > 1) {
    return notice("市场利基请逐个加入研究项目", "bad");
  }
  const marketplaceValue = String(
    marketplace || normalizedItems.find((item) => item.marketplace)?.marketplace || "US"
  ).trim().toUpperCase();
  const context = readResearchEvidenceContext();
  const contextualProjectId = context
    && context.target === assetType
    && context.marketplace === marketplaceValue
    ? context.projectId
    : "";
  const roles = researchAssociationRoleLabels(assetType);
  const storedRole = researchAssociationState[`${assetType}Role`];
  const contextualRole = context?.target === assetType ? context.defaultRole : "";
  const selectedRole = roles[defaultRole]
    ? defaultRole
    : roles[contextualRole]
      ? contextualRole
      : roles[storedRole]
        ? storedRole
        : Object.keys(roles)[0];
  const shownItems = normalizedItems.slice(0, 6);
  const modal = document.createElement("div");
  modal.id = "research-association-dialog";
  modal.className = "modal-backdrop";
  modal.innerHTML = `
    <section class="modal-panel research-association-modal" role="dialog" aria-modal="true" aria-label="加入研究项目">
      <div class="modal-head">
        <div>
          <h2>加入研究项目</h2>
          <small>${escapeHtml(marketplaceValue)} · ${escapeHtml(researchAssociationTypeLabel(assetType, normalizedItems.length))}</small>
        </div>
        <button class="icon-btn" type="button" data-research-association-close title="关闭" aria-label="关闭">×</button>
      </div>
      <div class="research-association-body">
        <div class="research-association-assets">
          <span>待关联资产</span>
          ${shownItems.map((item) => `<div><b>${escapeHtml(item.label)}</b><code>${escapeHtml(item.key)}</code></div>`).join("")}
          ${normalizedItems.length > shownItems.length ? `<small>另有 ${fmt.int(normalizedItems.length - shownItems.length)} 项未展开</small>` : ""}
        </div>
        <div class="research-association-form">
          <label>研究项目
            <select class="sel" data-research-project disabled><option value="">正在读取同站点项目…</option></select>
          </label>
          <label>资产角色
            <select class="sel" data-research-role>${researchRoleOptions(roles, selectedRole)}</select>
          </label>
          ${assetType === "niche" ? "" : `<label class="research-association-notes">关联备注（可选）
            <textarea rows="3" maxlength="5000" data-research-notes placeholder="只记录为什么把这项资产纳入本项目，不要把推测写成事实"></textarea>
          </label>`}
        </div>
        <div class="research-association-project-status" data-research-project-status>
          <span class="mini-spinner"></span>正在读取项目列表…
        </div>
      </div>
      <div class="research-association-actions">
        <button class="btn" type="button" data-research-association-cancel>取消</button>
        <button class="btn" type="button" data-research-association-submit disabled>加入研究项目</button>
      </div>
    </section>`;
  document.body.appendChild(modal);

  const onKeydown = (event) => {
    if (event.key === "Escape") closeResearchAssociationDialog();
  };
  researchAssociationDialogState = {
    modal,
    onKeydown,
    assetType,
    items: normalizedItems,
    marketplace: marketplaceValue,
    projects: [],
    projectDetail: null,
    loadToken: 0,
  };
  document.addEventListener("keydown", onKeydown);
  modal.addEventListener("click", (event) => {
    if (event.target === modal) closeResearchAssociationDialog();
  });
  modal.querySelector("[data-research-association-close]").onclick = closeResearchAssociationDialog;
  modal.querySelector("[data-research-association-cancel]").onclick = closeResearchAssociationDialog;
  modal.querySelector("[data-research-association-submit]").onclick = submitResearchAssociation;
  modal.querySelector("[data-research-project]").onchange = loadResearchAssociationProjectDetail;

  try {
    const query = new URLSearchParams({
      limit: "200",
      offset: "0",
      marketplace: marketplaceValue,
      sort_by: "updated_at",
      sort_dir: "desc",
    });
    const page = normalizePage(await api(`/api/research-projects?${query.toString()}`), 200);
    if (!researchAssociationDialogState || researchAssociationDialogState.modal !== modal) return;
    researchAssociationDialogState.projects = page.rows || [];
    const projectSelect = modal.querySelector("[data-research-project]");
    const available = researchAssociationDialogState.projects.filter(
      (project) => !["approved", "rejected"].includes(project.status)
    );
    projectSelect.innerHTML = `<option value="">选择研究项目</option>` + researchAssociationDialogState.projects.map((project) => {
      const frozen = ["approved", "rejected"].includes(project.status);
      const label = `#${project.id} ${project.name} · ${RESEARCH_STATUS_LABELS[project.status] || project.status}`;
      return `<option value="${Number(project.id)}"${frozen ? " disabled" : ""}>${escapeHtml(label)}${frozen ? "（需先退回可编辑阶段）" : ""}</option>`;
    }).join("");
    projectSelect.disabled = !available.length;
    const preferred = Number(defaultProjectId || contextualProjectId || researchAssociationState.lastProjectId);
    if (available.some((project) => Number(project.id) === preferred)) {
      projectSelect.value = String(preferred);
    } else if (available.length === 1) {
      projectSelect.value = String(available[0].id);
    }
    if (!available.length) {
      modal.querySelector("[data-research-project-status]").innerHTML = `
        <b>没有可编辑的同站点项目</b>
        <span>已批准或已拒绝项目需先在项目详情中退回可编辑阶段。</span>
        <a class="link" href="#/research-projects" data-research-open-projects>前往研究项目</a>`;
      modal.querySelector("[data-research-open-projects]").onclick = closeResearchAssociationDialog;
      return;
    }
    await loadResearchAssociationProjectDetail();
  } catch (err) {
    if (!researchAssociationDialogState || researchAssociationDialogState.modal !== modal) return;
    modal.querySelector("[data-research-project-status]").innerHTML = `<span class="warning-text">${escapeHtml(err.message || "研究项目读取失败")}</span>`;
  }
}

async function loadResearchAssociationProjectDetail() {
  const state = researchAssociationDialogState;
  if (!state) return;
  const projectId = Number(state.modal.querySelector("[data-research-project]").value);
  const status = state.modal.querySelector("[data-research-project-status]");
  const submit = state.modal.querySelector("[data-research-association-submit]");
  submit.disabled = true;
  state.projectDetail = null;
  if (!projectId) {
    status.innerHTML = "请选择一个可编辑研究项目。";
    return;
  }
  const token = ++state.loadToken;
  status.innerHTML = `<span class="mini-spinner"></span>正在核对已有关系…`;
  try {
    const detail = await api(`/api/research-projects/${encodeURIComponent(projectId)}`);
    if (!researchAssociationDialogState || state !== researchAssociationDialogState || token !== state.loadToken) return;
    state.projectDetail = detail;
    const project = detail.project || {};
    const existing = researchAssociationExistingRows(detail, state.assetType, state.items);
    const existingRoles = [...new Set(existing.map((row) => row.role).filter(Boolean))]
      .map((role) => researchAssociationRoleLabels(state.assetType)[role] || role);
    const evidenceContext = readResearchEvidenceContext();
    const returnsToReport = Number(evidenceContext?.projectId) === projectId
      && evidenceContext?.target === state.assetType;
    status.innerHTML = `
      <div>
        <b>${escapeHtml(project.name || `项目 #${projectId}`)}</b>
        ${researchStatusBadge(project.status)}
      </div>
      <span>${escapeHtml(truncate(project.objective || "尚未填写研究目标", 140))}</span>
      <small>当前关联 ${fmt.int(project.product_count)} 商品、${fmt.int(project.keyword_count)} 关键词；本次资产已有 ${fmt.int(existing.length)} / ${fmt.int(state.items.length)} 项。</small>
      ${existing.length ? `<small>提交后不会重复添加，将按本次选择更新角色${existingRoles.length ? `；已有角色：${escapeHtml(existingRoles.join("、"))}` : ""}。</small>` : ""}`;
    submit.textContent = existing.length === state.items.length
      ? (returnsToReport ? "更新并返回报告" : "更新关联")
      : (returnsToReport ? "加入并返回报告" : "加入研究项目");
    submit.disabled = ["approved", "rejected"].includes(project.status);
  } catch (err) {
    if (!researchAssociationDialogState || state !== researchAssociationDialogState || token !== state.loadToken) return;
    status.innerHTML = `<span class="warning-text">${escapeHtml(err.message || "项目详情读取失败")}</span>`;
  }
}

function researchAssociationExistingRows(detail, assetType, items) {
  const identities = new Set(items.map((item) =>
    assetType === "keyword" ? String(item.key).toLocaleLowerCase() : String(item.key)
  ));
  if (assetType === "product") {
    return (detail.products || []).filter((row) => identities.has(String(row.asin || "").toUpperCase()));
  }
  if (assetType === "keyword") {
    return (detail.keywords || []).filter((row) =>
      identities.has(String(row.keyword || "").trim().replace(/\s+/g, " ").toLocaleLowerCase())
    );
  }
  return (detail.niches || []).filter((row) => identities.has(String(row.niche_id)));
}

async function submitResearchAssociation() {
  const state = researchAssociationDialogState;
  if (!state) return;
  const projectId = Number(state.modal.querySelector("[data-research-project]").value);
  const role = state.modal.querySelector("[data-research-role]").value;
  const notes = state.modal.querySelector("[data-research-notes]")?.value.trim() || null;
  const submit = state.modal.querySelector("[data-research-association-submit]");
  if (!projectId) return notice("请选择研究项目", "bad");
  submit.disabled = true;
  try {
    let result;
    if (state.assetType === "product") {
      result = await apiSend(`/api/research-projects/${projectId}/products`, "POST", {
        asins: state.items.map((item) => item.key),
        role,
        notes,
      });
    } else if (state.assetType === "keyword") {
      result = await apiSend(`/api/research-projects/${projectId}/keywords`, "POST", {
        keywords: state.items.map((item) => item.key),
        role,
        notes,
      });
    } else {
      result = await apiSend(`/api/market-niches/${Number(state.items[0].key)}/projects`, "POST", {
        project_ids: [projectId],
        role,
      });
    }
    const missing = Array.isArray(result?.missing) ? result.missing : [];
    researchAssociationState.lastProjectId = String(projectId);
    researchAssociationState[`${state.assetType}Role`] = role;
    persistState(researchAssociationState);
    const message = missing.length
      ? `关联已保存；未找到或站点不一致：${missing.join("、")}`
      : `${researchAssociationTypeLabel(state.assetType, state.items.length)}已加入研究项目`;
    const assetType = state.assetType;
    closeResearchAssociationDialog();
    notice(message, missing.length ? "bad" : "ok");
    if (finishResearchEvidenceAssociation(projectId, assetType)) return;
    if (assetType === "niche" && /^#\/market-niches\/\d+$/.test(location.hash)) await router();
  } catch (err) {
    notice(err.message || "研究项目关联失败", "bad");
    if (researchAssociationDialogState === state) submit.disabled = false;
  }
}

/* ---------- 视图：研究项目复核队列与人工观察计划（P10.1 / P10.2） ---------- */
async function viewResearchReviewQueue() {
  content.innerHTML = `
    <section class="panel research-review-intro">
      <div>
        <span class="research-review-kicker">研究证据工作台</span>
        <h2>复核队列与项目观察计划</h2>
        <p>按证据时效、趋势门槛、核心词追踪、冻结基线和人工计划，整理当前最值得查看的项目。</p>
      </div>
      <a class="btn" href="#/research-projects">管理研究项目</a>
    </section>
    <section class="panel research-review-list">
      <div class="table-toolbar">
        <h2 style="margin:0">复核队列</h2>
        <span class="result-meta" id="research-review-evaluated"></span>
      </div>
      <div id="research-review-summary" class="research-summary-grid research-review-summary"></div>
      <div class="filters">
        <input id="research-review-keyword" placeholder="项目名称、目标或方向标签" />
        <input id="research-review-marketplace" placeholder="站点" style="width:82px" />
        <select id="research-review-status" class="sel">${researchStatusOptions("all", true)}</select>
        <select id="research-review-attention" class="sel">
          <option value="all">全部提醒</option>
          <option value="action_required">需要处理</option>
          <option value="waiting">等待证据</option>
          <option value="terminal">终态稳定</option>
        </select>
        <select id="research-review-monitoring" class="sel">
          <option value="all">全部观察计划</option>
          <option value="due">计划已到期</option>
          <option value="active">观察中</option>
          <option value="paused">计划已暂停</option>
          <option value="unplanned">未制定计划</option>
        </select>
        <button class="btn" id="research-review-apply">筛选</button>
        <button class="btn" id="research-review-reset">重置</button>
      </div>
      <div class="research-review-policy">
        队列仍按打开页面时只读计算。观察计划仅保存人工复核周期；到期不会自动创建任务、打开浏览器、联网采集、冻结报告或改变项目状态。
      </div>
      <div id="research-review-warning"></div>
      <div id="research-review-meta" class="result-meta"></div>
      <div id="research-review-table"></div>
      <div id="research-review-pager"></div>
    </section>`;

  document.getElementById("research-review-keyword").value = researchReviewQueueState.keyword;
  document.getElementById("research-review-marketplace").value = researchReviewQueueState.marketplace;
  document.getElementById("research-review-status").value = researchReviewQueueState.status;
  document.getElementById("research-review-attention").value = researchReviewQueueState.attention;
  document.getElementById("research-review-monitoring").value = researchReviewQueueState.monitoring;
  document.getElementById("research-review-apply").onclick = () => loadResearchReviewQueue(true);
  document.getElementById("research-review-reset").onclick = () => {
    Object.assign(researchReviewQueueState, {
      offset: 0,
      marketplace: "US",
      status: "all",
      attention: "all",
      monitoring: "all",
      keyword: "",
    });
    persistState(researchReviewQueueState);
    document.getElementById("research-review-keyword").value = "";
    document.getElementById("research-review-marketplace").value = "US";
    document.getElementById("research-review-status").value = "all";
    document.getElementById("research-review-attention").value = "all";
    document.getElementById("research-review-monitoring").value = "all";
    loadResearchReviewQueue();
  };
  bindStateInputs([
    "research-review-keyword",
    "research-review-marketplace",
    "research-review-status",
    "research-review-attention",
    "research-review-monitoring",
  ], saveResearchReviewQueueState);
  content.querySelectorAll(".filters input, .filters select").forEach((input) => {
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") loadResearchReviewQueue(true);
    });
  });
  await loadResearchReviewQueue();
}

function saveResearchReviewQueueState() {
  const value = (id, fallback = "") => document.getElementById(id)?.value ?? fallback;
  researchReviewQueueState.keyword = value(
    "research-review-keyword",
    researchReviewQueueState.keyword,
  ).trim();
  researchReviewQueueState.marketplace = (
    value("research-review-marketplace", researchReviewQueueState.marketplace) || "US"
  ).trim().toUpperCase();
  researchReviewQueueState.status = value(
    "research-review-status",
    researchReviewQueueState.status,
  ) || "all";
  researchReviewQueueState.attention = value(
    "research-review-attention",
    researchReviewQueueState.attention,
  ) || "all";
  researchReviewQueueState.monitoring = value(
    "research-review-monitoring",
    researchReviewQueueState.monitoring,
  ) || "all";
  persistState(researchReviewQueueState);
}

async function loadResearchReviewQueue(resetPage = false) {
  if (resetPage) {
    researchReviewQueueState.offset = 0;
    saveResearchReviewQueueState();
  }
  persistState(researchReviewQueueState);
  const table = document.getElementById("research-review-table");
  const meta = document.getElementById("research-review-meta");
  const pager = document.getElementById("research-review-pager");
  const warning = document.getElementById("research-review-warning");
  if (!table) return;
  table.innerHTML = `<div class="state"><div class="spinner"></div>计算复核优先级…</div>`;
  meta.textContent = "";
  pager.innerHTML = "";
  warning.innerHTML = "";
  const query = new URLSearchParams({
    limit: String(researchReviewQueueState.limit),
    offset: String(researchReviewQueueState.offset),
    marketplace: researchReviewQueueState.marketplace || "US",
  });
  if (researchReviewQueueState.keyword) query.set("keyword", researchReviewQueueState.keyword);
  if (researchReviewQueueState.status !== "all") query.set("status", researchReviewQueueState.status);
  if (researchReviewQueueState.attention !== "all") query.set("attention", researchReviewQueueState.attention);
  if (researchReviewQueueState.monitoring !== "all") query.set("monitoring", researchReviewQueueState.monitoring);
  try {
    const page = normalizePage(
      await api(`/api/research-review-queue?${query.toString()}`),
      researchReviewQueueState.limit,
    );
    researchReviewQueueCurrent = page;
    renderResearchReviewQueueSummary(page.summary || {});
    document.getElementById("research-review-evaluated").textContent = `评估日 ${page.evaluated_on || "—"}`;
    meta.textContent = `${pageSummary(page, "研究项目")} · 已扫描 ${fmt.int(page.scanned_projects)} 个项目 · 复核日仅表示人工检查时点`;
    const warnings = Array.isArray(page.warnings) ? page.warnings : [];
    warning.innerHTML = warnings.map((item) => `
      <div class="evidence-warning research-review-warning">
        <strong>范围提示</strong><span>${escapeHtml(item)}</span>
      </div>`).join("");
    table.innerHTML = page.rows.length
      ? renderResearchReviewQueueTable(page.rows)
      : `<div class="state">当前筛选下没有需要展示的研究项目。</div>`;
    bindResearchReviewQueueActions();
    pager.innerHTML = renderPager("research-review-queue-pager", page, [10, 25, 50, 100]);
    bindPager(
      "research-review-queue-pager",
      researchReviewQueueState,
      page,
      loadResearchReviewQueue,
    );
    rememberAgentBusinessContext();
  } catch (err) {
    researchReviewQueueCurrent = null;
    table.innerHTML = `<div class="state error">⚠ ${escapeHtml(err.message)}</div>`;
  }
}

function renderResearchReviewQueueSummary(summary) {
  const box = document.getElementById("research-review-summary");
  if (!box) return;
  box.innerHTML = `
    <div><span>需要处理</span><b>${fmt.int(summary.action_required)}</b><small>已有规则触发人工动作</small></div>
    <div><span>等待证据</span><b>${fmt.int(summary.waiting)}</b><small>尚未到下一复核时点</small></div>
    <div><span>终态稳定</span><b>${fmt.int(summary.terminal)}</b><small>终态基线没有新证据</small></div>
    <div><span>发现新证据</span><b>${fmt.int(summary.evidence_changed)}</b><small>冻结后证据发生变化</small></div>
    <div><span>观察计划到期</span><b>${fmt.int(summary.monitoring_due)}</b><small>等待人工复核</small></div>
    <div><span>未制定计划</span><b>${fmt.int(summary.monitoring_unplanned)}</b><small>尚未保存观察节奏</small></div>`;
}

function renderResearchReviewTimeline(timeline) {
  const qualified = Number(timeline.best_points || 0);
  const raw = Number(timeline.best_raw_points ?? qualified);
  const excluded = Number(timeline.best_excluded_points || Math.max(0, raw - qualified));
  const qualityWarnings = Array.isArray(timeline.best_quality_warnings)
    ? timeline.best_quality_warnings
    : [];
  return `
    <span class="badge ${timeline.preliminary_ready ? "badge-good" : "badge-warn"}">${fmt.int(qualified)} 个合格点 / ${fmt.int(timeline.best_span_days)} 天</span>
    <div class="cell-sub">${escapeHtml(timeline.best_name || "无决策相关序列")} · ${escapeHtml(timeline.best_quality_label || "质量未评估")}</div>
    ${raw !== qualified
      ? `<div class="cell-sub">原始 ${fmt.int(raw)} 点，排除 ${fmt.int(excluded)} 点</div>`
      : `<div class="cell-sub">原始点与合格点一致</div>`}
    ${timeline.preliminary_ready
      ? `<div class="cell-sub">达到初步趋势门槛</div>`
      : `<div class="cell-sub">还缺 ${fmt.int(timeline.missing_points)} 点 / ${fmt.int(timeline.missing_span_days)} 天</div>`}
    ${qualityWarnings.length ? `<div class="cell-sub text-warn">${escapeHtml(qualityWarnings[0])}</div>` : ""}`;
}

function renderResearchReviewQueueTable(rows) {
  const body = rows.map((item) => {
    const project = item.project || {};
    const primary = item.primary_status || {};
    const readiness = item.readiness || {};
    const freshness = item.freshness || {};
    const timeline = item.timeline || {};
    const tracking = item.tracking || {};
    const version = item.report_version || {};
    const monitoring = item.monitoring_plan || {};
    const actions = item.actions || {};
    const projectRoute = actions.project_route || `#/research-projects/${Number(project.id)}`;
    const reportRoute = actions.report_route || `${projectRoute}/report`;
    const keyword = actions.tracking_keyword || tracking.recommended_keyword || "";
    const trackingLabel = Number(tracking.missing || 0) > 0 ? "规划追踪" : "查看追踪";
    return `
      <tr>
        <td class="research-review-project">
          <a href="${escapeHtml(projectRoute)}"><b>${escapeHtml(project.name || `项目 #${project.id}`)}</b></a>
          <div class="cell-sub">#${fmt.int(project.id)} · ${researchStatusBadge(project.status)}</div>
          <div class="cell-sub">${escapeHtml(truncate(project.objective || "尚未填写研究目标", 70))}</div>
        </td>
        <td class="research-review-primary">
          ${researchReviewSignalBadge(primary)}
          <div class="cell-sub">${escapeHtml(primary.reason || "—")}</div>
          <div class="cell-sub">门禁 ${fmt.int(readiness.passed_count)} / ${fmt.int(readiness.total_count)} · 阻断 ${fmt.int(readiness.blocking_count)}</div>
        </td>
        <td>
          ${researchReviewFreshnessBadge(freshness)}
          <div class="cell-sub">最近 ${fmt.text(freshness.latest_source_at)}</div>
          <div class="cell-sub">最旧约 ${freshness.oldest_age_days == null ? "—" : `${fmt.int(freshness.oldest_age_days)} 天`}</div>
        </td>
        <td>
          ${renderResearchReviewTimeline(timeline)}
        </td>
        <td>
          ${researchReviewTrackingBadge(tracking)}
          <div class="cell-sub">已追踪 ${fmt.int(tracking.tracked_keyword_count)} / ${fmt.int(tracking.relevant_keyword_count)} 个核心词</div>
          <div class="cell-sub">${escapeHtml(researchReviewKeywordSummary(tracking.keywords))}</div>
        </td>
        <td>
          ${researchReviewVersionBadge(version)}
          <div class="cell-sub">${version.baseline ? `${escapeHtml(version.baseline_label)} #${fmt.int(version.baseline.version_no)}` : "尚无冻结基线"}</div>
          <div class="cell-sub">${version.baseline ? `冻结于 ${fmt.text(version.baseline.frozen_at)}` : "首次冻结仍需人工确认"}</div>
        </td>
        <td>
          ${researchObservationPlanBadge(monitoring)}
          <div class="cell-sub">${monitoring.exists ? `每 ${fmt.int(monitoring.cadence_days)} 天 · 下次 ${fmt.text(monitoring.next_review_on)}` : "尚未保存周期"}</div>
          <div class="cell-sub">${monitoring.last_reviewed_at ? `最近人工复核 ${fmt.text(monitoring.last_reviewed_at)}` : "尚无人工复核记录"}</div>
        </td>
        <td>
          <b>${escapeHtml(item.next_review_on || "按人工节奏")}</b>
          <div class="cell-sub">${escapeHtml(item.next_review_reason || "—")}</div>
        </td>
        <td>
          <div class="actions research-review-actions">
            <a class="btn btn-sm" href="${escapeHtml(reportRoute)}">看报告</a>
            <a class="btn btn-sm" href="${escapeHtml(projectRoute)}">项目</a>
            <a class="btn btn-sm ${monitoring.is_due ? "btn-warn" : ""}" href="${escapeHtml(actions.monitoring_route || `${projectRoute}/observation-plan`)}">观察计划</a>
            ${keyword ? `
              <button class="btn btn-sm ${Number(tracking.missing || 0) > 0 ? "btn-warn" : ""}"
                type="button" data-review-track
                data-project-id="${Number(project.id)}"
                data-project-name="${escapeHtml(project.name || `项目 #${project.id}`)}"
                data-marketplace="${escapeHtml(project.marketplace || "US")}"
                data-keyword="${escapeHtml(keyword)}"
                title="只预填追踪表单，不会创建任务或联网采集">${trackingLabel}</button>` : ""}
          </div>
        </td>
      </tr>`;
  }).join("");
  return wrapTable(`
    <table class="research-review-table">
      <thead><tr>
        <th>研究项目</th><th>主提醒</th><th>证据时效</th><th>趋势</th>
        <th>核心词追踪</th><th>报告基线</th><th>观察计划</th><th>规则建议</th><th>操作</th>
      </tr></thead>
      <tbody>${body}</tbody>
    </table>`);
}

function researchReviewSignalBadge(signal) {
  const cls = signal?.severity === "high"
    ? "badge-bad"
    : (signal?.severity === "medium" ? "badge-warn" : "badge-dim");
  return `<span class="badge ${cls}">${escapeHtml(signal?.label || "待判断")}</span>`;
}

function researchReviewFreshnessBadge(freshness) {
  const cls = ({
    current: "badge-good",
    mixed: "badge-warn",
    stale: "badge-bad",
    missing: "badge-dim",
  })[freshness?.status] || "badge-dim";
  return `<span class="badge ${cls}">${escapeHtml(freshness?.label || "无时间来源")}</span>`;
}

function researchReviewTrackingBadge(tracking) {
  let label = "追踪正常";
  let cls = "badge-good";
  if (!Number(tracking?.relevant_keyword_count || 0)) {
    label = "未定义核心/种子词";
    cls = "badge-dim";
  } else if (Number(tracking?.error || 0)) {
    label = `${fmt.int(tracking.error)} 个异常`;
    cls = "badge-bad";
  } else if (Number(tracking?.paused || 0)) {
    label = `${fmt.int(tracking.paused)} 个暂停`;
    cls = "badge-warn";
  } else if (Number(tracking?.missing || 0)) {
    label = `${fmt.int(tracking.missing)} 个未规划`;
    cls = "badge-warn";
  } else if (Number(tracking?.due || 0)) {
    label = `${fmt.int(tracking.due)} 个已到期`;
    cls = "badge-warn";
  } else if (Number(tracking?.waiting || 0)) {
    label = "等待安全间隔";
    cls = "badge-dim";
  }
  return `<span class="badge ${cls}">${label}</span>`;
}

function researchReviewKeywordSummary(rows) {
  const values = (rows || []).slice(0, 2).map((row) => (
    `${row.keyword || "—"}：${row.task_state_label || "未判断"}`
  ));
  if ((rows || []).length > 2) values.push(`另 ${rows.length - 2} 个`);
  return values.join(" · ") || "暂无核心/种子词";
}

function researchReviewVersionBadge(version) {
  if (!version?.baseline) return `<span class="badge badge-dim">尚无冻结版本</span>`;
  if (version.evidence_changed === true) return `<span class="badge badge-bad">发现新证据</span>`;
  if (version.time_only_change) return `<span class="badge badge-dim">证据未变 · 时效重算</span>`;
  return `<span class="badge badge-good">与冻结证据一致</span>`;
}

function researchObservationPlanBadge(plan) {
  const cls = ({
    overdue: "badge-bad",
    due: "badge-warn",
    scheduled: "badge-good",
    paused: "badge-dim",
    unplanned: "badge-dim",
  })[plan?.state] || "badge-dim";
  return `<span class="badge ${cls}">${escapeHtml(plan?.state_label || "未制定计划")}</span>`;
}

function bindResearchReviewQueueActions() {
  document.querySelectorAll("[data-review-track]").forEach((button) => {
    button.onclick = () => {
      const keyword = String(button.dataset.keyword || "").trim();
      if (!keyword) return notice("当前项目没有可预填的核心关键词", "bad");
      trackingFormState.keyword = keyword;
      trackingFormState.marketplace = String(button.dataset.marketplace || "US").toUpperCase();
      trackingFormState.targetSnapshots = Math.max(3, Number(trackingFormState.targetSnapshots) || 3);
      persistState(trackingFormState);
      writeResearchEvidenceContext({
        projectId: Number(button.dataset.projectId),
        projectName: String(button.dataset.projectName || `项目 #${button.dataset.projectId}`),
        marketplace: trackingFormState.marketplace,
        gapKey: "trend_evidence",
        gapTitle: "积累核心词趋势证据",
        gapLabel: "趋势证据",
        nextAction: `已预填关键词“${keyword}”；请先核对现有任务，再决定是否创建或采集。`,
        target: "tracking",
        defaultRole: "core",
        returnHash: "#/research-review-queue",
        returnLabel: "返回复核队列",
        createdAt: Date.now(),
      });
      location.hash = "#/tracking";
    };
  });
}

/* ---------- 视图：研究项目人工观察计划（P10.2） ---------- */
async function viewResearchObservationPlan(projectId) {
  const id = Number(projectId);
  if (!Number.isInteger(id) || id < 1) return emptyState("研究项目 ID 不正确");
  content.innerHTML = `<div class="state"><div class="spinner"></div>读取项目观察计划…</div>`;
  try {
    researchObservationPlanCurrent = await api(`/api/research-projects/${id}/observation-plan`);
    renderResearchObservationPlan(researchObservationPlanCurrent);
    rememberAgentBusinessContext();
  } catch (err) {
    researchObservationPlanCurrent = null;
    content.innerHTML = `<div class="state error">⚠ ${escapeHtml(err.message)}</div>`;
  }
}

function renderResearchObservationPlan(bundle) {
  const project = bundle?.project || {};
  const plan = bundle?.plan || {};
  const report = bundle?.current_report || {};
  const readiness = report.readiness || {};
  const timeline = report.timeline || {};
  const active = plan.status !== "paused";
  const nextReview = plan.next_review_on || researchReportDefaultDate(Number(plan.cadence_days || 14));
  const fingerprint = String(report.report_fingerprint || "");
  content.innerHTML = `
    <a class="back-link" href="#/research-review-queue">← 返回复核队列</a>
    <section class="panel research-observation-head">
      <div>
        <span class="research-review-kicker">项目 #${fmt.int(project.id)} · ${escapeHtml(project.marketplace || "US")}</span>
        <h2>${escapeHtml(project.name || "项目观察计划")}</h2>
        <p>${escapeHtml(project.objective || "尚未填写研究目标")}</p>
      </div>
      <div class="actions">
        ${researchStatusBadge(project.status)}
        ${researchObservationPlanBadge(plan)}
        <a class="btn" href="#/research-projects/${Number(project.id)}/report">当前报告</a>
        <a class="btn" href="#/research-projects/${Number(project.id)}">项目详情</a>
      </div>
    </section>
    <section class="panel research-observation-current">
      <div class="table-toolbar">
        <h2 style="margin:0">当前复核上下文</h2>
        <span class="result-meta">评估日 ${escapeHtml(report.evaluated_on || "—")} · 证据截至 ${fmt.text(report.evidence_as_of)}</span>
      </div>
      <div class="research-summary-grid research-observation-summary">
        <div><span>决策门禁</span><b>${fmt.int(readiness.passed_count)} / ${fmt.int(readiness.total_count)}</b><small>阻断 ${fmt.int(readiness.blocking_count)}</small></div>
        <div><span>最强序列</span><b>${fmt.int(timeline.best_points)} 点</b><small>${fmt.int(timeline.best_span_days)} 个完整自然日</small></div>
        <div><span>最近人工复核</span><b>${plan.last_reviewed_at ? escapeHtml(String(plan.last_reviewed_at).slice(0, 10)) : "尚无"}</b><small>${plan.last_review_evaluated_on ? `报告评估日 ${escapeHtml(plan.last_review_evaluated_on)}` : "保存计划不等于完成复核"}</small></div>
        <div><span>复核后证据</span><b>${plan.new_evidence_since_review === true ? "有变化" : (plan.new_evidence_since_review === false ? "未变化" : "未建立基线")}</b><small>只比较证据指纹</small></div>
      </div>
      <div class="research-observation-fingerprint">
        <span>当前报告指纹</span><code title="${escapeHtml(fingerprint)}">${escapeHtml(fingerprint.slice(0, 16) || "—")}</code>
        <span>${escapeHtml(report.method_version || "—")}</span>
      </div>
    </section>
    <section class="panel research-observation-editor">
      <div class="table-toolbar">
        <div><h2 style="margin:0">观察计划</h2><p>保存人工检查节奏；暂停只停止提醒，不影响项目和历史证据。</p></div>
        <span class="result-meta">周期范围 3–180 天</span>
      </div>
      <div class="research-observation-form">
        <fieldset class="research-observation-status">
          <legend>计划状态</legend>
          <label><input type="radio" name="research-observation-status" value="active" ${active ? "checked" : ""} /> 观察中</label>
          <label><input type="radio" name="research-observation-status" value="paused" ${active ? "" : "checked"} /> 已暂停</label>
        </fieldset>
        <label>复核周期（天）<input id="research-observation-cadence" type="number" min="3" max="180" step="1" value="${fmt.int(plan.cadence_days || 14)}" /></label>
        <label>下次复核日期<input id="research-observation-next" type="date" value="${escapeHtml(nextReview)}" /></label>
        <label class="research-observation-wide">观察重点<textarea id="research-observation-note" rows="3" maxlength="2000" placeholder="例如：重点核对核心词首屏稳定性、候选商品详情与供应链人工成本">${escapeHtml(plan.plan_note || "")}</textarea></label>
      </div>
      <div class="actions"><button class="btn btn-active" id="research-observation-save">保存观察计划</button></div>
    </section>
    <section class="panel research-observation-review">
      <div class="table-toolbar">
        <div><h2 style="margin:0">完成人工复核</h2><p>记录当前报告与证据指纹，并从今天起顺延一个复核周期。</p></div>
        <span class="result-meta">不会冻结报告或触发采集</span>
      </div>
      <label>本次复核摘要（可选）<textarea id="research-observation-review-note" rows="3" maxlength="2000" placeholder="记录本次看到了什么、为何继续观察，以及仍需人工补充的事项">${escapeHtml(plan.last_review_note || "")}</textarea></label>
      <div class="actions research-observation-review-actions">
        <button class="btn btn-active" id="research-observation-complete" ${!plan.exists || !active ? "disabled" : ""}>完成本次复核并安排下次</button>
        <a class="btn" href="#/tracking">查看关键词追踪</a>
      </div>
      ${!plan.exists ? `<div class="research-review-policy">请先保存观察计划，再完成本次复核。</div>` : ""}
      ${plan.status === "paused" ? `<div class="research-review-policy">计划已暂停；恢复为“观察中”并保存后才能完成复核。</div>` : ""}
    </section>`;
  document.getElementById("research-observation-save").onclick = () => saveResearchObservationPlan(Number(project.id));
  document.getElementById("research-observation-complete").onclick = () => completeResearchObservationReview(Number(project.id));
}

async function saveResearchObservationPlan(projectId) {
  const status = document.querySelector('input[name="research-observation-status"]:checked')?.value || "active";
  const cadenceDays = Number(document.getElementById("research-observation-cadence")?.value || 0);
  const nextReviewOn = String(document.getElementById("research-observation-next")?.value || "");
  const planNote = String(document.getElementById("research-observation-note")?.value || "").trim();
  if (!Number.isInteger(cadenceDays) || cadenceDays < 3 || cadenceDays > 180) return notice("复核周期必须是 3–180 天的整数", "bad");
  if (!nextReviewOn) return notice("请选择下次复核日期", "bad");
  if (!confirm(`确认保存该项目的观察计划？\n\n状态：${status === "active" ? "观察中" : "已暂停"}\n周期：${cadenceDays} 天\n下次复核：${nextReviewOn}\n\n该操作不会自动采集或冻结报告。`)) return;
  const button = document.getElementById("research-observation-save");
  button.disabled = true;
  try {
    researchObservationPlanCurrent = await apiSend(`/api/research-projects/${projectId}/observation-plan`, "PUT", {
      status,
      cadence_days: cadenceDays,
      next_review_on: nextReviewOn,
      plan_note: planNote || null,
    });
    renderResearchObservationPlan(researchObservationPlanCurrent);
    rememberAgentBusinessContext();
    notice("观察计划已保存", "ok");
  } catch (err) {
    notice(err.message || "观察计划保存失败", "bad");
    button.disabled = false;
  }
}

async function completeResearchObservationReview(projectId) {
  const bundle = researchObservationPlanCurrent || {};
  const plan = bundle.plan || {};
  const report = bundle.current_report || {};
  if (!plan.exists) return notice("请先保存观察计划", "bad");
  if (plan.status !== "active") return notice("请先恢复并保存观察计划", "bad");
  if (!report.report_fingerprint) return notice("当前报告指纹缺失，请刷新页面", "bad");
  const reviewNote = String(document.getElementById("research-observation-review-note")?.value || "").trim();
  if (!confirm(`确认完成本次人工复核？\n\n系统会记录当前报告与证据指纹，并从今天起顺延 ${fmt.int(plan.cadence_days)} 天。\n不会冻结报告、改变项目状态或触发采集。`)) return;
  const button = document.getElementById("research-observation-complete");
  button.disabled = true;
  try {
    researchObservationPlanCurrent = await apiSend(`/api/research-projects/${projectId}/observation-plan/review`, "POST", {
      confirmed: true,
      evaluated_on: report.evaluated_on,
      expected_report_fingerprint: report.report_fingerprint,
      review_note: reviewNote || null,
    });
    renderResearchObservationPlan(researchObservationPlanCurrent);
    rememberAgentBusinessContext();
    notice("本次人工复核已记录，下次日期已顺延", "ok");
  } catch (err) {
    notice(err.message || "完成项目复核失败", "bad");
    button.disabled = false;
  }
}

async function viewResearchProjects() {
  content.innerHTML = `
    <details class="panel research-create" id="research-create"${researchProjectState.createOpen ? " open" : ""}>
      <summary>新建研究项目</summary>
      <div class="research-form-grid">
        <label>项目名称<input id="research-create-name" maxlength="255" placeholder="例如：低竞争减压玩具验证" /></label>
        <label>方向标签<input id="research-create-strategy" maxlength="64" placeholder="例如：差异化 / 长尾机会" /></label>
        <label class="research-form-wide">研究目标<textarea id="research-create-objective" rows="3" maxlength="5000" placeholder="这次准备验证什么问题，以及什么证据会改变判断"></textarea></label>
      </div>
      <div class="actions research-form-actions">
        <button class="btn" id="research-create-submit">创建项目</button>
      </div>
    </details>
    <div class="panel research-list-panel">
      <div class="table-toolbar">
        <h2 style="margin:0">研究项目</h2>
        <span class="result-meta">从方向构想到人工结论</span>
      </div>
      <div id="research-summary" class="research-summary-grid"></div>
      <div class="filters">
        <input id="research-filter-keyword" placeholder="名称、目标或方向标签" />
        <input id="research-filter-marketplace" placeholder="站点" style="width:82px" />
        <select id="research-filter-status" class="sel">${researchStatusOptions("all", true)}</select>
        <button class="btn" id="research-filter-apply">筛选</button>
        <button class="btn" id="research-filter-reset">重置</button>
      </div>
      <div id="research-meta" class="result-meta"></div>
      <div id="research-table"></div>
      <div id="research-pager"></div>
    </div>`;

  document.getElementById("research-create-name").value = researchProjectState.draftName;
  document.getElementById("research-create-strategy").value = researchProjectState.draftStrategy;
  document.getElementById("research-create-objective").value = researchProjectState.draftObjective;
  document.getElementById("research-filter-keyword").value = researchProjectState.keyword;
  document.getElementById("research-filter-marketplace").value = researchProjectState.marketplace;
  document.getElementById("research-filter-status").value = researchProjectState.status;
  document.getElementById("research-create").ontoggle = (event) => {
    researchProjectState.createOpen = event.currentTarget.open;
    saveResearchProjectListState();
  };
  document.getElementById("research-create-submit").onclick = createResearchProject;
  document.getElementById("research-filter-apply").onclick = () => loadResearchProjects(true);
  document.getElementById("research-filter-reset").onclick = () => {
    researchProjectState.keyword = "";
    researchProjectState.marketplace = "US";
    researchProjectState.status = "all";
    researchProjectState.offset = 0;
    document.getElementById("research-filter-keyword").value = "";
    document.getElementById("research-filter-marketplace").value = "US";
    document.getElementById("research-filter-status").value = "all";
    loadResearchProjects();
  };
  bindStateInputs([
    "research-create-name", "research-create-strategy", "research-create-objective",
    "research-filter-keyword", "research-filter-marketplace", "research-filter-status",
  ], saveResearchProjectListState);
  content.querySelectorAll(".filters input, .filters select").forEach((input) => {
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") loadResearchProjects(true);
    });
  });
  await loadResearchProjects();
}

function saveResearchProjectListState() {
  const value = (id, fallback = "") => document.getElementById(id)?.value ?? fallback;
  researchProjectState.draftName = value("research-create-name", researchProjectState.draftName).trim();
  researchProjectState.draftStrategy = value("research-create-strategy", researchProjectState.draftStrategy).trim();
  researchProjectState.draftObjective = value("research-create-objective", researchProjectState.draftObjective);
  researchProjectState.keyword = value("research-filter-keyword", researchProjectState.keyword).trim();
  researchProjectState.marketplace = (value("research-filter-marketplace", researchProjectState.marketplace) || "US").trim().toUpperCase();
  researchProjectState.status = value("research-filter-status", researchProjectState.status) || "all";
  persistState(researchProjectState);
}

async function createResearchProject() {
  saveResearchProjectListState();
  if (!researchProjectState.draftName) return notice("请填写项目名称", "bad");
  const button = document.getElementById("research-create-submit");
  button.disabled = true;
  try {
    const data = await apiSend("/api/research-projects", "POST", {
      name: researchProjectState.draftName,
      marketplace: researchProjectState.marketplace || "US",
      objective: researchProjectState.draftObjective || null,
      strategy: researchProjectState.draftStrategy || null,
    });
    const projectId = data?.project?.id;
    researchProjectState.draftName = "";
    researchProjectState.draftObjective = "";
    researchProjectState.draftStrategy = "";
    persistState(researchProjectState);
    notice("研究项目已创建", "ok");
    if (projectId) location.hash = `#/research-projects/${encodeURIComponent(projectId)}`;
    else await viewResearchProjects();
  } catch (err) {
    notice(err.message, "bad");
  } finally {
    if (button) button.disabled = false;
  }
}

async function loadResearchProjects(resetPage = false) {
  if (resetPage) {
    researchProjectState.offset = 0;
    saveResearchProjectListState();
  }
  persistState(researchProjectState);
  const table = document.getElementById("research-table");
  const meta = document.getElementById("research-meta");
  const pager = document.getElementById("research-pager");
  if (!table) return;
  table.innerHTML = `<div class="state"><div class="spinner"></div>加载研究项目…</div>`;
  meta.textContent = "";
  pager.innerHTML = "";
  const query = new URLSearchParams({
    limit: String(researchProjectState.limit),
    offset: String(researchProjectState.offset),
    marketplace: researchProjectState.marketplace || "US",
    sort_by: researchProjectState.sortBy,
    sort_dir: researchProjectState.sortDir,
  });
  if (researchProjectState.keyword) query.set("keyword", researchProjectState.keyword);
  if (researchProjectState.status !== "all") query.set("status", researchProjectState.status);
  try {
    const page = normalizePage(await api(`/api/research-projects?${query.toString()}`), researchProjectState.limit);
    syncRemoteSortState(researchProjectState, page);
    const rows = page.rows || [];
    renderResearchProjectSummary(page.status_counts || {});
    meta.textContent = `${pageSummary(page, "研究项目")} · 证据覆盖只统计已关联资产，不替代人工判断`;
    if (!rows.length) {
      table.innerHTML = `<div class="state">暂无符合条件的研究项目。</div>`;
    } else {
      renderSortableTable(table, [
        { key: "name", label: "项目", render: (row) => `<b>${escapeHtml(row.name)}</b><div class="cell-sub">${escapeHtml(truncate(row.objective || "尚未填写研究目标", 56))}</div>`, sortVal: (row) => row.name },
        { key: "status", label: "阶段", render: (row) => researchStatusBadge(row.status), sortVal: (row) => row.status },
        { key: "product_count", label: "商品", align: "num", numeric: true, render: (row) => `${fmt.int(row.product_count)}<span class="cell-sub inline"> / 详情 ${fmt.int(row.detail_collected_count)}</span>`, sortVal: (row) => row.product_count },
        { key: "keyword_count", label: "关键词", align: "num", numeric: true, render: (row) => `${fmt.int(row.keyword_count)}<span class="cell-sub inline"> / 快照 ${fmt.int(row.keyword_with_snapshots)}</span>`, sortVal: (row) => row.keyword_count },
        { key: "evidence_coverage", label: "证据覆盖", align: "num", numeric: true, render: (row) => researchCoverage(row.evidence_coverage), sortVal: (row) => row.evidence_coverage },
        { key: "note_count", label: "人工记录", align: "num", numeric: true, render: (row) => `${fmt.int(row.note_count)}<div class="cell-sub">机会 ${fmt.int(row.opportunity_count)} · 风险 ${fmt.int(row.risk_count)}</div>`, sortVal: (row) => row.note_count },
        { key: "strategy", label: "方向标签", render: (row) => escapeHtml(fmt.text(row.strategy)), sortVal: (row) => row.strategy || "" },
        { key: "updated_at", label: "最近更新", render: (row) => fmt.text(row.updated_at), sortVal: (row) => row.updated_at },
        { key: "actions", label: "操作", sortable: false, csv: false, render: (row) => `<button class="btn btn-sm" onclick="event.stopPropagation();window.researchProjectOpen(${Number(row.id)})">打开</button>` },
      ], rows, {
        remoteSort: remoteSortOptions(researchProjectState, loadResearchProjects),
        rowHash: (row) => `#/research-projects/${encodeURIComponent(row.id)}`,
        exportName: "研究项目",
      });
    }
    pager.innerHTML = renderPager("research-project-pager", page, [10, 25, 50, 100]);
    bindPager("research-project-pager", researchProjectState, page, loadResearchProjects);
  } catch (err) {
    table.innerHTML = `<div class="state error">⚠ ${escapeHtml(err.message)}</div>`;
  }
}

function renderResearchProjectSummary(counts) {
  const box = document.getElementById("research-summary");
  if (!box) return;
  const active = ["idea", "collecting", "validating", "candidate", "manual_review"]
    .reduce((sum, key) => sum + Number(counts[key] || 0), 0);
  box.innerHTML = `
    <div><span>进行中</span><b>${fmt.int(active)}</b></div>
    <div><span>收集 / 验证</span><b>${fmt.int(Number(counts.collecting || 0) + Number(counts.validating || 0))}</b></div>
    <div><span>人工复核</span><b>${fmt.int(counts.manual_review)}</b></div>
    <div><span>已批准</span><b>${fmt.int(counts.approved)}</b></div>
    <div><span>已拒绝</span><b>${fmt.int(counts.rejected)}</b></div>`;
}

function researchStatusOptions(selected = "all", includeAll = false, values = null) {
  const keys = values || Object.keys(RESEARCH_STATUS_LABELS);
  const rows = includeAll ? [["all", "全部阶段"]] : [];
  keys.forEach((key) => rows.push([key, RESEARCH_STATUS_LABELS[key] || key]));
  return rows.map(([value, label]) => `<option value="${escapeHtml(value)}"${value === selected ? " selected" : ""}>${escapeHtml(label)}</option>`).join("");
}

function researchStatusBadge(status) {
  const cls = ({ approved: "badge-good", rejected: "badge-bad", candidate: "badge-warn", manual_review: "badge-warn", validating: "badge-good" })[status] || "badge-dim";
  return `<span class="badge ${cls}">${escapeHtml(RESEARCH_STATUS_LABELS[status] || status || "—")}</span>`;
}

function researchCoverage(value) {
  const score = Number(value || 0);
  const cls = score >= 75 ? "badge-good" : score > 0 ? "badge-warn" : "badge-dim";
  return `<span class="badge ${cls}">${score.toFixed(0)}%</span>`;
}

window.researchProjectOpen = (projectId) => {
  location.hash = `#/research-projects/${encodeURIComponent(projectId)}`;
};

async function viewResearchProjectDetail(projectId) {
  const id = Number(projectId);
  if (!Number.isInteger(id) || id < 1) return emptyState("研究项目 ID 不正确");
  if (Number(researchProjectDetailState.projectId) !== id) {
    Object.assign(researchProjectDetailState, {
      projectId: id,
      asins: "",
      productRole: "candidate",
      keywords: "",
      keywordRole: "candidate",
      noteType: "observation",
      noteContent: "",
      decisionSummary: "",
    });
    persistState(researchProjectDetailState);
  }
  loading();
  await loadResearchProjectDetail(id);
}

async function loadResearchProjectDetail(projectId) {
  try {
    const [projectData, detailReadiness] = await Promise.all([
      api(`/api/research-projects/${encodeURIComponent(projectId)}`),
      api(`/api/research-projects/${encodeURIComponent(projectId)}/detail-readiness`).catch((err) => ({
        error: err.message || "详情证据准备度读取失败",
      })),
    ]);
    researchProjectCurrent = { ...projectData, detail_readiness: detailReadiness };
    if (!researchProjectDetailState.decisionSummary && researchProjectCurrent.project?.decision_summary) {
      researchProjectDetailState.decisionSummary = researchProjectCurrent.project.decision_summary;
      persistState(researchProjectDetailState);
    }
    renderResearchProjectDetail(researchProjectCurrent);
    rememberAgentBusinessContext();
  } catch (err) {
    errorState(err);
  }
}

function renderResearchProjectDetail(data) {
  const project = data.project || {};
  const products = data.products || [];
  const keywords = data.keywords || [];
  const niches = data.niches || [];
  const notes = data.notes || [];
  const detailReadiness = data.detail_readiness || {};
  const nextStatuses = project.next_statuses || [];
  const frozen = ["approved", "rejected"].includes(project.status);
  content.innerHTML = `
    <a class="back-link" href="#/research-projects">← 返回研究项目</a>
    <div class="research-detail-head">
      <div>
        <h2>${escapeHtml(project.name || "研究项目")}</h2>
        <div class="meta">项目 #${fmt.int(project.id)} · ${escapeHtml(project.marketplace || "US")} · 更新 ${fmt.text(project.updated_at)}</div>
      </div>
      <div class="research-detail-actions">
        ${researchStatusBadge(project.status)}
        ${renderResearchProjectCompareAction(products)}
        <a class="btn" href="#/research-projects/${Number(project.id)}/report">决策报告</a>
        <a class="btn" href="#/research-projects/${Number(project.id)}/observation-plan">观察计划</a>
        <a class="btn" href="#/research-projects/${Number(project.id)}/report-versions">版本历史</a>
      </div>
    </div>
    ${frozen ? `<div class="evidence-warning research-project-frozen">
      <strong>项目证据已冻结</strong>
      <span>当前项目已${project.status === "approved" ? "批准" : "拒绝"}。${
        project.current_decision_report_version_no
          ? `终态依据为<a class="link" href="#/research-projects/${Number(project.id)}/report-versions/${Number(project.current_decision_report_version_no)}">报告版本 #${fmt.int(project.current_decision_report_version_no)}</a>。`
          : "这是历史未绑定终态，系统不会用当前动态证据伪造过去版本。"
      }如需修改定义、资产关系或人工记录，请先在下方将项目退回“${project.status === "approved" ? "人工复核" : "方向构想"}”。</span>
    </div>` : ""}
    <div class="research-summary-grid research-detail-summary">
      <div><span>关联商品</span><b>${fmt.int(project.product_count)}</b><small>详情可用 ${fmt.int(detailReadiness.summary?.ready_total)}</small></div>
      <div><span>关联关键词</span><b>${fmt.int(project.keyword_count)}</b><small>已有快照 ${fmt.int(project.keyword_with_snapshots)}</small></div>
      <div><span>证据覆盖</span><b>${fmt.num(project.evidence_coverage, 0)}%</b><small>只统计关联资产</small></div>
      <div><span>人工记录</span><b>${fmt.int(project.note_count)}</b><small>机会 ${fmt.int(project.opportunity_count)} · 风险 ${fmt.int(project.risk_count)}</small></div>
    </div>
    ${renderResearchDetailReadinessOverview(detailReadiness)}
    <div class="panel">
      <div class="table-toolbar"><h2 style="margin:0">项目定义</h2><button class="btn btn-sm" id="research-save-project">保存</button></div>
      <div class="research-form-grid">
        <label>项目名称<input id="research-edit-name" maxlength="255" value="${escapeHtml(project.name || "")}" /></label>
        <label>方向标签<input id="research-edit-strategy" maxlength="64" value="${escapeHtml(project.strategy || "")}" /></label>
        <label class="research-form-wide">研究目标<textarea id="research-edit-objective" rows="3" maxlength="5000">${escapeHtml(project.objective || "")}</textarea></label>
      </div>
    </div>
    <div class="panel research-decision-panel">
      <h2>阶段与人工结论</h2>
      <div class="research-decision-row">
        <label>目标阶段
          <span class="research-current-stage">当前：${escapeHtml(RESEARCH_STATUS_LABELS[project.status] || project.status || "—")}</span>
          <select id="research-next-status" class="sel">
            <option value="">${nextStatuses.length ? "请选择目标阶段" : "当前没有可变更阶段"}</option>
            ${researchStatusOptions("", false, nextStatuses)}
          </select>
        </label>
        <label class="research-decision-summary">阶段结论 / 最终结论
          <textarea id="research-decision-summary" rows="3" maxlength="10000" placeholder="批准或拒绝时至少填写 10 个字符；说明证据、风险和进入边界">${escapeHtml(researchProjectDetailState.decisionSummary || "")}</textarea>
        </label>
        <button class="btn" id="research-advance" disabled>确认变更</button>
      </div>
      ${project.decision_summary ? `<div class="research-existing-decision"><b>当前结论</b><p>${escapeHtml(project.decision_summary)}</p><span>${fmt.text(project.decided_at || project.status_changed_at)}</span></div>` : ""}
    </div>
    <div class="panel">
      <div class="table-toolbar"><h2 style="margin:0">关联市场利基</h2><a class="link" href="#/market-niches">前往市场与利基</a></div>
      ${renderResearchNiches(niches)}
    </div>
    <div class="panel">
      <div class="table-toolbar"><h2 style="margin:0">关联商品与详情证据</h2><span class="result-meta">类目、首次可售日期、BSR 共 3 项</span></div>
      <div class="research-inline-form">
        <textarea id="research-add-asins" rows="2" placeholder="输入已入库 ASIN，空格、逗号或换行分隔">${escapeHtml(researchProjectDetailState.asins)}</textarea>
        <select id="research-product-role" class="sel">${researchRoleOptions(RESEARCH_PRODUCT_ROLE_LABELS, researchProjectDetailState.productRole)}</select>
        <button class="btn" id="research-add-products">关联商品</button>
      </div>
      <div id="research-products-table">${renderResearchProducts(products, detailReadiness.rows || [], project.id)}</div>
    </div>
    <div class="panel">
      <div class="table-toolbar"><h2 style="margin:0">关联关键词与市场证据</h2><span class="result-meta">只关联关键词资产库中已有关键词</span></div>
      <div class="research-inline-form">
        <textarea id="research-add-keywords" rows="2" placeholder="输入已入库关键词，逗号或换行分隔">${escapeHtml(researchProjectDetailState.keywords)}</textarea>
        <select id="research-keyword-role" class="sel">${researchRoleOptions(RESEARCH_KEYWORD_ROLE_LABELS, researchProjectDetailState.keywordRole)}</select>
        <button class="btn" id="research-add-keywords-btn">关联关键词</button>
      </div>
      <div id="research-keywords-table">${renderResearchKeywords(keywords)}</div>
    </div>
    <div class="panel">
      <h2>人工判断记录</h2>
      <div class="research-note-form">
        <select id="research-note-type" class="sel">${researchRoleOptions(RESEARCH_NOTE_TYPE_LABELS, researchProjectDetailState.noteType)}</select>
        <textarea id="research-note-content" rows="3" maxlength="20000" placeholder="记录观察、机会、风险或决策依据；不要把推测写成事实">${escapeHtml(researchProjectDetailState.noteContent)}</textarea>
        <button class="btn" id="research-add-note">添加记录</button>
      </div>
      <div class="research-note-list">${renderResearchNotes(notes)}</div>
    </div>`;

  document.getElementById("research-save-project").onclick = () => updateResearchProject(project.id);
  document.getElementById("research-advance").onclick = () => advanceResearchProject(project.id);
  document.getElementById("research-next-status").onchange = (event) => {
    document.getElementById("research-advance").disabled = !event.target.value;
  };
  document.getElementById("research-add-products").onclick = () => addResearchProducts(project.id);
  document.getElementById("research-add-keywords-btn").onclick = () => addResearchKeywords(project.id);
  document.getElementById("research-add-note").onclick = () => addResearchNote(project.id);
  bindStateInputs([
    "research-decision-summary", "research-add-asins", "research-product-role",
    "research-add-keywords", "research-keyword-role", "research-note-type", "research-note-content",
  ], saveResearchProjectDetailDraft);
  if (frozen) {
    [
      "research-save-project", "research-edit-name", "research-edit-strategy", "research-edit-objective",
      "research-add-asins", "research-product-role", "research-add-products",
      "research-add-keywords", "research-keyword-role", "research-add-keywords-btn",
      "research-note-type", "research-note-content", "research-add-note",
    ].forEach((id) => {
      const element = document.getElementById(id);
      if (element) element.disabled = true;
    });
    content.querySelectorAll("[data-research-evidence-write]").forEach((button) => {
      button.disabled = true;
      button.title = "项目证据已冻结，请先退回可编辑阶段";
    });
  }
}

function renderResearchProjectCompareAction(rows) {
  const roleOrder = { candidate: 0, benchmark: 1, competitor: 2, reference: 3 };
  const asins = [...new Set((rows || [])
    .slice()
    .sort((left, right) => (roleOrder[left.role] ?? 9) - (roleOrder[right.role] ?? 9))
    .map((row) => String(row.asin || "").trim().toUpperCase())
    .filter((asin) => /^[A-Z0-9]{10}$/.test(asin)))];
  if (asins.length < 2) return "";
  const selected = asins.slice(0, 5);
  const limited = asins.length > selected.length;
  const label = limited ? "对比优先 5 个" : "商品对比";
  const title = limited
    ? `项目共 ${asins.length} 个商品；现有对比页上限为 5，本次按候选、对标、竞品、参考顺序带入前 5 个。`
    : `并排对比项目内 ${selected.length} 个商品`;
  return `<a class="btn" href="#/compare/${encodeURIComponent(selected.join(","))}" title="${escapeHtml(title)}">${label}</a>`;
}

function researchRoleOptions(labels, selected) {
  return Object.entries(labels).map(([value, label]) => `<option value="${escapeHtml(value)}"${value === selected ? " selected" : ""}>${escapeHtml(label)}</option>`).join("");
}

function saveResearchProjectDetailDraft() {
  const value = (id, fallback = "") => document.getElementById(id)?.value ?? fallback;
  researchProjectDetailState.decisionSummary = value("research-decision-summary", researchProjectDetailState.decisionSummary);
  researchProjectDetailState.asins = value("research-add-asins", researchProjectDetailState.asins);
  researchProjectDetailState.productRole = value("research-product-role", researchProjectDetailState.productRole);
  researchProjectDetailState.keywords = value("research-add-keywords", researchProjectDetailState.keywords);
  researchProjectDetailState.keywordRole = value("research-keyword-role", researchProjectDetailState.keywordRole);
  researchProjectDetailState.noteType = value("research-note-type", researchProjectDetailState.noteType);
  researchProjectDetailState.noteContent = value("research-note-content", researchProjectDetailState.noteContent);
  persistState(researchProjectDetailState);
}

async function updateResearchProject(projectId) {
  const button = document.getElementById("research-save-project");
  button.disabled = true;
  try {
    await apiSend(`/api/research-projects/${projectId}`, "PATCH", {
      name: document.getElementById("research-edit-name").value.trim(),
      objective: document.getElementById("research-edit-objective").value,
      strategy: document.getElementById("research-edit-strategy").value,
    });
    notice("项目定义已保存", "ok");
    await loadResearchProjectDetail(projectId);
  } catch (err) {
    notice(err.message, "bad");
  } finally {
    if (button) button.disabled = false;
  }
}

async function advanceResearchProject(projectId) {
  saveResearchProjectDetailDraft();
  const status = document.getElementById("research-next-status")?.value;
  if (!status) return notice("请先选择目标阶段", "bad");
  const label = RESEARCH_STATUS_LABELS[status] || status;
  const button = document.getElementById("research-advance");
  if (["approved", "rejected"].includes(status)) {
    const summary = String(researchProjectDetailState.decisionSummary || "").trim();
    if (summary.length < 10) return notice("批准或拒绝前，请填写不少于 10 个字符的人工结论", "bad");
    button.disabled = true;
    try {
      const evaluatedOn = researchReportDefaultDate();
      const report = await api(
        `/api/research-projects/${projectId}/decision-report?as_of=${encodeURIComponent(evaluatedOn)}`,
      );
      openResearchReportFreezeDialog({
        projectId,
        report,
        mode: "decision",
        targetStatus: status,
        decisionSummary: summary,
        onSuccess: async () => {
          notice(`项目已进入“${label}”，终态证据版本已冻结`, "ok");
          await loadResearchProjectDetail(projectId);
        },
      });
    } catch (err) {
      notice(err.message, "bad");
    } finally {
      button.disabled = false;
    }
    return;
  }
  if (!confirm(`确定将研究项目推进到“${label}”吗？`)) return;
  button.disabled = true;
  try {
    await apiSend(`/api/research-projects/${projectId}/status`, "POST", {
      status,
      decision_summary: researchProjectDetailState.decisionSummary || null,
    });
    notice(`项目已进入“${label}”`, "ok");
    await loadResearchProjectDetail(projectId);
  } catch (err) {
    notice(err.message, "bad");
    button.disabled = false;
  }
}

async function addResearchProducts(projectId) {
  saveResearchProjectDetailDraft();
  const asins = researchProjectDetailState.asins.split(/[\s,，;；]+/).map((item) => item.trim()).filter(Boolean);
  if (!asins.length) return notice("请填写至少一个 ASIN", "bad");
  try {
    const result = await apiSend(`/api/research-projects/${projectId}/products`, "POST", {
      asins,
      role: researchProjectDetailState.productRole,
    });
    researchProjectDetailState.asins = "";
    persistState(researchProjectDetailState);
    const missing = result.missing || [];
    notice(missing.length ? `已关联 ${result.linked} 个，未入库：${missing.join("、")}` : `已关联 ${result.linked} 个商品`, missing.length ? "bad" : "ok");
    await loadResearchProjectDetail(projectId);
  } catch (err) {
    notice(err.message, "bad");
  }
}

async function addResearchKeywords(projectId) {
  saveResearchProjectDetailDraft();
  const keywords = researchProjectDetailState.keywords.split(/[\r\n,，;；]+/).map((item) => item.trim()).filter(Boolean);
  if (!keywords.length) return notice("请填写至少一个已入库关键词", "bad");
  try {
    const result = await apiSend(`/api/research-projects/${projectId}/keywords`, "POST", {
      keywords,
      role: researchProjectDetailState.keywordRole,
    });
    researchProjectDetailState.keywords = "";
    persistState(researchProjectDetailState);
    const missing = result.missing || [];
    notice(missing.length ? `已关联 ${result.linked} 个，资产库未找到：${missing.join("、")}` : `已关联 ${result.linked} 个关键词`, missing.length ? "bad" : "ok");
    await loadResearchProjectDetail(projectId);
  } catch (err) {
    notice(err.message, "bad");
  }
}

async function addResearchNote(projectId) {
  saveResearchProjectDetailDraft();
  if (!researchProjectDetailState.noteContent.trim()) return notice("请填写记录内容", "bad");
  try {
    await apiSend(`/api/research-projects/${projectId}/notes`, "POST", {
      note_type: researchProjectDetailState.noteType,
      content: researchProjectDetailState.noteContent,
    });
    researchProjectDetailState.noteContent = "";
    persistState(researchProjectDetailState);
    notice("人工记录已添加", "ok");
    await loadResearchProjectDetail(projectId);
  } catch (err) {
    notice(err.message, "bad");
  }
}

function renderResearchDetailReadinessOverview(data) {
  if (data?.error) {
    return `<section class="panel research-detail-readiness-panel"><div class="state error">${escapeHtml(data.error)}</div></section>`;
  }
  const summary = data?.summary || {};
  const scan = data?.local_scan || {};
  const next = summary.next_action || null;
  const projectId = Number(data?.project?.id || 0);
  return `<section class="panel research-detail-readiness-panel">
    <div class="table-toolbar">
      <div><h2 style="margin:0">详情证据准备度</h2><span class="result-meta">项目成员范围 · 不进入评分与决策门禁</span></div>
      <button class="btn btn-sm" type="button" onclick="window.researchOpenDetailEvidence(${projectId})">证据队列</button>
    </div>
    <div class="research-detail-readiness-lead">
      ${researchDetailReadinessLevelBadge(summary)}
      <span>${escapeHtml(summary.statement || "尚无详情证据准备度结果。")}</span>
    </div>
    <div class="research-detail-readiness-summary">
      <div><span>当前可用</span><b>${fmt.int(summary.ready_total)} / ${fmt.int(summary.product_total)}</b></div>
      <div><span>需显式联网</span><b>${fmt.int(summary.network_action_total)}</b></div>
      <div><span>可先用本地</span><b>${fmt.int(summary.local_action_total)}</b></div>
      <div><span>页面受限</span><b>${fmt.int(summary.hold_total)}</b></div>
      <div><span>人工核对</span><b>${fmt.int(summary.review_total)}</b></div>
    </div>
    ${next ? `<div class="research-detail-next-action"><span>下一项</span><b>${escapeHtml(next.asin || "—")} · ${escapeHtml(next.label || "人工核对")}</b><small>${escapeHtml(next.reason || "")}</small></div>` : ""}
    <div class="research-detail-scan-note" title="${escapeHtml(scan.meaning || "")}">本地 HTML：${scan.checked ? (scan.complete ? "目录核对完整" : "仅核对当前扫描范围") : "本轮未核对"} · ${fmt.int(scan.file_count)} / 上限 ${fmt.int(scan.file_limit)}</div>
  </section>`;
}

function researchDetailReadinessLevelBadge(summary) {
  const level = summary?.level || "empty";
  const cls = level === "ready" ? "badge-good" : level === "action_required" ? "badge-warn" : level === "page_limited" ? "badge-dim" : "badge-dim";
  return `<span class="badge ${cls}">${escapeHtml(summary?.label || "尚未评估")}</span>`;
}

function researchDetailEvidenceStatus(row) {
  if (!row) return `<span class="badge badge-dim">未读取</span>`;
  const status = row.evidence_status || "partial";
  const cls = status === "ready" ? "badge-good" : status === "not_collected" ? "badge-bad" : "badge-warn";
  const fields = (row.field_statuses || []).map((field) => `<span class="badge ${field.status === "collected" ? "badge-good" : "badge-dim"}">${escapeHtml(field.label || field.key || "字段")}</span>`).join("");
  return `<div class="research-detail-evidence-cell"><span class="badge ${cls}">${escapeHtml(row.evidence_status_label || status)} ${fmt.int(row.evidence_collected)}/${fmt.int(row.evidence_total)}</span><div class="research-detail-field-badges">${fields}</div><small>${row.detail_collected_at ? `${fmt.text(row.detail_collected_at)} · ${fmt.int(row.detail_age_days)} 天` : "尚无详情采集时间"}</small></div>`;
}

function researchDetailDispositionCell(row) {
  const action = row?.recommended_action || {};
  const kind = action.kind || "review";
  const cls = kind === "ready" ? "badge-good" : kind === "network" ? "badge-warn" : kind === "local" ? "badge-good" : kind === "hold" ? "badge-dim" : "badge-warn";
  return `<div class="research-detail-disposition"><span class="badge ${cls}">${escapeHtml(action.label || "人工核对")}</span><small title="${escapeHtml(action.reason || "")}">${escapeHtml(truncate(action.reason || "暂无处置说明", 92))}</small></div>`;
}

function renderResearchDetailReadinessReport(data) {
  if (data?.error) {
    return `<section id="research-report-detail-evidence" class="research-report-section research-report-anchor"><div class="state error">${escapeHtml(data.error)}</div></section>`;
  }
  const summary = data?.summary || {};
  const rows = Array.isArray(data?.rows) ? data.rows : [];
  const scan = data?.local_scan || {};
  const projectId = Number(data?.project?.id || 0);
  const body = rows.map((row) => {
    const hash = productDetailHash(row.asin);
    const missing = (row.missing_fields || []).map((field) => field.label || field.key).join("、") || "无核心字段缺口";
    const local = row.has_local_evidence
      ? `${metricEvidenceStatusBadge(row.local_evidence_status)}<div class="cell-sub">${fmt.text(row.local_evidence_captured_at)}</div>`
      : `<span class="cell-sub">${scan.complete ? "目录未发现匹配 HTML" : "当前扫描范围未匹配"}</span>`;
    return `<tr${selectableEntryAttrs(hash, row.asin)}>
      <td><b>${escapeHtml(row.asin || "—")}</b><div class="cell-sub">${escapeHtml(truncate(row.title || "未命名商品", 48))} · ${escapeHtml(RESEARCH_PRODUCT_ROLE_LABELS[row.role] || row.role || "—")}</div></td>
      <td>${researchDetailEvidenceStatus(row)}</td>
      <td>${escapeHtml(missing)}</td>
      <td>${researchDetailDispositionCell(row)}</td>
      <td>${local}</td>
      <td class="research-row-actions"><a class="btn btn-sm" href="#/metrics/${encodeURIComponent(row.asin)}" onclick="event.stopPropagation()">指标</a><button class="btn btn-sm" type="button" onclick="event.stopPropagation();window.researchOpenDetailEvidence(${projectId})">证据队列</button></td>
    </tr>`;
  }).join("");
  return `<section id="research-report-detail-evidence" class="research-report-section research-report-anchor research-detail-readiness-report">
    <div class="research-report-section-head">
      <div><h2>详情证据准备度</h2><p>${escapeHtml(summary.statement || "尚无详情证据结果。")}</p></div>
      ${researchDetailReadinessLevelBadge(summary)}
    </div>
    <div class="research-detail-readiness-summary research-detail-readiness-summary-report">
      <div><span>当前可用</span><b>${fmt.int(summary.ready_total)} / ${fmt.int(summary.product_total)}</b></div>
      <div><span>需显式联网</span><b>${fmt.int(summary.network_action_total)}</b></div>
      <div><span>可先用本地</span><b>${fmt.int(summary.local_action_total)}</b></div>
      <div><span>页面受限</span><b>${fmt.int(summary.hold_total)}</b></div>
      <div><span>人工核对</span><b>${fmt.int(summary.review_total)}</b></div>
    </div>
    ${rows.length ? wrapTable(`<table class="research-detail-readiness-table"><thead><tr><th>项目商品</th><th>详情状态</th><th>核心缺口</th><th>处置建议</th><th>本地证据</th><th>入口</th></tr></thead><tbody>${body}</tbody></table>`) : `<div class="state research-empty">当前项目尚未关联商品。</div>`}
    <div class="research-detail-scan-note" title="${escapeHtml(scan.meaning || "")}">本地 HTML：${scan.checked ? (scan.complete ? "目录核对完整" : "仅核对当前扫描范围") : "本轮未核对"} · 准备度不改变决策门禁或冻结报告指纹</div>
  </section>`;
}

window.researchOpenDetailEvidence = (projectId = null) => {
  const normalizedProjectId = Number(projectId || 0);
  metricEvidenceState.projectId = Number.isInteger(normalizedProjectId) && normalizedProjectId > 0
    ? normalizedProjectId
    : "";
  metricEvidenceState.projectName = "";
  metricEvidenceState.gapPriority = metricEvidenceState.projectId ? "all" : "focus";
  metricEvidenceState.offset = 0;
  persistState(metricEvidenceState);
  location.hash = "#/metrics/evidence";
};

function renderResearchProducts(rows, readinessRows = [], projectId = null) {
  if (!rows.length) return `<div class="state research-empty">尚未关联商品。先从商品池确认 ASIN，再在上方关联。</div>`;
  const readinessByAsin = new Map(readinessRows.map((row) => [String(row.asin || "").toUpperCase(), row]));
  const body = rows.map((row) => {
    const hash = productDetailHash(row.asin, row.score_keyword);
    const detail = readinessByAsin.get(String(row.asin || "").toUpperCase());
    return `<tr${selectableEntryAttrs(hash, `${row.asin}|${row.score_keyword || ""}`)}>
    <td>${amazonProductLink(row, displayTitle(row), 48)}<div class="cell-sub">${escapeHtml(row.asin)} · ${escapeHtml(RESEARCH_PRODUCT_ROLE_LABELS[row.role] || row.role)}</div></td>
    <td class="num">${scoreBadge(row.total_score)}</td>
    <td class="num">${fmt.money(row.price)}</td>
    <td class="num">${fmt.num(row.rating)} / ${fmt.int(row.review_count)}</td>
    <td class="num">${fmt.int(row.monthly_bought)}</td>
    <td>${researchDetailEvidenceStatus(detail)}</td>
    <td>${researchDetailDispositionCell(detail)}</td>
    <td>${fmt.text(row.snapshot_at)}<div class="cell-sub">快照 ${fmt.int(row.snapshot_count)}</div></td>
    <td class="research-row-actions">
      <a class="btn btn-sm" href="#/metrics/${encodeURIComponent(row.asin)}" onclick="event.stopPropagation()">指标</a>
      <button class="btn btn-sm" type="button" onclick="event.stopPropagation();window.researchOpenDetailEvidence(${Number(projectId || 0)})">证据</button>
      <button class="btn btn-sm" data-research-evidence-write onclick="event.stopPropagation();window.researchRemoveProduct(${Number(row.product_id)})">移除</button>
    </td>
  </tr>`;
  }).join("");
  return wrapTable(`<table class="research-project-products-table"><thead><tr><th>商品</th><th class="num">综合分</th><th class="num">价格</th><th class="num">评分 / 评论</th><th class="num">近月购买</th><th>详情证据</th><th>处置建议</th><th>最近快照</th><th>操作</th></tr></thead><tbody>${body}</tbody></table>`);
}

function renderResearchKeywords(rows) {
  if (!rows.length) return `<div class="state research-empty">尚未关联关键词。关键词需先进入关键词资产库。</div>`;
  const body = rows.map((row) => `<tr>
    <td><button class="research-text-link" onclick="window.researchOpenKeywordById(${Number(row.keyword_id)})">${escapeHtml(row.keyword)}</button><div class="cell-sub">${escapeHtml(RESEARCH_KEYWORD_ROLE_LABELS[row.role] || row.role)}</div></td>
    <td class="num">${fmt.int(row.product_count)}</td>
    <td class="num">${fmt.int(row.rank_snapshot_count)}</td>
    <td>${fmt.text(row.latest_snapshot_at)}</td>
    <td class="num">${fmt.num(row.avg_organic_rank, 1)}</td>
    <td class="num">${scoreBadge(row.avg_total_score)}</td>
    <td>${keywordLibraryTrackingBadge(row.tracking_status || "none")}</td>
    <td>${row.idea_score == null ? "—" : `${scoreBadge(row.idea_score)} <span class="cell-sub inline">置信 ${fmt.num(row.confidence_score, 0)}</span>`}</td>
    <td><button class="btn btn-sm" data-research-evidence-write onclick="window.researchRemoveKeyword(${Number(row.keyword_id)})">移除</button></td>
  </tr>`).join("");
  return wrapTable(`<table><thead><tr><th>关键词</th><th class="num">商品</th><th class="num">排名快照</th><th>最近采集</th><th class="num">平均序位</th><th class="num">机会信号</th><th>追踪</th><th>创意证据</th><th>操作</th></tr></thead><tbody>${body}</tbody></table>`);
}

function renderResearchNotes(rows) {
  if (!rows.length) return `<div class="state research-empty">暂无人工记录。</div>`;
  return rows.map((row) => `<div class="research-note-row research-note-${escapeHtml(row.note_type)}">
    <div class="research-note-meta"><span class="badge badge-dim">${escapeHtml(RESEARCH_NOTE_TYPE_LABELS[row.note_type] || row.note_type)}</span><span>${fmt.text(row.created_at)}</span></div>
    <p>${escapeHtml(row.content)}</p>
    <button class="btn btn-sm" data-research-evidence-write onclick="window.researchDeleteNote(${Number(row.id)})">删除</button>
  </div>`).join("");
}

function renderResearchNiches(rows) {
  if (!rows.length) return `<div class="state research-empty">尚未关联市场利基。请在“市场与利基”详情页选择本研究项目。</div>`;
  const body = rows.map((row) => `<tr>
    <td><a class="link" href="#/market-niches/${Number(row.niche_id)}">${escapeHtml(row.name)}</a><div class="cell-sub">${escapeHtml(NICHE_PROJECT_ROLE_LABELS[row.role] || row.role)}</div></td>
    <td>${marketNicheStatusBadge(row.status)}</td>
    <td>${marketNicheEvidenceBadge({ evidence_level: row.evidence_level, evidence_level_label: ({ usable: "较完整", partial: "部分可用", insufficient: "证据不足", not_generated: "尚未生成" })[row.evidence_level] })}</td>
    <td class="num">${fmt.int(row.observed_product_count)}</td>
    <td>${marketRatio(row.serp_coverage)}</td>
    <td>${marketRatio(row.cross_keyword_overlap)}</td>
    <td>${fmt.text(row.latest_snapshot_at)}</td>
  </tr>`).join("");
  return wrapTable(`<table><thead><tr><th>利基</th><th>状态</th><th>证据状态</th><th class="num">去重 ASIN</th><th>SERP 覆盖</th><th>跨词重合</th><th>最近快照</th></tr></thead><tbody>${body}</tbody></table>`);
}

window.researchRemoveProduct = async (productId) => {
  const projectId = Number(researchProjectCurrent?.project?.id);
  if (!projectId || !confirm("确定从当前研究项目移除该商品吗？不会删除商品主数据。")) return;
  try {
    await apiSend(`/api/research-projects/${projectId}/products/${Number(productId)}`, "DELETE");
    notice("商品已从项目移除", "ok");
    await loadResearchProjectDetail(projectId);
  } catch (err) { notice(err.message, "bad"); }
};

window.researchRemoveKeyword = async (keywordId) => {
  const projectId = Number(researchProjectCurrent?.project?.id);
  if (!projectId || !confirm("确定从当前研究项目移除该关键词吗？不会删除关键词资产。")) return;
  try {
    await apiSend(`/api/research-projects/${projectId}/keywords/${Number(keywordId)}`, "DELETE");
    notice("关键词已从项目移除", "ok");
    await loadResearchProjectDetail(projectId);
  } catch (err) { notice(err.message, "bad"); }
};

window.researchDeleteNote = async (noteId) => {
  const projectId = Number(researchProjectCurrent?.project?.id);
  if (!projectId || !confirm("确定删除这条人工记录吗？")) return;
  try {
    await apiSend(`/api/research-projects/${projectId}/notes/${Number(noteId)}`, "DELETE");
    notice("记录已删除", "ok");
    await loadResearchProjectDetail(projectId);
  } catch (err) { notice(err.message, "bad"); }
};

window.researchOpenKeywordById = (keywordId) => {
  const row = (researchProjectCurrent?.keywords || []).find((item) => Number(item.keyword_id) === Number(keywordId));
  if (!row) return;
  keywordLibraryState.keyword = String(row.keyword || "");
  keywordLibraryState.offset = 0;
  persistState(keywordLibraryState);
  location.hash = "#/keyword-library";
};

/* ---------- 视图：研究项目决策报告 ---------- */
const RESEARCH_REPORT_AXIS_STATUS = {
  supporting: ["形成支持信号", "badge-good"],
  mixed: ["信号混合", "badge-warn"],
  risk: ["存在明显压力", "badge-bad"],
  insufficient: ["证据不足", "badge-dim"],
};
const RESEARCH_REPORT_SEVERITY = {
  blocker: "badge-bad",
  high: "badge-warn",
  medium: "badge-dim",
  low: "badge-dim",
};
const RESEARCH_REPORT_SOURCE_LABELS = {
  niche_snapshot: "利基快照",
  product_snapshot: "商品快照",
  keyword_rank: "关键词排名",
  manual_note: "人工记录",
  manual_or_official_input: "人工/官方输入",
  official_input: "官方输入",
  project_relation: "项目关系",
  product_detail: "详情证据",
  time_series: "时间序列",
  project_summary: "项目汇总",
  source_manifest: "来源清单",
};

async function viewResearchDecisionReport(projectId) {
  const id = Number(projectId);
  if (!Number.isInteger(id) || id < 1) return emptyState("研究项目 ID 不正确");
  if (Number(researchDecisionReportState.projectId) !== id) {
    researchDecisionReportState.projectId = id;
    researchDecisionReportState.asOf = "";
    persistState(researchDecisionReportState);
  }
  loading();
  await loadResearchDecisionReport(id);
}

async function loadResearchDecisionReport(projectId) {
  try {
    const query = new URLSearchParams();
    if (researchDecisionReportState.asOf) query.set("as_of", researchDecisionReportState.asOf);
    const suffix = query.toString() ? `?${query.toString()}` : "";
    const [baselineComparison, detailReadiness] = await Promise.all([
      api(`/api/research-projects/${encodeURIComponent(projectId)}/report-baseline-diff${suffix}`),
      api(`/api/research-projects/${encodeURIComponent(projectId)}/detail-readiness`).catch((err) => ({
        error: err.message || "详情证据准备度读取失败",
      })),
    ]);
    researchDecisionBaselineComparison = baselineComparison;
    researchDecisionReportCurrent = {
      ...researchDecisionBaselineComparison.current_report,
      detail_evidence_readiness: detailReadiness,
    };
    renderResearchDecisionReport(researchDecisionReportCurrent, researchDecisionBaselineComparison);
    rememberAgentBusinessContext();
  } catch (err) {
    researchDecisionBaselineComparison = null;
    errorState(err);
  }
}

function renderResearchDecisionReport(report, baselineComparison = null) {
  const project = report.project || {};
  const readiness = report.readiness || {};
  const health = report.evidence_health || {};
  const freshness = health.freshness || {};
  const timeline = health.timeline || {};
  const detailReadiness = report.detail_evidence_readiness || {};
  const axes = report.decision_axes || [];
  const gaps = report.data_gaps || [];
  const guardrails = report.guardrails || {};
  const hasTrendEvidence = Array.isArray(timeline.sequences) && timeline.sequences.length > 0;
  const baseline = baselineComparison?.baseline || null;
  const baselineDiff = baselineComparison?.diff || null;
  const jumpItems = [
    ["research-report-overview", "概览"],
    ...(baselineComparison ? [["research-report-baseline", "冻结后变化"]] : []),
    ...(hasTrendEvidence ? [["research-report-trend", "趋势证据"]] : []),
    ["research-report-detail-evidence", "详情证据"],
    ["research-report-gates", "决策门禁"],
    ["research-report-axes", "判断轴线"],
    ["research-report-arguments", "支持与风险"],
    ["research-report-gaps", "数据缺口"],
    ["research-report-operations", "运营核查"],
    ["research-report-sources", "方法与来源"],
  ];
  const readinessClass = `research-report-readiness-${escapeHtml(readiness.level || "not_started")}`;
  content.innerHTML = `
    <div class="research-report-page">
      <div class="research-report-topline">
        <a class="back-link" href="#/research-projects/${Number(project.id)}">← 返回研究项目</a>
        <div class="research-report-actions">
          <label class="research-report-date">评估日期
            <input id="research-report-as-of" type="date" value="${escapeHtml(report.evaluated_on || "")}" />
          </label>
          <button class="btn" id="research-report-refresh">刷新证据</button>
          <button class="btn btn-active" id="research-report-freeze">冻结版本</button>
          ${baseline ? `<a class="btn" href="#/research-projects/${Number(project.id)}/report-live-compare/${Number(baseline.version_no)}">冻结后变化</a>` : ""}
          <a class="btn" href="#/research-projects/${Number(project.id)}/observation-plan">观察计划</a>
          <a class="btn" href="#/research-projects/${Number(project.id)}/report-versions">版本历史</a>
          <button class="btn" id="research-report-json">导出 JSON</button>
          <button class="btn" id="research-report-markdown">导出 Markdown</button>
        </div>
      </div>
      <nav class="research-report-jump" aria-label="报告段落">
        ${jumpItems.map(([target, label]) => `<button type="button" data-report-jump="${target}">${label}</button>`).join("")}
      </nav>
      <header id="research-report-overview" class="research-report-header research-report-anchor">
        <div>
          <div class="research-report-kicker">研究项目 #${fmt.int(project.id)} · ${escapeHtml(project.marketplace || "US")}</div>
          <h2>${escapeHtml(project.name || "决策报告")}</h2>
          <p class="report-context-summary">${escapeHtml(readiness.summary || "")}</p>
        </div>
        <div class="research-report-stage">
          ${researchStatusBadge(project.status)}
          ${baselineDiff ? `<span class="badge ${baselineDiff.evidence_fingerprint_changed ? "badge-warn" : "badge-dim"}">${baselineDiff.evidence_fingerprint_changed ? "冻结后有新证据" : "证据与基线一致"}</span>` : ""}
          <span class="badge ${readiness.blocking_count ? "badge-bad" : "badge-good"}">阻断 ${fmt.int(readiness.blocking_count || 0)}</span>
        </div>
      </header>
      <section class="research-report-readiness ${readinessClass}">
        <div class="research-report-readiness-main">
          <span>决策就绪度</span>
          <b>${escapeHtml(readiness.label || "尚未评估")}</b>
          <small>这是证据门禁，不是机会分或自动推荐。</small>
        </div>
        <div class="research-report-kpis">
          <div><span>门禁通过</span><b>${fmt.int(readiness.passed_count || 0)} / ${fmt.int(readiness.total_count || 0)}</b><small>最终判断仍由人工完成</small></div>
          <div><span>证据截至</span><b>${escapeHtml(report.evidence_as_of || "无时间来源")}</b><small>${escapeHtml(freshness.label || "未评估")}</small></div>
          <div><span>最强序列</span><b>${fmt.int(timeline.best_points || 0)} 合格 / ${fmt.int(timeline.best_raw_points ?? timeline.best_points ?? 0)} 原始</b><small>${fmt.int(timeline.best_span_days || 0)} 天 · ${escapeHtml(timeline.best_quality_label || timeline.best_source_label || "暂无时间序列")}</small></div>
          <div><span>关联范围</span><b>${fmt.int(health.product_count || 0)} 商品 · ${fmt.int(health.keyword_count || 0)} 词</b><small>${fmt.int(health.niche_count || 0)} 个市场利基</small></div>
        </div>
      </section>
      ${renderResearchBaselineReview(baselineComparison, Number(project.id))}
      ${renderResearchTrendQuality(timeline)}
      ${renderResearchDetailReadinessReport(detailReadiness)}
      ${project.decision_summary ? `
        <section class="research-report-human-decision">
          <span>当前人工结论</span>
          <p>${escapeHtml(project.decision_summary)}</p>
        </section>` : ""}
      <section id="research-report-gates" class="research-report-section research-report-anchor">
        <div class="research-report-section-head">
          <div><h2>决策门禁</h2><p>缺失项限制最终进入结论，但不会阻止早期项目继续积累证据。</p></div>
          <span class="result-meta">${fmt.int(readiness.passed_count || 0)} / ${fmt.int(readiness.total_count || 0)} 已通过</span>
        </div>
        <div class="research-report-gates">${renderResearchReportGates(readiness.gates || [])}</div>
      </section>
      <section id="research-report-axes" class="research-report-section research-report-anchor">
        <div class="research-report-section-head">
          <div><h2>业务判断轴线</h2><p>需求、竞争、差异化、趋势、财务和证据质量分别判断，不压成单一总分。</p></div>
        </div>
        <div class="research-report-axis-grid">${axes.map(renderResearchReportAxis).join("")}</div>
      </section>
      <section id="research-report-arguments" class="research-report-arguments research-report-anchor">
        ${renderResearchReportArguments("支持理由", report.supporting_arguments || [], "support")}
        ${renderResearchReportArguments("反对理由与风险", report.counter_arguments || [], "counter")}
      </section>
      <section id="research-report-gaps" class="research-report-section research-report-anchor">
        <div class="research-report-section-head">
          <div><h2>数据缺口与下一动作</h2><p>动作只指向现有模块，不自动采集、不自动写结论。</p></div>
          <span class="result-meta">${fmt.int(gaps.length)} 项</span>
        </div>
        ${renderResearchReportGaps(gaps)}
      </section>
      <section id="research-report-operations" class="research-report-section research-report-anchor">
        <div class="research-report-section-head">
          <div><h2>进入前人工运营核查</h2><p>这些现实条件无法从 Amazon 前台证据可靠推断。</p></div>
        </div>
        <div class="research-report-due-grid">${(report.operational_due_diligence || []).map(renderResearchDueItem).join("")}</div>
      </section>
      ${renderResearchReportAssets(report.assets || {})}
      <details id="research-report-sources" class="research-report-method panel research-report-anchor">
        <summary>方法、来源与不可推断边界</summary>
        <div class="research-report-method-body">
          <p>${escapeHtml(guardrails.statement || "")}</p>
          <ul>${(guardrails.prohibited_inferences || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
          <div class="research-report-fingerprints">
            <div><span>方法版本</span><code>${escapeHtml(report.method_version || "—")}</code></div>
            <div><span>证据指纹</span><code>${escapeHtml(report.evidence_fingerprint || "—")}</code></div>
            <div><span>报告指纹</span><code>${escapeHtml(report.report_fingerprint || "—")}</code></div>
            <div><span>生成时间</span><code>${escapeHtml(report.generated_at || "—")}</code></div>
          </div>
        </div>
      </details>
    </div>`;

  document.getElementById("research-report-refresh").onclick = () => loadResearchDecisionReport(Number(project.id));
  document.getElementById("research-report-freeze").onclick = () => openResearchReportFreezeDialog({
    projectId: Number(project.id),
    report,
    mode: "manual",
    onSuccess: (result) => {
      const versionNo = result?.version?.version_no;
      notice(`报告版本 #${fmt.int(versionNo)} 已冻结`, "ok");
    },
  });
  document.getElementById("research-report-json").onclick = () => downloadResearchDecisionReport(Number(project.id), "json");
  document.getElementById("research-report-markdown").onclick = () => downloadResearchDecisionReport(Number(project.id), "markdown");
  content.querySelectorAll("[data-report-jump]").forEach((button) => {
    button.onclick = () => {
      document.getElementById(button.dataset.reportJump)?.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    };
  });
  document.getElementById("research-report-as-of").onchange = async (event) => {
    researchDecisionReportState.asOf = String(event.target.value || "");
    persistState(researchDecisionReportState);
    await loadResearchDecisionReport(Number(project.id));
  };
  content.querySelectorAll("[data-research-gap-action]").forEach((button) => {
    button.onclick = () => {
      const gap = gaps.find((item) => item.key === button.dataset.researchGapAction);
      if (gap) startResearchEvidenceAction(project, gap);
    };
  });
}

function renderResearchBaselineReview(comparison, projectId) {
  if (!comparison) return "";
  if (!comparison.has_baseline) {
    return `
      <section id="research-report-baseline" class="research-report-section research-report-anchor research-baseline-review">
        <div class="research-report-section-head">
          <div><h2>冻结后变化</h2><p>${escapeHtml(comparison.message || "当前没有可比较的冻结基线。")}</p></div>
          <a class="btn btn-sm" href="#/research-projects/${projectId}/report-versions">版本历史</a>
        </div>
      </section>`;
  }
  const baseline = comparison.baseline || {};
  const current = comparison.current || {};
  const diff = comparison.diff || {};
  const changes = Array.isArray(diff.changes) ? diff.changes : [];
  const gateChanges = changes.filter((row) => row.category === "决策门禁" && row.field === "passed");
  const gapChanges = changes.filter((row) => row.category === "数据缺口" && ["added", "removed"].includes(row.change_type));
  const highlights = [...gateChanges, ...gapChanges].slice(0, 4);
  return `
    <section id="research-report-baseline" class="research-report-section research-report-anchor research-baseline-review">
      <div class="research-report-section-head">
        <div>
          <h2>冻结后变化</h2>
          <p>${escapeHtml(comparison.baseline_label || "冻结基线")} #${fmt.int(baseline.version_no)} 与当前动态证据的只读比较。</p>
        </div>
        <a class="btn btn-sm" href="#/research-projects/${projectId}/report-live-compare/${Number(baseline.version_no)}">查看完整差异</a>
      </div>
      ${diff.warning ? `<div class="evidence-warning research-baseline-warning"><strong>口径变化</strong><span>${escapeHtml(diff.warning)}</span></div>` : ""}
      <div class="research-baseline-grid">
        <div><span>证据截至</span><b>${escapeHtml(researchReportDateTime(baseline.evidence_as_of))} → ${escapeHtml(researchReportDateTime(current.evidence_as_of))}</b></div>
        <div><span>门禁通过</span><b>${fmt.int(baseline.readiness_passed_count)} → ${fmt.int(current.readiness_passed_count)}</b></div>
        <div><span>结构化变化</span><b>${fmt.int(diff.summary?.material_change_count)} 项</b><small>${diff.method_compatible ? "同口径可解释" : "仅作字段对照"}</small></div>
        <div><span>基准冻结时间</span><b>${escapeHtml(researchReportDateTime(baseline.frozen_at))}</b><small>#${fmt.int(baseline.version_no)} · ${escapeHtml(baseline.method_version || "—")}</small></div>
      </div>
      ${highlights.length ? `<div class="research-baseline-highlights">${highlights.map((row) => `
        <div>
          <span class="badge ${row.change_type === "removed" || row.to === true ? "badge-good" : row.change_type === "added" ? "badge-warn" : "badge-dim"}">${row.change_type === "removed" ? "缺口已解决" : row.change_type === "added" ? "新增缺口" : "门禁变化"}</span>
          <b>${escapeHtml(row.item_label || row.label || row.item_key || "变化")}</b>
          <small>${escapeHtml(researchBaselineHighlightSummary(row))}</small>
        </div>`).join("")}</div>` : ""}
    </section>`;
}

function researchBaselineHighlightSummary(row) {
  if (row.category === "决策门禁" && row.field === "passed") {
    return `${row.from ? "已通过" : "未通过"} → ${row.to ? "已通过" : "未通过"}`;
  }
  if (row.category === "数据缺口" && row.change_type === "removed") {
    return "冻结时存在 → 当前已移出缺口清单";
  }
  if (row.category === "数据缺口" && row.change_type === "added") {
    return "冻结时不存在 → 当前新增为待补缺口";
  }
  return `${researchDiffValue(row.from)} → ${researchDiffValue(row.to)}`;
}

function researchReportDateTime(value) {
  if (value == null || value === "") return "—";
  return String(value).replace("T", " ").replace(/\.\d+(?=(?:Z|[+-]\d\d:\d\d)?$)/, "");
}

function researchTrendRoleLabel(row) {
  if (row.source === "keyword") return RESEARCH_KEYWORD_ROLE_LABELS[row.role] || row.role || "—";
  if (row.source === "product") return RESEARCH_PRODUCT_ROLE_LABELS[row.role] || row.role || "—";
  if (row.source === "niche") return NICHE_PROJECT_ROLE_LABELS[row.role] || row.role || "—";
  return row.role || "—";
}

function researchTrendQualityBadge(row) {
  const status = row.quality_status || "missing";
  const cls = ["qualified", "direct"].includes(status)
    ? "badge-good"
    : ["partial", "aggregate_only"].includes(status)
      ? "badge-warn"
      : "badge-bad";
  return `<span class="badge ${cls}">${escapeHtml(row.quality_label || "质量未评估")}</span>`;
}

function researchTrendFreshnessBadge(row) {
  const status = row.freshness_status || "missing";
  const cls = status === "current" ? "badge-good" : status === "stale" ? "badge-warn" : "badge-dim";
  return `<span class="badge ${cls}">${escapeHtml(row.freshness_label || "无时间点")}</span>`;
}

function renderResearchTrendQuality(timeline) {
  const rows = Array.isArray(timeline.sequences) ? timeline.sequences : [];
  if (!rows.length) return "";
  const body = rows.map((row) => {
    const warnings = Array.isArray(row.quality_warnings) ? row.quality_warnings : [];
    return `
      <tr>
        <td><b>${escapeHtml(row.name || "—")}</b><div class="cell-sub">${escapeHtml(row.source_label || row.source || "—")} · ${escapeHtml(researchTrendRoleLabel(row))}</div></td>
        <td class="num">${fmt.int(row.raw_points)}</td>
        <td class="num"><b>${fmt.int(row.points)}</b></td>
        <td>${fmt.int(row.span_days)} 天<div class="cell-sub">${fmt.text(row.first_snapshot_at)} → ${fmt.text(row.latest_snapshot_at)}</div></td>
        <td>${researchTrendQualityBadge(row)}${Number(row.excluded_points || 0) ? `<div class="cell-sub">排除 ${fmt.int(row.excluded_points)} 点</div>` : ""}${warnings.length ? `<div class="cell-sub text-warn">${escapeHtml(warnings[0])}</div>` : ""}</td>
        <td>${researchTrendFreshnessBadge(row)}<div class="cell-sub">${row.latest_age_days == null ? "—" : `最近约 ${fmt.int(row.latest_age_days)} 天前`}</div></td>
      </tr>`;
  }).join("");
  return `
    <section id="research-report-trend" class="research-report-section research-trend-quality research-report-anchor">
      <div class="research-report-section-head">
        <div><h2>趋势证据质量</h2><p>原始采集次数不直接通过门槛；关键词批次还需满足排名完整性与至少 24 小时独立窗口。</p></div>
        <span class="result-meta">${fmt.int(rows.length)} 条决策相关序列</span>
      </div>
      ${wrapTable(`<table class="research-trend-quality-table"><thead><tr><th>序列</th><th class="num">原始点</th><th class="num">合格点</th><th>有效跨度</th><th>质量</th><th>新鲜度</th></tr></thead><tbody>${body}</tbody></table>`)}
    </section>`;
}

function renderResearchReportGates(rows) {
  if (!rows.length) return `<div class="state">尚无门禁定义。</div>`;
  return rows.map((row) => `
    <div class="research-report-gate ${row.passed ? "is-passed" : "is-missing"}">
      <div class="research-report-gate-state">
        <span class="badge ${row.passed ? "badge-good" : RESEARCH_REPORT_SEVERITY[row.severity] || "badge-dim"}">${row.passed ? "已通过" : escapeHtml(row.severity_label || "待补")}</span>
        ${row.critical_for_final_decision ? `<span class="research-report-required">最终决策必需</span>` : `<span class="research-report-conditional">条件性证据</span>`}
      </div>
      <div>
        <b>${escapeHtml(row.label || "")}</b>
        <p>${escapeHtml(row.detail || "")}</p>
        ${row.passed ? "" : `<small>${escapeHtml(row.action || "")}</small>`}
      </div>
    </div>`).join("");
}

function renderResearchReportAxis(axis) {
  const [fallbackLabel, cls] = RESEARCH_REPORT_AXIS_STATUS[axis.status] || ["未评估", "badge-dim"];
  const facts = (axis.facts || []).map((fact) => `
    <div class="research-report-fact">
      <span>${escapeHtml(fact.label || "")}</span>
      <b>${escapeHtml(fact.display || "—")}</b>
      <small>${escapeHtml(RESEARCH_REPORT_SOURCE_LABELS[fact.source_type] || fact.source_type || "来源未标注")}</small>
    </div>`).join("");
  const caveats = (axis.caveats || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  return `
    <article class="research-report-axis research-report-axis-${escapeHtml(axis.status || "insufficient")}">
      <div class="research-report-axis-head">
        <h3>${escapeHtml(axis.label || axis.key || "")}</h3>
        <div><span class="badge ${cls}">${escapeHtml(axis.status_label || fallbackLabel)}</span><span class="badge badge-dim">置信 ${escapeHtml(axis.confidence_label || "低")}</span></div>
      </div>
      <p>${escapeHtml(axis.summary || "")}</p>
      <div class="research-report-facts">${facts || `<div class="research-report-fact"><span>证据</span><b>暂无</b></div>`}</div>
      ${caveats ? `<ul class="research-report-caveats">${caveats}</ul>` : ""}
    </article>`;
}

function renderResearchReportArguments(title, rows, kind) {
  const empty = kind === "support" ? "暂无可核验支持理由。" : "暂无已记录反方理由；这不代表没有风险。";
  return `
    <section class="research-report-argument research-report-argument-${kind}">
      <div class="research-report-section-head"><h2>${escapeHtml(title)}</h2><span class="result-meta">${fmt.int(rows.length)} 条</span></div>
      <div class="research-report-argument-list">
        ${rows.length ? rows.map((row) => `
          <div>
            <b>${escapeHtml(row.title || "")}</b>
            <p>${escapeHtml(row.detail || "")}</p>
            <small>${escapeHtml(row.source || "来源未标注")} · ${escapeHtml(row.strength_label || "")}</small>
          </div>`).join("") : `<div class="state research-empty">${escapeHtml(empty)}</div>`}
      </div>
    </section>`;
}

function renderResearchReportGaps(rows) {
  if (!rows.length) return `<div class="state">结构化门禁暂无缺口；仍需完成人工运营核查。</div>`;
  const body = rows.map((row) => `
    <tr>
      <td><span class="badge ${RESEARCH_REPORT_SEVERITY[row.severity] || "badge-dim"}">${escapeHtml(row.severity_label || row.severity || "—")}</span></td>
      <td><b>${escapeHtml(row.title || "")}</b><div class="cell-sub">${escapeHtml(row.reason || "")}</div></td>
      <td>${escapeHtml(row.next_action || "")}</td>
      <td>${row.route ? `<button class="btn btn-sm" type="button" data-research-gap-action="${escapeHtml(row.key)}">前往处理</button>` : "—"}</td>
    </tr>`).join("");
  return wrapTable(`<table class="research-report-gap-table"><thead><tr><th>优先级</th><th>缺口</th><th>下一动作</th><th>入口</th></tr></thead><tbody>${body}</tbody></table>`);
}

function renderResearchDueItem(item) {
  const cls = item.status === "verified" ? "badge-good" : item.status === "partial" ? "badge-warn" : "badge-dim";
  return `
    <div class="research-report-due-item">
      <div><b>${escapeHtml(item.label || "")}</b><span class="badge ${cls}">${escapeHtml(item.status_label || "未核验")}</span></div>
      <p>${escapeHtml(item.detail || "")}</p>
    </div>`;
}

function renderResearchReportAssets(assets) {
  const products = assets.products || [];
  const keywords = assets.keywords || [];
  const niches = assets.niches || [];
  const notes = assets.notes || [];
  return `
    <details class="research-report-assets panel">
      <summary>证据资产摘要 · ${fmt.int(products.length)} 商品 / ${fmt.int(keywords.length)} 关键词 / ${fmt.int(niches.length)} 利基 / ${fmt.int(notes.length)} 人工记录</summary>
      <div class="research-report-assets-body">
        <h3>关联商品</h3>
        ${renderResearchReportProductAssets(products)}
        <h3>关联关键词</h3>
        ${renderResearchReportKeywordAssets(keywords)}
        <h3>关联利基</h3>
        ${renderResearchReportNicheAssets(niches)}
      </div>
    </details>`;
}

function renderResearchReportProductAssets(rows) {
  if (!rows.length) return `<div class="state research-empty">暂无关联商品。</div>`;
  const body = rows.map((row) => {
    const hash = productDetailHash(row.asin);
    return `
    <tr${selectableEntryAttrs(hash, row.asin)}>
      <td>${amazonProductLink(row, displayTitle(row), 54)}<div class="cell-sub">${escapeHtml(row.asin || "—")} · ${escapeHtml(RESEARCH_PRODUCT_ROLE_LABELS[row.role] || row.role || "—")}</div></td>
      <td class="num">${fmt.money(row.price)}</td>
      <td class="num">${fmt.int(row.review_count)}</td>
      <td class="num">${fmt.int(row.monthly_bought)}</td>
      <td>${fmt.int(row.timepoint_count)} 点<div class="cell-sub">${fmt.text(row.first_snapshot_at)} → ${fmt.text(row.latest_snapshot_at)}</div></td>
      <td><span class="badge ${row.has_complete_unit_cost ? "badge-good" : row.has_any_cost ? "badge-warn" : "badge-dim"}">${row.has_complete_unit_cost ? "成本完整" : row.has_any_cost ? "部分成本" : "未录成本"}</span></td>
      <td><a class="btn btn-sm" href="#/metrics/${encodeURIComponent(row.asin)}" onclick="event.stopPropagation()">指标</a></td>
    </tr>`;
  }).join("");
  return wrapTable(`<table><thead><tr><th>商品</th><th class="num">价格</th><th class="num">评论</th><th class="num">近月购买</th><th>时间序列</th><th>财务输入</th><th>入口</th></tr></thead><tbody>${body}</tbody></table>`);
}

function renderResearchReportKeywordAssets(rows) {
  if (!rows.length) return `<div class="state research-empty">暂无关联关键词。</div>`;
  const body = rows.map((row) => {
    const raw = Number(row.raw_timepoint_count ?? row.timepoint_count ?? 0);
    const qualified = Number(row.qualified_timepoint_count ?? row.timepoint_count ?? 0);
    const warnings = Array.isArray(row.timepoint_quality_warnings) ? row.timepoint_quality_warnings : [];
    const qualityClass = row.timepoint_quality_status === "qualified"
      ? "badge-good"
      : row.timepoint_quality_status === "invalid"
        ? "badge-bad"
        : "badge-warn";
    return `
      <tr>
        <td>${escapeHtml(row.keyword || "—")}<div class="cell-sub">${escapeHtml(RESEARCH_KEYWORD_ROLE_LABELS[row.role] || row.role || "—")}</div></td>
        <td class="num">${fmt.int(row.latest_batch_product_count)}</td>
        <td><b>${fmt.int(qualified)} / ${fmt.int(raw)}</b><div class="cell-sub">合格 / 原始</div>${warnings.length ? `<div class="cell-sub text-warn">${escapeHtml(warnings[0])}</div>` : ""}</td>
        <td><span class="badge ${qualityClass}">${escapeHtml(row.timepoint_quality_label || "质量未评估")}</span><div class="cell-sub">${escapeHtml(row.timepoint_freshness_label || "—")}</div></td>
        <td>${fmt.text(row.qualified_latest_snapshot_at || row.latest_snapshot_at)}</td>
        <td>${keywordLibraryTrackingBadge(row.tracking_status || "none")}</td>
      </tr>`;
  }).join("");
  return wrapTable(`<table><thead><tr><th>关键词</th><th class="num">当前批次商品</th><th>趋势点</th><th>质量</th><th>最近合格点</th><th>追踪</th></tr></thead><tbody>${body}</tbody></table>`);
}

function renderResearchReportNicheAssets(rows) {
  if (!rows.length) return `<div class="state research-empty">暂无关联利基。</div>`;
  const evidenceLabels = { usable: "较完整", partial: "部分可用", insufficient: "证据不足", not_generated: "尚未生成" };
  const body = rows.map((row) => `
    <tr>
      <td><a class="link" href="#/market-niches/${Number(row.niche_id)}">${escapeHtml(row.name || "—")}</a><div class="cell-sub">${escapeHtml(NICHE_PROJECT_ROLE_LABELS[row.role] || row.role || "—")}</div></td>
      <td><span class="badge ${row.evidence_level === "usable" ? "badge-good" : row.evidence_level === "partial" ? "badge-warn" : "badge-dim"}">${escapeHtml(evidenceLabels[row.evidence_level] || row.evidence_level || "—")}</span></td>
      <td class="num">${fmt.int(row.observed_product_count)}</td>
      <td>${marketRatio(row.monthly_bought_coverage)}</td>
      <td>${marketRatio(row.ad_density)}</td>
      <td>${fmt.text(row.snapshot_at)}<div class="cell-sub">${escapeHtml(marketNicheRankSourceMeta(row))}</div></td>
    </tr>`).join("");
  return wrapTable(`<table><thead><tr><th>市场利基</th><th>证据</th><th class="num">去重 ASIN</th><th>需求覆盖</th><th>广告密度</th><th>快照时间</th></tr></thead><tbody>${body}</tbody></table>`);
}

async function downloadResearchDecisionReport(projectId, format) {
  const query = new URLSearchParams({ format });
  const asOf = researchDecisionReportCurrent?.evaluated_on || researchDecisionReportState.asOf;
  if (asOf) query.set("as_of", asOf);
  const buttons = ["research-report-json", "research-report-markdown"].map((id) => document.getElementById(id)).filter(Boolean);
  buttons.forEach((button) => { button.disabled = true; });
  try {
    const response = await fetch(`/api/research-projects/${encodeURIComponent(projectId)}/decision-report/export?${query.toString()}`);
    if (!response.ok) {
      let message = `导出失败（HTTP ${response.status}）`;
      try {
        const payload = await response.json();
        message = payload.message || message;
      } catch { /* 非 JSON 错误保留 HTTP 状态。 */ }
      throw new Error(message);
    }
    const disposition = response.headers.get("Content-Disposition") || "";
    const encodedMatch = disposition.match(/filename\*=UTF-8''([^;]+)/i);
    const plainMatch = disposition.match(/filename=([^;]+)/i);
    const filename = encodedMatch ? decodeURIComponent(encodedMatch[1]) : (plainMatch ? plainMatch[1].replace(/"/g, "") : `research_project_${projectId}.${format === "json" ? "json" : "md"}`);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
    notice(`决策报告已导出：${filename}`, "ok");
  } catch (err) {
    notice(err.message || "决策报告导出失败", "bad");
  } finally {
    buttons.forEach((button) => { button.disabled = false; });
  }
}

function researchReportDefaultDate(offsetDays = 0) {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000);
  local.setUTCDate(local.getUTCDate() + Number(offsetDays || 0));
  return local.toISOString().slice(0, 10);
}

function researchReportIdempotencyKey(prefix = "report") {
  if (globalThis.crypto?.randomUUID) return `${prefix}:${globalThis.crypto.randomUUID()}`;
  const random = Math.random().toString(36).slice(2, 12);
  return `${prefix}:${Date.now().toString(36)}:${random}`;
}

function closeResearchReportFreezeDialog() {
  researchReportFreezeDialogState?.modal?.remove();
  researchReportFreezeDialogState = null;
}

function renderResearchReportFreezePreview(state) {
  const report = state.report || {};
  const readiness = report.readiness || {};
  const preview = state.modal.querySelector("[data-report-freeze-preview]");
  if (!preview) return;
  preview.innerHTML = `
    <div><span>决策就绪度</span><b>${escapeHtml(readiness.label || "尚未评估")}</b></div>
    <div><span>门禁通过</span><b>${fmt.int(readiness.passed_count || 0)} / ${fmt.int(readiness.total_count || 0)}</b></div>
    <div><span>方法版本</span><code>${escapeHtml(report.method_version || "—")}</code></div>
    <div><span>报告指纹</span><code title="${escapeHtml(report.report_fingerprint || "")}">${escapeHtml(String(report.report_fingerprint || "—").slice(0, 16))}</code></div>`;
  const warning = state.modal.querySelector("[data-report-freeze-warning]");
  if (warning) {
    warning.textContent = readiness.blocking_count
      ? `仍有 ${fmt.int(readiness.blocking_count)} 项阻断；冻结只保存证据，不代表自动推荐进入。`
      : "当前没有报告阻断；最终批准或拒绝仍是人工决策。";
  }
}

function openResearchReportFreezeDialog({
  projectId,
  report,
  mode = "manual",
  targetStatus = null,
  decisionSummary = null,
  onSuccess = null,
}) {
  closeResearchReportFreezeDialog();
  const decisionMode = mode === "decision";
  const targetLabel = RESEARCH_STATUS_LABELS[targetStatus] || targetStatus || "";
  const confirmLabel = decisionMode
    ? `冻结当前报告并${targetStatus === "approved" ? "批准" : "拒绝"}`
    : "确认冻结版本";
  const modal = document.createElement("div");
  modal.id = "research-report-freeze-dialog";
  modal.className = "modal-backdrop";
  modal.innerHTML = `
    <section class="modal-panel research-report-freeze-modal" role="dialog" aria-modal="true" aria-label="${escapeHtml(confirmLabel)}">
      <div class="modal-head">
        <div>
          <h2>${decisionMode ? escapeHtml(`冻结证据并推进到“${targetLabel}”`) : "冻结报告版本"}</h2>
          <small>服务端会在同一事务内重新生成报告并核对指纹；证据变化时不会写入。</small>
        </div>
        <button class="btn btn-icon" type="button" data-report-freeze-close title="关闭">×</button>
      </div>
      <div class="research-report-freeze-body">
        <div class="research-report-freeze-form">
          <label>评估日期<input type="date" data-report-freeze-date value="${escapeHtml(report?.evaluated_on || researchReportDefaultDate())}" /></label>
          <label>版本说明（可选）<textarea rows="3" maxlength="2000" data-report-freeze-note placeholder="例如：补齐第二个采集时点后的阶段复核"></textarea></label>
        </div>
        <div class="research-report-freeze-summary" data-report-freeze-preview></div>
        ${decisionMode ? `
          <div class="research-report-freeze-decision">
            <span>本次人工结论 · ${escapeHtml(targetLabel)}</span>
            <p>${escapeHtml(decisionSummary || "")}</p>
          </div>` : ""}
        <p class="research-report-freeze-warning" data-report-freeze-warning></p>
        <div class="research-report-freeze-error" data-report-freeze-error aria-live="polite"></div>
      </div>
      <div class="modal-actions">
        <button class="btn" type="button" data-report-freeze-cancel>取消</button>
        <button class="btn btn-active" type="button" data-report-freeze-submit>${escapeHtml(confirmLabel)}</button>
      </div>
    </section>`;
  document.body.appendChild(modal);
  const state = {
    modal,
    projectId: Number(projectId),
    report,
    mode,
    targetStatus,
    decisionSummary,
    onSuccess,
    idempotencyKey: researchReportIdempotencyKey(decisionMode ? "decision" : "freeze"),
  };
  researchReportFreezeDialogState = state;
  renderResearchReportFreezePreview(state);

  const close = () => closeResearchReportFreezeDialog();
  modal.querySelector("[data-report-freeze-close]").onclick = close;
  modal.querySelector("[data-report-freeze-cancel]").onclick = close;
  modal.addEventListener("click", (event) => {
    if (event.target === modal) close();
  });
  modal.querySelector("[data-report-freeze-date]").onchange = async (event) => {
    const submit = modal.querySelector("[data-report-freeze-submit]");
    const error = modal.querySelector("[data-report-freeze-error]");
    submit.disabled = true;
    error.textContent = "";
    try {
      const evaluatedOn = String(event.target.value || "");
      state.report = await api(
        `/api/research-projects/${state.projectId}/decision-report?as_of=${encodeURIComponent(evaluatedOn)}`,
        { allowStale: true },
      );
      renderResearchReportFreezePreview(state);
    } catch (err) {
      error.textContent = err.message || "报告预览刷新失败";
    } finally {
      submit.disabled = false;
    }
  };
  modal.querySelector("[data-report-freeze-submit]").onclick = async () => {
    const submit = modal.querySelector("[data-report-freeze-submit]");
    const error = modal.querySelector("[data-report-freeze-error]");
    const evaluatedOn = modal.querySelector("[data-report-freeze-date]").value;
    const versionNote = modal.querySelector("[data-report-freeze-note]").value.trim() || null;
    submit.disabled = true;
    error.textContent = "";
    try {
      const body = {
        confirmed: true,
        evaluated_on: evaluatedOn,
        expected_report_fingerprint: state.report.report_fingerprint,
        idempotency_key: state.idempotencyKey,
        version_note: versionNote,
      };
      let result;
      if (state.mode === "decision") {
        result = await apiSend(`/api/research-projects/${state.projectId}/status`, "POST", {
          ...body,
          status: state.targetStatus,
          decision_summary: state.decisionSummary,
        });
      } else {
        result = await apiSend(`/api/research-projects/${state.projectId}/report-versions`, "POST", body);
      }
      closeResearchReportFreezeDialog();
      if (typeof state.onSuccess === "function") await state.onSuccess(result);
    } catch (err) {
      const current = err.data?.current_report_fingerprint;
      error.textContent = current
        ? `${err.message} 当前指纹：${String(current).slice(0, 16)}`
        : (err.message || "报告冻结失败");
      submit.disabled = false;
    }
  };
  modal.querySelector("[data-report-freeze-submit]").focus();
}

async function viewResearchReportVersions(projectId) {
  const id = Number(projectId);
  if (!Number.isInteger(id) || id < 1) return emptyState("研究项目 ID 不正确");
  loading();
  const [bundle, history] = await Promise.all([
    api(`/api/research-projects/${id}`),
    api(`/api/research-projects/${id}/report-versions?limit=100&offset=0`),
  ]);
  const project = bundle.project || {};
  const rows = history.rows || [];
  if (Number(researchReportVersionState.projectId) !== id) {
    researchReportVersionState.projectId = id;
    researchReportVersionState.toVersion = rows[0]?.version_no || null;
    researchReportVersionState.fromVersion = rows[1]?.version_no || rows[0]?.version_no || null;
    persistState(researchReportVersionState);
  }
  const available = new Set(rows.map((row) => Number(row.version_no)));
  if (!available.has(Number(researchReportVersionState.toVersion))) {
    researchReportVersionState.toVersion = rows[0]?.version_no || null;
  }
  if (!available.has(Number(researchReportVersionState.fromVersion))) {
    researchReportVersionState.fromVersion = rows[1]?.version_no || rows[0]?.version_no || null;
  }
  persistState(researchReportVersionState);
  const options = rows.map((row) => `
    <option value="${Number(row.version_no)}">#${fmt.int(row.version_no)} · ${escapeHtml(reportVersionKindLabel(row))} · ${escapeHtml(row.evaluated_on || "—")}</option>`).join("");
  content.innerHTML = `
    <div class="research-report-page">
      <div class="research-report-topline">
        <a class="back-link" href="#/research-projects/${id}/report">← 返回动态报告</a>
        <div class="research-report-actions">
          <a class="btn" href="#/research-projects/${id}">项目详情</a>
          <a class="btn btn-active" href="#/research-projects/${id}/report">查看当前报告</a>
          ${rows.length ? `<a class="btn" href="#/research-projects/${id}/report-live-compare/${Number(rows[0].version_no)}">最近冻结版与当前</a>` : ""}
        </div>
      </div>
      <header class="research-report-header">
        <div>
          <div class="research-report-kicker">研究项目 #${fmt.int(id)} · 不可变审计档案</div>
          <h2>${escapeHtml(project.name || "报告版本历史")}</h2>
          <p class="report-context-summary">历史版本只追加、不自动删除。动态报告继续读取当前证据，冻结版本用于复现当时的人工判断基础。</p>
        </div>
        <div class="research-report-stage">
          ${researchStatusBadge(project.status)}
          <span class="badge badge-dim">${fmt.int(history.total)} 个版本</span>
        </div>
      </header>
      ${history.total >= 100 ? `<div class="evidence-warning"><strong>版本较多</strong><span>当前项目已有 ${fmt.int(history.total)} 个版本；系统不会自动删除，请按真实复核节点冻结。</span></div>` : ""}
      <section class="research-report-version-compare-bar">
        <label>基准版本<select class="sel" id="research-version-from">${options}</select></label>
        <span class="research-version-arrow">→</span>
        <label>目标版本<select class="sel" id="research-version-to">${options}</select></label>
        <button class="btn" id="research-version-compare"${rows.length < 2 ? " disabled" : ""}>比较版本</button>
      </section>
      <section class="research-report-section">
        <div class="research-report-section-head">
          <div><h2>版本历史</h2><p>决策版本同时保存终态与人工结论；普通版本只记录阶段证据。</p></div>
          <span class="result-meta">共 ${fmt.int(history.total)} 个</span>
        </div>
        ${renderResearchReportVersionTable(rows)}
      </section>
    </div>`;
  const fromSelect = document.getElementById("research-version-from");
  const toSelect = document.getElementById("research-version-to");
  if (fromSelect) fromSelect.value = String(researchReportVersionState.fromVersion || "");
  if (toSelect) toSelect.value = String(researchReportVersionState.toVersion || "");
  document.getElementById("research-version-compare").onclick = () => {
    const from = Number(fromSelect.value);
    const to = Number(toSelect.value);
    if (!from || !to || from === to) return notice("请选择两个不同的报告版本", "bad");
    researchReportVersionState.fromVersion = from;
    researchReportVersionState.toVersion = to;
    persistState(researchReportVersionState);
    location.hash = `#/research-projects/${id}/report-compare/${from}/${to}`;
  };
}

function reportVersionKindLabel(row) {
  if (row.freeze_kind !== "decision") return "普通冻结";
  return row.decision_status === "approved" ? "批准依据" : "拒绝依据";
}

function renderResearchReportVersionTable(rows) {
  if (!rows.length) {
    return `<div class="state research-empty">尚无冻结版本。返回动态报告后点击“冻结版本”即可建立第一份档案。</div>`;
  }
  const body = rows.map((row) => `
    <tr>
      <td><a class="link" href="#/research-projects/${Number(row.project_id)}/report-versions/${Number(row.version_no)}">#${fmt.int(row.version_no)}</a></td>
      <td><span class="badge ${row.freeze_kind === "decision" ? "badge-good" : "badge-dim"}">${escapeHtml(reportVersionKindLabel(row))}</span><div class="cell-sub">${escapeHtml(RESEARCH_STATUS_LABELS[row.source_project_status] || row.source_project_status || "—")}</div></td>
      <td>${escapeHtml(row.readiness_label || "—")}<div class="cell-sub">${fmt.int(row.readiness_passed_count)} / ${fmt.int(row.readiness_total_count)} 门禁</div></td>
      <td>${escapeHtml(row.evaluated_on || "—")}<div class="cell-sub">冻结 ${fmt.text(row.frozen_at)}</div></td>
      <td><code title="${escapeHtml(row.report_fingerprint || "")}">${escapeHtml(String(row.report_fingerprint || "—").slice(0, 12))}</code><div class="cell-sub">${escapeHtml(row.method_version || "—")}</div></td>
      <td class="research-version-note">${escapeHtml(row.version_note || row.decision_summary || "—")}</td>
      <td><a class="btn btn-sm" href="#/research-projects/${Number(row.project_id)}/report-versions/${Number(row.version_no)}">查看</a></td>
    </tr>`).join("");
  return wrapTable(`<table class="research-version-table"><thead><tr><th>版本</th><th>类型 / 阶段</th><th>就绪度</th><th>评估 / 冻结</th><th>报告身份</th><th>说明 / 结论</th><th></th></tr></thead><tbody>${body}</tbody></table>`);
}

async function viewResearchReportVersion(projectId, versionNo) {
  const id = Number(projectId);
  const version = Number(versionNo);
  if (!Number.isInteger(id) || id < 1 || !Number.isInteger(version) || version < 1) {
    return emptyState("报告版本地址不正确");
  }
  loading();
  const detail = await api(`/api/research-projects/${id}/report-versions/${version}`);
  researchDecisionBaselineComparison = null;
  researchDecisionReportCurrent = detail.report;
  renderResearchDecisionReport(detail.report);
  const metadata = detail.version || {};
  const topline = content.querySelector(".research-report-topline");
  topline.querySelector(".back-link").href = `#/research-projects/${id}/report-versions`;
  topline.querySelector(".back-link").textContent = "← 返回版本历史";
  topline.querySelector(".research-report-actions").innerHTML = `
    <a class="btn" href="#/research-projects/${id}/report">当前动态报告</a>
    <a class="btn" href="#/research-projects/${id}/report-live-compare/${version}">与当前比较</a>
    <button class="btn" id="research-frozen-json">导出 JSON</button>
    <button class="btn" id="research-frozen-markdown">导出 Markdown</button>`;
  const banner = document.createElement("section");
  banner.className = "research-report-frozen-banner";
  banner.innerHTML = `
    <div>
      <span>不可变历史版本</span>
      <b>#${fmt.int(metadata.version_no)} · ${escapeHtml(reportVersionKindLabel(metadata))}</b>
      <small>冻结前阶段：${escapeHtml(RESEARCH_STATUS_LABELS[metadata.source_project_status] || metadata.source_project_status || "—")} · 冻结时间 ${fmt.text(metadata.frozen_at)}</small>
    </div>
    <div>
      <span>${metadata.decision_status ? "人工决策结论" : "版本说明"}</span>
      <p>${escapeHtml(metadata.decision_summary || metadata.version_note || "未填写")}</p>
    </div>`;
  topline.insertAdjacentElement("afterend", banner);
  content.querySelectorAll("[data-research-gap-action]").forEach((button) => button.remove());
  document.getElementById("research-frozen-json").onclick = () => downloadFrozenResearchReport(id, version, "json");
  document.getElementById("research-frozen-markdown").onclick = () => downloadFrozenResearchReport(id, version, "markdown");
}

async function downloadFrozenResearchReport(projectId, versionNo, format) {
  const buttons = ["research-frozen-json", "research-frozen-markdown"].map((id) => document.getElementById(id)).filter(Boolean);
  buttons.forEach((button) => { button.disabled = true; });
  try {
    const response = await fetch(
      `/api/research-projects/${encodeURIComponent(projectId)}/report-versions/${encodeURIComponent(versionNo)}/export?format=${encodeURIComponent(format)}`,
    );
    if (!response.ok) {
      let message = `导出失败（HTTP ${response.status}）`;
      try {
        const payload = await response.json();
        message = payload.message || message;
      } catch { /* 保留 HTTP 状态。 */ }
      throw new Error(message);
    }
    const disposition = response.headers.get("Content-Disposition") || "";
    const encodedMatch = disposition.match(/filename\*=UTF-8''([^;]+)/i);
    const plainMatch = disposition.match(/filename=([^;]+)/i);
    const filename = encodedMatch
      ? decodeURIComponent(encodedMatch[1])
      : (plainMatch ? plainMatch[1].replace(/"/g, "") : `research_project_${projectId}_v${versionNo}.${format === "json" ? "json" : "md"}`);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
    notice(`冻结报告已导出：${filename}`, "ok");
  } catch (err) {
    notice(err.message || "冻结报告导出失败", "bad");
  } finally {
    buttons.forEach((button) => { button.disabled = false; });
  }
}

async function viewResearchReportComparison(projectId, fromVersion, toVersion) {
  const id = Number(projectId);
  const from = Number(fromVersion);
  const to = Number(toVersion);
  if (![id, from, to].every((value) => Number.isInteger(value) && value > 0) || from === to) {
    return emptyState("请选择两个不同的有效报告版本");
  }
  loading();
  const [bundle, history, diff] = await Promise.all([
    api(`/api/research-projects/${id}`),
    api(`/api/research-projects/${id}/report-versions?limit=100&offset=0`),
    api(`/api/research-projects/${id}/report-version-diff?from_version=${from}&to_version=${to}`),
  ]);
  researchReportVersionState.projectId = id;
  researchReportVersionState.fromVersion = from;
  researchReportVersionState.toVersion = to;
  persistState(researchReportVersionState);
  const project = bundle.project || {};
  const options = (history.rows || []).map((row) => `
    <option value="${Number(row.version_no)}">#${fmt.int(row.version_no)} · ${escapeHtml(reportVersionKindLabel(row))} · ${escapeHtml(row.evaluated_on || "—")}</option>`).join("");
  researchDecisionBaselineComparison = null;
  content.innerHTML = `
    <div class="research-report-page">
      <div class="research-report-topline">
        <a class="back-link" href="#/research-projects/${id}/report-versions">← 返回版本历史</a>
        <div class="research-report-actions">
          <a class="btn" href="#/research-projects/${id}/report">当前动态报告</a>
        </div>
      </div>
      <header class="research-report-header">
        <div>
          <div class="research-report-kicker">研究项目 #${fmt.int(id)} · 结构化报告差异</div>
          <h2>${escapeHtml(project.name || "版本比较")}</h2>
          <p class="report-context-summary">差异按业务稳定键计算，不比较 Markdown 排版，也不会生成自动机会分。</p>
        </div>
        <div class="research-report-stage">
          <span class="badge ${diff.method_compatible ? "badge-good" : "badge-warn"}">${escapeHtml(diff.comparison_scope_label || "已比较")}</span>
          <span class="badge badge-dim">${fmt.int(diff.summary?.material_change_count)} 项实质变化</span>
        </div>
      </header>
      <section class="research-report-version-compare-bar">
        <label>基准版本<select class="sel" id="research-compare-from">${options}</select></label>
        <span class="research-version-arrow">→</span>
        <label>目标版本<select class="sel" id="research-compare-to">${options}</select></label>
        <button class="btn" id="research-compare-run">重新比较</button>
      </section>
      ${diff.warning ? `<div class="evidence-warning"><strong>口径变化</strong><span>${escapeHtml(diff.warning)}</span></div>` : ""}
      ${renderResearchReportDiffSections(diff)}
    </div>`;
  document.getElementById("research-compare-from").value = String(from);
  document.getElementById("research-compare-to").value = String(to);
  document.getElementById("research-compare-run").onclick = () => {
    const nextFrom = Number(document.getElementById("research-compare-from").value);
    const nextTo = Number(document.getElementById("research-compare-to").value);
    if (!nextFrom || !nextTo || nextFrom === nextTo) return notice("请选择两个不同的报告版本", "bad");
    location.hash = `#/research-projects/${id}/report-compare/${nextFrom}/${nextTo}`;
  };
}

async function viewResearchLiveReportComparison(projectId, fromVersion) {
  const id = Number(projectId);
  const from = Number(fromVersion);
  if (![id, from].every((value) => Number.isInteger(value) && value > 0)) {
    return emptyState("冻结基线地址不正确");
  }
  if (Number(researchDecisionReportState.projectId) !== id) {
    researchDecisionReportState.projectId = id;
    researchDecisionReportState.asOf = "";
    persistState(researchDecisionReportState);
  }
  loading();
  const query = new URLSearchParams({ from_version: String(from) });
  if (researchDecisionReportState.asOf) query.set("as_of", researchDecisionReportState.asOf);
  const [bundle, history, comparison] = await Promise.all([
    api(`/api/research-projects/${id}`),
    api(`/api/research-projects/${id}/report-versions?limit=100&offset=0`),
    api(`/api/research-projects/${id}/report-baseline-diff?${query.toString()}`),
  ]);
  if (!comparison.has_baseline || !comparison.diff) {
    return emptyState(comparison.message || "当前没有可比较的冻结基线");
  }
  researchDecisionBaselineComparison = comparison;
  researchDecisionReportCurrent = comparison.current_report;
  rememberAgentBusinessContext();
  researchReportVersionState.projectId = id;
  researchReportVersionState.fromVersion = from;
  persistState(researchReportVersionState);
  const project = bundle.project || {};
  const baseline = comparison.baseline || {};
  const current = comparison.current || {};
  const diff = comparison.diff || {};
  const options = (history.rows || []).map((row) => `
    <option value="${Number(row.version_no)}">#${fmt.int(row.version_no)} · ${escapeHtml(reportVersionKindLabel(row))} · ${escapeHtml(row.evaluated_on || "—")}</option>`).join("");
  content.innerHTML = `
    <div class="research-report-page">
      <div class="research-report-topline">
        <a class="back-link" href="#/research-projects/${id}/report">← 返回动态报告</a>
        <div class="research-report-actions">
          <a class="btn" href="#/research-projects/${id}/report-versions">版本历史</a>
          <a class="btn" href="#/research-projects/${id}/report-versions/${Number(baseline.version_no)}">查看基准版本</a>
        </div>
      </div>
      <header class="research-report-header">
        <div>
          <div class="research-report-kicker">研究项目 #${fmt.int(id)} · 冻结基线复核</div>
          <h2>${escapeHtml(project.name || "冻结后变化")}</h2>
          <p class="report-context-summary">以不可变报告为基准，对照当前证据；本页只读，不会冻结报告或改变项目状态。</p>
        </div>
        <div class="research-report-stage">
          <span class="badge ${diff.evidence_fingerprint_changed ? "badge-warn" : "badge-dim"}">${diff.evidence_fingerprint_changed ? "证据已变化" : "证据未变化"}</span>
          <span class="badge ${diff.method_compatible ? "badge-good" : "badge-warn"}">${escapeHtml(diff.comparison_scope_label || "已比较")}</span>
          <span class="badge badge-dim">${fmt.int(diff.summary?.material_change_count)} 项实质变化</span>
        </div>
      </header>
      <section class="research-report-version-compare-bar research-live-compare-bar">
        <label>冻结基线<select class="sel" id="research-live-compare-from">${options}</select></label>
        <span class="research-version-arrow">→</span>
        <div class="research-live-target"><span>比较目标</span><b>当前动态报告</b><small>${escapeHtml(current.evaluated_on || "—")} · 证据截至 ${escapeHtml(researchReportDateTime(current.evidence_as_of))}</small></div>
        <button class="btn" id="research-live-compare-run">重新比较</button>
      </section>
      <div class="research-baseline-grid research-live-summary">
        <div><span>门禁通过</span><b>${fmt.int(baseline.readiness_passed_count)} → ${fmt.int(current.readiness_passed_count)}</b></div>
        <div><span>阻断项</span><b>${fmt.int(baseline.readiness_blocking_count)} → ${fmt.int(current.readiness_blocking_count)}</b></div>
        <div><span>证据截至</span><b>${escapeHtml(researchReportDateTime(baseline.evidence_as_of))} → ${escapeHtml(researchReportDateTime(current.evidence_as_of))}</b></div>
        <div><span>方法版本</span><b>${escapeHtml(baseline.method_version || "—")} → ${escapeHtml(current.method_version || "—")}</b></div>
      </div>
      ${diff.warning ? `<div class="evidence-warning research-baseline-warning"><strong>口径变化</strong><span>${escapeHtml(diff.warning)}</span></div>` : ""}
      ${renderResearchReportDiffSections(diff)}
    </div>`;
  document.getElementById("research-live-compare-from").value = String(from);
  document.getElementById("research-live-compare-run").onclick = () => {
    const nextFrom = Number(document.getElementById("research-live-compare-from").value);
    if (!nextFrom) return notice("请选择有效的冻结基线", "bad");
    location.hash = `#/research-projects/${id}/report-live-compare/${nextFrom}`;
  };
}

function renderResearchReportDiffSections(diff) {
  const rows = Array.isArray(diff?.changes) ? diff.changes : [];
  if (!rows.length) return `<div class="state research-empty">两份报告没有结构化差异。</div>`;
  const grouped = new Map();
  rows.forEach((change) => {
    const category = change.category || "其他";
    if (!grouped.has(category)) grouped.set(category, []);
    grouped.get(category).push(change);
  });
  return Array.from(grouped.entries()).map(([category, changes]) => `
    <section class="research-report-diff-section">
      <div class="research-report-section-head"><div><h2>${escapeHtml(category)}</h2></div><span class="result-meta">${fmt.int(changes.length)} 项</span></div>
      ${renderResearchReportDiffRows(changes)}
    </section>`).join("");
}

function renderResearchReportDiffRows(rows) {
  const body = rows.map((row) => `
    <tr>
      <td><b>${escapeHtml(row.item_label || row.label || row.item_key || "变化")}</b>${row.field ? `<div class="cell-sub">${escapeHtml(row.label || row.field)}</div>` : ""}</td>
      <td><span class="badge ${row.change_type === "added" ? "badge-good" : row.change_type === "removed" ? "badge-bad" : "badge-dim"}">${row.change_type === "added" ? "新增" : row.change_type === "removed" ? "移除" : "变化"}</span></td>
      <td class="research-diff-value">${escapeHtml(researchDiffValue(row.from))}</td>
      <td class="research-diff-value">${escapeHtml(researchDiffValue(row.to))}${row.delta === undefined ? "" : `<div class="cell-sub">Δ ${escapeHtml(String(row.delta))}</div>`}</td>
    </tr>`).join("");
  return wrapTable(`<table class="research-diff-table"><thead><tr><th>项目</th><th>类型</th><th>原值</th><th>新值</th></tr></thead><tbody>${body}</tbody></table>`);
}

function researchDiffValue(value) {
  if (value === null || value === undefined || value === "") return "—";
  if (value === true) return "是";
  if (value === false) return "否";
  if (typeof value === "object") {
    const text = JSON.stringify(value);
    return text.length > 240 ? `${text.slice(0, 237)}...` : text;
  }
  const labels = {
    manual: "普通冻结",
    decision: "决策冻结",
    dynamic: "当前动态报告",
    no_material_change: "无实质变化",
    decision_changed: "人工决策变化",
    method_changed: "报告口径变化",
    time_passage: "评估时间推进",
    evidence_changed: "证据发生变化",
  };
  if (labels[value]) return labels[value];
  if (RESEARCH_STATUS_LABELS[value]) return RESEARCH_STATUS_LABELS[value];
  return String(value);
}

/* ---------- 视图：市场与利基 ---------- */
const NICHE_STATUS_LABELS = { draft: "草稿", active: "观察中", archived: "已归档" };
const NICHE_KEYWORD_ROLE_LABELS = { seed: "种子词", core: "核心词", long_tail: "长尾词", reference: "参考词" };
const NICHE_PRODUCT_ROLE_LABELS = { candidate: "候选商品", benchmark: "对标商品", competitor: "竞争商品", reference: "参考商品" };
const NICHE_PROJECT_ROLE_LABELS = { candidate: "候选利基", primary: "主利基", reference: "参考利基" };

async function viewMarketNiches() {
  content.innerHTML = `
    <details class="panel research-create" id="niche-create"${marketNicheState.createOpen ? " open" : ""}>
      <summary>新建市场利基</summary>
      <div class="research-form-grid">
        <label>利基名称<input id="niche-create-name" maxlength="255" placeholder="例如：动物造型慢回弹减压玩具" /></label>
        <label>类目范围<input id="niche-create-category" maxlength="512" placeholder="例如：Toys & Games / Novelty Toys" /></label>
        <label class="research-form-wide">包含与排除边界<textarea id="niche-create-definition" rows="3" maxlength="20000" placeholder="说明这个市场包含哪些用途、形态或人群，以及明确排除什么"></textarea></label>
      </div>
      <div class="actions research-form-actions"><button class="btn" id="niche-create-submit">创建利基</button></div>
    </details>
    <div class="panel niche-list-panel">
      <div class="table-toolbar">
        <h2 style="margin:0">市场与利基</h2>
        <span class="result-meta">多个关键词组成一个可复核市场</span>
      </div>
      <div id="niche-summary" class="research-summary-grid niche-summary-grid"></div>
      <div class="filters">
        <input id="niche-filter-keyword" placeholder="名称、定义或类目范围" />
        <input id="niche-filter-marketplace" placeholder="站点" style="width:82px" />
        <select id="niche-filter-status" class="sel">${marketNicheStatusOptions("all", true)}</select>
        <button class="btn" id="niche-filter-apply">筛选</button>
        <button class="btn" id="niche-filter-reset">重置</button>
      </div>
      <div id="niche-meta" class="result-meta"></div>
      <div id="niche-table"></div>
      <div id="niche-pager"></div>
    </div>`;

  document.getElementById("niche-create-name").value = marketNicheState.draftName;
  document.getElementById("niche-create-category").value = marketNicheState.draftCategoryScope;
  document.getElementById("niche-create-definition").value = marketNicheState.draftDefinition;
  document.getElementById("niche-filter-keyword").value = marketNicheState.keyword;
  document.getElementById("niche-filter-marketplace").value = marketNicheState.marketplace;
  document.getElementById("niche-filter-status").value = marketNicheState.status;
  document.getElementById("niche-create").ontoggle = (event) => {
    marketNicheState.createOpen = event.currentTarget.open;
    saveMarketNicheListState();
  };
  document.getElementById("niche-create-submit").onclick = createMarketNiche;
  document.getElementById("niche-filter-apply").onclick = () => loadMarketNiches(true);
  document.getElementById("niche-filter-reset").onclick = () => {
    marketNicheState.keyword = "";
    marketNicheState.marketplace = "US";
    marketNicheState.status = "all";
    marketNicheState.offset = 0;
    document.getElementById("niche-filter-keyword").value = "";
    document.getElementById("niche-filter-marketplace").value = "US";
    document.getElementById("niche-filter-status").value = "all";
    persistState(marketNicheState);
    loadMarketNiches();
  };
  bindStateInputs([
    "niche-create-name", "niche-create-category", "niche-create-definition",
    "niche-filter-keyword", "niche-filter-marketplace", "niche-filter-status",
  ], saveMarketNicheListState);
  content.querySelectorAll(".filters input, .filters select").forEach((input) => {
    input.addEventListener("keydown", (event) => { if (event.key === "Enter") loadMarketNiches(true); });
  });
  await loadMarketNiches();
}

function saveMarketNicheListState() {
  const value = (id, fallback = "") => document.getElementById(id)?.value ?? fallback;
  marketNicheState.draftName = value("niche-create-name", marketNicheState.draftName).trim();
  marketNicheState.draftCategoryScope = value("niche-create-category", marketNicheState.draftCategoryScope).trim();
  marketNicheState.draftDefinition = value("niche-create-definition", marketNicheState.draftDefinition);
  marketNicheState.keyword = value("niche-filter-keyword", marketNicheState.keyword).trim();
  marketNicheState.marketplace = (value("niche-filter-marketplace", marketNicheState.marketplace) || "US").trim().toUpperCase();
  marketNicheState.status = value("niche-filter-status", marketNicheState.status) || "all";
  persistState(marketNicheState);
}

async function createMarketNiche() {
  saveMarketNicheListState();
  if (!marketNicheState.draftName) return notice("请填写利基名称", "bad");
  const button = document.getElementById("niche-create-submit");
  button.disabled = true;
  try {
    const data = await apiSend("/api/market-niches", "POST", {
      name: marketNicheState.draftName,
      marketplace: marketNicheState.marketplace || "US",
      definition: marketNicheState.draftDefinition || null,
      category_scope: marketNicheState.draftCategoryScope || null,
    });
    const nicheId = data?.niche?.id;
    marketNicheState.draftName = "";
    marketNicheState.draftDefinition = "";
    marketNicheState.draftCategoryScope = "";
    persistState(marketNicheState);
    notice("市场利基已创建", "ok");
    if (nicheId) location.hash = `#/market-niches/${encodeURIComponent(nicheId)}`;
    else await viewMarketNiches();
  } catch (err) {
    notice(err.message, "bad");
  } finally {
    if (button) button.disabled = false;
  }
}

async function loadMarketNiches(resetPage = false) {
  if (resetPage) {
    marketNicheState.offset = 0;
    saveMarketNicheListState();
  }
  persistState(marketNicheState);
  const table = document.getElementById("niche-table");
  const meta = document.getElementById("niche-meta");
  const pager = document.getElementById("niche-pager");
  if (!table) return;
  table.innerHTML = `<div class="state"><div class="spinner"></div>加载市场利基…</div>`;
  meta.textContent = "";
  pager.innerHTML = "";
  const query = new URLSearchParams({
    limit: String(marketNicheState.limit),
    offset: String(marketNicheState.offset),
    marketplace: marketNicheState.marketplace || "US",
    sort_by: marketNicheState.sortBy,
    sort_dir: marketNicheState.sortDir,
  });
  if (marketNicheState.keyword) query.set("keyword", marketNicheState.keyword);
  if (marketNicheState.status !== "all") query.set("status", marketNicheState.status);
  try {
    const page = normalizePage(await api(`/api/market-niches?${query.toString()}`), marketNicheState.limit);
    syncRemoteSortState(marketNicheState, page);
    marketNicheRows.clear();
    (page.rows || []).forEach((row) => marketNicheRows.set(Number(row.id), row));
    renderMarketNicheSummary(page.status_counts || {});
    meta.textContent = `${pageSummary(page, "市场利基")} · 证据等级只表示数据覆盖，不代表值得进入`;
    if (!page.rows.length) {
      table.innerHTML = `<div class="state">暂无符合条件的市场利基。</div>`;
    } else {
      renderSortableTable(table, [
        { key: "name", label: "利基", render: (row) => `<b>${escapeHtml(row.name)}</b><div class="cell-sub">${escapeHtml(truncate(row.definition || "尚未定义包含与排除边界", 60))}</div>`, sortVal: (row) => row.name },
        { key: "status", label: "状态", render: (row) => marketNicheStatusBadge(row.status), sortVal: (row) => row.status },
        { key: "keyword_count", label: "成员关键词", align: "num", numeric: true, render: (row) => fmt.int(row.keyword_count), sortVal: (row) => row.keyword_count },
        { key: "observed_product_count", label: "去重 ASIN", align: "num", numeric: true, render: (row) => row.latest_snapshot_id ? fmt.int(row.observed_product_count) : "—", sortVal: (row) => row.observed_product_count },
        { key: "serp_coverage", label: "SERP 覆盖", align: "num", numeric: true, render: (row) => row.latest_snapshot_id ? marketRatio(row.serp_coverage) : "—", sortVal: (row) => row.serp_coverage },
        { key: "cross_keyword_overlap", label: "跨词重合", align: "num", numeric: true, render: (row) => marketRatio(row.cross_keyword_overlap), sortVal: (row) => row.cross_keyword_overlap },
        { key: "evidence_level", label: "证据状态", render: (row) => marketNicheEvidenceBadge(row), sortVal: (row) => row.evidence_level },
        { key: "project_count", label: "研究项目", align: "num", numeric: true, render: (row) => fmt.int(row.project_count), sortVal: (row) => row.project_count },
        { key: "latest_snapshot_at", label: "最近快照", render: (row) => fmt.text(row.latest_snapshot_at), sortVal: (row) => row.latest_snapshot_at || "" },
        { key: "actions", label: "操作", sortable: false, csv: false, render: (row) => `<div class="actions">
          <button class="btn btn-sm" onclick="event.stopPropagation();window.marketNicheResearch(${Number(row.id)})">入项目</button>
          <button class="btn btn-sm" onclick="event.stopPropagation();window.marketNicheOpen(${Number(row.id)})">打开</button>
        </div>` },
      ], page.rows, {
        remoteSort: remoteSortOptions(marketNicheState, loadMarketNiches),
        rowHash: (row) => `#/market-niches/${encodeURIComponent(row.id)}`,
        exportName: "市场利基",
      });
    }
    pager.innerHTML = renderPager("market-niche-pager", page, [10, 25, 50, 100]);
    bindPager("market-niche-pager", marketNicheState, page, loadMarketNiches);
  } catch (err) {
    table.innerHTML = `<div class="state error">⚠ ${escapeHtml(err.message)}</div>`;
  }
}

function renderMarketNicheSummary(counts) {
  const box = document.getElementById("niche-summary");
  if (!box) return;
  const total = Object.values(counts).reduce((sum, value) => sum + Number(value || 0), 0);
  box.innerHTML = `
    <div><span>全部利基</span><b>${fmt.int(total)}</b></div>
    <div><span>观察中</span><b>${fmt.int(counts.active || 0)}</b></div>
    <div><span>草稿</span><b>${fmt.int(counts.draft || 0)}</b></div>
    <div><span>已归档</span><b>${fmt.int(counts.archived || 0)}</b></div>`;
}

function marketNicheStatusOptions(selected = "all", includeAll = false) {
  const rows = includeAll ? [["all", "全部状态"]] : [];
  Object.entries(NICHE_STATUS_LABELS).forEach((entry) => rows.push(entry));
  return rows.map(([value, label]) => `<option value="${escapeHtml(value)}"${value === selected ? " selected" : ""}>${escapeHtml(label)}</option>`).join("");
}

function marketNicheStatusBadge(status) {
  const cls = status === "active" ? "badge-good" : status === "archived" ? "badge-dim" : "badge-warn";
  return `<span class="badge ${cls}">${escapeHtml(NICHE_STATUS_LABELS[status] || status || "—")}</span>`;
}

function marketNicheEvidenceBadge(row) {
  const level = row?.evidence_level || "not_generated";
  const cls = level === "usable" ? "badge-good" : level === "partial" ? "badge-warn" : level === "insufficient" ? "badge-bad" : "badge-dim";
  return `<span class="badge ${cls}" title="只表示样本与字段覆盖，不是市场机会评分">${escapeHtml(row?.evidence_level_label || "尚未生成")}</span>`;
}

function marketRatio(value, digits = 0) {
  return value == null || value === "" ? "—" : `${(Number(value) * 100).toFixed(digits)}%`;
}

window.marketNicheOpen = (nicheId) => { location.hash = `#/market-niches/${encodeURIComponent(nicheId)}`; };
window.marketNicheResearch = (nicheId) => {
  const row = marketNicheRows.get(Number(nicheId)) || {};
  openResearchAssociationDialog({
    assetType: "niche",
    items: [{ key: Number(nicheId), label: row.name || `利基 #${nicheId}`, marketplace: row.marketplace || marketNicheState.marketplace || "US" }],
    marketplace: row.marketplace || marketNicheState.marketplace || "US",
  });
};

async function viewMarketNicheDetail(nicheId) {
  const id = Number(nicheId);
  if (!Number.isInteger(id) || id < 1) return emptyState("市场利基 ID 不正确");
  const isNew = Number(marketNicheDetailState.nicheId) !== id;
  if (isNew) {
    Object.assign(marketNicheDetailState, {
      nicheId: id,
      editName: "",
      editDefinition: "",
      editCategoryScope: "",
      editStatus: "draft",
      keywords: "",
      keywordRole: "core",
      asins: "",
      productRole: "benchmark",
      projectId: "",
      projectRole: "candidate",
    });
    persistState(marketNicheDetailState);
  }
  loading();
  try {
    const detail = await api(`/api/market-niches/${encodeURIComponent(id)}`);
    const projectQuery = new URLSearchParams({
      limit: "200",
      offset: "0",
      marketplace: detail.niche?.marketplace || "US",
      sort_by: "updated_at",
      sort_dir: "desc",
    });
    const projectPayload = await api(`/api/research-projects?${projectQuery.toString()}`);
    marketNicheCurrent = detail;
    marketNicheAvailableProjects = normalizePage(projectPayload, 200).rows || [];
    const context = readResearchEvidenceContext();
    if (
      context
      && context.target === "niche"
      && context.marketplace === String(detail.niche?.marketplace || "US").toUpperCase()
      && marketNicheAvailableProjects.some(
        (project) => Number(project.id) === context.projectId && !["approved", "rejected"].includes(project.status)
      )
    ) {
      marketNicheDetailState.projectId = String(context.projectId);
      if (NICHE_PROJECT_ROLE_LABELS[context.defaultRole]) {
        marketNicheDetailState.projectRole = context.defaultRole;
      }
      persistState(marketNicheDetailState);
    }
    if (isNew) {
      marketNicheDetailState.editName = detail.niche?.name || "";
      marketNicheDetailState.editDefinition = detail.niche?.definition || "";
      marketNicheDetailState.editCategoryScope = detail.niche?.category_scope || "";
      marketNicheDetailState.editStatus = detail.niche?.status || "draft";
      persistState(marketNicheDetailState);
    }
    renderMarketNicheDetail();
  } catch (err) {
    errorState(err);
  }
}

function renderMarketNicheDetail() {
  const data = marketNicheCurrent || {};
  const niche = data.niche || {};
  const keywords = data.keywords || [];
  const products = data.products || [];
  const projects = data.projects || [];
  const snapshots = data.snapshots || [];
  const latest = snapshots[0] || null;
  content.innerHTML = `
    <a class="back-link" href="#/market-niches">← 返回市场与利基</a>
    <div class="research-detail-head">
      <div><h2>${escapeHtml(niche.name)}</h2><div class="meta">利基 #${fmt.int(niche.id)} · ${escapeHtml(niche.marketplace || "US")} · 更新 ${fmt.text(niche.updated_at)}</div></div>
      <div class="research-detail-actions">
        ${marketNicheStatusBadge(niche.status)}
        <button class="btn" id="niche-add-to-research" type="button">加入研究项目</button>
      </div>
    </div>
    <div class="research-summary-grid research-detail-summary niche-detail-summary">
      <div><span>成员关键词</span><b>${fmt.int(niche.keyword_count)}</b><small>有排名 ${fmt.int(latest?.keyword_with_rank_count || 0)}</small></div>
      <div><span>去重观察 ASIN</span><b>${latest ? fmt.int(latest.observed_product_count) : "—"}</b><small>人工锚点 ${fmt.int(niche.manual_product_count)}</small></div>
      <div><span>SERP 覆盖</span><b>${latest ? marketRatio(latest.serp_coverage) : "—"}</b><small>成员关键词字段证据</small></div>
      <div><span>证据状态</span><b class="niche-evidence-text">${escapeHtml(latest?.evidence_level_label || "尚未生成")}</b><small>不是市场机会评分</small></div>
    </div>
    <div class="panel">
      <div class="table-toolbar"><h2 style="margin:0">利基定义</h2><button class="btn btn-sm" id="niche-save">保存</button></div>
      <div class="research-form-grid">
        <label>利基名称<input id="niche-edit-name" maxlength="255" value="${escapeHtml(marketNicheDetailState.editName)}" /></label>
        <label>状态<select id="niche-edit-status" class="sel">${marketNicheStatusOptions(marketNicheDetailState.editStatus)}</select></label>
        <label class="research-form-wide">类目范围<input id="niche-edit-category" maxlength="512" value="${escapeHtml(marketNicheDetailState.editCategoryScope)}" /></label>
        <label class="research-form-wide">包含与排除边界<textarea id="niche-edit-definition" rows="3" maxlength="20000">${escapeHtml(marketNicheDetailState.editDefinition)}</textarea></label>
      </div>
    </div>
    <div class="panel niche-evidence-panel">
      <div class="table-toolbar">
        <div><h2 style="margin:0">市场证据快照</h2><div class="result-meta">只聚合现有数据，不访问 Amazon；人工锚点商品不进入市场样本</div></div>
        <div class="actions">
          ${latest ? `<a class="btn" href="#/competitive-graph/${Number(niche.id)}">打开竞品图谱</a>` : ""}
          <button class="btn" id="niche-generate-snapshot">生成证据快照</button>
        </div>
      </div>
      ${renderMarketNicheSnapshot(latest)}
    </div>
    <div class="panel">
      <div class="table-toolbar"><h2 style="margin:0">成员关键词</h2><span class="result-meta">市场样本由这些关键词的最新排名证据合并</span></div>
      <div class="research-inline-form">
        <textarea id="niche-add-keywords" rows="2" placeholder="输入已入库关键词，逗号或换行分隔">${escapeHtml(marketNicheDetailState.keywords)}</textarea>
        <select id="niche-keyword-role" class="sel">${nicheRoleOptions(NICHE_KEYWORD_ROLE_LABELS, marketNicheDetailState.keywordRole)}</select>
        <button class="btn" id="niche-add-keywords-btn">关联关键词</button>
      </div>
      ${renderMarketNicheKeywords(keywords)}
    </div>
    <div class="panel">
      <div class="table-toolbar"><h2 style="margin:0">人工锚点商品</h2><span class="result-meta">仅用于候选、对标和竞争分析，不改变市场统计</span></div>
      <div class="research-inline-form">
        <textarea id="niche-add-asins" rows="2" placeholder="输入已入库 ASIN，空格、逗号或换行分隔">${escapeHtml(marketNicheDetailState.asins)}</textarea>
        <select id="niche-product-role" class="sel">${nicheRoleOptions(NICHE_PRODUCT_ROLE_LABELS, marketNicheDetailState.productRole)}</select>
        <button class="btn" id="niche-add-products">关联商品</button>
      </div>
      ${renderMarketNicheProducts(products)}
    </div>
    <div class="panel">
      <div class="table-toolbar"><h2 style="margin:0">关联研究项目</h2><span class="result-meta">一个项目可以比较多个利基</span></div>
      <div class="research-inline-form niche-project-form">
        <select id="niche-project-id" class="sel">${marketNicheProjectOptions(marketNicheDetailState.projectId)}</select>
        <select id="niche-project-role" class="sel">${nicheRoleOptions(NICHE_PROJECT_ROLE_LABELS, marketNicheDetailState.projectRole)}</select>
        <button class="btn" id="niche-add-project"${marketNicheAvailableProjects.length ? "" : " disabled"}>关联项目</button>
      </div>
      ${marketNicheAvailableProjects.length ? "" : `<div class="result-meta niche-project-empty">当前站点尚无研究项目，可先在“研究项目”中创建。</div>`}
      ${renderMarketNicheProjects(projects)}
    </div>
    <div class="panel">
      <div class="table-toolbar"><h2 style="margin:0">快照历史</h2><span class="result-meta">相同来源证据不会重复生成</span></div>
      ${renderMarketNicheSnapshotHistory(snapshots)}
    </div>`;

  document.getElementById("niche-save").onclick = () => saveMarketNiche(niche.id);
  document.getElementById("niche-add-to-research").onclick = () => openResearchAssociationDialog({
    assetType: "niche",
    items: [{ key: Number(niche.id), label: niche.name, marketplace: niche.marketplace || "US" }],
    marketplace: niche.marketplace || "US",
  });
  document.getElementById("niche-generate-snapshot").onclick = () => generateMarketNicheSnapshot(niche.id);
  document.getElementById("niche-add-keywords-btn").onclick = () => addMarketNicheKeywords(niche.id);
  document.getElementById("niche-add-products").onclick = () => addMarketNicheProducts(niche.id);
  document.getElementById("niche-add-project").onclick = () => addMarketNicheProject(niche.id);
  bindStateInputs([
    "niche-edit-name", "niche-edit-status", "niche-edit-category", "niche-edit-definition",
    "niche-add-keywords", "niche-keyword-role", "niche-add-asins", "niche-product-role",
    "niche-project-id", "niche-project-role",
  ], saveMarketNicheDetailDraft);
}

function nicheRoleOptions(labels, selected) {
  return Object.entries(labels).map(([value, label]) => `<option value="${escapeHtml(value)}"${value === selected ? " selected" : ""}>${escapeHtml(label)}</option>`).join("");
}

function marketNicheProjectOptions(selected) {
  const options = [`<option value="">${marketNicheAvailableProjects.length ? "选择研究项目" : "暂无研究项目"}</option>`];
  marketNicheAvailableProjects.forEach((project) => {
    const value = String(project.id);
    const frozen = ["approved", "rejected"].includes(project.status);
    options.push(`<option value="${value}"${!frozen && value === String(selected || "") ? " selected" : ""}${frozen ? " disabled" : ""}>#${value} ${escapeHtml(project.name)}${frozen ? "（证据已冻结）" : ""}</option>`);
  });
  return options.join("");
}

function saveMarketNicheDetailDraft() {
  const value = (id, fallback = "") => document.getElementById(id)?.value ?? fallback;
  marketNicheDetailState.editName = value("niche-edit-name", marketNicheDetailState.editName);
  marketNicheDetailState.editStatus = value("niche-edit-status", marketNicheDetailState.editStatus);
  marketNicheDetailState.editCategoryScope = value("niche-edit-category", marketNicheDetailState.editCategoryScope);
  marketNicheDetailState.editDefinition = value("niche-edit-definition", marketNicheDetailState.editDefinition);
  marketNicheDetailState.keywords = value("niche-add-keywords", marketNicheDetailState.keywords);
  marketNicheDetailState.keywordRole = value("niche-keyword-role", marketNicheDetailState.keywordRole);
  marketNicheDetailState.asins = value("niche-add-asins", marketNicheDetailState.asins);
  marketNicheDetailState.productRole = value("niche-product-role", marketNicheDetailState.productRole);
  marketNicheDetailState.projectId = value("niche-project-id", marketNicheDetailState.projectId);
  marketNicheDetailState.projectRole = value("niche-project-role", marketNicheDetailState.projectRole);
  persistState(marketNicheDetailState);
}

async function saveMarketNiche(nicheId) {
  saveMarketNicheDetailDraft();
  if (!marketNicheDetailState.editName.trim()) return notice("请填写利基名称", "bad");
  const button = document.getElementById("niche-save");
  button.disabled = true;
  try {
    await apiSend(`/api/market-niches/${nicheId}`, "PATCH", {
      name: marketNicheDetailState.editName.trim(),
      status: marketNicheDetailState.editStatus,
      definition: marketNicheDetailState.editDefinition,
      category_scope: marketNicheDetailState.editCategoryScope,
    });
    notice("利基定义已保存", "ok");
    await viewMarketNicheDetail(nicheId);
  } catch (err) {
    notice(err.message, "bad");
  } finally {
    if (button) button.disabled = false;
  }
}

async function addMarketNicheKeywords(nicheId) {
  saveMarketNicheDetailDraft();
  const keywords = marketNicheDetailState.keywords.split(/[\r\n,，;；]+/).map((item) => item.trim()).filter(Boolean);
  if (!keywords.length) return notice("请输入已入库关键词", "bad");
  try {
    const result = await apiSend(`/api/market-niches/${nicheId}/keywords`, "POST", {
      keywords,
      role: marketNicheDetailState.keywordRole,
    });
    marketNicheDetailState.keywords = "";
    persistState(marketNicheDetailState);
    notice(result.missing?.length ? `已关联可用关键词；未找到：${result.missing.join("、")}` : "关键词已关联", result.missing?.length ? "bad" : "ok");
    await viewMarketNicheDetail(nicheId);
  } catch (err) { notice(err.message, "bad"); }
}

async function addMarketNicheProducts(nicheId) {
  saveMarketNicheDetailDraft();
  const asins = marketNicheDetailState.asins.split(/[\s,，;；]+/).map((item) => item.trim()).filter(Boolean);
  if (!asins.length) return notice("请输入已入库 ASIN", "bad");
  try {
    const result = await apiSend(`/api/market-niches/${nicheId}/products`, "POST", {
      asins,
      role: marketNicheDetailState.productRole,
    });
    marketNicheDetailState.asins = "";
    persistState(marketNicheDetailState);
    notice(result.missing?.length ? `已关联可用商品；未找到：${result.missing.join("、")}` : "人工锚点商品已关联", result.missing?.length ? "bad" : "ok");
    await viewMarketNicheDetail(nicheId);
  } catch (err) { notice(err.message, "bad"); }
}

async function addMarketNicheProject(nicheId) {
  saveMarketNicheDetailDraft();
  const projectId = Number(marketNicheDetailState.projectId);
  if (!Number.isInteger(projectId) || projectId < 1) return notice("请选择研究项目", "bad");
  try {
    const result = await apiSend(`/api/market-niches/${nicheId}/projects`, "POST", {
      project_ids: [projectId],
      role: marketNicheDetailState.projectRole,
    });
    marketNicheDetailState.projectId = "";
    persistState(marketNicheDetailState);
    notice(result.missing?.length ? "研究项目不存在或站点不一致" : "研究项目已关联", result.missing?.length ? "bad" : "ok");
    if (!result.missing?.length && finishResearchEvidenceAssociation(projectId, "niche")) return;
    await viewMarketNicheDetail(nicheId);
  } catch (err) { notice(err.message, "bad"); }
}

async function generateMarketNicheSnapshot(nicheId) {
  if (!confirm("生成一次市场证据快照？\n\n只读取当前 MySQL 中已有的关键词排名、SERP 和商品快照，不会访问 Amazon；证据未变化时会复用原快照。")) return;
  const button = document.getElementById("niche-generate-snapshot");
  button.disabled = true;
  try {
    const result = await apiSend(`/api/market-niches/${nicheId}/snapshots`, "POST");
    notice(result.created ? "市场证据快照已生成" : "来源证据未变化，已复用现有快照", "ok");
    await viewMarketNicheDetail(nicheId);
  } catch (err) {
    notice(err.message, "bad");
  } finally {
    if (button) button.disabled = false;
  }
}

function renderMarketNicheSnapshot(snapshot) {
  if (!snapshot) return `<div class="state niche-snapshot-empty">尚未生成证据快照。先关联已入库关键词，再由用户显式生成。</div>`;
  const priceRange = nicheRange(snapshot.price_p25, snapshot.price_median, snapshot.price_p75, fmt.money);
  const reviewRange = nicheRange(
    snapshot.review_p25,
    snapshot.review_median,
    snapshot.review_p75,
    (value) => value == null ? "—" : Math.round(Number(value)).toLocaleString(),
  );
  const warningHtml = snapshot.warnings?.length
    ? `<div class="niche-warning-list"><b>证据限制</b>${snapshot.warnings.map((warning) => `<div>${escapeHtml(warning)}</div>`).join("")}</div>`
    : `<div class="niche-evidence-note">当前核心覆盖达到可用门槛，仍需结合时间序列、利润和人工判断。</div>`;
  return `
    <div class="niche-snapshot-meta">
      ${marketNicheEvidenceBadge(snapshot)}
      <span>生成 ${fmt.text(snapshot.snapshot_at)}</span>
      <span>${escapeHtml(marketNicheRankSourceMeta(snapshot))}</span>
      <span>全部证据最新 ${fmt.text(snapshot.source_latest_at)}</span>
      <span>${fmt.int(snapshot.keyword_count)} 个成员关键词 · ${fmt.int(snapshot.page_count)} 页 SERP 证据</span>
    </div>
    <div class="niche-metrics">
      ${nicheMetric("去重观察 ASIN", fmt.int(snapshot.observed_product_count), `商品快照覆盖 ${marketRatio(snapshot.product_snapshot_coverage)}`)}
      ${nicheMetric("跨词重合度", marketRatio(snapshot.cross_keyword_overlap), `${fmt.int(snapshot.repeated_product_count)} 个 ASIN 出现在至少两个关键词`)}
      ${nicheMetric("价格带 P25 / 中位 / P75", priceRange, "按去重 ASIN 最新价格")}
      ${nicheMetric("评论门槛 P25 / 中位 / P75", reviewRange, "按去重 ASIN 最新评论数")}
      ${nicheMetric("近月购买量下界代理", fmt.int(snapshot.monthly_bought_total), `字段覆盖 ${marketRatio(snapshot.monthly_bought_coverage)}`)}
      ${nicheMetric("需求 CR3 / CR10", `${marketRatio(snapshot.demand_cr3)} / ${marketRatio(snapshot.demand_cr10)}`, "基于有近月购买量的去重 ASIN")}
      ${nicheMetric("广告卡片密度", marketRatio(snapshot.ad_density), `SERP 关键词覆盖 ${marketRatio(snapshot.serp_coverage)}`)}
      ${nicheMetric("品牌商品 CR3", marketRatio(snapshot.brand_product_cr3), `品牌字段覆盖 ${marketRatio(snapshot.brand_coverage)}，非销售份额`)}
    </div>
    ${warningHtml}`;
}

function nicheMetric(label, value, hint) {
  return `<div><span>${escapeHtml(label)}</span><b>${escapeHtml(value)}</b><small>${escapeHtml(hint)}</small></div>`;
}

function nicheRange(low, median, high, formatter) {
  if (low == null && median == null && high == null) return "—";
  return `${formatter(low)} / ${formatter(median)} / ${formatter(high)}`;
}

function marketNicheRankSourceMeta(snapshot) {
  const first = String(snapshot?.rank_source_first_at || "");
  const latest = String(snapshot?.rank_source_latest_at || "");
  if (!first && !latest) return "排名来源 —";
  const range = first === latest || !first || !latest
    ? fmt.text(first || latest)
    : `${fmt.text(first)} 至 ${fmt.text(latest)}`;
  const alignmentLabels = {
    missing: "无可用批次",
    single: "单一批次",
    synchronous: "严格同期",
    adjacent: "相邻批次",
    mixed_period: "混合时点",
  };
  const alignment = snapshot?.rank_source_alignment_label
    || alignmentLabels[snapshot?.rank_source_alignment]
    || "时间关系未标注";
  const span = marketNicheSourceSpan(snapshot?.rank_source_span_hours);
  return `排名来源 ${range} · ${alignment}${span ? `（跨度 ${span}）` : ""}`;
}

function marketNicheSourceSpan(value) {
  const hours = Number(value);
  if (!Number.isFinite(hours) || hours <= 0) return "";
  if (hours >= 24) return `${(hours / 24).toFixed(1)} 天`;
  if (hours >= 1) return `${hours.toFixed(1)} 小时`;
  return `${Math.round(hours * 60)} 分钟`;
}

function renderMarketNicheKeywords(rows) {
  if (!rows.length) return `<div class="state research-empty">尚未关联成员关键词。</div>`;
  const body = rows.map((row) => `<tr>
    <td><button class="research-text-link" onclick="window.marketNicheOpenKeywordById(${Number(row.keyword_id)})">${escapeHtml(row.keyword)}</button><div class="cell-sub">${escapeHtml(NICHE_KEYWORD_ROLE_LABELS[row.role] || row.role)}</div></td>
    <td class="num">${fmt.int(row.observed_product_count)}</td>
    <td class="num">${fmt.int(row.rank_snapshot_count)}</td>
    <td>${fmt.text(row.latest_rank_at)}</td>
    <td class="num">${fmt.int(row.serp_snapshot_count)}</td>
    <td>${fmt.text(row.latest_serp_at)}</td>
    <td><button class="btn btn-sm" onclick="window.marketNicheRemoveKeyword(${Number(row.keyword_id)})">移除</button></td>
  </tr>`).join("");
  return wrapTable(`<table><thead><tr><th>关键词</th><th class="num">累计 ASIN</th><th class="num">排名快照</th><th>最近排名</th><th class="num">SERP 快照</th><th>最近 SERP</th><th>操作</th></tr></thead><tbody>${body}</tbody></table>`);
}

function renderMarketNicheProducts(rows) {
  if (!rows.length) return `<div class="state research-empty">尚未关联人工锚点商品。</div>`;
  const body = rows.map((row) => {
    const hash = productDetailHash(row.asin);
    return `<tr${selectableEntryAttrs(hash, row.asin)}>
    <td>${amazonProductLink(row, displayTitle(row), 54)}<div class="cell-sub">${escapeHtml(row.asin)} · ${escapeHtml(NICHE_PRODUCT_ROLE_LABELS[row.role] || row.role)}</div></td>
    <td>${escapeHtml(fmt.text(row.brand))}</td>
    <td>${fmt.money(row.price)}</td>
    <td>${fmt.num(row.rating, 1)} / ${fmt.int(row.review_count)}</td>
    <td>${fmt.int(row.monthly_bought)}</td>
    <td>${fmt.text(row.latest_snapshot_at)}</td>
    <td><button class="btn btn-sm" onclick="window.marketNicheRemoveProduct(${Number(row.product_id)})">移除</button></td>
  </tr>`;
  }).join("");
  return wrapTable(`<table><thead><tr><th>商品</th><th>品牌</th><th>价格</th><th>评分 / 评论</th><th>近月购买</th><th>最近快照</th><th>操作</th></tr></thead><tbody>${body}</tbody></table>`);
}

function renderMarketNicheProjects(rows) {
  if (!rows.length) return `<div class="state research-empty">尚未关联研究项目。</div>`;
  const body = rows.map((row) => `<tr>
    <td><a class="link" href="#/research-projects/${Number(row.project_id)}">${escapeHtml(row.name)}</a><div class="cell-sub">项目 #${fmt.int(row.project_id)}</div></td>
    <td>${escapeHtml(NICHE_PROJECT_ROLE_LABELS[row.role] || row.role)}</td>
    <td>${researchStatusBadge(row.status)}</td>
    <td>${escapeHtml(truncate(row.objective || "—", 80))}</td>
    <td><button class="btn btn-sm" onclick="window.marketNicheRemoveProject(${Number(row.project_id)})"${["approved", "rejected"].includes(row.status) ? ` disabled title="项目证据已冻结，请先退回可编辑阶段"` : ""}>移除</button></td>
  </tr>`).join("");
  return wrapTable(`<table><thead><tr><th>研究项目</th><th>关系</th><th>阶段</th><th>研究目标</th><th>操作</th></tr></thead><tbody>${body}</tbody></table>`);
}

function renderMarketNicheSnapshotHistory(rows) {
  if (!rows.length) return `<div class="state research-empty">暂无市场证据快照。</div>`;
  const body = rows.map((row) => `<tr>
    <td>${fmt.text(row.snapshot_at)}</td>
    <td>${marketNicheEvidenceBadge(row)}</td>
    <td class="num">${fmt.int(row.keyword_count)}</td>
    <td class="num">${fmt.int(row.observed_product_count)}</td>
    <td>${marketRatio(row.serp_coverage)}</td>
    <td>${marketRatio(row.cross_keyword_overlap)}</td>
    <td>${marketRatio(row.ad_density)}</td>
    <td><code>${escapeHtml(String(row.evidence_hash || "").slice(0, 10))}</code></td>
  </tr>`).join("");
  return wrapTable(`<table><thead><tr><th>生成时间</th><th>证据状态</th><th class="num">关键词</th><th class="num">去重 ASIN</th><th>SERP 覆盖</th><th>跨词重合</th><th>广告密度</th><th>证据哈希</th></tr></thead><tbody>${body}</tbody></table>`);
}

window.marketNicheOpenKeywordById = (keywordId) => {
  const row = (marketNicheCurrent?.keywords || []).find((item) => Number(item.keyword_id) === Number(keywordId));
  if (!row) return;
  keywordLibraryState.keyword = String(row.keyword || "");
  keywordLibraryState.offset = 0;
  persistState(keywordLibraryState);
  location.hash = "#/keyword-library";
};

window.marketNicheRemoveKeyword = async (keywordId) => {
  const nicheId = Number(marketNicheCurrent?.niche?.id);
  if (!nicheId || !confirm("从该利基移除这个成员关键词？历史利基快照不会删除。")) return;
  try {
    await apiSend(`/api/market-niches/${nicheId}/keywords/${Number(keywordId)}`, "DELETE");
    notice("成员关键词已移除", "ok");
    await viewMarketNicheDetail(nicheId);
  } catch (err) { notice(err.message, "bad"); }
};

window.marketNicheRemoveProduct = async (productId) => {
  const nicheId = Number(marketNicheCurrent?.niche?.id);
  if (!nicheId || !confirm("移除这个人工锚点商品？不会删除商品主数据。")) return;
  try {
    await apiSend(`/api/market-niches/${nicheId}/products/${Number(productId)}`, "DELETE");
    notice("人工锚点商品已移除", "ok");
    await viewMarketNicheDetail(nicheId);
  } catch (err) { notice(err.message, "bad"); }
};

window.marketNicheRemoveProject = async (projectId) => {
  const nicheId = Number(marketNicheCurrent?.niche?.id);
  if (!nicheId || !confirm("解除该利基与研究项目的关系？双方业务数据都会保留。")) return;
  try {
    await apiSend(`/api/market-niches/${nicheId}/projects/${Number(projectId)}`, "DELETE");
    notice("研究项目关系已解除", "ok");
    await viewMarketNicheDetail(nicheId);
  } catch (err) { notice(err.message, "bad"); }
};

/* ---------- 视图：竞品与关键词图谱 ---------- */
async function viewCompetitiveGraph() {
  competitiveGraphCurrent = null;
  content.innerHTML = `
    <div class="panel graph-list-panel">
      <div class="table-toolbar">
        <div><h2 style="margin:0">竞品图谱</h2><div class="result-meta">从已生成的利基快照重建关键词与 ASIN 关系</div></div>
        <a class="btn" href="#/market-niches">管理市场利基</a>
      </div>
      <div class="filters">
        <input id="graph-list-keyword" placeholder="利基名称、定义或类目" />
        <input id="graph-list-marketplace" placeholder="站点" style="width:82px" />
        <button class="btn" id="graph-list-filter">筛选</button>
        <button class="btn" id="graph-list-reset">重置</button>
      </div>
      <div id="graph-list-meta" class="result-meta"></div>
      <div id="graph-list-table"><div class="state"><div class="spinner"></div>加载利基…</div></div>
      <div id="graph-list-pager"></div>
    </div>
    <div class="niche-evidence-note graph-boundary-note">
      图谱不会访问 Amazon，也不会推断全量排名。请先在“市场与利基”中明确成员关键词并生成证据快照。
    </div>`;
  document.getElementById("graph-list-keyword").value = competitiveGraphState.keyword;
  document.getElementById("graph-list-marketplace").value = competitiveGraphState.marketplace;
  document.getElementById("graph-list-filter").onclick = () => loadCompetitiveGraphNiches(true);
  document.getElementById("graph-list-reset").onclick = () => {
    competitiveGraphState.keyword = "";
    competitiveGraphState.marketplace = "US";
    competitiveGraphState.listOffset = 0;
    persistState(competitiveGraphState);
    document.getElementById("graph-list-keyword").value = "";
    document.getElementById("graph-list-marketplace").value = "US";
    loadCompetitiveGraphNiches();
  };
  ["graph-list-keyword", "graph-list-marketplace"].forEach((id) => {
    document.getElementById(id).onkeydown = (event) => {
      if (event.key === "Enter") loadCompetitiveGraphNiches(true);
    };
  });
  await loadCompetitiveGraphNiches();
}

async function loadCompetitiveGraphNiches(reset = false) {
  if (reset) competitiveGraphState.listOffset = 0;
  competitiveGraphState.keyword = String(document.getElementById("graph-list-keyword")?.value || "").trim();
  competitiveGraphState.marketplace = String(document.getElementById("graph-list-marketplace")?.value || "US").trim().toUpperCase() || "US";
  persistState(competitiveGraphState);
  const table = document.getElementById("graph-list-table");
  table.innerHTML = `<div class="state"><div class="spinner"></div>加载利基…</div>`;
  const query = new URLSearchParams({
    limit: String(competitiveGraphState.listLimit),
    offset: String(competitiveGraphState.listOffset),
    marketplace: competitiveGraphState.marketplace,
    keyword: competitiveGraphState.keyword,
    sort_by: "latest_snapshot_at",
    sort_dir: "desc",
  });
  try {
    const page = normalizePage(await api(`/api/market-niches?${query.toString()}`), competitiveGraphState.listLimit);
    competitiveGraphState.listLimit = page.limit;
    competitiveGraphState.listOffset = page.offset;
    persistState(competitiveGraphState);
    document.getElementById("graph-list-meta").textContent = `${pageSummary(page, "利基")} · 仅有证据快照的利基可进入图谱`;
    if (!page.rows.length) {
      table.innerHTML = `<div class="state">当前筛选下没有市场利基。</div>`;
    } else {
      const rows = page.rows.map((row) => ({
        cells: [
          `<b>${escapeHtml(row.name)}</b><div class="cell-sub">#${fmt.int(row.id)} · ${escapeHtml(row.marketplace || "US")}</div>`,
          fmt.int(row.keyword_count),
          row.latest_snapshot_id ? fmt.int(row.observed_product_count) : "—",
          marketNicheEvidenceBadge(row),
          escapeHtml(fmt.text(row.latest_snapshot_at)),
          row.latest_snapshot_id
            ? `<a class="btn btn-sm" href="#/competitive-graph/${Number(row.id)}">打开图谱</a>`
            : `<a class="btn btn-sm" href="#/market-niches/${Number(row.id)}">先生成快照</a>`,
        ],
      }));
      table.innerHTML = tableHtml(["市场利基", "成员词", "观察 ASIN", "证据状态", "最近快照", "操作"], rows);
    }
    document.getElementById("graph-list-pager").innerHTML = renderPager("graph-list-pager-inner", page, [10, 25, 50]);
    bindCompetitiveGraphListPager(page);
  } catch (err) {
    table.innerHTML = `<div class="state error">⚠ ${escapeHtml(err.message)}</div>`;
  }
}

function bindCompetitiveGraphListPager(page) {
  const pager = document.getElementById("graph-list-pager-inner");
  if (!pager) return;
  const total = Number(page.total || 0);
  const limit = Number(page.limit || competitiveGraphState.listLimit);
  const totalPages = Math.max(1, Math.ceil(total / limit));
  const jump = pager.querySelector("[data-page-jump]");
  const go = () => {
    const pageNo = Math.max(1, Math.min(Number(jump?.value) || 1, totalPages));
    competitiveGraphState.listOffset = (pageNo - 1) * limit;
    persistState(competitiveGraphState);
    loadCompetitiveGraphNiches();
  };
  pager.querySelector('[data-page="prev"]')?.addEventListener("click", () => {
    competitiveGraphState.listOffset = Math.max(0, competitiveGraphState.listOffset - limit);
    persistState(competitiveGraphState);
    loadCompetitiveGraphNiches();
  });
  pager.querySelector('[data-page="next"]')?.addEventListener("click", () => {
    competitiveGraphState.listOffset = Math.min(Math.max(0, (totalPages - 1) * limit), competitiveGraphState.listOffset + limit);
    persistState(competitiveGraphState);
    loadCompetitiveGraphNiches();
  });
  pager.querySelector('[data-page="go"]')?.addEventListener("click", go);
  if (jump) jump.onkeydown = (event) => { if (event.key === "Enter") go(); };
  pager.querySelector("[data-page-size]")?.addEventListener("change", (event) => {
    competitiveGraphState.listLimit = Number(event.currentTarget.value) || 25;
    competitiveGraphState.listOffset = 0;
    persistState(competitiveGraphState);
    loadCompetitiveGraphNiches();
  });
}

async function viewCompetitiveGraphDetail(nicheId) {
  const id = Number(nicheId);
  if (!Number.isInteger(id) || id < 1) return emptyState("市场利基 ID 不正确");
  if (Number(competitiveGraphState.nicheId) !== id) {
    Object.assign(competitiveGraphState, {
      nicheId: id,
      snapshotId: null,
      focusAsin: "",
      activeView: "relations",
    });
    persistState(competitiveGraphState);
  }
  content.innerHTML = `<a class="back-link" href="#/competitive-graph">← 返回竞品图谱</a><div class="state"><div class="spinner"></div>正在从冻结快照构建关系…</div>`;
  await loadCompetitiveGraphData();
}

async function loadCompetitiveGraphData() {
  const nicheId = Number(competitiveGraphState.nicheId);
  const query = new URLSearchParams({
    limit: String(competitiveGraphState.productLimit),
    min_shared: String(competitiveGraphState.minShared),
  });
  if (competitiveGraphState.snapshotId) query.set("snapshot_id", String(competitiveGraphState.snapshotId));
  if (competitiveGraphState.focusAsin) query.set("focus_asin", competitiveGraphState.focusAsin);
  try {
    const data = await api(`/api/competitive-graph/${nicheId}?${query.toString()}`);
    competitiveGraphCurrent = data;
    competitiveGraphState.snapshotId = Number(data.snapshot?.id) || null;
    persistState(competitiveGraphState);
    renderCompetitiveGraphDetail();
  } catch (err) {
    competitiveGraphCurrent = null;
    content.innerHTML = `<a class="back-link" href="#/competitive-graph">← 返回竞品图谱</a><div class="state error">⚠ ${escapeHtml(err.message)}<div class="hint"><a class="link" href="#/market-niches/${nicheId}">前往市场利基检查成员与证据快照</a></div></div>`;
  }
}

function renderCompetitiveGraphDetail() {
  const data = competitiveGraphCurrent || {};
  const niche = data.niche || {};
  const snapshot = data.snapshot || {};
  const summary = data.summary || {};
  const integrity = data.source_integrity || {};
  const rankSourceRange = summary.rank_source_first_at
    ? (summary.rank_source_first_at === summary.rank_source_latest_at
      ? fmt.text(summary.rank_source_first_at)
      : `${fmt.text(summary.rank_source_first_at)} 至 ${fmt.text(summary.rank_source_latest_at)}`)
    : "—";
  const snapshotOptions = (data.available_snapshots || []).map((row) => {
    const label = `#${row.id} · ${fmt.text(row.snapshot_at)} · ${fmt.int(row.observed_product_count)} ASIN`;
    return `<option value="${Number(row.id)}"${Number(row.id) === Number(snapshot.id) ? " selected" : ""}>${escapeHtml(label)}</option>`;
  }).join("");
  const warningHtml = data.warnings?.length
    ? `<div class="niche-warning-list graph-warning-list"><b>证据与展示限制</b>${data.warnings.map((warning) => `<div>${escapeHtml(warning)}</div>`).join("")}</div>`
    : `<div class="niche-evidence-note">冻结来源记录完整；图谱仍只代表已采集页面范围。</div>`;
  content.innerHTML = `
    <a class="back-link" href="#/competitive-graph">← 返回竞品图谱</a>
    <div class="research-detail-head graph-detail-head">
      <div><h2>${escapeHtml(niche.name || "竞品图谱")}</h2><div class="meta">利基 #${fmt.int(niche.id)} · ${escapeHtml(niche.marketplace || "US")} · 快照 #${fmt.int(snapshot.id)}</div></div>
      <div class="actions"><span class="badge ${integrity.complete ? "badge-good" : "badge-warn"}">${integrity.complete ? "冻结来源完整" : "冻结来源有缺口"}</span><a class="btn btn-sm" href="#/market-niches/${Number(niche.id)}">利基详情</a></div>
    </div>
    <div class="panel graph-controls">
      <div class="graph-control-grid">
        <label>证据快照<select id="graph-snapshot" class="sel">${snapshotOptions}</select></label>
        <label>展示竞品数<select id="graph-product-limit" class="sel">
          ${[50, 100, 200].map((value) => `<option value="${value}"${value === Number(competitiveGraphState.productLimit) ? " selected" : ""}>${value}</option>`).join("")}
        </select></label>
        <label>最少共同 ASIN<input id="graph-min-shared" type="number" min="1" max="100000" value="${Number(competitiveGraphState.minShared) || 1}" /></label>
        <label class="graph-focus-field">目标 ASIN<input id="graph-focus-asin" maxlength="10" value="${escapeHtml(competitiveGraphState.focusAsin)}" placeholder="例如 B0XXXXXXXX" /></label>
        <button class="btn" id="graph-apply">应用</button>
        <button class="btn" id="graph-clear-focus"${competitiveGraphState.focusAsin ? "" : " disabled"}>清除目标</button>
      </div>
      <div class="result-meta">快照生成 ${fmt.text(snapshot.snapshot_at)} · 排名来源 ${escapeHtml(rankSourceRange)} · ${escapeHtml(data.basis || "")}</div>
    </div>
    <div class="research-summary-grid graph-summary-grid">
      <div><span>成员关键词</span><b>${fmt.int(summary.keyword_count)}</b><small>冻结排名边 ${fmt.int(summary.rank_edge_count)}</small></div>
      <div><span>观察 ASIN</span><b>${fmt.int(summary.observed_product_count)}</b><small>当前展示 ${fmt.int(summary.returned_product_count)}</small></div>
      <div><span>跨词商品</span><b>${fmt.int(summary.cross_keyword_product_count)}</b><small>覆盖至少 2 个成员词</small></div>
      <div><span>关键词关系</span><b>${fmt.int(summary.keyword_relation_count)}</b><small>共同 ASIN ≥ ${fmt.int(summary.min_shared)}</small></div>
    </div>
    ${warningHtml}
    <div class="graph-tabs" role="tablist" aria-label="竞品图谱视图">
      ${graphTabButton("relations", "关键词关系")}
      ${graphTabButton("competitors", "竞品覆盖")}
      ${graphTabButton("gaps", "ASIN 缺口")}
    </div>
    <div id="graph-active-view"></div>`;

  document.getElementById("graph-snapshot").onchange = (event) => {
    competitiveGraphState.snapshotId = Number(event.currentTarget.value) || null;
    persistState(competitiveGraphState);
    loadCompetitiveGraphData();
  };
  document.getElementById("graph-apply").onclick = applyCompetitiveGraphControls;
  document.getElementById("graph-clear-focus").onclick = () => {
    competitiveGraphState.focusAsin = "";
    competitiveGraphState.activeView = "competitors";
    persistState(competitiveGraphState);
    loadCompetitiveGraphData();
  };
  document.getElementById("graph-focus-asin").onkeydown = (event) => {
    if (event.key === "Enter") applyCompetitiveGraphControls();
  };
  content.querySelectorAll("[data-graph-view]").forEach((button) => {
    button.onclick = () => {
      competitiveGraphState.activeView = button.dataset.graphView;
      persistState(competitiveGraphState);
      renderCompetitiveGraphActiveView();
      content.querySelectorAll("[data-graph-view]").forEach((item) => item.classList.toggle("btn-active", item.dataset.graphView === competitiveGraphState.activeView));
    };
  });
  renderCompetitiveGraphActiveView();
}

function graphTabButton(value, label) {
  return `<button class="btn${competitiveGraphState.activeView === value ? " btn-active" : ""}" data-graph-view="${value}" role="tab" aria-selected="${competitiveGraphState.activeView === value}">${label}</button>`;
}

function applyCompetitiveGraphControls() {
  competitiveGraphState.productLimit = Number(document.getElementById("graph-product-limit")?.value) || 100;
  competitiveGraphState.minShared = Math.max(1, Number(document.getElementById("graph-min-shared")?.value) || 1);
  competitiveGraphState.focusAsin = String(document.getElementById("graph-focus-asin")?.value || "").trim().toUpperCase();
  if (competitiveGraphState.focusAsin && !/^[A-Z0-9]{10}$/.test(competitiveGraphState.focusAsin)) {
    notice("目标 ASIN 必须是 10 位字母或数字", "bad");
    return;
  }
  if (competitiveGraphState.focusAsin) competitiveGraphState.activeView = "gaps";
  persistState(competitiveGraphState);
  loadCompetitiveGraphData();
}

function renderCompetitiveGraphActiveView() {
  const box = document.getElementById("graph-active-view");
  if (!box || !competitiveGraphCurrent) return;
  if (competitiveGraphState.activeView === "competitors") box.innerHTML = renderCompetitiveCoverageView(competitiveGraphCurrent);
  else if (competitiveGraphState.activeView === "gaps") box.innerHTML = renderCompetitiveGapView(competitiveGraphCurrent);
  else box.innerHTML = renderKeywordRelationView(competitiveGraphCurrent);
  box.querySelectorAll("[data-graph-focus]").forEach((button) => {
    button.onclick = () => {
      competitiveGraphState.focusAsin = String(button.dataset.graphFocus || "").toUpperCase();
      competitiveGraphState.activeView = "gaps";
      persistState(competitiveGraphState);
      loadCompetitiveGraphData();
    };
  });
}

function renderKeywordRelationView(data) {
  const nodes = data.keyword_nodes || [];
  const relations = data.keyword_relations || [];
  const nodeRows = nodes.map((row) => ({ cells: [
    `<b>${escapeHtml(row.keyword)}</b>`,
    fmt.int(row.observed_product_count),
    fmt.int(row.organic_product_count),
    fmt.int(row.sponsored_product_count),
  ] }));
  const relationRows = relations.map((row) => ({ cells: [
    `<b>${escapeHtml(row.left_keyword)}</b><div class="cell-sub">样本 ${fmt.int(row.left_product_count)}</div>`,
    `<b>${escapeHtml(row.right_keyword)}</b><div class="cell-sub">样本 ${fmt.int(row.right_product_count)}</div>`,
    fmt.int(row.shared_product_count),
    fmt.int(row.shared_organic_product_count),
    marketRatio(row.jaccard),
    `${marketRatio(row.left_containment)} / ${marketRatio(row.right_containment)}`,
    graphConfidenceBadge(row),
  ] }));
  return `
    <div class="panel">
      <div class="table-toolbar"><h2 style="margin:0">关键词观察样本</h2><span class="result-meta">广告与自然观察都计入共现，另行拆分自然样本</span></div>
      ${nodeRows.length ? tableHtml(["关键词", "观察 ASIN", "自然 ASIN", "广告 ASIN"], nodeRows) : `<div class="state">所选快照没有可用关键词节点。</div>`}
    </div>
    <div class="panel">
      <div class="table-toolbar"><h2 style="margin:0">关键词共现关系</h2><span class="result-meta">Jaccard = 共同 ASIN / 两词并集 ASIN</span></div>
      ${relationRows.length ? tableHtml(["关键词 A", "关键词 B", "共同 ASIN", "共同自然", "Jaccard", "A / B 包含率", "样本置信"], relationRows) : `<div class="state">当前阈值下没有关键词共现关系。可降低“最少共同 ASIN”，或补充成员词排名证据。</div>`}
    </div>`;
}

function graphConfidenceBadge(row) {
  const cls = row.confidence === "high" ? "badge-good" : row.confidence === "medium" ? "badge-warn" : "badge-dim";
  return `<span class="badge ${cls}" title="${escapeHtml(row.confidence_reason || "仅表示样本规模")}">${escapeHtml(row.confidence_label || "低样本")}</span>`;
}

function renderCompetitiveCoverageView(data) {
  const keywords = data.keyword_nodes || [];
  const competitors = data.competitors || [];
  if (!competitors.length) return `<div class="panel"><div class="state">所选快照没有可展示的关键词-ASIN覆盖关系。</div></div>`;
  const headers = keywords.map((row) => `<th class="graph-keyword-head" title="${escapeHtml(row.keyword)}">${escapeHtml(truncate(row.keyword, 24))}</th>`).join("");
  const rows = competitors.map((row) => {
    const cells = new Map((row.keyword_observations || []).map((item) => [Number(item.keyword_id), item]));
    const coverage = keywords.map((keyword) => renderGraphCoverageCell(cells.get(Number(keyword.keyword_id)))).join("");
    const hash = productDetailHash(row.asin);
    return `<tr${selectableEntryAttrs(hash, row.asin)}>
      <td class="graph-product-cell">${amazonProductLink(row, displayTitle(row, row.asin), 44)}<div class="cell-sub">${amazonProductLink(row, row.asin)} · ${escapeHtml(fmt.text(row.brand))}</div></td>
      <td class="num">${fmt.int(row.observed_keyword_count)} / ${fmt.int(keywords.length)}</td>
      <td class="num">${fmt.int(row.organic_keyword_count)}</td>
      <td class="num">${row.best_organic_rank == null ? "—" : `#${fmt.int(row.best_organic_rank)}`}</td>
      <td class="num">${row.average_organic_rank == null ? "—" : fmt.num(row.average_organic_rank, 1)}</td>
      <td class="num" title="自然位次贡献按 1/log2(rank+1) 计算，并除以全部成员关键词数">${row.organic_visibility_proxy == null ? "—" : fmt.num(Number(row.organic_visibility_proxy) * 100, 1)}</td>
      ${coverage}
      <td><button class="btn btn-sm" data-graph-focus="${escapeHtml(row.asin || "")}">分析缺口</button></td>
    </tr>`;
  }).join("");
  return `<div class="panel graph-matrix-panel">
    <div class="table-toolbar"><div><h2 style="margin:0">竞品关键词覆盖矩阵</h2><div class="result-meta">绿色为自然位次，黄色为仅观察到广告，灰点为未在已采集范围观察到</div></div><span class="result-meta">自然可见度为 0-100 位次代理，不是流量份额</span></div>
    <div class="graph-matrix-wrap"><table class="graph-matrix"><thead><tr>
      <th class="graph-product-head">商品</th><th>覆盖</th><th>自然词</th><th>最佳自然</th><th>平均自然</th><th>自然可见度</th>${headers}<th>操作</th>
    </tr></thead><tbody>${rows}</tbody></table></div>
  </div>`;
}

function renderGraphCoverageCell(item) {
  const status = item?.status || "unobserved";
  const label = item?.status_label || "未在已采集范围观察到";
  const text = status === "organic" ? `#${fmt.int(item.organic_rank)}` : status === "sponsored" ? "广告" : "·";
  return `<td class="graph-matrix-cell graph-cell-${status}" title="${escapeHtml(label)}${item?.page_no ? ` · 第 ${fmt.int(item.page_no)} 页` : ""}" aria-label="${escapeHtml(label)}">${text}</td>`;
}

function renderCompetitiveGapView(data) {
  const focus = data.focus;
  if (!focus) return `<div class="panel"><div class="state graph-focus-empty">输入一个 10 位目标 ASIN，系统会在所选快照的成员关键词范围内检查自然位次、广告观察和未观察项。</div></div>`;
  const product = focus.product || {};
  const gapOrder = { unobserved: 0, sponsored: 1, organic: 2 };
  const gapRows = (focus.keyword_gaps || []).slice().sort((a, b) => (gapOrder[a.status] ?? 9) - (gapOrder[b.status] ?? 9)).map((row) => ({ cells: [
    `<b>${escapeHtml(row.keyword)}</b>`,
    graphObservationBadge(row.status, row.status_label),
    row.organic_rank == null ? "—" : `#${fmt.int(row.organic_rank)}`,
    row.page_no == null ? "—" : `第 ${fmt.int(row.page_no)} 页`,
  ] }));
  const similarRows = (focus.similar_competitors || []).map((row) => ({
    _hash: productDetailHash(row.asin),
    _key: row.asin,
    cells: [
    `${amazonProductLink(row, displayTitle(row, row.asin), 48)}<div class="cell-sub">${amazonProductLink(row, row.asin)}</div>`,
    fmt.int(row.shared_keyword_count),
    marketRatio(row.coverage_jaccard),
    escapeHtml((row.shared_keywords || []).join("、") || "—"),
    `<button class="btn btn-sm" data-graph-focus="${escapeHtml(row.asin || "")}">切换目标</button>`,
  ] }));
  return `
    <div class="panel graph-focus-panel">
      <div class="table-toolbar"><div><h2 style="margin:0">${amazonProductLink(product, displayTitle(product, product.asin), 72)}</h2><div class="result-meta">${amazonProductLink(product, product.asin)} · ${escapeHtml(focus.message || "")}</div></div>${focus.found_in_database ? `<a class="btn btn-sm" href="#/product/${encodeURIComponent(product.asin)}">商品详情</a>` : ""}</div>
      <div class="research-summary-grid graph-focus-summary">
        <div><span>成员关键词</span><b>${fmt.int(focus.summary?.keyword_count)}</b><small>仅限所选快照</small></div>
        <div><span>自然位次</span><b>${fmt.int(focus.summary?.organic_count)}</b><small>观察到非广告位次</small></div>
        <div><span>仅广告观察</span><b>${fmt.int(focus.summary?.sponsored_count)}</b><small>不能推断持续投放</small></div>
        <div><span>未观察到</span><b>${fmt.int(focus.summary?.unobserved_count)}</b><small>不等于没有排名</small></div>
      </div>
      ${tableHtml(["成员关键词", "观察状态", "自然位次", "页面"], gapRows)}
    </div>
    <div class="panel">
      <div class="table-toolbar"><h2 style="margin:0">相似覆盖竞品</h2><span class="result-meta">只按共同成员关键词排序，不推断功能或受众相似</span></div>
      ${similarRows.length ? tableHtml(["商品", "共同关键词", "覆盖 Jaccard", "共同词", "操作"], similarRows) : `<div class="state">当前快照中没有可比较的共同关键词覆盖竞品。</div>`}
    </div>`;
}

function graphObservationBadge(status, label) {
  const cls = status === "organic" ? "badge-good" : status === "sponsored" ? "badge-warn" : "badge-dim";
  return `<span class="badge ${cls}">${escapeHtml(label || status || "—")}</span>`;
}

/* ---------- 视图：领域评分模型 ---------- */
async function viewDomainModels() {
  content.innerHTML = `
    <div class="panel score-replay-head">
      <div class="table-toolbar">
        <div><h2 style="margin:0">模型实验室</h2><div class="result-meta">通用基线 + 卖家领域模型 · 参数版本可追溯</div></div>
        <span class="badge badge-warn">生产评分冻结</span>
      </div>
      ${renderScoringModeTabs("domain")}
      <div class="domain-model-toolbar">
        <label>站点<input id="domain-model-marketplace" maxlength="8" value="${escapeHtml(domainModelState.marketplace)}" /></label>
        <label>状态<select id="domain-model-status" class="sel">
          <option value="all">全部状态</option><option value="draft">草稿</option><option value="active">已启用</option><option value="archived">已归档</option>
        </select></label>
        <div class="actions"><button class="btn" id="domain-model-refresh">刷新</button><button class="btn btn-primary" id="domain-model-new">新建模型</button></div>
      </div>
      <div class="score-replay-boundary">领域模型表达卖家的研究偏好，不是利润或爆款预测。新版本会自动退回草稿，必须显式启用；试算不会写入生产综合分。</div>
    </div>
    <div class="domain-model-layout">
      <section class="panel domain-model-list-panel">
        <div class="table-toolbar"><h2 style="margin:0">模型列表</h2><span id="domain-model-count" class="result-meta"></span></div>
        <div id="domain-model-list"><div class="state"><div class="spinner"></div>读取模型…</div></div>
      </section>
      <section class="panel domain-model-editor-panel">
        <div id="domain-model-editor"><div class="state"><div class="spinner"></div>读取参数目录…</div></div>
      </section>
    </div>
    <section class="panel domain-validation-panel">
      <div id="domain-model-validation"><div class="state">选择一套已保存模型后，可生成只读验证样本。</div></div>
    </section>`;
  bindScoringModeTabs();
  document.getElementById("domain-model-status").value = domainModelState.status;
  document.getElementById("domain-model-refresh").onclick = () => {
    domainModelValidationPage = null;
    loadDomainModelWorkspace();
  };
  document.getElementById("domain-model-new").onclick = () => {
    domainModelState.selectedId = "";
    domainModelState.selectedVersionId = "";
    domainModelState.draftProfileId = "new";
    domainModelState.draftJson = "";
    domainModelDetail = null;
    domainModelValidationPage = null;
    domainModelState.offset = 0;
    persistState(domainModelState);
    renderDomainModelWorkspace();
  };
  document.getElementById("domain-model-status").onchange = (event) => {
    domainModelState.status = event.target.value || "all";
    domainModelState.selectedId = "";
    domainModelDetail = null;
    domainModelValidationPage = null;
    domainModelState.offset = 0;
    persistState(domainModelState);
    loadDomainModelWorkspace();
  };
  document.getElementById("domain-model-marketplace").addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    domainModelState.marketplace = String(event.target.value || "US").trim().toUpperCase() || "US";
    domainModelState.selectedId = "";
    domainModelState.draftJson = "";
    domainModelDetail = null;
    domainModelValidationPage = null;
    domainModelState.offset = 0;
    persistState(domainModelState);
    loadDomainModelWorkspace();
  });
  await loadDomainModelWorkspace();
}

async function loadDomainModelWorkspace() {
  const list = document.getElementById("domain-model-list");
  const editor = document.getElementById("domain-model-editor");
  if (!list || !editor) return;
  list.innerHTML = `<div class="state"><div class="spinner"></div>读取模型…</div>`;
  try {
    const query = new URLSearchParams({
      marketplace: domainModelState.marketplace || "US",
      limit: "200",
      offset: "0",
    });
    if (domainModelState.status !== "all") query.set("status", domainModelState.status);
    const nicheQuery = new URLSearchParams({ marketplace: domainModelState.marketplace || "US", limit: "200", offset: "0" });
    const [catalog, page, niches] = await Promise.all([
      api("/api/domain-models/catalog"),
      api(`/api/domain-models?${query.toString()}`),
      api(`/api/market-niches?${nicheQuery.toString()}`).catch(() => ({ rows: [] })),
    ]);
    domainModelCatalog = catalog;
    domainModelPage = page;
    domainModelNiches = Array.isArray(niches?.rows) ? niches.rows : [];
    const selectedId = Number(domainModelState.selectedId || 0);
    if (selectedId && (page.rows || []).some((row) => Number(row.id) === selectedId)) {
      domainModelDetail = await api(`/api/domain-models/${selectedId}`);
    } else {
      domainModelDetail = null;
      if (selectedId) domainModelState.selectedId = "";
    }
    persistState(domainModelState);
    renderDomainModelWorkspace();
  } catch (err) {
    list.innerHTML = `<div class="state error">${escapeHtml(err.message || "领域模型读取失败")}</div>`;
    editor.innerHTML = `<div class="state error">请确认 MySQL 已完成最新迁移。</div>`;
  }
}

function renderDomainModelWorkspace() {
  renderDomainModelList();
  renderDomainModelEditor();
  renderDomainModelValidation();
}

function renderDomainModelList() {
  const target = document.getElementById("domain-model-list");
  const count = document.getElementById("domain-model-count");
  if (!target) return;
  const rows = domainModelPage?.rows || [];
  if (count) count.textContent = `共 ${fmt.int(domainModelPage?.total || 0)} 套`;
  if (!rows.length) {
    target.innerHTML = `<div class="state">当前筛选下暂无领域模型。</div>`;
    return;
  }
  const body = rows.map((row) => {
    const version = row.current_version || {};
    const selected = String(row.id) === String(domainModelState.selectedId);
    const statusClass = row.status === "active" ? "badge-good" : row.status === "archived" ? "badge-dim" : "badge-warn";
    const scope = version.scope_type === "category"
      ? version.category_scope
      : version.scope_type === "niche"
        ? version.niche_name
        : version.scope_type === "hybrid"
          ? `${version.category_scope || "—"} + ${version.niche_name || "—"}`
          : "站点通用";
    return `<tr data-domain-profile="${row.id}" class="${selected ? "interactive-selected" : ""}" tabindex="0">
      <td><b>${escapeHtml(row.name)}</b><div class="cell-sub">${escapeHtml(row.description || "未填写领域说明")}</div></td>
      <td><span class="badge ${statusClass}">${escapeHtml(row.status_label)}</span></td>
      <td>${escapeHtml(version.scope_label || "—")}<div class="cell-sub">${escapeHtml(scope || "—")}</div></td>
      <td>v${fmt.int(version.version_no)}<div class="cell-sub">共 ${fmt.int(row.version_count)} 版</div></td>
      <td>${escapeHtml(fmt.text(row.updated_at))}</td>
    </tr>`;
  }).join("");
  target.innerHTML = wrapTable(`<table class="domain-model-table"><thead><tr><th>模型</th><th>状态</th><th>适用范围</th><th>版本</th><th>更新时间</th></tr></thead><tbody>${body}</tbody></table>`);
  target.querySelectorAll("[data-domain-profile]").forEach((row) => {
    const select = () => selectDomainModel(row.dataset.domainProfile);
    row.onclick = select;
    row.ondblclick = () => {
      select();
      document.getElementById("domain-model-editor")?.scrollIntoView({ behavior: "smooth", block: "start" });
    };
    row.onkeydown = (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); select(); } };
  });
}

async function selectDomainModel(profileId) {
  const id = Number(profileId || 0);
  if (!id) return;
  domainModelState.selectedId = String(id);
  domainModelState.selectedVersionId = "";
  domainModelState.offset = 0;
  domainModelValidationPage = null;
  if (domainModelState.draftProfileId !== String(id)) domainModelState.draftJson = "";
  domainModelState.draftProfileId = String(id);
  persistState(domainModelState);
  document.querySelectorAll("[data-domain-profile]").forEach((row) => {
    row.classList.toggle("interactive-selected", row.dataset.domainProfile === String(id));
  });
  const editor = document.getElementById("domain-model-editor");
  if (editor) editor.innerHTML = `<div class="state"><div class="spinner"></div>读取模型版本…</div>`;
  try {
    domainModelDetail = await api(`/api/domain-models/${id}`);
    renderDomainModelEditor();
    renderDomainModelValidation();
  } catch (err) {
    if (editor) editor.innerHTML = `<div class="state error">${escapeHtml(err.message)}</div>`;
    renderDomainModelValidation();
  }
}

function selectedDomainValidationVersion() {
  const profile = domainModelDetail?.profile;
  const versions = domainModelDetail?.versions || [];
  if (!profile || !versions.length) return null;
  const requested = Number(domainModelState.selectedVersionId || profile.current_version_id || 0);
  return versions.find((version) => Number(version.id) === requested) || profile.current_version || versions[0];
}

function renderDomainModelValidation() {
  const target = document.getElementById("domain-model-validation");
  if (!target) return;
  const profile = domainModelDetail?.profile || null;
  if (!profile) {
    target.innerHTML = `
      <div class="table-toolbar"><div><h2 style="margin:0">批量验证台</h2><div class="result-meta">专项模型与通用父策略并列试算</div></div><span class="badge badge-dim">只读</span></div>
      <div class="state">先在上方选择一套已保存模型。新建但未保存的参数不会参与验证。</div>`;
    return;
  }
  const selectedVersion = selectedDomainValidationVersion();
  const versions = domainModelDetail.versions || [];
  const versionOptions = versions.map((version) => `
    <option value="${version.id}"${Number(version.id) === Number(selectedVersion?.id) ? " selected" : ""}>v${fmt.int(version.version_no)} · ${escapeHtml(version.change_note || "无版本说明")}</option>`).join("");
  const unsaved = Boolean(domainModelState.draftJson);
  target.innerHTML = `
    <div class="table-toolbar domain-validation-head">
      <div><h2 style="margin:0">批量验证台</h2><div class="result-meta">${escapeHtml(profile.name)} · 最多 50 条可复现验证样本</div></div>
      <span class="badge badge-dim">不写生产评分</span>
    </div>
    ${unsaved ? `<div class="domain-validation-notice warning">编辑区存在未保存输入；验证只使用数据库中已保存的版本。</div>` : ""}
    <div class="domain-validation-controls">
      <label>模型版本<select class="sel" id="domain-validation-version">${versionOptions}</select></label>
      <label class="domain-validation-search">商品搜索<input id="domain-validation-search" maxlength="255" value="${escapeHtml(domainModelState.validationSearch)}" placeholder="ASIN / 中英文标题" /></label>
      <label>证据上下文<select class="sel" id="domain-validation-evidence">
        <option value="all">全部上下文</option><option value="product_keyword">关键词 + 商品</option><option value="product_only">仅商品快照</option>
      </select></label>
      <label>关注信号<select class="sel" id="domain-validation-signal">
        <option value="all">全部样本</option><option value="changed">专项分有变化</option><option value="low_confidence">低置信</option><option value="scope_review">范围待核对</option>
      </select></label>
      <label>最低绝对分差<input id="domain-validation-min-delta" type="number" min="0" max="100" step="0.1" value="${escapeHtml(domainModelState.validationMinDelta)}" placeholder="0" /></label>
      <label>排序<select class="sel" id="domain-validation-sort">
        <option value="abs_delta">绝对分差</option><option value="specialized_score">专项分</option><option value="confidence">置信度</option><option value="applicability">模型适用度</option><option value="opportunity">机会分</option><option value="risk">风险分</option><option value="snapshot_at">采集时间</option>
      </select></label>
      <label>样本上限<select class="sel" id="domain-validation-sample-limit"><option value="20">20 条</option><option value="50">50 条</option></select></label>
      <div class="domain-validation-actions">
        <button class="btn btn-icon" id="domain-validation-sort-dir" type="button" title="切换升序或降序" aria-label="切换排序方向">${domainModelState.validationSortDir === "asc" ? "↑" : "↓"}</button>
        <button class="btn btn-primary" id="domain-validation-run" type="button">运行批量验证</button>
      </div>
    </div>
    <div id="domain-validation-result">${renderDomainValidationResult(domainModelValidationPage)}</div>`;
  document.getElementById("domain-validation-evidence").value = domainModelState.validationEvidence;
  document.getElementById("domain-validation-signal").value = domainModelState.validationSignal;
  document.getElementById("domain-validation-sort").value = domainModelState.validationSortBy;
  document.getElementById("domain-validation-sample-limit").value = String(domainModelState.validationSampleLimit);
  bindDomainModelValidation();
  if (domainModelValidationPage) bindDomainValidationResult();
}

function persistDomainValidationControls() {
  domainModelState.selectedVersionId = document.getElementById("domain-validation-version")?.value || "";
  domainModelState.validationSearch = String(document.getElementById("domain-validation-search")?.value || "").trim();
  domainModelState.validationEvidence = document.getElementById("domain-validation-evidence")?.value || "all";
  domainModelState.validationSignal = document.getElementById("domain-validation-signal")?.value || "all";
  domainModelState.validationMinDelta = String(document.getElementById("domain-validation-min-delta")?.value || "").trim();
  domainModelState.validationSortBy = document.getElementById("domain-validation-sort")?.value || "abs_delta";
  domainModelState.validationSampleLimit = Number(document.getElementById("domain-validation-sample-limit")?.value || 50);
  persistState(domainModelState);
}

function bindDomainModelValidation() {
  const version = document.getElementById("domain-validation-version");
  if (version) version.onchange = () => {
    persistDomainValidationControls();
    domainModelState.offset = 0;
    domainModelValidationPage = null;
    persistState(domainModelState);
    renderDomainModelValidation();
  };
  document.querySelectorAll("#domain-model-validation input, #domain-model-validation select").forEach((element) => {
    if (element.id === "domain-validation-version") return;
    element.addEventListener("change", persistDomainValidationControls);
  });
  const search = document.getElementById("domain-validation-search");
  if (search) search.onkeydown = (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    persistDomainValidationControls();
    domainModelState.offset = 0;
    loadDomainModelValidation();
  };
  const direction = document.getElementById("domain-validation-sort-dir");
  if (direction) direction.onclick = () => {
    domainModelState.validationSortDir = domainModelState.validationSortDir === "asc" ? "desc" : "asc";
    domainModelState.offset = 0;
    persistState(domainModelState);
    renderDomainModelValidation();
  };
  const run = document.getElementById("domain-validation-run");
  if (run) run.onclick = () => {
    persistDomainValidationControls();
    domainModelState.offset = 0;
    loadDomainModelValidation();
  };
}

async function loadDomainModelValidation() {
  const profile = domainModelDetail?.profile;
  const result = document.getElementById("domain-validation-result");
  if (!profile || !result) return;
  persistDomainValidationControls();
  result.innerHTML = `<div class="state"><div class="spinner"></div>正在按模型范围选样并并列试算…</div>`;
  const button = document.getElementById("domain-validation-run");
  if (button) button.disabled = true;
  try {
    domainModelValidationPage = await apiSend(`/api/domain-models/${profile.id}/evaluate-batch`, "POST", {
      version_id: Number(domainModelState.selectedVersionId || profile.current_version_id),
      search: domainModelState.validationSearch,
      evidence_scope: domainModelState.validationEvidence,
      signal: domainModelState.validationSignal,
      min_abs_delta: Number(domainModelState.validationMinDelta || 0),
      sort_by: domainModelState.validationSortBy,
      sort_dir: domainModelState.validationSortDir,
      sample_limit: Number(domainModelState.validationSampleLimit || 50),
      limit: Number(domainModelState.limit || 20),
      offset: Number(domainModelState.offset || 0),
    });
    renderDomainModelValidation();
  } catch (err) {
    result.innerHTML = `<div class="state error">${escapeHtml(err.message || "批量验证失败")}</div>`;
  } finally {
    if (button && document.body.contains(button)) button.disabled = false;
  }
}

function renderDomainValidationResult(page) {
  if (!page) {
    return `<div class="domain-validation-empty">
      <b>等待运行</b>
      <span>系统会先按已采集类目或已绑定利基选出候选，再比较父策略与专项参数；不会自动训练或写入榜单。</span>
    </div>`;
  }
  const summary = page.candidate_summary || {};
  const comparison = page.parameter_comparison || {};
  const rows = page.rows || [];
  const parameterClass = comparison.identical_to_parent ? "warning" : "good";
  const tableRows = rows.map((row) => {
    const hash = productDetailHash(row.asin, row.keyword || "");
    const delta = Number(row.specialized_score_delta || 0);
    const deltaClass = delta > 0 ? "positive" : delta < 0 ? "negative" : "neutral";
    const contextLabel = row.context_scope === "product_only" ? "仅商品快照" : `关键词：${row.keyword || "—"}`;
    const decisionClass = row.decision?.code === "priority_validate" ? "badge-good" : row.decision?.code === "pause" || row.decision?.code === "scope_review" ? "badge-warn" : "badge-dim";
    const warningText = (row.evidence_warnings || []).join("；");
    return `<tr${selectableEntryAttrs(hash, `domain-validation:${row.asin}:${row.keyword || ""}`)}>
      <td><b title="${escapeHtml(row.title_zh || row.title || row.asin)}">${escapeHtml(truncate(row.title_zh || row.title || row.asin, 52))}</b><div class="cell-sub">${escapeHtml(row.asin)}</div></td>
      <td><span class="badge ${row.context_scope === "product_only" ? "badge-warn" : "badge-good"}">${escapeHtml(contextLabel)}</span><div class="cell-sub" title="${escapeHtml((row.scope_evidence || []).join("；"))}">${escapeHtml(truncate((row.scope_evidence || []).join("；") || "—", 48))}</div></td>
      <td><span class="domain-validation-score-pair">${fmt.num(row.baseline_specialized_score, 1)} → <b>${fmt.num(row.custom_specialized_score, 1)}</b></span></td>
      <td><b class="domain-validation-delta ${deltaClass}">${delta > 0 ? "+" : ""}${fmt.num(delta, 2)}</b></td>
      <td>${fmt.num(row.opportunity_score, 1)} / ${fmt.num(row.risk_score, 1)}</td>
      <td>${fmt.num(row.confidence_score, 1)}<div class="cell-sub">${escapeHtml(row.confidence_level || "—")}</div></td>
      <td>${fmt.num(row.applicability?.score, 0)}<div class="cell-sub">${escapeHtml(row.applicability?.level || "—")}</div></td>
      <td><span class="badge ${decisionClass}">${escapeHtml(row.decision?.label || "—")}</span>${warningText ? `<div class="cell-sub" title="${escapeHtml(warningText)}">${escapeHtml(truncate(warningText, 45))}</div>` : ""}</td>
    </tr>`;
  }).join("");
  const table = rows.length
    ? wrapTable(`<table class="domain-validation-table"><thead><tr><th>商品</th><th>范围与上下文</th><th>基线 → 专项</th><th>分差</th><th>机会 / 风险</th><th>置信度</th><th>适用度</th><th>专项结论</th></tr></thead><tbody>${tableRows}</tbody></table>`)
    : `<div class="state">验证样本已生成，但当前筛选下没有结果。</div>`;
  const truncation = summary.sample_truncated ? `范围内还有候选，本轮按证据强度与采集时间截取前 ${fmt.int(summary.sample_limit)} 条。` : "本轮已覆盖当前范围内全部候选。";
  return `
    <div class="domain-validation-notice ${parameterClass}">${escapeHtml(comparison.message || "")}</div>
    <div class="research-summary-grid domain-validation-summary">
      <div><span>范围候选</span><b>${fmt.int(summary.scope_candidate_total)}</b><small>只认显式范围证据</small></div>
      <div><span>本次样本</span><b>${fmt.int(summary.scored_total)}</b><small>${escapeHtml(truncation)}</small></div>
      <div><span>关键词上下文</span><b>${fmt.int(summary.context_counts?.product_keyword)}</b><small>含自然排名证据机会</small></div>
      <div><span>仅商品快照</span><b>${fmt.int(summary.context_counts?.product_only)}</b><small>排名中性并降置信</small></div>
      <div><span>低置信</span><b>${fmt.int(summary.low_confidence_total)}</b><small>证据置信度低于 55</small></div>
      <div><span>专项分变化</span><b>${fmt.int(summary.changed_total)}</b><small>绝对分差至少 0.01</small></div>
    </div>
    <div class="domain-validation-table-meta"><span>${pageSummary(page, "筛选结果")}</span><span>生成于 ${escapeHtml(fmt.text(page.generated_at))}</span></div>
    ${table}
    <div id="domain-validation-pager">${renderPager("domain-validation-pager-inner", page, [10, 20, 50])}</div>
    ${(page.failures || []).length ? `<div class="domain-validation-failures">${fmt.int(page.failures.length)} 条样本未能试算：${escapeHtml(page.failures.map((item) => `${item.asin} ${item.reason}`).join("；"))}</div>` : ""}
    <div class="score-replay-boundary">${escapeHtml((page.boundaries || []).join(" "))}</div>`;
}

function bindDomainValidationResult() {
  const page = domainModelValidationPage;
  if (!page) return;
  bindPager("domain-validation-pager-inner", domainModelState, page, loadDomainModelValidation);
  restoreInteractiveSelection(document.getElementById("domain-validation-result"));
}

function domainModelInitialDraft() {
  const profile = domainModelDetail?.profile || null;
  const version = profile?.current_version || null;
  const defaults = domainModelCatalog?.default_config || {};
  return {
    profileId: profile ? String(profile.id) : "new",
    name: profile?.name || "",
    description: profile?.description || "",
    marketplace: profile?.marketplace || domainModelState.marketplace || "US",
    scopeType: version?.scope_type || "category",
    categoryScope: version?.category_scope || "",
    nicheId: version?.niche_id ? String(version.niche_id) : "",
    changeNote: "",
    config: JSON.parse(JSON.stringify(version?.config || defaults)),
  };
}

function domainModelDraft() {
  const expectedId = domainModelDetail?.profile ? String(domainModelDetail.profile.id) : "new";
  if (domainModelState.draftProfileId === expectedId && domainModelState.draftJson) {
    try {
      const parsed = JSON.parse(domainModelState.draftJson);
      if (parsed && parsed.profileId === expectedId) return parsed;
    } catch (_) { /* 使用当前模型参数恢复 */ }
  }
  return domainModelInitialDraft();
}

function renderDomainModelEditor() {
  const target = document.getElementById("domain-model-editor");
  if (!target || !domainModelCatalog) return;
  const profile = domainModelDetail?.profile || null;
  const draft = domainModelDraft();
  const config = draft.config || domainModelCatalog.default_config;
  const archived = profile?.status === "archived";
  const strategyOptions = (domainModelCatalog.base_strategies || []).map((item) =>
    `<option value="${escapeHtml(item.code)}"${item.code === config.base_strategy ? " selected" : ""}>${escapeHtml(item.label)}</option>`
  ).join("");
  const nicheOptions = [`<option value="">未选择</option>`, ...domainModelNiches.map((item) =>
    `<option value="${item.id}"${String(item.id) === String(draft.nicheId) ? " selected" : ""}>${escapeHtml(item.name)} · ${escapeHtml(item.status_label || item.status || "")}</option>`
  )].join("");
  const opportunityInputs = domainModelWeightInputs("opportunity", domainModelCatalog.opportunity_components, config.opportunity_weights);
  const riskInputs = domainModelWeightInputs("risk", domainModelCatalog.risk_components, config.risk_weights);
  const policyInputs = Object.entries(domainModelCatalog.recommendation_policy || {}).map(([key, label]) => `
    <label>${escapeHtml(label)}<input data-domain-form data-domain-policy="${escapeHtml(key)}" type="number" min="0" max="100" step="0.1" value="${fmt.num(config.recommendation_policy?.[key], 1)}" /></label>`).join("");
  const statusClass = profile?.status === "active" ? "badge-good" : profile?.status === "archived" ? "badge-dim" : "badge-warn";
  target.innerHTML = `
    <div class="table-toolbar domain-model-editor-head">
      <div><h2 style="margin:0">${profile ? escapeHtml(profile.name) : "新建领域模型"}</h2><div class="result-meta">${profile ? `当前 v${fmt.int(profile.current_version?.version_no)} · ${fmt.int(profile.version_count)} 个不可变版本` : "从通用基线创建第一版人工参数"}</div></div>
      ${profile ? `<span class="badge ${statusClass}">${escapeHtml(profile.status_label)}</span>` : `<span class="badge badge-warn">未保存</span>`}
    </div>
    <div class="domain-model-form-grid">
      <label>模型名称<input data-domain-form id="domain-model-name" maxlength="128" value="${escapeHtml(draft.name)}" /></label>
      <label>站点<input id="domain-model-form-marketplace" value="${escapeHtml(draft.marketplace)}" disabled /></label>
      <label class="domain-model-form-wide">领域说明<textarea data-domain-form id="domain-model-description" maxlength="20000" rows="2">${escapeHtml(draft.description)}</textarea></label>
      <label>适用范围<select data-domain-form id="domain-model-scope-type" class="sel">
        ${Object.entries(domainModelCatalog.scope_types || {}).map(([code, label]) => `<option value="${code}"${code === draft.scopeType ? " selected" : ""}>${escapeHtml(label)}</option>`).join("")}
      </select></label>
      <label>Amazon 类目范围<input data-domain-form id="domain-model-category" maxlength="512" value="${escapeHtml(draft.categoryScope)}" placeholder="例如 Toys & Games" /></label>
      <label>市场利基<select data-domain-form id="domain-model-niche" class="sel">${nicheOptions}</select></label>
      <label>通用父策略<select data-domain-form id="domain-model-base-strategy" class="sel">${strategyOptions}</select></label>
      <div class="domain-model-template-action"><button class="btn btn-sm" id="domain-model-apply-template" type="button">套用父策略权重</button></div>
    </div>
    <div class="domain-model-parameter-section">
      <div class="domain-model-section-head"><h3>机会轴权重</h3><span id="domain-opportunity-total"></span></div>
      <div class="domain-model-weight-grid">${opportunityInputs}</div>
    </div>
    <div class="domain-model-parameter-section">
      <div class="domain-model-section-head"><h3>风险轴权重</h3><span id="domain-risk-total"></span></div>
      <div class="domain-model-weight-grid">${riskInputs}</div>
    </div>
    <div class="domain-model-parameter-section">
      <div class="domain-model-section-head"><h3>专项分组合</h3><span id="domain-axis-total"></span></div>
      <div class="domain-model-weight-grid domain-model-axis-grid">
        <label>机会轴<input data-domain-form data-domain-axis="opportunity" type="number" min="0" max="100" step="0.1" value="${fmt.num(Number(config.axis_blend?.opportunity || 0) * 100, 1)}" /></label>
        <label>风险控制轴<input data-domain-form data-domain-axis="risk_control" type="number" min="0" max="100" step="0.1" value="${fmt.num(Number(config.axis_blend?.risk_control || 0) * 100, 1)}" /></label>
        <label>最低模型适用度<input data-domain-form id="domain-model-min-applicability" type="number" min="0" max="100" step="0.1" value="${fmt.num(config.minimum_applicability, 1)}" /></label>
        <label class="check-inline domain-model-trend-guard"><input data-domain-form id="domain-model-trend-guard" type="checkbox"${config.trend_priority_guard ? " checked" : ""} />趋势证据不足时阻止强建议</label>
      </div>
    </div>
    <details class="domain-model-policy">
      <summary>建议阈值</summary>
      <div class="domain-model-policy-grid">${policyInputs}</div>
    </details>
    <div class="domain-model-save-row">
      <label class="domain-model-change-note">版本说明<input data-domain-form id="domain-model-change-note" maxlength="1000" value="${escapeHtml(draft.changeNote)}" placeholder="说明本次领域判断变化" /></label>
      <div class="actions">
        ${profile ? `<button class="btn" id="domain-model-reset" type="button">撤销未保存修改</button>` : ""}
        <button class="btn btn-primary" id="domain-model-save" type="button"${archived ? " disabled" : ""}>${profile ? "保存修改" : "创建模型"}</button>
      </div>
    </div>
    ${profile ? renderDomainModelLifecycle(profile) : ""}
    ${profile ? renderDomainModelVersions(domainModelDetail.versions || [], profile.current_version_id, profile.status) : ""}`;
  bindDomainModelEditor();
  updateDomainModelWeightTotals();
  updateDomainModelScopeControls();
}

function domainModelWeightInputs(axis, labels, values) {
  return Object.entries(labels || {}).map(([key, label]) => `
    <label>${escapeHtml(label)}<input data-domain-form data-domain-weight-axis="${axis}" data-domain-weight="${escapeHtml(key)}" type="number" min="0" max="100" step="0.1" value="${fmt.num(Number(values?.[key] || 0) * 100, 1)}" /></label>`).join("");
}

function renderDomainModelLifecycle(profile) {
  const actions = [];
  if (profile.status !== "active") actions.push(`<button class="btn btn-sm" data-domain-status="active"${profile.status === "archived" ? " disabled" : ""}>启用当前版本</button>`);
  if (profile.status !== "draft") actions.push(`<button class="btn btn-sm" data-domain-status="draft">转为草稿</button>`);
  if (profile.status !== "archived") actions.push(`<button class="btn btn-sm" data-domain-status="archived">归档</button>`);
  return `<div class="domain-model-lifecycle"><div><b>模型状态</b><span>只有已启用模型出现在商品详情；新版本保存后自动转回草稿。</span></div><div class="actions">${actions.join("")}</div></div>`;
}

function renderDomainModelVersions(versions, currentVersionId, profileStatus) {
  const rows = versions.map((version) => `<tr>
    <td>v${fmt.int(version.version_no)} ${Number(version.id) === Number(currentVersionId) ? `<span class="badge badge-good">当前</span>` : ""}</td>
    <td>${escapeHtml(version.scope_label)}<div class="cell-sub">${escapeHtml(version.category_scope || version.niche_name || "站点通用")}</div></td>
    <td>${escapeHtml(version.source_type === "trained" ? "训练候选" : "人工参数")}</td>
    <td>${escapeHtml(version.change_note || "—")}</td>
    <td>${escapeHtml(fmt.text(version.created_at))}</td>
    <td>${Number(version.id) === Number(currentVersionId) ? "—" : `<button class="btn btn-sm" data-domain-version="${version.id}"${profileStatus === "archived" ? " disabled" : ""}>设为当前</button>`}</td>
  </tr>`).join("");
  return `<div class="domain-model-version-history"><div class="table-toolbar"><h3 style="margin:0">版本历史</h3><span class="result-meta">历史参数不会被改写</span></div>${wrapTable(`<table><thead><tr><th>版本</th><th>范围</th><th>来源</th><th>说明</th><th>创建时间</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table>`)}</div>`;
}

function bindDomainModelEditor() {
  document.querySelectorAll("[data-domain-form]").forEach((element) => {
    element.addEventListener("input", () => {
      persistDomainModelDraftFromForm();
      updateDomainModelWeightTotals();
      if (element.id === "domain-model-scope-type") updateDomainModelScopeControls();
    });
    element.addEventListener("change", () => {
      persistDomainModelDraftFromForm();
      if (element.id === "domain-model-scope-type") updateDomainModelScopeControls();
    });
  });
  document.getElementById("domain-model-save").onclick = saveDomainModel;
  const reset = document.getElementById("domain-model-reset");
  if (reset) reset.onclick = () => {
    domainModelState.draftJson = "";
    persistState(domainModelState);
    renderDomainModelEditor();
  };
  document.getElementById("domain-model-apply-template").onclick = applyDomainModelBaseTemplate;
  document.querySelectorAll("[data-domain-status]").forEach((button) => {
    button.onclick = () => changeDomainModelStatus(button.dataset.domainStatus);
  });
  document.querySelectorAll("[data-domain-version]").forEach((button) => {
    button.onclick = () => switchDomainModelVersion(button.dataset.domainVersion);
  });
}

function readDomainModelForm() {
  const number = (selector) => Number(document.querySelector(selector)?.value ?? 0);
  const weights = (axis) => Object.fromEntries([...document.querySelectorAll(`[data-domain-weight-axis="${axis}"]`)].map((input) => [input.dataset.domainWeight, Number(input.value) / 100]));
  const policy = Object.fromEntries([...document.querySelectorAll("[data-domain-policy]")].map((input) => [input.dataset.domainPolicy, Number(input.value)]));
  return {
    profileId: domainModelDetail?.profile ? String(domainModelDetail.profile.id) : "new",
    name: String(document.getElementById("domain-model-name")?.value || "").trim(),
    description: String(document.getElementById("domain-model-description")?.value || "").trim(),
    marketplace: String(document.getElementById("domain-model-form-marketplace")?.value || domainModelState.marketplace || "US").trim().toUpperCase(),
    scopeType: document.getElementById("domain-model-scope-type")?.value || "marketplace",
    categoryScope: String(document.getElementById("domain-model-category")?.value || "").trim(),
    nicheId: document.getElementById("domain-model-niche")?.value || "",
    changeNote: String(document.getElementById("domain-model-change-note")?.value || "").trim(),
    config: {
      base_strategy: document.getElementById("domain-model-base-strategy")?.value || "balanced",
      normalization_mode: "v2_global_signals",
      opportunity_weights: weights("opportunity"),
      risk_weights: weights("risk"),
      axis_blend: {
        opportunity: number("[data-domain-axis='opportunity']") / 100,
        risk_control: number("[data-domain-axis='risk_control']") / 100,
      },
      recommendation_policy: policy,
      minimum_applicability: number("#domain-model-min-applicability"),
      trend_priority_guard: Boolean(document.getElementById("domain-model-trend-guard")?.checked),
    },
  };
}

function persistDomainModelDraftFromForm() {
  if (!document.getElementById("domain-model-name")) return;
  const draft = readDomainModelForm();
  domainModelState.draftProfileId = draft.profileId;
  domainModelState.draftJson = JSON.stringify(draft);
  persistState(domainModelState);
}

function updateDomainModelWeightTotals() {
  const total = (selector) => [...document.querySelectorAll(selector)].reduce((sum, input) => sum + Number(input.value || 0), 0);
  [["#domain-opportunity-total", "[data-domain-weight-axis='opportunity']"], ["#domain-risk-total", "[data-domain-weight-axis='risk']"], ["#domain-axis-total", "[data-domain-axis]"]].forEach(([targetSelector, inputSelector]) => {
    const target = document.querySelector(targetSelector);
    if (!target) return;
    const value = total(inputSelector);
    target.textContent = `合计 ${fmt.num(value, 0)}%`;
    target.classList.toggle("warning-text", Math.abs(value - 100) > 0.1);
  });
}

function updateDomainModelScopeControls() {
  const scope = document.getElementById("domain-model-scope-type")?.value || "marketplace";
  const category = document.getElementById("domain-model-category");
  const niche = document.getElementById("domain-model-niche");
  if (category) category.disabled = scope !== "category" && scope !== "hybrid";
  if (niche) niche.disabled = scope !== "niche" && scope !== "hybrid";
}

function applyDomainModelBaseTemplate() {
  const code = document.getElementById("domain-model-base-strategy")?.value || "balanced";
  const template = (domainModelCatalog.base_strategies || []).find((item) => item.code === code);
  if (!template) return;
  Object.entries(template.opportunity_weights || {}).forEach(([key, value]) => {
    const input = document.querySelector(`[data-domain-weight-axis="opportunity"][data-domain-weight="${CSS.escape(key)}"]`);
    if (input) input.value = String(Math.round(Number(value) * 100));
  });
  Object.entries(template.risk_weights || {}).forEach(([key, value]) => {
    const input = document.querySelector(`[data-domain-weight-axis="risk"][data-domain-weight="${CSS.escape(key)}"]`);
    if (input) input.value = String(Math.round(Number(value) * 100));
  });
  const guard = document.getElementById("domain-model-trend-guard");
  if (guard) guard.checked = code === "trend";
  persistDomainModelDraftFromForm();
  updateDomainModelWeightTotals();
}

function domainModelVersionChanged(draft, current) {
  if (!current) return true;
  const candidate = {
    scope_type: draft.scopeType,
    category_scope: draft.scopeType === "category" || draft.scopeType === "hybrid" ? draft.categoryScope || null : null,
    niche_id: draft.scopeType === "niche" || draft.scopeType === "hybrid" ? Number(draft.nicheId || 0) || null : null,
    config: draft.config,
  };
  const existing = {
    scope_type: current.scope_type,
    category_scope: current.category_scope || null,
    niche_id: current.niche_id || null,
    config: current.config,
  };
  return stableDomainModelJson(candidate) !== stableDomainModelJson(existing);
}

function stableDomainModelJson(value) {
  if (Array.isArray(value)) return `[${value.map(stableDomainModelJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableDomainModelJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

async function saveDomainModel() {
  const button = document.getElementById("domain-model-save");
  const draft = readDomainModelForm();
  if (!draft.name) { notice("请填写模型名称", "bad"); return; }
  if (button) button.disabled = true;
  try {
    let result;
    if (!domainModelDetail?.profile) {
      result = await apiSend("/api/domain-models", "POST", {
        name: draft.name,
        marketplace: draft.marketplace,
        description: draft.description || null,
        scope_type: draft.scopeType,
        category_scope: draft.categoryScope || null,
        niche_id: Number(draft.nicheId || 0) || null,
        base_strategy: draft.config.base_strategy,
        config: draft.config,
        change_note: draft.changeNote || "创建领域模型",
      });
      domainModelState.selectedId = String(result.profile.id);
      notice("领域模型已创建为草稿", "ok");
    } else {
      const profile = domainModelDetail.profile;
      const metadataChanged = draft.name !== profile.name || draft.description !== String(profile.description || "");
      if (metadataChanged) {
        await apiSend(`/api/domain-models/${profile.id}`, "PATCH", { name: draft.name, description: draft.description });
      }
      if (domainModelVersionChanged(draft, profile.current_version)) {
        result = await apiSend(`/api/domain-models/${profile.id}/versions`, "POST", {
          scope_type: draft.scopeType,
          category_scope: draft.categoryScope || null,
          niche_id: Number(draft.nicheId || 0) || null,
          config: draft.config,
          change_note: draft.changeNote || "人工调整领域参数",
        });
        notice("新版本已保存，模型已转回草稿等待重新启用", "ok");
      } else {
        result = await api(`/api/domain-models/${profile.id}`);
        notice(metadataChanged ? "模型说明已更新" : "没有需要保存的参数变化", "ok");
      }
    }
    domainModelState.draftJson = "";
    domainModelState.draftProfileId = domainModelState.selectedId || "new";
    domainModelState.selectedVersionId = "";
    domainModelState.offset = 0;
    domainModelValidationPage = null;
    persistState(domainModelState);
    await loadDomainModelWorkspace();
  } catch (err) {
    notice(err.message || "领域模型保存失败", "bad");
  } finally {
    if (button && document.body.contains(button)) button.disabled = false;
  }
}

async function changeDomainModelStatus(status) {
  const profile = domainModelDetail?.profile;
  if (!profile) return;
  const label = status === "active" ? "启用" : status === "archived" ? "归档" : "转为草稿";
  if (!confirm(`确认${label}领域模型“${profile.name}”？`)) return;
  try {
    await apiSend(`/api/domain-models/${profile.id}/status`, "POST", { status });
    notice(`模型已${label}`, "ok");
    domainModelState.draftJson = "";
    domainModelValidationPage = null;
    domainModelState.offset = 0;
    persistState(domainModelState);
    await loadDomainModelWorkspace();
  } catch (err) {
    notice(err.message || "模型状态更新失败", "bad");
  }
}

async function switchDomainModelVersion(versionId) {
  const profile = domainModelDetail?.profile;
  if (!profile) return;
  if (!confirm(`确认将模型“${profile.name}”切换到所选历史版本？\n\n切换后模型会转回草稿，需重新启用。`)) return;
  try {
    await apiSend(`/api/domain-models/${profile.id}/current-version`, "POST", { version_id: Number(versionId) });
    notice("已切换历史版本，模型已转回草稿", "ok");
    domainModelState.draftJson = "";
    domainModelState.selectedVersionId = "";
    domainModelState.offset = 0;
    domainModelValidationPage = null;
    persistState(domainModelState);
    await loadDomainModelWorkspace();
  } catch (err) {
    notice(err.message || "版本切换失败", "bad");
  }
}

/* ---------- 视图：评分模型 V2 影子回放 ---------- */
async function viewScoringReplay() {
  scoringReplayCurrent = null;
  if (scoringReplayState.mode === "domain") {
    await viewDomainModels();
    return;
  }
  if (scoringReplayState.mode === "calibration") {
    await viewScoringCalibration();
    return;
  }
  content.innerHTML = `
    <div class="panel score-replay-head">
      <div class="table-toolbar">
        <div>
          <h2 style="margin:0">评分模型回放</h2>
          <div class="result-meta">机会、风险、置信度三轴 · 与旧综合分并列审计</div>
        </div>
        <span class="badge badge-warn">只读影子模型</span>
      </div>
      ${renderScoringModeTabs("replay")}
      <div class="score-replay-filters">
        <label class="score-replay-search">关键词 / ASIN / 标题<input id="score-replay-keyword" placeholder="留空查看全部当前批次" /></label>
        <label>站点<input id="score-replay-marketplace" maxlength="8" /></label>
        <label>策略模板<select id="score-replay-strategy" class="sel">
          <option value="balanced">均衡研究</option>
          <option value="low_budget">低资金</option>
          <option value="differentiation">差异化</option>
          <option value="trend">趋势型</option>
        </select></label>
        <label>影子建议<select id="score-replay-recommendation" class="sel">
          <option value="all">全部建议</option>
          <option value="priority_validate">优先人工验证</option>
          <option value="observe">可进入观察池</option>
          <option value="benchmark_only">仅适合作为对标</option>
          <option value="pause">暂缓</option>
        </select></label>
        <label>最低置信度<input id="score-replay-min-confidence" type="number" min="0" max="100" placeholder="0-100" /></label>
        <label>最高风险分<input id="score-replay-max-risk" type="number" min="0" max="100" placeholder="0-100" /></label>
        <label>排序<select id="score-replay-sort" class="sel">
          <option value="opportunity_score">机会分</option>
          <option value="risk_score">风险分</option>
          <option value="confidence_score">置信度</option>
          <option value="legacy_total_score">旧综合分</option>
          <option value="monthly_bought">近月购买量</option>
          <option value="review_count">评论数</option>
        </select></label>
        <label>方向<select id="score-replay-dir" class="sel"><option value="desc">从高到低</option><option value="asc">从低到高</option></select></label>
        <div class="score-replay-filter-actions">
          <button class="btn" id="score-replay-apply">回放</button>
          <button class="btn" id="score-replay-reset">重置</button>
        </div>
      </div>
      <div class="niche-evidence-note score-replay-boundary">
        当前结果按每个关键词最新完整采集批次计算，不写入生产评分。近月购买量是下界代理，自然序位是页面估算；利润、评论痛点、供应链与合规仍需人工验证。
      </div>
    </div>
    <div id="score-replay-summary"></div>
    <div class="panel score-replay-results">
      <div class="table-toolbar"><h2 style="margin:0">回放结果</h2><span id="score-replay-meta" class="result-meta"></span></div>
      <div id="score-replay-table"><div class="state"><div class="spinner"></div>计算影子评分…</div></div>
      <div id="score-replay-pager"></div>
    </div>
    <div id="score-replay-detail"></div>`;

  bindScoringModeTabs();
  document.getElementById("score-replay-keyword").value = scoringReplayState.keyword;
  document.getElementById("score-replay-marketplace").value = scoringReplayState.marketplace;
  document.getElementById("score-replay-strategy").value = scoringReplayState.strategy;
  document.getElementById("score-replay-recommendation").value = scoringReplayState.recommendation;
  document.getElementById("score-replay-min-confidence").value = scoringReplayState.minConfidence;
  document.getElementById("score-replay-max-risk").value = scoringReplayState.maxRisk;
  document.getElementById("score-replay-sort").value = scoringReplayState.sortBy;
  document.getElementById("score-replay-dir").value = scoringReplayState.sortDir;
  document.getElementById("score-replay-apply").onclick = () => loadScoringReplay(true);
  document.getElementById("score-replay-reset").onclick = () => {
    Object.assign(scoringReplayState, {
      limit: 50,
      offset: 0,
      marketplace: "US",
      keyword: "",
      strategy: "balanced",
      recommendation: "all",
      minConfidence: "",
      maxRisk: "",
      sortBy: "opportunity_score",
      sortDir: "desc",
      selectedKey: "",
    });
    persistState(scoringReplayState);
    viewScoringReplay();
  };
  content.querySelectorAll(".score-replay-filters input, .score-replay-filters select").forEach((element) => {
    element.addEventListener("keydown", (event) => { if (event.key === "Enter") loadScoringReplay(true); });
  });
  await loadScoringReplay();
}

function renderScoringModeTabs(active) {
  return `<div class="score-replay-tabs" role="tablist" aria-label="评分工作区">
    <button class="btn btn-sm${active === "replay" ? " is-active" : ""}" data-score-mode="replay" role="tab" aria-selected="${active === "replay"}">回放列表</button>
    <button class="btn btn-sm${active === "calibration" ? " is-active" : ""}" data-score-mode="calibration" role="tab" aria-selected="${active === "calibration"}">校准抽样</button>
    <button class="btn btn-sm${active === "domain" ? " is-active" : ""}" data-score-mode="domain" role="tab" aria-selected="${active === "domain"}">领域模型</button>
  </div>`;
}

function bindScoringModeTabs() {
  document.querySelectorAll("[data-score-mode]").forEach((button) => {
    button.onclick = () => {
      const requested = button.dataset.scoreMode;
      const mode = requested === "calibration" || requested === "domain" ? requested : "replay";
      if (mode === scoringReplayState.mode) return;
      scoringReplayState.mode = mode;
      persistState(scoringReplayState);
      viewScoringReplay();
    };
  });
}

async function viewScoringCalibration() {
  scoringCalibrationCurrent = null;
  content.innerHTML = `
    <div class="panel score-replay-head">
      <div class="table-toolbar">
        <div>
          <h2 style="margin:0">评分模型校准抽样</h2>
          <div class="result-meta">随机分层 · 批次可复现 · 已复核商品优先排除</div>
        </div>
        <span class="badge badge-warn">只读校准</span>
      </div>
      ${renderScoringModeTabs("calibration")}
      <div class="score-calibration-filters">
        <label class="score-replay-search">关键词 / ASIN / 标题<input id="score-calibration-keyword" placeholder="留空抽样全部当前批次" /></label>
        <label>站点<input id="score-calibration-marketplace" maxlength="8" /></label>
        <label>分层主策略<select id="score-calibration-strategy" class="sel">
          <option value="balanced">均衡研究</option>
          <option value="low_budget">低资金</option>
          <option value="differentiation">差异化</option>
          <option value="trend">趋势型</option>
        </select></label>
        <label>每层样本数<select id="score-calibration-count" class="sel">
          <option value="1">1</option><option value="2">2</option><option value="3">3</option>
          <option value="4">4</option><option value="5">5</option>
        </select></label>
        <label>导出范围<select id="score-calibration-export-scope" class="sel">
          <option value="reviewed">仅已复核（推荐）</option>
          <option value="all">全部样本（审计）</option>
        </select></label>
        <div class="score-calibration-actions">
          <label class="score-calibration-history">历史批次<select id="score-calibration-history" class="sel" aria-label="选择历史校准批次"></select></label>
          <button class="btn" id="score-calibration-replay-batch">复现批次</button>
          <button class="btn" id="score-calibration-load">换一批样本</button>
          <button class="btn" id="score-calibration-export">导出 JSON</button>
          <button class="btn" id="score-calibration-clear">清空复核</button>
        </div>
      </div>
      <div class="score-calibration-session score-replay-boundary">
        <div id="score-calibration-export-status" class="score-calibration-export-status"></div>
        <details class="score-calibration-boundary">
          <summary>导出位置与模型边界</summary>
          <p>审计标记只表示优先人工检查；全部样本仅供审计。导出不会写 MySQL、修改权重或切换生产评分。</p>
          <div id="score-calibration-export-location"></div>
        </details>
      </div>
    </div>
    <div id="score-calibration-summary"></div>
    <div class="panel score-calibration-results">
      <div class="table-toolbar score-calibration-workspace-head">
        <div class="score-calibration-view-tabs" role="tablist" aria-label="校准记录视图">
          <button class="btn btn-sm" data-calibration-view="current" role="tab">当前批次</button>
          <button class="btn btn-sm" data-calibration-view="reviewed" role="tab">已复核记录 <span id="score-calibration-archive-count">0</span></button>
        </div>
        <span id="score-calibration-meta" class="result-meta"></span>
      </div>
      <div id="score-calibration-current-view">
        <div id="score-calibration-buckets"></div>
        <div id="score-calibration-table"><div class="state"><div class="spinner"></div>生成校准样本…</div></div>
      </div>
      <div id="score-calibration-reviewed-view" hidden>
        <div id="score-calibration-archive-note" class="score-calibration-archive-note"></div>
        <div id="score-calibration-archive-table"><div class="state"><div class="spinner"></div>读取已导出复核记录…</div></div>
      </div>
    </div>
    <div id="score-replay-detail"></div>`;

  bindScoringModeTabs();
  bindScoringCalibrationViewTabs();
  document.getElementById("score-calibration-keyword").value = scoringCalibrationState.keyword;
  document.getElementById("score-calibration-marketplace").value = scoringCalibrationState.marketplace;
  document.getElementById("score-calibration-strategy").value = scoringCalibrationState.strategy;
  document.getElementById("score-calibration-count").value = String(scoringCalibrationState.samplePerBucket);
  document.getElementById("score-calibration-export-scope").value = scoringCalibrationState.exportScope === "all" ? "all" : "reviewed";
  document.getElementById("score-calibration-load").onclick = generateNextScoringCalibrationBatch;
  const replayBatchButton = document.getElementById("score-calibration-replay-batch");
  replayBatchButton.onclick = replaySelectedScoringCalibrationBatch;
  replayBatchButton.onkeydown = (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    replaySelectedScoringCalibrationBatch();
  };
  const historySelect = document.getElementById("score-calibration-history");
  historySelect.onchange = (event) => {
    scoringCalibrationState.selectedBatchId = event.target.value || "";
    persistState(scoringCalibrationState);
    renderScoringCalibrationBatchHistory();
  };
  historySelect.onkeydown = (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    replaySelectedScoringCalibrationBatch();
  };
  document.getElementById("score-calibration-export").onclick = exportScoringCalibration;
  document.getElementById("score-calibration-clear").onclick = clearScoringCalibrationReviews;
  document.getElementById("score-calibration-export-scope").onchange = (event) => {
    scoringCalibrationState.exportScope = event.target.value === "all" ? "all" : "reviewed";
    persistState(scoringCalibrationState);
    renderScoringCalibrationExportStatus();
  };
  document.querySelectorAll(".score-calibration-filters input, .score-calibration-filters select:not(#score-calibration-history)").forEach((element) => {
    element.addEventListener("keydown", (event) => { if (event.key === "Enter") loadScoringCalibration(); });
  });
  renderScoringCalibrationBatchHistory();
  renderScoringCalibrationWorkspaceView();
  renderScoringCalibrationExportStatus();
  await Promise.all([
    loadScoringCalibration(),
    probeScoringCalibrationExportApi(),
    loadScoringCalibrationExportedReviews(),
  ]);
}

async function probeScoringCalibrationExportApi() {
  try {
    const response = await fetch("/api/scoring-v2/calibration/export", { method: "GET", cache: "no-store" });
    let payload = null;
    try { payload = await response.json(); } catch (_) { /* 旧进程可能返回非 JSON 404 */ }
    scoringCalibrationExportApiAvailable = Boolean(
      response.ok
      && payload?.ok
      && payload?.data?.available
      && payload?.data?.schema_version === "scoring-calibration-dataset-v2"
    );
    scoringCalibrationExportDirectory = scoringCalibrationExportApiAvailable ? String(payload.data.directory || "") : "";
  } catch (_) {
    scoringCalibrationExportApiAvailable = null;
    scoringCalibrationExportDirectory = "";
  }
  renderScoringCalibrationExportStatus();
}

function scoringCalibrationBatchHistory() {
  try {
    const parsed = JSON.parse(scoringCalibrationState.batchHistoryJson || "[]");
    return Array.isArray(parsed) ? parsed.filter((item) => item && item.id && item.seed).slice(0, 30) : [];
  } catch (_) {
    return [];
  }
}

function scoringCalibrationBatchId(batch) {
  const source = JSON.stringify([
    Number(batch.batchNumber || 1),
    String(batch.seed || "baseline"),
    String(batch.marketplace || "US"),
    String(batch.keyword || ""),
    String(batch.strategy || "balanced"),
    Number(batch.samplePerBucket || 2),
    [...(batch.excludedAsins || [])].sort(),
  ]);
  let hash = 2166136261;
  for (let index = 0; index < source.length; index += 1) {
    hash ^= source.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return `batch-${(hash >>> 0).toString(16).padStart(8, "0")}`;
}

function recordScoringCalibrationBatch(report) {
  const batch = {
    batchNumber: Math.max(1, Number(scoringCalibrationState.batchNumber || 1)),
    seed: String(report?.sampling?.seed || scoringCalibrationState.sampleSeed || "baseline"),
    marketplace: scoringCalibrationState.marketplace || "US",
    keyword: scoringCalibrationState.keyword || "",
    strategy: scoringCalibrationState.strategy || "balanced",
    samplePerBucket: Math.max(1, Number(scoringCalibrationState.samplePerBucket || 2)),
    excludedAsins: scoringCalibrationBatchExcludedAsins(),
    generatedAt: report?.generated_at || new Date().toISOString(),
    recordedAt: new Date().toISOString(),
    sampleCount: Number(report?.summary?.sample_count || 0),
    bucketCount: Number(report?.summary?.bucket_count || 0),
    expectedSampleKeys: (report?.rows || []).map((row) => row.sample_key),
  };
  batch.id = scoringCalibrationBatchId(batch);
  const history = scoringCalibrationBatchHistory();
  const existingIndex = history.findIndex((item) => item.id === batch.id);
  if (existingIndex >= 0) {
    const existing = history.splice(existingIndex, 1)[0];
    history.unshift({ ...batch, ...existing, lastOpenedAt: new Date().toISOString() });
  } else {
    history.unshift(batch);
  }
  scoringCalibrationState.batchHistoryJson = JSON.stringify(history.slice(0, 30));
  scoringCalibrationState.selectedBatchId = batch.id;
  persistState(scoringCalibrationState);
  renderScoringCalibrationBatchHistory();
}

function renderScoringCalibrationBatchHistory() {
  const select = document.getElementById("score-calibration-history");
  const replayButton = document.getElementById("score-calibration-replay-batch");
  if (!select) return;
  const history = scoringCalibrationBatchHistory();
  select.replaceChildren();
  if (!history.length) {
    select.add(new Option("生成首批后可在此复现", ""));
    select.disabled = true;
    if (replayButton) replayButton.disabled = true;
    return;
  }
  select.disabled = false;
  history.forEach((batch) => {
    const seed = String(batch.seed || "baseline").slice(0, 8);
    const keyword = batch.keyword ? ` · ${truncate(batch.keyword, 16)}` : "";
    select.add(new Option(
      `第 ${Math.max(1, Number(batch.batchNumber || 1))} 批 · ${seed} · ${Number(batch.sampleCount || 0)} 样本 · ${batch.marketplace || "US"}${keyword}`,
      batch.id,
    ));
  });
  const selected = history.some((item) => item.id === scoringCalibrationState.selectedBatchId)
    ? scoringCalibrationState.selectedBatchId
    : history[0].id;
  select.value = selected;
  if (scoringCalibrationState.selectedBatchId !== selected) {
    scoringCalibrationState.selectedBatchId = selected;
    persistState(scoringCalibrationState);
  }
  if (replayButton) replayButton.disabled = false;
}

async function replaySelectedScoringCalibrationBatch() {
  const selectedId = document.getElementById("score-calibration-history")?.value || scoringCalibrationState.selectedBatchId;
  const batch = scoringCalibrationBatchHistory().find((item) => item.id === selectedId);
  if (!batch) return notice("请选择一个可复现的历史批次", "bad");

  scoringCalibrationState.marketplace = String(batch.marketplace || "US");
  scoringCalibrationState.keyword = String(batch.keyword || "");
  scoringCalibrationState.strategy = String(batch.strategy || "balanced");
  scoringCalibrationState.samplePerBucket = Math.max(1, Math.min(5, Number(batch.samplePerBucket || 2)));
  scoringCalibrationState.sampleSeed = String(batch.seed || "baseline");
  scoringCalibrationState.excludedAsinsJson = JSON.stringify(batch.excludedAsins || []);
  scoringCalibrationState.batchNumber = Math.max(1, Number(batch.batchNumber || 1));
  scoringCalibrationState.selectedBatchId = batch.id;
  scoringCalibrationState.selectedKey = "";
  scoringCalibrationState.activeView = "current";
  scoringCalibrationReplayExpectation = batch;
  persistState(scoringCalibrationState);

  document.getElementById("score-calibration-keyword").value = scoringCalibrationState.keyword;
  document.getElementById("score-calibration-marketplace").value = scoringCalibrationState.marketplace;
  document.getElementById("score-calibration-strategy").value = scoringCalibrationState.strategy;
  document.getElementById("score-calibration-count").value = String(scoringCalibrationState.samplePerBucket);
  renderScoringCalibrationWorkspaceView();
  await loadScoringCalibration();
}

async function loadScoringCalibrationExportedReviews() {
  try {
    const archive = await api("/api/scoring-v2/calibration/reviews?limit=500");
    scoringCalibrationExportedReviews = Array.isArray(archive.rows) ? archive.rows : [];
    scoringCalibrationReviewArchiveWarnings = Array.isArray(archive.warnings) ? archive.warnings : [];
    scoringCalibrationReviewArchiveFileCount = Number(archive.file_count || 0);
    scoringCalibrationReviewArchiveServerTotal = Number(archive.total || scoringCalibrationExportedReviews.length);
  } catch (error) {
    scoringCalibrationExportedReviews = [];
    scoringCalibrationReviewArchiveWarnings = [`已导出复核记录读取失败：${error.message}`];
    scoringCalibrationReviewArchiveFileCount = 0;
    scoringCalibrationReviewArchiveServerTotal = 0;
  }
  renderScoringCalibrationWorkspaceView();
  renderScoringCalibrationExportStatus();
}

function scoringCalibrationLocalReviewArchiveRows() {
  const reviews = scoringCalibrationReviews();
  return scoringCalibrationReviewedEntries(reviews).flatMap(([sampleKey, review]) => {
    const snapshot = review?.sample_snapshot;
    if (!snapshot) return [];
    return [{
      ...snapshot,
      sample_key: sampleKey,
      human_label: {
        judgment: review.judgment,
        note: review.note || "",
        reviewed_at: review.updated_at || snapshot.human_label?.reviewed_at || null,
      },
      archive_source: "browser_draft",
      source_file: "",
      exported_at: "",
      selection: {
        marketplace: review.marketplace || scoringCalibrationState.marketplace || "US",
        primary_strategy: review.strategy || "balanced",
        batch_number: review.batch_number || null,
      },
    }];
  });
}

function scoringCalibrationArchiveTimestamp(item) {
  const value = item?.human_label?.reviewed_at || item?.exported_at || "";
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function scoringCalibrationReviewArchive() {
  const merged = new Map();
  scoringCalibrationExportedReviews.forEach((item) => {
    if (item?.sample_key) merged.set(item.sample_key, item);
  });
  scoringCalibrationLocalReviewArchiveRows().forEach((item) => {
    const existing = merged.get(item.sample_key);
    if (!existing || scoringCalibrationArchiveTimestamp(item) >= scoringCalibrationArchiveTimestamp(existing)) {
      merged.set(item.sample_key, item);
    }
  });
  return [...merged.values()].sort((left, right) => scoringCalibrationArchiveTimestamp(right) - scoringCalibrationArchiveTimestamp(left));
}

function scoringCalibrationAllReviewedAsins() {
  const seen = new Set(scoringCalibrationReviewedAsins());
  scoringCalibrationExportedReviews.forEach((item) => {
    const asin = String(item?.asin || "").trim().toUpperCase();
    if (/^[A-Z0-9]{10}$/.test(asin)) seen.add(asin);
  });
  return [...seen].slice(0, 300);
}

const SCORING_CALIBRATION_JUDGMENT_LABELS = {
  pending: "未复核",
  reasonable: "结论合理",
  too_optimistic: "过于乐观",
  too_conservative: "过于保守",
  evidence_issue: "证据异常",
  needs_collection: "需要补采",
};

const SCORING_CALIBRATION_RECOMMENDATION_LABELS = {
  priority_validate: "优先人工验证",
  observe: "可进入观察池",
  benchmark_only: "仅适合作为对标",
  pause: "暂缓",
};

function bindScoringCalibrationViewTabs() {
  document.querySelectorAll("[data-calibration-view]").forEach((button) => {
    const activate = () => {
      scoringCalibrationState.activeView = button.dataset.calibrationView === "reviewed" ? "reviewed" : "current";
      persistState(scoringCalibrationState);
      renderScoringCalibrationWorkspaceView();
    };
    button.onclick = activate;
    button.onkeydown = (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      activate();
    };
  });
}

function renderScoringCalibrationWorkspaceView() {
  const activeView = scoringCalibrationState.activeView === "reviewed" ? "reviewed" : "current";
  const summary = document.getElementById("score-calibration-summary");
  const currentView = document.getElementById("score-calibration-current-view");
  const reviewedView = document.getElementById("score-calibration-reviewed-view");
  const meta = document.getElementById("score-calibration-meta");
  const archive = scoringCalibrationReviewArchive();
  const archiveTotal = Math.max(archive.length, scoringCalibrationReviewArchiveServerTotal);
  const count = document.getElementById("score-calibration-archive-count");
  if (count) count.textContent = fmt.int(archiveTotal);

  document.querySelectorAll("[data-calibration-view]").forEach((button) => {
    const active = button.dataset.calibrationView === activeView;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", String(active));
  });
  if (summary) summary.hidden = activeView !== "current";
  if (currentView) currentView.hidden = activeView !== "current";
  if (reviewedView) reviewedView.hidden = activeView !== "reviewed";
  renderScoringCalibrationReviewArchive(archive, { renderDetail: activeView === "reviewed" });

  if (activeView === "reviewed") {
    if (meta) meta.textContent = `${fmt.int(archiveTotal)} 条已复核 · ${fmt.int(scoringCalibrationReviewArchiveFileCount)} 个本地导出文件`;
    return;
  }

  const report = scoringCalibrationCurrent || {};
  if (meta) {
    meta.textContent = report?.rows
      ? `第 ${fmt.int(scoringCalibrationState.batchNumber)} 批 · ${fmt.int(report.summary?.sample_count)} 个样本 · ${fmt.int(report.summary?.bucket_count)} 个分层 · ${report.strategy?.label || "均衡研究"}`
      : "正在生成当前批次…";
  }
  const rows = report.rows || [];
  const selected = rows.find((row) => row.sample_key === scoringCalibrationState.selectedKey) || rows[0] || null;
  renderScoringReplayDetail(selected);
}

function formatScoringCalibrationDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("zh-CN", { hour12: false });
}

function scoringCalibrationRecommendationLabel(code) {
  return scoringCalibrationCurrent?.model?.recommendations?.[code]
    || SCORING_CALIBRATION_RECOMMENDATION_LABELS[code]
    || code
    || "—";
}

function renderScoringCalibrationReviewArchive(rows = scoringCalibrationReviewArchive(), { renderDetail = true } = {}) {
  const target = document.getElementById("score-calibration-archive-table");
  const note = document.getElementById("score-calibration-archive-note");
  if (!target) return;
  if (note) {
    const warningDetails = scoringCalibrationReviewArchiveWarnings.length
      ? `<details><summary>${fmt.int(scoringCalibrationReviewArchiveWarnings.length)} 个文件读取提示</summary><ul>${scoringCalibrationReviewArchiveWarnings.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></details>`
      : "";
    note.innerHTML = `<span>已合并固定导出目录与本机浏览器草稿；同一样本仅显示最新一次人工判断。</span>${warningDetails}`;
  }
  if (!rows.length) {
    target.innerHTML = `<div class="state">暂无已复核记录。完成一条人工判断后会先保存为本机草稿，导出后仍可在这里回看。</div>`;
    if (renderDetail) renderScoringCalibrationArchiveDetail(null);
    return;
  }

  if (!rows.some((row) => row.sample_key === scoringCalibrationState.selectedReviewKey)) {
    scoringCalibrationState.selectedReviewKey = rows[0].sample_key;
    persistState(scoringCalibrationState);
  }
  const body = rows.map((row) => {
    const selection = row.selection || {};
    const product = { ...row, marketplace: selection.marketplace || "US" };
    const human = row.human_label || {};
    const output = row.primary_output || {};
    const selected = row.sample_key === scoringCalibrationState.selectedReviewKey ? " class=\"score-replay-selected\"" : "";
    const sourceLabel = row.archive_source === "exported_json" ? "已导出 JSON" : "本机草稿";
    const sourceClass = row.archive_source === "exported_json" ? "badge-good" : "badge-warn";
    const batchLabel = selection.batch_number ? `第 ${fmt.int(selection.batch_number)} 批` : "历史批次";
    const stratumParts = String(row.stratum?.label || "").split("·");
    const confidenceLabel = stratumParts.at(-1)?.trim() || "置信层未知";
    return `<tr${selected}>
      <td>${amazonProductLink(product, displayTitle(row, row.asin), 46)}<div class="cell-sub">${amazonProductLink(product, row.asin)} · ${escapeHtml(row.keyword?.text || "—")}</div></td>
      <td><span class="badge badge-warn">${escapeHtml(SCORING_CALIBRATION_JUDGMENT_LABELS[human.judgment] || human.judgment || "—")}</span><div class="cell-sub">${escapeHtml(truncate(human.note || "无复核备注", 50))}</div></td>
      <td>${scoreReplayRecommendationBadge(output.recommendation_code, scoringCalibrationRecommendationLabel(output.recommendation_code))}<div class="cell-sub">分层 · ${escapeHtml(confidenceLabel)}</div></td>
      <td><div class="score-calibration-axes">${scoreReplayAxisBadge(output.opportunity_score, "opportunity")}${scoreReplayAxisBadge(output.risk_score, "risk")}${scoreReplayAxisBadge(output.confidence_score, "confidence")}</div><div class="cell-sub">机会 / 风险 / 置信</div></td>
      <td><span class="badge ${sourceClass}" title="${escapeHtml(row.source_file || "尚未导出")}">${sourceLabel}</span><div class="cell-sub">${escapeHtml(batchLabel)} · ${escapeHtml(String(row.sample_seed || "baseline").slice(0, 10))}</div><div class="cell-sub">${escapeHtml(formatScoringCalibrationDate(human.reviewed_at || row.exported_at))}</div></td>
      <td><button class="btn btn-sm" data-calibration-archive-detail="${escapeHtml(row.sample_key)}">查看</button></td>
    </tr>`;
  }).join("");
  target.innerHTML = wrapTable(`<table class="score-replay-table score-calibration-archive-table"><thead><tr>
    <th>商品与关键词</th><th>人工判断</th><th>模型结论</th><th>三轴分数</th><th>批次与来源</th><th>详情</th>
  </tr></thead><tbody>${body}</tbody></table>`);

  const byKey = new Map(rows.map((row) => [row.sample_key, row]));
  if (renderDetail) renderScoringCalibrationArchiveDetail(byKey.get(scoringCalibrationState.selectedReviewKey) || rows[0]);
  document.querySelectorAll("[data-calibration-archive-detail]").forEach((button) => {
    const activate = () => {
      const row = byKey.get(button.dataset.calibrationArchiveDetail);
      if (!row) return;
      scoringCalibrationState.selectedReviewKey = row.sample_key;
      persistState(scoringCalibrationState);
      renderScoringCalibrationArchiveDetail(row);
      document.querySelectorAll(".score-calibration-archive-table tbody tr").forEach((tr) => tr.classList.remove("score-replay-selected"));
      button.closest("tr")?.classList.add("score-replay-selected");
      document.getElementById("score-replay-detail")?.scrollIntoView({ behavior: "smooth", block: "start" });
    };
    button.onclick = activate;
    button.onkeydown = (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      activate();
    };
  });
}

function renderScoringCalibrationArchivedComponents(group, title, type) {
  const labels = {
    demand: "需求", growth: "增长", acceptance: "评分质量", visibility: "可见度", improvement_space: "改进空间",
    review_barrier: "评论壁垒", incumbent_pressure: "头部压力", quality_risk: "质量风险", price_risk: "价格风险", promo_risk: "促销风险",
    field_completeness: "字段完整", trend_evidence: "趋势证据", rank_reliability: "排名可信", freshness: "新鲜度", traceability: "可追溯性",
  };
  const rows = Object.entries(group || {}).map(([code, item]) => {
    const score = item?.score == null ? null : Number(item.score);
    const width = Number.isFinite(score) ? Math.max(0, Math.min(100, score)) : 0;
    return `<div class="score-component-row">
      <div class="score-component-head"><span>${escapeHtml(labels[code] || code)}</span><b>${Number.isFinite(score) ? score.toFixed(0) : "—"}</b></div>
      <div class="score-component-bar score-component-${type}"><i style="width:${width}%"></i></div>
      <small>权重 ${item?.weight == null ? "—" : escapeHtml(item.weight)} · ${escapeHtml(scoreComponentStatusLabel(item?.status))}</small>
    </div>`;
  }).join("");
  return `<section class="score-axis-column"><div class="score-axis-head"><h3>${escapeHtml(title)}</h3></div>${rows || `<div class="state">暂无分项。</div>`}</section>`;
}

function renderScoringCalibrationArchiveDetail(row) {
  const target = document.getElementById("score-replay-detail");
  if (!target) return;
  if (!row) {
    target.innerHTML = `<div class="panel"><div class="state">选择一条已复核记录查看模型输入与评分拆解。</div></div>`;
    return;
  }
  const selection = row.selection || {};
  const product = { ...row, marketplace: selection.marketplace || "US" };
  const human = row.human_label || {};
  const output = row.primary_output || {};
  const inputs = row.model_inputs || {};
  const trend = row.trend_features || {};
  const components = row.component_scores || {};
  const evidence = row.evidence_ids || {};
  const sourceLabel = row.archive_source === "exported_json" ? `已导出：${row.source_file || "本地 JSON"}` : "本机浏览器草稿（尚未或已再次修改）";
  target.innerHTML = `<div class="panel score-replay-detail">
    <div class="table-toolbar score-replay-detail-head">
      <div><h2>${amazonProductLink(product, displayTitle(row, row.asin), 96)}</h2><div class="result-meta">${amazonProductLink(product, row.asin)} · ${escapeHtml(row.keyword?.text || "—")} · ${escapeHtml(row.stratum?.label || "—")}</div></div>
      <span class="badge badge-warn">${escapeHtml(SCORING_CALIBRATION_JUDGMENT_LABELS[human.judgment] || human.judgment || "—")}</span>
    </div>
    <div class="score-replay-summary">
      <div><span>人工判断</span><b>${escapeHtml(SCORING_CALIBRATION_JUDGMENT_LABELS[human.judgment] || human.judgment || "—")}</b><small>${escapeHtml(human.note || "无复核备注")}</small></div>
      <div><span>模型建议</span><b>${scoreReplayRecommendationBadge(output.recommendation_code, scoringCalibrationRecommendationLabel(output.recommendation_code))}</b><small>${escapeHtml(selection.primary_strategy || "balanced")}</small></div>
      <div><span>机会分</span><b>${fmt.num(output.opportunity_score, 1)}</b><small>越高代表验证价值越高</small></div>
      <div><span>风险分</span><b>${fmt.num(output.risk_score, 1)}</b><small>越高代表进入风险越高</small></div>
      <div><span>置信分</span><b>${fmt.num(output.confidence_score, 1)}</b><small>证据覆盖与质量</small></div>
      <div><span>复核时间</span><b>${escapeHtml(formatScoringCalibrationDate(human.reviewed_at || row.exported_at))}</b><small>${escapeHtml(sourceLabel)}</small></div>
    </div>
    <div class="score-source-grid">
      <div><span>价格</span><b>${fmt.money(inputs.price)}</b></div><div><span>评分 / 评论数</span><b>${fmt.num(inputs.rating, 1)} / ${fmt.int(inputs.review_count)}</b></div><div><span>近期购买量</span><b>${fmt.int(inputs.monthly_bought)}</b></div>
      <div><span>自然排名</span><b>${fmt.int(inputs.organic_rank)}</b></div><div><span>排名置信</span><b>${escapeHtml(inputs.rank_confidence || "—")}</b></div><div><span>促销</span><b>${inputs.is_deal == null ? "—" : inputs.is_deal ? "是" : "否"}</b></div>
      <div><span>趋势样本 / 跨度</span><b>${fmt.int(trend.sample_size)} / ${fmt.num(trend.span_days, 0)} 天</b></div><div><span>趋势增长 / 置信</span><b>${fmt.num(trend.growth_score, 1)} / ${fmt.num(trend.confidence_score, 1)}</b></div><div><span>样本种子</span><b>${escapeHtml(row.sample_seed || "baseline")}</b></div>
    </div>
    <div class="score-axis-grid">
      ${renderScoringCalibrationArchivedComponents(components.opportunity, "机会分项", "opportunity")}
      ${renderScoringCalibrationArchivedComponents(components.risk, "风险分项", "risk")}
      ${renderScoringCalibrationArchivedComponents(components.confidence, "置信分项", "confidence")}
    </div>
    <div class="score-replay-explanation">
      <section><h3>证据标识</h3><p>商品快照 ${escapeHtml(evidence.product_snapshot_id || "—")} · 排名快照 ${escapeHtml(evidence.rank_snapshot_id || "—")}</p><p>历史快照 ${escapeHtml((evidence.history_snapshot_ids || []).join(", ") || "—")}</p><p>关键词排名快照 ${escapeHtml((evidence.keyword_rank_snapshot_ids || []).join(", ") || "—")}</p></section>
      <section><h3>保存边界</h3><p>${escapeHtml(sourceLabel)}</p><p>该记录仅用于人工校准与审计，不会自动修改评分权重或生产推荐。</p></section>
    </div>
  </div>`;
}

function renderScoringCalibrationExportStatus() {
  const target = document.getElementById("score-calibration-export-status");
  if (!target) return;
  const location = document.getElementById("score-calibration-export-location");
  const rows = scoringCalibrationCurrent?.rows || [];
  const reviews = scoringCalibrationReviews();
  const currentReviewedCount = rows.filter((row) => {
    const judgment = reviews[row.sample_key]?.judgment;
    return judgment && judgment !== "pending";
  }).length;
  const cumulativeReviewedCount = scoringCalibrationReviewArchive().length;
  const exportableReviewedCount = scoringCalibrationReviewedSamples(scoringCalibrationCurrent, reviews).length;
  const exportScope = scoringCalibrationState.exportScope === "all" ? "all" : "reviewed";
  const exportCount = exportScope === "reviewed" ? exportableReviewedCount : rows.length;
  const button = document.getElementById("score-calibration-export");
  if (button) {
    const saving = button.dataset.saving === "true";
    button.disabled = saving || exportCount === 0;
    button.textContent = saving ? "正在保存…" : exportScope === "reviewed" ? "导出校准标签" : "导出审计样本";
  }

  const scopeText = exportScope === "reviewed"
    ? `可导出 <b>${fmt.int(exportCount)}</b> 条累计校准标签`
    : `可导出当前批次 <b>${fmt.int(exportCount)}</b> 条审计样本，其中 ${fmt.int(currentReviewedCount)} 条已复核`;
  const sampling = scoringCalibrationCurrent?.sampling || {};
  const seed = String(sampling.seed || scoringCalibrationState.sampleSeed || "baseline");
  const excludedCount = Number(sampling.excluded_asin_count || 0);
  target.innerHTML = `<div><span class="badge badge-dim">第 ${fmt.int(scoringCalibrationState.batchNumber)} 批</span>${scopeText}</div>
    <small>随机种子 ${escapeHtml(seed.slice(0, 12))} · 本批已排除 ${fmt.int(excludedCount)} 个复核商品 · 累计可回看 ${fmt.int(cumulativeReviewedCount)} 条</small>`;

  let locationText = "正在确认固定导出目录…";
  if (scoringCalibrationState.lastExportPath) {
    const suffix = scoringCalibrationState.lastExportAt ? ` · ${escapeHtml(scoringCalibrationState.lastExportAt)}` : "";
    locationText = `最近保存：<code>${escapeHtml(scoringCalibrationState.lastExportPath)}</code>${suffix}`;
  } else if (scoringCalibrationExportApiAvailable === false) {
    locationText = "当前桌面进程尚未加载固定目录接口；本次将使用系统保存窗口。";
  } else if (scoringCalibrationExportDirectory) {
    locationText = `固定目录：<code>${escapeHtml(scoringCalibrationExportDirectory)}</code>`;
  }
  if (location) location.innerHTML = locationText;
}

function scoringCalibrationReviews() {
  try {
    const parsed = JSON.parse(scoringCalibrationState.reviewJson || "{}");
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch (_) {
    return {};
  }
}

function scoringCalibrationReviewedEntries(reviews = scoringCalibrationReviews()) {
  return Object.entries(reviews)
    .filter(([, review]) => review?.judgment && review.judgment !== "pending")
    .sort((left, right) => String(right[1]?.updated_at || "").localeCompare(String(left[1]?.updated_at || "")));
}

function scoringCalibrationReviewedAsins(reviews = scoringCalibrationReviews()) {
  const seen = new Set();
  const asins = [];
  scoringCalibrationReviewedEntries(reviews).forEach(([, review]) => {
    const asin = String(review?.asin || "").trim().toUpperCase();
    if (/^[A-Z0-9]{10}$/.test(asin) && !seen.has(asin)) {
      seen.add(asin);
      asins.push(asin);
    }
  });
  return asins.slice(0, 300);
}

function scoringCalibrationBatchExcludedAsins() {
  try {
    const parsed = JSON.parse(scoringCalibrationState.excludedAsinsJson || "[]");
    return Array.isArray(parsed) ? parsed.filter((asin) => /^[A-Z0-9]{10}$/.test(String(asin))) : [];
  } catch (_) {
    return [];
  }
}

function createScoringCalibrationSeed() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
}

async function generateNextScoringCalibrationBatch() {
  scoringCalibrationState.sampleSeed = createScoringCalibrationSeed();
  scoringCalibrationState.excludedAsinsJson = JSON.stringify(scoringCalibrationAllReviewedAsins());
  scoringCalibrationState.batchNumber = Math.max(1, Number(scoringCalibrationState.batchNumber || 1) + 1);
  scoringCalibrationState.selectedBatchId = "";
  scoringCalibrationState.selectedKey = "";
  scoringCalibrationState.activeView = "current";
  persistState(scoringCalibrationState);
  renderScoringCalibrationWorkspaceView();
  await loadScoringCalibration();
}

function readScoringCalibrationFilters() {
  scoringCalibrationState.keyword = String(document.getElementById("score-calibration-keyword")?.value || "").trim();
  scoringCalibrationState.marketplace = String(document.getElementById("score-calibration-marketplace")?.value || "US").trim().toUpperCase() || "US";
  scoringCalibrationState.strategy = document.getElementById("score-calibration-strategy")?.value || "balanced";
  scoringCalibrationState.exportScope = document.getElementById("score-calibration-export-scope")?.value === "all" ? "all" : "reviewed";
  const count = Number(document.getElementById("score-calibration-count")?.value || 2);
  scoringCalibrationState.samplePerBucket = Math.max(1, Math.min(5, Number.isFinite(count) ? count : 2));
  persistState(scoringCalibrationState);
}

async function loadScoringCalibration() {
  readScoringCalibrationFilters();
  const table = document.getElementById("score-calibration-table");
  const meta = document.getElementById("score-calibration-meta");
  const summary = document.getElementById("score-calibration-summary");
  const buckets = document.getElementById("score-calibration-buckets");
  const detail = document.getElementById("score-replay-detail");
  if (!table) return;
  table.innerHTML = `<div class="state"><div class="spinner"></div>生成可复现的随机分层样本…</div>`;
  if (meta) meta.textContent = "";
  if (summary) summary.innerHTML = "";
  if (buckets) buckets.innerHTML = "";
  if (detail) detail.innerHTML = "";
  const query = new URLSearchParams({
    sample_per_bucket: String(scoringCalibrationState.samplePerBucket),
    marketplace: scoringCalibrationState.marketplace,
    keyword: scoringCalibrationState.keyword,
    strategy: scoringCalibrationState.strategy,
    sample_seed: scoringCalibrationState.sampleSeed || "baseline",
  });
  scoringCalibrationBatchExcludedAsins().forEach((asin) => query.append("exclude_asin", asin));
  try {
    scoringCalibrationCurrent = await api(`/api/scoring-v2/calibration?${query.toString()}`);
    const replayExpectation = scoringCalibrationReplayExpectation;
    scoringCalibrationReplayExpectation = null;
    hydrateScoringCalibrationReviewSnapshots(scoringCalibrationCurrent);
    const rows = scoringCalibrationCurrent.rows || [];
    if (!rows.some((row) => row.sample_key === scoringCalibrationState.selectedKey)) {
      scoringCalibrationState.selectedKey = rows[0]?.sample_key || "";
      persistState(scoringCalibrationState);
    }
    recordScoringCalibrationBatch(scoringCalibrationCurrent);
    renderScoringCalibrationData();
    if (replayExpectation) {
      const expectedKeys = Array.isArray(replayExpectation.expectedSampleKeys) ? replayExpectation.expectedSampleKeys : [];
      const actualKeys = rows.map((row) => row.sample_key);
      const exact = expectedKeys.length > 0
        && JSON.stringify([...new Set(expectedKeys)].sort()) === JSON.stringify([...new Set(actualKeys)].sort());
      notice(
        !expectedKeys.length
          ? `已按第 ${fmt.int(replayExpectation.batchNumber)} 批保存条件重新计算`
          : exact
          ? `已复现第 ${fmt.int(replayExpectation.batchNumber)} 批，样本与原批次一致`
          : `已按第 ${fmt.int(replayExpectation.batchNumber)} 批原条件重算，但底层数据已变化，样本与首次生成不完全一致`,
        exact || !expectedKeys.length ? "ok" : "bad",
      );
    }
    rememberAgentBusinessContext();
  } catch (err) {
    scoringCalibrationReplayExpectation = null;
    table.innerHTML = `<div class="state error">⚠ ${escapeHtml(err.message)}</div>`;
  }
}

function renderScoringCalibrationData() {
  const report = scoringCalibrationCurrent || {};
  const summaryBox = document.getElementById("score-calibration-summary");
  const bucketBox = document.getElementById("score-calibration-buckets");
  const table = document.getElementById("score-calibration-table");
  const meta = document.getElementById("score-calibration-meta");
  if (!table) return;
  if (summaryBox) summaryBox.innerHTML = renderScoringCalibrationSummary(report);
  if (bucketBox) bucketBox.innerHTML = renderScoringCalibrationBuckets(report.summary?.buckets || []);
  table.innerHTML = renderScoringCalibrationTable(report);
  if (meta) meta.textContent = `第 ${fmt.int(scoringCalibrationState.batchNumber)} 批 · ${fmt.int(report.summary?.sample_count)} 个样本 · ${fmt.int(report.summary?.bucket_count)} 个分层 · ${report.strategy?.label || "均衡研究"}`;
  bindScoringCalibrationRows(report.rows || []);
  renderScoringCalibrationWorkspaceView();
  renderScoringCalibrationExportStatus();
}

function renderScoringCalibrationSummary(report) {
  const summary = report.summary || {};
  const changes = summary.strategy_changes || {};
  const reviews = scoringCalibrationReviews();
  const rows = report.rows || [];
  const reviewed = rows.filter((row) => reviews[row.sample_key]?.judgment && reviews[row.sample_key].judgment !== "pending").length;
  const cumulativeReviewed = scoringCalibrationReviewArchive().length;
  const sourceContexts = summary.source_context_count ?? summary.context_count;
  const excludedCount = report.sampling?.excluded_asin_count || 0;
  return `<div class="score-replay-summary score-calibration-summary">
    <div><span>校准母集</span><b>${fmt.int(sourceContexts)}</b><small>可抽样 ${fmt.int(summary.context_count)} 个上下文 · 排除 ${fmt.int(excludedCount)} 商品</small></div>
    <div><span>分层样本</span><b>${fmt.int(summary.sample_count)}</b><small>${fmt.int(summary.bucket_count)} 个实际建议×置信层</small></div>
    <div><span>策略分歧</span><b>${fmt.int(summary.strategy_disagreement_count)}</b><small>至少两种策略建议不同</small></div>
    <div><span>多关键词商品</span><b>${fmt.int(summary.multi_context_asin_count)}</b><small>校准抽样已尽量跨层去重</small></div>
    <div><span>策略变化</span><b>${fmt.int(changes.balanced)} / ${fmt.int(changes.low_budget)} / ${fmt.int(changes.differentiation)} / ${fmt.int(changes.trend)}</b><small>均衡 / 低资金 / 差异化 / 趋势型</small></div>
    <div><span>本批复核</span><b id="score-calibration-reviewed">${fmt.int(reviewed)} / ${fmt.int(rows.length)}</b><small id="score-calibration-reviewed-total">累计可回看 ${fmt.int(cumulativeReviewed)} 条</small></div>
  </div>`;
}

function renderScoringCalibrationBuckets(rows) {
  if (!rows.length) return `<div class="state">当前范围没有可抽样分层。</div>`;
  return `<div class="score-calibration-buckets">${rows.map((row) => `
    <div><b>${escapeHtml(row.label)}</b><span>${fmt.int(row.context_count)} 个上下文 · ${fmt.int(row.unique_asin_count)} 个 ASIN · 抽 ${fmt.int(row.sample_count)}</span></div>
  `).join("")}</div>`;
}

function renderScoringCalibrationTable(report) {
  const rows = report.rows || [];
  if (!rows.length) return `<div class="state">当前范围没有可校准样本。</div>`;
  const reviews = scoringCalibrationReviews();
  const judgments = report.calibration?.judgments || [
    { code: "pending", label: "未复核" },
    { code: "reasonable", label: "结论合理" },
    { code: "too_optimistic", label: "过于乐观" },
    { code: "too_conservative", label: "过于保守" },
    { code: "evidence_issue", label: "证据异常" },
    { code: "needs_collection", label: "需要补采" },
  ];
  const body = rows.map((row) => {
    const review = reviews[row.sample_key] || {};
    const selected = row.sample_key === scoringCalibrationState.selectedKey ? " class=\"score-replay-selected\"" : "";
    const outcomes = Object.values(row.strategy_outcomes || {}).map((outcome) => `
      <div class="score-calibration-outcome"><span>${escapeHtml(outcome.strategy_label || "—")}</span>${scoreReplayRecommendationBadge(outcome.recommendation_code, outcome.recommendation_label)}</div>
    `).join("");
    const flags = (row.calibration_flags || []).map((flag) => {
      const cls = flag.severity === "attention" ? "badge-warn" : flag.severity === "limitation" ? "badge-dim" : "badge-dim";
      return `<span class="badge ${cls}" title="${escapeHtml(flag.reason || "")}">${escapeHtml(flag.label || flag.code)}</span>`;
    }).join("");
    const options = judgments.map((item) => `<option value="${escapeHtml(item.code)}"${(review.judgment || "pending") === item.code ? " selected" : ""}>${escapeHtml(item.label)}</option>`).join("");
    return `<tr${selected}>
      <td>${amazonProductLink(row, displayTitle(row, row.asin), 44)}<div class="cell-sub">${amazonProductLink(row, row.asin)} · ${escapeHtml(row.keyword || "—")}</div><div class="cell-sub">${escapeHtml(row.sample_bucket_label || "—")}</div></td>
      <td><div class="score-calibration-axes">${scoreReplayAxisBadge(row.opportunity_score, "opportunity")}${scoreReplayAxisBadge(row.risk_score, "risk")}${scoreReplayAxisBadge(row.confidence_score, "confidence")}</div><div class="cell-sub">机会 / 风险 / 置信</div></td>
      <td><div class="score-calibration-outcomes">${outcomes}</div></td>
      <td><div class="score-calibration-flags">${flags || `<span class="badge badge-dim">无额外标记</span>`}</div></td>
      <td><div class="score-calibration-review"><select class="sel" data-calibration-judgment="${escapeHtml(row.sample_key)}">${options}</select><input data-calibration-note="${escapeHtml(row.sample_key)}" value="${escapeHtml(review.note || "")}" placeholder="复核备注" /></div></td>
      <td><button class="btn btn-sm" data-calibration-detail="${escapeHtml(row.sample_key)}">拆解</button></td>
    </tr>`;
  }).join("");
  return wrapTable(`<table class="score-replay-table score-calibration-table"><thead><tr>
    <th>商品与分层</th><th>当前三轴</th><th>四策略建议</th><th>审计标记</th><th>人工复核</th><th>证据</th>
  </tr></thead><tbody>${body}</tbody></table>`);
}

function bindScoringCalibrationRows(rows) {
  const byKey = new Map(rows.map((row) => [row.sample_key, row]));
  const selected = byKey.get(scoringCalibrationState.selectedKey) || rows[0] || null;
  renderScoringReplayDetail(selected);
  document.querySelectorAll("[data-calibration-detail]").forEach((button) => {
    button.onclick = () => {
      const row = byKey.get(button.dataset.calibrationDetail);
      if (!row) return;
      scoringCalibrationState.selectedKey = row.sample_key;
      persistState(scoringCalibrationState);
      renderScoringReplayDetail(row);
      document.querySelectorAll(".score-calibration-table tbody tr").forEach((tr) => tr.classList.remove("score-replay-selected"));
      button.closest("tr")?.classList.add("score-replay-selected");
      document.getElementById("score-replay-detail")?.scrollIntoView({ behavior: "smooth", block: "start" });
      rememberAgentBusinessContext();
    };
  });
  document.querySelectorAll("[data-calibration-judgment]").forEach((select) => {
    select.onchange = () => updateScoringCalibrationReview(select.dataset.calibrationJudgment, byKey);
  });
  document.querySelectorAll("[data-calibration-note]").forEach((input) => {
    input.oninput = () => updateScoringCalibrationReview(input.dataset.calibrationNote, byKey);
  });
}

function compactScoringCalibrationComponents(items) {
  return Object.fromEntries((items || []).map((item) => [item.key, {
    score: item.score,
    weight: item.weight,
    status: item.status,
  }]));
}

function compactScoringCalibrationTrend(trend = {}) {
  return {
    sample_size: trend.sample_size,
    signal_point_count: trend.signal_point_count,
    span_days: trend.span_days,
    confidence_score: trend.confidence_score,
    growth_score: trend.growth_score,
    promotion_warning: Boolean(trend.promo_warning),
    metrics: Object.fromEntries((trend.metrics || []).map((item) => [item.key, {
      start: item.start,
      end: item.end,
      change_ratio: item.change_ratio,
      direction: item.direction,
    }])),
  };
}

function compactScoringCalibrationOutcomes(outcomes = {}) {
  return Object.fromEntries(Object.entries(outcomes).map(([code, item]) => [code, {
    opportunity_score: item.opportunity_score,
    risk_score: item.risk_score,
    confidence_score: item.confidence_score,
    recommendation_code: item.recommendation_code,
  }]));
}

function buildScoringCalibrationSample(row, review = {}, sampleSeed = "baseline") {
  const source = row.source_identity || {};
  return {
    sample_key: row.sample_key,
    sample_seed: sampleSeed || "baseline",
    product_id: row.product_id,
    asin: row.asin,
    title: row.title,
    keyword: { id: row.keyword_id, text: row.keyword },
    stratum: { code: row.sample_bucket, label: row.sample_bucket_label },
    observed_at: row.snapshot_at,
    human_label: {
      judgment: review.judgment || "pending",
      note: review.note || "",
      reviewed_at: review.updated_at || null,
    },
    primary_output: {
      opportunity_score: row.opportunity_score,
      risk_score: row.risk_score,
      confidence_score: row.confidence_score,
      legacy_total_score: row.legacy_total_score,
      recommendation_code: row.recommendation_code,
    },
    model_inputs: {
      price: row.price,
      rating: row.rating,
      review_count: row.review_count,
      monthly_bought: row.monthly_bought,
      organic_rank: row.organic_rank,
      rank_confidence: row.rank_confidence,
      is_deal: Boolean(row.is_deal),
    },
    trend_features: compactScoringCalibrationTrend(row.trend),
    component_scores: {
      opportunity: compactScoringCalibrationComponents(row.opportunity_components),
      risk: compactScoringCalibrationComponents(row.risk_components),
      confidence: compactScoringCalibrationComponents(row.confidence_components),
    },
    strategy_outputs: compactScoringCalibrationOutcomes(row.strategy_outcomes),
    audit_flag_codes: (row.calibration_flags || []).map((flag) => flag.code),
    evidence_ids: {
      rank_snapshot_id: source.rank_snapshot_id || null,
      product_snapshot_id: source.product_snapshot_id || null,
      history_snapshot_ids: source.history_snapshot_ids || [],
      keyword_rank_snapshot_ids: source.keyword_rank_snapshot_ids || [],
    },
  };
}

function hydrateScoringCalibrationReviewSnapshots(report) {
  const reviews = scoringCalibrationReviews();
  let changed = false;
  (report?.rows || []).forEach((row) => {
    const review = reviews[row.sample_key];
    if (!review?.judgment || review.judgment === "pending" || review.sample_snapshot) return;
    review.sample_snapshot = buildScoringCalibrationSample(
      row,
      review,
      report?.sampling?.seed || scoringCalibrationState.sampleSeed,
    );
    changed = true;
  });
  if (changed) {
    scoringCalibrationState.reviewJson = JSON.stringify(reviews);
    persistState(scoringCalibrationState);
  }
}

function scoringCalibrationReviewedSamples(report, reviews = scoringCalibrationReviews()) {
  const currentRows = new Map((report?.rows || []).map((row) => [row.sample_key, row]));
  const currentVersion = report?.model?.version || "";
  const samples = [];
  const seen = new Set();
  scoringCalibrationReviewedEntries(reviews).forEach(([sampleKey, review]) => {
    if (currentVersion && review?.model_version && review.model_version !== currentVersion) return;
    let snapshot = review?.sample_snapshot || null;
    if (!snapshot && currentRows.has(sampleKey)) {
      snapshot = buildScoringCalibrationSample(
        currentRows.get(sampleKey),
        review,
        report?.sampling?.seed || scoringCalibrationState.sampleSeed,
      );
    }
    if (!snapshot || seen.has(sampleKey)) return;
    seen.add(sampleKey);
    samples.push({
      ...snapshot,
      human_label: {
        judgment: review.judgment,
        note: review.note || "",
        reviewed_at: review.updated_at || snapshot.human_label?.reviewed_at || null,
      },
    });
  });
  return samples;
}

function updateScoringCalibrationReview(sampleKey, byKey = null) {
  const row = byKey?.get(sampleKey) || scoringCalibrationCurrent?.rows?.find((item) => item.sample_key === sampleKey);
  if (!row) return;
  const select = document.querySelector(`[data-calibration-judgment="${CSS.escape(sampleKey)}"]`);
  const input = document.querySelector(`[data-calibration-note="${CSS.escape(sampleKey)}"]`);
  const judgment = select?.value || "pending";
  const note = String(input?.value || "").trim();
  const reviews = scoringCalibrationReviews();
  if (judgment === "pending" && !note) {
    delete reviews[sampleKey];
  } else {
    const source = row.source_identity || {};
    const review = {
      judgment,
      note,
      updated_at: new Date().toISOString(),
      model_version: source.model_version || scoringCalibrationCurrent?.model?.version || "",
      strategy: scoringCalibrationState.strategy,
      marketplace: scoringCalibrationState.marketplace,
      batch_number: scoringCalibrationState.batchNumber,
      asin: row.asin,
      keyword_id: row.keyword_id,
      keyword: row.keyword,
      product_id: row.product_id,
      bucket: row.sample_bucket,
      opportunity_score: row.opportunity_score,
      risk_score: row.risk_score,
      confidence_score: row.confidence_score,
      rank_snapshot_id: source.rank_snapshot_id || null,
      product_snapshot_id: source.product_snapshot_id || null,
    };
    review.sample_snapshot = buildScoringCalibrationSample(
      row,
      review,
      scoringCalibrationCurrent?.sampling?.seed || scoringCalibrationState.sampleSeed,
    );
    reviews[sampleKey] = review;
  }
  scoringCalibrationState.reviewJson = JSON.stringify(reviews);
  persistState(scoringCalibrationState);
  updateScoringCalibrationProgress();
  const archiveCount = document.getElementById("score-calibration-archive-count");
  if (archiveCount) archiveCount.textContent = fmt.int(scoringCalibrationReviewArchive().length);
  renderScoringCalibrationExportStatus();
}

function updateScoringCalibrationProgress() {
  const target = document.getElementById("score-calibration-reviewed");
  if (!target) return;
  const reviews = scoringCalibrationReviews();
  const rows = scoringCalibrationCurrent?.rows || [];
  const reviewed = rows.filter((row) => reviews[row.sample_key]?.judgment && reviews[row.sample_key].judgment !== "pending").length;
  target.textContent = `${fmt.int(reviewed)} / ${fmt.int(rows.length)}`;
  const total = document.getElementById("score-calibration-reviewed-total");
  if (total) total.textContent = `累计可回看 ${fmt.int(scoringCalibrationReviewArchive().length)} 条`;
}

function clearScoringCalibrationReviews() {
  if (!confirm("清空当前设备保存的评分校准复核草稿？\n\n不会影响 MySQL、影子分数或已经导出的 JSON。")) return;
  scoringCalibrationState.reviewJson = "{}";
  persistState(scoringCalibrationState);
  renderScoringCalibrationData();
  notice("本机校准复核草稿已清空", "ok");
}

async function exportScoringCalibration() {
  const report = scoringCalibrationCurrent;
  if (!report?.rows?.length) return notice("当前没有可导出的校准样本", "bad");
  const reviews = scoringCalibrationReviews();
  const exportScope = scoringCalibrationState.exportScope === "all" ? "all" : "reviewed";
  const payload = buildScoringCalibrationExportPayload(report, reviews, exportScope);
  if (!payload.samples.length) return notice("当前没有已复核样本，请先填写人工判断再导出", "bad");
  const prefix = exportScope === "reviewed" ? "评分V2校准标签" : "评分V2审计样本";
  const filename = `${prefix}_${scoringCalibrationState.strategy}_${new Date().toISOString().slice(0, 10)}.json`;
  const button = document.getElementById("score-calibration-export");
  if (button) {
    button.dataset.saving = "true";
    button.disabled = true;
    button.textContent = "正在保存…";
  }
  try {
    if (scoringCalibrationExportApiAvailable === false) {
      const fallback = await saveScoringCalibrationWithPicker(payload, filename);
      if (!fallback) return;
      scoringCalibrationState.lastExportPath = `系统保存窗口：${fallback.filename}`;
      scoringCalibrationState.lastExportAt = new Date().toLocaleString("zh-CN", { hour12: false });
      persistState(scoringCalibrationState);
      renderScoringCalibrationExportStatus();
      notice(`${exportScope === "reviewed" ? "校准标签" : "审计样本"}已由系统保存窗口写入`, "ok");
      return;
    }

    const saved = await apiSend("/api/scoring-v2/calibration/export", "POST", { payload });
    scoringCalibrationState.lastExportPath = saved.path || "";
    scoringCalibrationState.lastExportAt = saved.saved_at || "";
    persistState(scoringCalibrationState);
    await loadScoringCalibrationExportedReviews();
    renderScoringCalibrationExportStatus();
    notice(`已保存 ${fmt.int(saved.sample_count)} 条${exportScope === "reviewed" ? "校准标签" : "审计样本"}：${saved.filename}`, "ok");
  } catch (err) {
    notice(`评分校准导出失败：${err.message}`, "bad");
  } finally {
    if (button) {
      delete button.dataset.saving;
    }
    renderScoringCalibrationExportStatus();
  }
}

function buildScoringCalibrationExportPayload(report, reviews, exportScope) {
  const rows = report.rows || [];
  const sampleSeed = report.sampling?.seed || scoringCalibrationState.sampleSeed || "baseline";
  const selectedSamples = exportScope === "reviewed"
    ? scoringCalibrationReviewedSamples(report, reviews)
    : rows.map((row) => buildScoringCalibrationSample(
      row,
      reviews[row.sample_key] || { judgment: "pending", note: "" },
      sampleSeed,
    ));
  const strategies = Object.fromEntries((report.model?.strategies || []).map((item) => [item.code, {
    opportunity_weights: item.opportunity_weights || {},
    risk_weights: item.risk_weights || {},
  }]));

  return {
    schema_version: "scoring-calibration-dataset-v2",
    export_type: "scoring_v2_shadow_calibration",
    dataset_purpose: exportScope === "reviewed" ? "model_calibration_labels" : "audit_snapshot",
    exported_at: new Date().toISOString(),
    selection: {
      marketplace: scoringCalibrationState.marketplace,
      keyword_filter: scoringCalibrationState.keyword,
      sample_per_bucket: scoringCalibrationState.samplePerBucket,
      export_scope: exportScope,
      source_sample_count: rows.length,
      review_pool_count: scoringCalibrationReviewedEntries(reviews).length,
      bucket_count: report.summary?.bucket_count || 0,
      sample_generated_at: report.generated_at,
      sample_seed: sampleSeed,
      sampling_method: report.sampling?.method || "seeded_quantile_v1",
      excluded_asin_count: report.sampling?.excluded_asin_count || 0,
      excluded_asins: scoringCalibrationBatchExcludedAsins(),
      batch_number: scoringCalibrationState.batchNumber,
    },
    samples: selectedSamples,
    model_spec: {
      version: report.model?.version,
      scope: report.model?.scope,
      rank_basis: report.model?.rank_basis,
      primary_strategy: scoringCalibrationState.strategy,
      strategies,
      recommendation_labels: report.model?.recommendations || {},
      missing_value_policy: report.model?.missing_value_policy,
    },
    calibration_spec: {
      version: report.calibration?.version,
      label_definitions: report.calibration?.judgments || [],
      audit_flag_labels: report.calibration?.flag_labels || {},
    },
  };
}

async function saveScoringCalibrationWithPicker(payload, filename) {
  if (typeof window.showSaveFilePicker !== "function") {
    throw new Error("当前桌面进程不支持可靠保存。请暂时不要清空或关闭本页，重启前先保留现有复核草稿。");
  }
  try {
    const handle = await window.showSaveFilePicker({
      suggestedName: filename,
      startIn: "downloads",
      types: [{ description: "JSON 文件", accept: { "application/json": [".json"] } }],
    });
    const writable = await handle.createWritable();
    await writable.write(JSON.stringify({
      ...payload,
      local_export: {
        saved_at: new Date().toISOString(),
        storage: "system_save_picker",
        filename: handle.name || filename,
      },
    }, null, 2));
    await writable.close();
    return { filename: handle.name || filename };
  } catch (err) {
    if (err?.name === "AbortError") {
      notice("已取消导出，复核草稿仍保留在当前页面", "ok");
      return null;
    }
    throw err;
  }
}

function readScoringReplayFilters() {
  scoringReplayState.keyword = String(document.getElementById("score-replay-keyword")?.value || "").trim();
  scoringReplayState.marketplace = String(document.getElementById("score-replay-marketplace")?.value || "US").trim().toUpperCase() || "US";
  scoringReplayState.strategy = document.getElementById("score-replay-strategy")?.value || "balanced";
  scoringReplayState.recommendation = document.getElementById("score-replay-recommendation")?.value || "all";
  scoringReplayState.minConfidence = String(document.getElementById("score-replay-min-confidence")?.value || "").trim();
  scoringReplayState.maxRisk = String(document.getElementById("score-replay-max-risk")?.value || "").trim();
  scoringReplayState.sortBy = document.getElementById("score-replay-sort")?.value || "opportunity_score";
  scoringReplayState.sortDir = document.getElementById("score-replay-dir")?.value || "desc";
}

async function loadScoringReplay(resetPage = false) {
  if (resetPage) {
    scoringReplayState.offset = 0;
    scoringReplayState.selectedKey = "";
  }
  readScoringReplayFilters();
  persistState(scoringReplayState);
  const table = document.getElementById("score-replay-table");
  const meta = document.getElementById("score-replay-meta");
  const summary = document.getElementById("score-replay-summary");
  const detail = document.getElementById("score-replay-detail");
  const pager = document.getElementById("score-replay-pager");
  if (!table) return;
  table.innerHTML = `<div class="state"><div class="spinner"></div>按全部当前批次计算并排序…</div>`;
  meta.textContent = "";
  if (summary) summary.innerHTML = "";
  if (detail) detail.innerHTML = "";
  if (pager) pager.innerHTML = "";
  const query = new URLSearchParams({
    limit: String(scoringReplayState.limit),
    offset: String(scoringReplayState.offset),
    marketplace: scoringReplayState.marketplace,
    keyword: scoringReplayState.keyword,
    strategy: scoringReplayState.strategy,
    sort_by: scoringReplayState.sortBy,
    sort_dir: scoringReplayState.sortDir,
  });
  if (scoringReplayState.recommendation !== "all") query.set("recommendation", scoringReplayState.recommendation);
  if (scoringReplayState.minConfidence !== "") query.set("min_confidence", scoringReplayState.minConfidence);
  if (scoringReplayState.maxRisk !== "") query.set("max_risk", scoringReplayState.maxRisk);
  try {
    const page = normalizePage(await api(`/api/scoring-v2/replay?${query.toString()}`), scoringReplayState.limit);
    scoringReplayCurrent = page;
    scoringReplayState.limit = page.limit;
    scoringReplayState.offset = page.offset;
    persistState(scoringReplayState);
    meta.textContent = `${pageSummary(page, "评分上下文")} · 全局${page.sort_dir === "asc" ? "升序" : "降序"} · ${page.strategy?.label || "均衡研究"}`;
    summary.innerHTML = renderScoringReplaySummary(page);
    table.innerHTML = renderScoringReplayTable(page.rows || []);
    pager.innerHTML = renderPager("score-replay-pager-inner", page, [20, 50, 100]);
    bindPager("score-replay-pager-inner", scoringReplayState, page, loadScoringReplay);
    bindScoringReplayRows(page.rows || []);
    rememberAgentBusinessContext();
  } catch (err) {
    table.innerHTML = `<div class="state error">⚠ ${escapeHtml(err.message)}</div>`;
  }
}

function renderScoringReplaySummary(page) {
  const s = page.summary || {};
  const coverage = s.coverage || {};
  const recommendations = s.recommendation_counts || {};
  return `<div class="score-replay-summary">
    <div><span>回放上下文</span><b>${fmt.int(s.context_count)}</b><small>${fmt.int(s.product_count)} 个商品 · ${fmt.int(s.keyword_count)} 个关键词</small></div>
    <div><span>高机会信号</span><b>${fmt.int(s.high_opportunity_count)}</b><small>机会分 ≥ 70，不等于低风险</small></div>
    <div><span>高风险上下文</span><b>${fmt.int(s.high_risk_count)}</b><small>风险分 ≥ 60，数值越高越不利</small></div>
    <div><span>低置信上下文</span><b>${fmt.int(s.low_confidence_count)}</b><small>置信度 &lt; 55，应先补证据</small></div>
    <div><span>行动分布</span><b>${fmt.int(recommendations["优先人工验证"] || 0)} / ${fmt.int(recommendations["可进入观察池"] || 0)}</b><small>优先验证 / 观察池</small></div>
    <div><span>证据覆盖</span><b>${marketRatio(coverage.monthly_bought)} / ${marketRatio(coverage.usable_rank)}</b><small>月购下界 / 可用排名置信</small></div>
  </div>`;
}

function renderScoringReplayTable(rows) {
  if (!rows.length) return `<div class="state">当前筛选下没有可回放的商品 × 关键词上下文。</div>`;
  const body = rows.map((row) => {
    const trend = row.trend || {};
    const selected = scoreReplayRowKey(row) === scoringReplayState.selectedKey ? " class=\"score-replay-selected\"" : "";
    return `<tr${selected}>
      <td>${amazonProductLink(row, displayTitle(row, row.asin), 48)}<div class="cell-sub">${amazonProductLink(row, row.asin)} · ${fmt.money(row.price)}</div></td>
      <td><b>${escapeHtml(row.keyword || "—")}</b><div class="cell-sub">${escapeHtml(fmt.text(row.snapshot_at))} · ${scoreRankConfidenceLabel(row.rank_confidence)}</div></td>
      <td class="num">${scoreReplayAxisBadge(row.opportunity_score, "opportunity")}</td>
      <td class="num">${scoreReplayAxisBadge(row.risk_score, "risk")}</td>
      <td class="num">${scoreReplayAxisBadge(row.confidence_score, "confidence", row.confidence_level)}</td>
      <td>${escapeHtml(trend.confidence || "无法判断")}<div class="cell-sub">${fmt.int(trend.sample_size)} 条 · ${fmt.num(trend.span_days, 0)} 天</div></td>
      <td class="num">${scoreBadge(row.legacy_total_score)}</td>
      <td>${scoreReplayRecommendationBadge(row.recommendation_code, row.recommendation_label)}</td>
      <td><div class="actions"><button class="btn btn-sm" data-score-replay-detail="${escapeHtml(scoreReplayRowKey(row))}">拆解</button><a class="btn btn-sm" href="${escapeHtml(productDetailHash(row.asin, row.keyword))}">详情</a></div></td>
    </tr>`;
  }).join("");
  return wrapTable(`<table class="score-replay-table"><thead><tr>
    <th>商品</th><th>关键词证据</th><th class="num">机会</th><th class="num">风险</th><th class="num">置信</th><th>趋势证据</th><th class="num">旧分</th><th>影子建议</th><th>操作</th>
  </tr></thead><tbody>${body}</tbody></table>`);
}

function bindScoringReplayRows(rows) {
  const byKey = new Map(rows.map((row) => [scoreReplayRowKey(row), row]));
  let selected = byKey.get(scoringReplayState.selectedKey);
  if (!selected && rows.length) {
    selected = rows[0];
    scoringReplayState.selectedKey = scoreReplayRowKey(selected);
    persistState(scoringReplayState);
  }
  renderScoringReplayDetail(selected || null);
  document.querySelectorAll("[data-score-replay-detail]").forEach((button) => {
    button.onclick = () => {
      const row = byKey.get(button.dataset.scoreReplayDetail);
      if (!row) return;
      scoringReplayState.selectedKey = scoreReplayRowKey(row);
      persistState(scoringReplayState);
      renderScoringReplayDetail(row);
      document.querySelectorAll(".score-replay-table tbody tr").forEach((tr) => tr.classList.remove("score-replay-selected"));
      button.closest("tr")?.classList.add("score-replay-selected");
      document.getElementById("score-replay-detail")?.scrollIntoView({ behavior: "smooth", block: "start" });
      rememberAgentBusinessContext();
    };
  });
}

function renderScoringReplayDetail(row) {
  const box = document.getElementById("score-replay-detail");
  if (!box) return;
  if (!row) {
    box.innerHTML = "";
    return;
  }
  const source = row.source_identity || {};
  const trend = row.trend || {};
  box.innerHTML = `<div class="panel score-replay-detail">
    <div class="table-toolbar score-replay-detail-head">
      <div>
        <h2 style="margin:0">${amazonProductLink(row, displayTitle(row, row.asin), 84)}</h2>
        <div class="result-meta">${amazonProductLink(row, row.asin)} · ${escapeHtml(row.keyword || "—")} · ${escapeHtml(row.recommendation_reason || "")}</div>
      </div>
      <div class="actions">${scoreReplayRecommendationBadge(row.recommendation_code, row.recommendation_label)}<a class="btn btn-sm" href="${escapeHtml(productDetailHash(row.asin, row.keyword))}">商品详情</a></div>
    </div>
    <div class="score-axis-grid">
      ${renderScoringReplayAxis("机会分", row.opportunity_score, "opportunity", row.opportunity_components || [])}
      ${renderScoringReplayAxis("风险分", row.risk_score, "risk", row.risk_components || [])}
      ${renderScoringReplayAxis("置信度", row.confidence_score, "confidence", row.confidence_components || [])}
    </div>
    <div class="score-replay-explanation">
      <section><h3>支持理由</h3>${renderScoringReasonList(row.supporting_reasons, "当前没有强支持理由")}</section>
      <section><h3>反对与缺口</h3>${renderScoringReasonList(row.opposing_reasons, "当前未识别明显硬风险")}</section>
    </div>
    <div class="score-source-grid">
      <div><span>模型版本</span><b>${escapeHtml(source.model_version || "—")}</b></div>
      <div><span>策略模板</span><b>${escapeHtml(source.strategy || "—")}</b></div>
      <div><span>关键词排名快照</span><b>${source.rank_snapshot_id ? `#${fmt.int(source.rank_snapshot_id)}` : "—"}</b></div>
      <div><span>商品快照</span><b>${source.product_snapshot_id ? `#${fmt.int(source.product_snapshot_id)}` : "—"}</b></div>
      <div><span>证据时间</span><b>${escapeHtml(fmt.text(source.snapshot_at))}</b></div>
      <div><span>历史证据</span><b>${fmt.int(source.history_snapshot_ids?.length || 0)} 商品快照 / ${fmt.int(source.keyword_rank_snapshot_ids?.length || 0)} 同词位次</b></div>
    </div>
    <div class="niche-evidence-note score-replay-detail-note">
      趋势口径：${escapeHtml(trend.summary || "样本不足")} 排名变化只使用同一商品与同一关键词的历史，旧综合分 ${fmt.num(row.legacy_total_score, 1)} 仅作迁移基线。
    </div>
  </div>`;
}

function renderScoringReplayAxis(label, score, type, components) {
  const rows = components.map((item) => `<div class="score-component-row">
    <div class="score-component-head"><b>${escapeHtml(item.label)}</b><span>${fmt.num(item.score, 0)} × ${fmt.num(Number(item.weight || 0) * 100, 0)}%</span></div>
    <div class="score-component-bar score-component-${type}"><i style="width:${Math.max(0, Math.min(100, Number(item.score) || 0))}%"></i></div>
    <p>${escapeHtml(item.reason || "—")}</p>
    <small>${escapeHtml(scoreComponentStatusLabel(item.status))} · 贡献 ${fmt.num(item.contribution, 1)}</small>
  </div>`).join("");
  return `<section class="score-axis-column score-axis-${type}">
    <div class="score-axis-head"><h3>${escapeHtml(label)}</h3>${scoreReplayAxisBadge(score, type)}</div>
    ${rows || `<div class="state">暂无分项。</div>`}
  </section>`;
}

function renderScoringReasonList(rows, empty) {
  const values = Array.isArray(rows) ? rows.filter(Boolean) : [];
  return values.length ? `<ul>${values.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : `<p>${escapeHtml(empty)}</p>`;
}

function scoreReplayRowKey(row) {
  return `${Number(row?.keyword_id) || 0}:${Number(row?.product_id) || 0}`;
}

function scoreReplayAxisBadge(value, type, suffix = "") {
  const number = Number(value);
  if (!Number.isFinite(number)) return `<span class="badge badge-dim">—</span>`;
  let cls;
  if (type === "risk") cls = number >= 60 ? "badge-bad" : number >= 40 ? "badge-warn" : "badge-good";
  else if (type === "confidence") cls = number >= 75 ? "badge-good" : number >= 55 ? "badge-warn" : number >= 35 ? "badge-dim" : "badge-bad";
  else cls = number >= 70 ? "badge-good" : number >= 55 ? "badge-warn" : "badge-dim";
  return `<span class="badge ${cls}" title="${escapeHtml(type === "risk" ? "风险分越高越不利" : "0-100")}">${number.toFixed(0)}${suffix ? ` · ${escapeHtml(suffix)}` : ""}</span>`;
}

function scoreReplayRecommendationBadge(code, label) {
  const cls = code === "priority_validate" ? "badge-good" : code === "observe" ? "badge-warn" : code === "benchmark_only" ? "badge-warn" : "badge-dim";
  return `<span class="badge ${cls}">${escapeHtml(label || "—")}</span>`;
}

function scoreComponentStatusLabel(status) {
  return ({ observed: "已观察", neutral_missing: "缺失按中性", low_confidence: "低置信", heuristic: "启发式" })[status] || status || "—";
}

function scoreRankConfidenceLabel(value) {
  return ({ page_first: "首批次可信", batch_continuous: "连续批次可信", page_gap: "缺页", sponsored: "广告", unknown: "排名置信未知" })[value] || escapeHtml(value || "排名置信未知");
}

/* ---------- 视图：关键词资产库 ---------- */
async function viewKeywordLibrary() {
  content.innerHTML = `
    <div class="panel">
      <div class="table-toolbar">
        <h2 style="margin:0;font-size:14px">关键词资产库</h2>
        <div class="actions">
          <button class="btn btn-sm" id="klib-view-table">表格</button>
          <button class="btn btn-sm" id="klib-view-tree">树结构</button>
          <span id="klib-selected" class="selected-count">已选 0 个</span>
          <button class="btn btn-sm" id="klib-add-project" disabled>加入研究项目</button>
          <button class="btn btn-sm" id="klib-track">创建追踪</button>
          <button class="btn btn-sm" id="klib-clear">清空选择</button>
        </div>
      </div>
      <div id="klib-summary" class="keyword-library-summary"></div>
      <div class="filters">
        <input id="klib-keyword" placeholder="关键词过滤" />
        <input id="klib-marketplace" placeholder="站点" value="US" style="width:82px" />
        <select id="klib-snapshot" class="sel">
          <option value="all">全部快照</option>
          <option value="with">已有快照</option>
          <option value="without">暂无快照</option>
        </select>
        <select id="klib-tracking" class="sel">
          <option value="all">全部追踪</option>
          <option value="active">追踪中</option>
          <option value="paused">已暂停</option>
          <option value="completed">已完成</option>
          <option value="error">异常</option>
          <option value="any">有追踪</option>
          <option value="none">无追踪</option>
        </select>
        <select id="klib-source" class="sel">
          <option value="all">全部来源</option>
          <option value="workshop">来自创意工坊</option>
          <option value="non_workshop">非创意工坊</option>
        </select>
        <button class="btn" id="klib-apply">筛选</button>
        <button class="btn" id="klib-reset">重置</button>
      </div>
      <div id="klib-meta" class="result-meta"></div>
      <div id="klib-table"></div>
      <div id="klib-pager-wrap"></div>
    </div>
    <div id="klib-detail"></div>`;

  document.getElementById("klib-keyword").value = keywordLibraryState.keyword;
  document.getElementById("klib-marketplace").value = keywordLibraryState.marketplace;
  document.getElementById("klib-snapshot").value = keywordLibraryState.snapshotFilter;
  document.getElementById("klib-tracking").value = keywordLibraryState.trackingFilter;
  document.getElementById("klib-source").value = keywordLibraryState.sourceFilter;
  document.getElementById("klib-view-table").onclick = () => setKeywordLibraryViewMode("table");
  document.getElementById("klib-view-tree").onclick = () => setKeywordLibraryViewMode("tree");
  document.getElementById("klib-apply").onclick = () => loadKeywordLibrary(true);
  document.getElementById("klib-reset").onclick = () => {
    keywordLibraryState.keyword = "";
    keywordLibraryState.marketplace = "US";
    keywordLibraryState.snapshotFilter = "all";
    keywordLibraryState.trackingFilter = "all";
    keywordLibraryState.sourceFilter = "all";
    keywordLibrarySelected.clear();
    document.getElementById("klib-keyword").value = "";
    document.getElementById("klib-marketplace").value = "US";
    document.getElementById("klib-snapshot").value = "all";
    document.getElementById("klib-tracking").value = "all";
    document.getElementById("klib-source").value = "all";
    document.getElementById("klib-detail").innerHTML = "";
    loadKeywordLibrary(true);
  };
  document.getElementById("klib-add-project").onclick = addSelectedKeywordsToResearchProject;
  document.getElementById("klib-track").onclick = createTrackingFromKeywordLibrary;
  document.getElementById("klib-clear").onclick = () => {
    keywordLibrarySelected.clear();
    document.querySelectorAll(".klib-check").forEach((check) => { check.checked = false; });
    updateKeywordLibrarySelected();
    rememberAgentBusinessContext();
  };
  content.querySelectorAll(".filters input, .filters select").forEach((inp) => {
    inp.addEventListener("keydown", (e) => { if (e.key === "Enter") loadKeywordLibrary(true); });
  });
  await loadKeywordLibrary();
}

function setKeywordLibraryViewMode(mode) {
  if (!["table", "tree"].includes(mode) || keywordLibraryState.viewMode === mode) return;
  keywordLibraryState.viewMode = mode;
  keywordLibraryState.offset = 0;
  persistState(keywordLibraryState);
  keywordLibrarySelected.clear();
  const detail = document.getElementById("klib-detail");
  if (detail) detail.innerHTML = "";
  loadKeywordLibrary();
}

function updateKeywordLibraryViewModeButtons() {
  const table = document.getElementById("klib-view-table");
  const tree = document.getElementById("klib-view-tree");
  if (table) table.classList.toggle("btn-active", keywordLibraryState.viewMode === "table");
  if (tree) tree.classList.toggle("btn-active", keywordLibraryState.viewMode === "tree");
}

async function loadKeywordLibrary(resetPage = false) {
  if (resetPage) {
    keywordLibraryState.offset = 0;
    keywordLibraryState.keyword = document.getElementById("klib-keyword").value.trim();
    keywordLibraryState.marketplace = (document.getElementById("klib-marketplace").value.trim() || "US").toUpperCase();
    keywordLibraryState.snapshotFilter = document.getElementById("klib-snapshot").value;
    keywordLibraryState.trackingFilter = document.getElementById("klib-tracking").value;
    keywordLibraryState.sourceFilter = document.getElementById("klib-source").value;
    keywordLibrarySelected.clear();
    keywordLibraryTreeCollapsed.clear();
    document.getElementById("klib-detail").innerHTML = "";
  }
  persistState(keywordLibraryState);
  const table = document.getElementById("klib-table");
  const meta = document.getElementById("klib-meta");
  const pager = document.getElementById("klib-pager-wrap");
  if (!table) return;
  table.innerHTML = `<div class="state"><div class="spinner"></div>加载关键词资产…</div>`;
  meta.textContent = "";
  pager.innerHTML = "";
  updateKeywordLibraryViewModeButtons();
  updateKeywordLibrarySelected();
  if (keywordLibraryState.viewMode === "tree") {
    await loadKeywordLibraryTree();
    return;
  }
  const q = new URLSearchParams({
    limit: String(keywordLibraryState.limit),
    offset: String(keywordLibraryState.offset),
    marketplace: keywordLibraryState.marketplace || "US",
    snapshot_filter: keywordLibraryState.snapshotFilter,
    tracking_filter: keywordLibraryState.trackingFilter,
    source_filter: keywordLibraryState.sourceFilter,
    sort_by: keywordLibraryState.sortBy,
    sort_dir: keywordLibraryState.sortDir,
  });
  if (keywordLibraryState.keyword) q.set("keyword", keywordLibraryState.keyword);
  try {
    const page = normalizePage(await api(`/api/keyword-library/keywords?${q.toString()}`), keywordLibraryState.limit);
    syncRemoteSortState(keywordLibraryState, page);
    const rows = page.rows || [];
    renderKeywordLibrarySummary(page.summary || {});
    meta.textContent = pageSummary(page, "关键词资产") + " · 资产库包含已入库但暂无快照的关键词";
    keywordLibraryRows.clear();
    rows.forEach((row) => keywordLibraryRows.set(String(row.keyword_id), row));
    const visibleIds = new Set(rows.map((row) => Number(row.keyword_id)));
    keywordLibrarySelected.forEach((id) => { if (!visibleIds.has(Number(id))) keywordLibrarySelected.delete(id); });
    if (!rows.length) {
      table.innerHTML = `<div class="state">暂无关键词资产。可先从本地 HTML 入库、关键词创意工坊或追踪任务创建关键词。</div>`;
      pager.innerHTML = renderPager("klib-pager", page, [20, 50, 100]);
      bindPager("klib-pager", keywordLibraryState, page, loadKeywordLibrary);
      updateKeywordLibrarySelected();
      return;
    }
    renderSortableTable(table, [
      { key: "select", label: `<input type="checkbox" id="klib-check-all" onclick="window.keywordLibraryToggleAll(this)" />`, align: "check", sortable: false, csv: false,
        render: (r) => `<input type="checkbox" class="klib-check" value="${escapeHtml(r.keyword_id)}"${keywordLibrarySelected.has(Number(r.keyword_id)) ? " checked" : ""} onclick="event.stopPropagation()" onchange="window.keywordLibraryToggle(this)" />` },
      { key: "keyword", label: "关键词", render: (r) => `<b>${escapeHtml(r.keyword)}</b>`, sortVal: (r) => r.keyword },
      { key: "product_count", label: "关联商品", align: "num", numeric: true, render: (r) => fmt.int(r.product_count), sortVal: (r) => r.product_count },
      { key: "snapshot_time_count", label: "快照时间点", align: "num", numeric: true, render: (r) => keywordLibrarySnapshotBadge(r), sortVal: (r) => r.snapshot_time_count },
      { key: "latest_snapshot_at", label: "最近采集", render: (r) => fmt.text(r.latest_snapshot_at), sortVal: (r) => r.latest_snapshot_at || "" },
      { key: "avg_total_score", label: "机会信号", align: "num", numeric: true, render: (r) => scoreBadge(r.avg_total_score), sortVal: (r) => r.avg_total_score },
      { key: "avg_organic_rank", label: "自然序位估算", align: "num", numeric: true, render: (r) => fmt.num(r.avg_organic_rank, 0), sortVal: (r) => r.avg_organic_rank },
      { key: "source_types", label: "来源", render: (r) => keywordLibrarySourceBadges(r), csv: (r) => keywordLibrarySourceText(r) },
      { key: "tracking_status", label: "追踪", render: (r) => keywordLibraryTrackingBadge(r.tracking_status), sortVal: (r) => r.tracking_status },
      { key: "actions", label: "操作", sortable: false, csv: false, render: (r) => keywordLibraryActions(r) },
    ], rows, { remoteSort: remoteSortOptions(keywordLibraryState, loadKeywordLibrary), exportName: "关键词资产库", onRowClick: showKeywordLibraryDetail });
    pager.innerHTML = renderPager("klib-pager", page, [20, 50, 100]);
    bindPager("klib-pager", keywordLibraryState, page, loadKeywordLibrary);
    updateKeywordLibrarySelected();
  } catch (err) {
    table.innerHTML = `<div class="state error">⚠ ${escapeHtml(err.message)}</div>`;
  }
}

async function loadKeywordLibraryTree() {
  const table = document.getElementById("klib-table");
  const meta = document.getElementById("klib-meta");
  const pager = document.getElementById("klib-pager-wrap");
  if (!table) return;
  const q = new URLSearchParams({
    marketplace: keywordLibraryState.marketplace || "US",
    snapshot_filter: keywordLibraryState.snapshotFilter,
    tracking_filter: keywordLibraryState.trackingFilter,
    source_filter: keywordLibraryState.sourceFilter,
    max_keywords: "500",
  });
  if (keywordLibraryState.keyword) q.set("keyword", keywordLibraryState.keyword);
  try {
    const tree = await api(`/api/keyword-library/tree?${q.toString()}`);
    const roots = Array.isArray(tree.roots) ? tree.roots : [];
    renderKeywordLibrarySummary(tree.summary || {});
    keywordLibraryRows.clear();
    flattenKeywordLibraryTree(roots).forEach((row) => keywordLibraryRows.set(String(row.keyword_id), row));
    const visibleIds = new Set([...keywordLibraryRows.keys()].map((id) => Number(id)));
    keywordLibrarySelected.forEach((id) => { if (!visibleIds.has(Number(id))) keywordLibrarySelected.delete(id); });
    const cut = tree.truncated ? ` · 已截断为前 ${fmt.int(tree.node_count)} 个` : "";
    meta.textContent = `共 ${fmt.int(tree.total)} 个关键词资产 · 一级 ${fmt.int(tree.root_count)} 个 · 最大层级 ${fmt.int(tree.max_depth)}${cut} · 树结构为自动推断`;
    pager.innerHTML = "";
    if (!roots.length) {
      table.innerHTML = `<div class="state">暂无可归类关键词。可调整筛选，或先从本地 HTML 入库、关键词创意工坊创建关键词。</div>`;
      updateKeywordLibrarySelected();
      return;
    }
    table.innerHTML = `
      <div class="keyword-tree-head">
        <label class="check-inline"><input type="checkbox" id="klib-check-all" onclick="window.keywordLibraryToggleAll(this)" /> 全选当前树</label>
        <span>自动按词组包含关系归类；没有明确上级的关键词作为一级词。</span>
      </div>
      <div class="keyword-tree" role="tree" aria-label="关键词层级">
        ${roots.map((node) => renderKeywordLibraryTreeNode(node)).join("")}
      </div>`;
    updateKeywordLibrarySelected();
    restoreInteractiveSelection(table);
  } catch (err) {
    table.innerHTML = `<div class="state error">⚠ ${escapeHtml(err.message)}</div>`;
  }
}

function flattenKeywordLibraryTree(nodes) {
  const result = [];
  const visit = (node) => {
    result.push(node);
    (node.children || []).forEach(visit);
  };
  (nodes || []).forEach(visit);
  return result;
}

function renderKeywordLibraryTreeNode(node) {
  const id = Number(node.keyword_id);
  const isVirtual = node.virtual || id <= 0;
  const children = node.children || [];
  const hasChildren = children.length > 0;
  const collapsed = keywordLibraryTreeCollapsed.has(id);
  const depth = Math.max(1, Number(node.depth || 1));
  const indent = 10 + (depth - 1) * 24;
  const checked = keywordLibrarySelected.has(id) ? " checked" : "";
  const toggle = hasChildren
    ? `<button class="keyword-tree-toggle" onclick="event.stopPropagation();window.keywordLibraryTreeToggle(${id})">${collapsed ? "+" : "-"}</button>`
    : `<span class="keyword-tree-toggle keyword-tree-toggle-empty"></span>`;
  const selector = isVirtual
    ? `<span class="keyword-tree-virtual-dot"></span>`
    : `<input type="checkbox" class="klib-check" value="${escapeHtml(id)}"${checked} onclick="event.stopPropagation()" onchange="window.keywordLibraryToggle(this)" />`;
  const title = isVirtual
    ? `<span class="keyword-tree-title keyword-tree-title-virtual">${escapeHtml(node.keyword)}</span>`
    : `<span class="keyword-tree-title">${escapeHtml(node.keyword)}</span>`;
  const relation = isVirtual ? "自动分类" : (node.parent_keyword ? `上级：${escapeHtml(node.parent_keyword)}` : "一级关键词");
  const childText = hasChildren ? ` · 子词 ${fmt.int(children.length)} / 后代 ${fmt.int(node.descendant_count)}` : "";
  const actions = isVirtual ? `<span class="badge badge-dim">分类节点</span>` : keywordLibraryActions(node);
  const entryAttrs = isVirtual
    ? ""
    : `${selectableEntryAttrs("", `keyword-library:${id}`)} data-keyword-library-detail="${id}"`;
  return `
    <div class="keyword-tree-node">
      <div class="keyword-tree-row"${isVirtual ? "" : ' role="treeitem"'}${entryAttrs} style="--tree-indent:${indent}px">
        ${selector}
        ${toggle}
        ${title}
        <div class="keyword-tree-stats">
          <span>${relation}${childText}</span>
          <span>商品 ${fmt.int(node.product_count)}</span>
          <span>快照 ${fmt.int(node.snapshot_time_count)}</span>
          <span>机会 ${scoreBadge(node.avg_total_score)}</span>
          <span>${keywordLibraryTrackingBadge(node.tracking_status)}</span>
        </div>
        <div class="keyword-tree-actions">${actions}</div>
      </div>
      ${hasChildren && !collapsed ? `<div class="keyword-tree-children">${children.map((child) => renderKeywordLibraryTreeNode(child)).join("")}</div>` : ""}
    </div>`;
}

window.keywordLibraryTreeToggle = (keywordId) => {
  const id = Number(keywordId);
  if (!id) return;
  if (keywordLibraryTreeCollapsed.has(id)) keywordLibraryTreeCollapsed.delete(id);
  else keywordLibraryTreeCollapsed.add(id);
  loadKeywordLibraryTree();
};

function renderKeywordLibrarySummary(summary) {
  const box = document.getElementById("klib-summary");
  if (!box) return;
  box.innerHTML = `
    <div><span>关键词总数</span><b>${fmt.int(summary.total_keywords)}</b></div>
    <div><span>已有快照</span><b>${fmt.int(summary.with_snapshots)}</b></div>
    <div><span>追踪中</span><b>${fmt.int(summary.active_tracking)}</b></div>
    <div><span>创意入库</span><b>${fmt.int(summary.workshop_keywords)}</b></div>
    <div><span>最近采集</span><b>${fmt.text(summary.latest_snapshot_at)}</b></div>`;
}

function keywordLibrarySnapshotBadge(row) {
  const count = Number(row.snapshot_time_count || 0);
  if (!count) return `<span class="badge badge-dim">暂无快照</span>`;
  return `<span class="badge badge-good">${fmt.int(count)}</span>`;
}

function keywordLibraryTrackingBadge(status) {
  const map = {
    active: ["追踪中", "badge-good"],
    paused: ["已暂停", "badge-warn"],
    completed: ["已完成", "badge-dim"],
    error: ["异常", "badge-bad"],
    other: ["有追踪", "badge-dim"],
    none: ["未追踪", "badge-dim"],
  };
  const item = map[status] || [status || "未追踪", "badge-dim"];
  return `<span class="badge ${item[1]}">${escapeHtml(item[0])}</span>`;
}

function keywordLibrarySourceText(row) {
  const items = [];
  if (row.has_workshop_idea) items.push((row.source_types || []).map(keywordIdeaSourceLabel).join(" / ") || "创意工坊");
  if (!items.length) items.push("采集/入库");
  return items.join(" / ");
}

function keywordLibrarySourceBadges(row) {
  if (row.has_workshop_idea) {
    const sources = (row.source_types || []).map((source) => `<span class="badge badge-dim">${escapeHtml(keywordIdeaSourceLabel(source))}</span>`).join(" ");
    const score = row.idea_score != null ? ` ${scoreBadge(row.idea_score)}` : "";
    return `${sources || `<span class="badge badge-dim">创意工坊</span>`}${score}`;
  }
  return `<span class="badge badge-dim">采集/入库</span>`;
}

function keywordLibraryActions(row) {
  const id = Number(row.keyword_id);
  return `<div class="actions">
    <button class="btn btn-sm" onclick="event.stopPropagation();window.keywordLibraryResearch(${id})">入项目</button>
    <button class="btn btn-sm" onclick="event.stopPropagation();window.keywordLibraryDetail(${id})">详情</button>
    <button class="btn btn-sm" onclick="event.stopPropagation();window.keywordLibraryOpportunity(${id})">看机会</button>
    <button class="btn btn-sm" onclick="event.stopPropagation();window.keywordLibraryCopy(${id})">复制</button>
  </div>`;
}

window.keywordLibraryToggle = (checkbox) => {
  const id = Number(checkbox.value);
  if (!id) return;
  if (checkbox.checked) keywordLibrarySelected.add(id);
  else keywordLibrarySelected.delete(id);
  updateKeywordLibrarySelected();
  rememberAgentBusinessContext();
};

window.keywordLibraryToggleAll = (checkbox) => {
  document.querySelectorAll(".klib-check").forEach((cb) => {
    cb.checked = checkbox.checked;
    const id = Number(cb.value);
    if (checkbox.checked) keywordLibrarySelected.add(id);
    else keywordLibrarySelected.delete(id);
  });
  updateKeywordLibrarySelected();
  rememberAgentBusinessContext();
};

function updateKeywordLibrarySelected() {
  const el = document.getElementById("klib-selected");
  if (el) el.textContent = `已选 ${keywordLibrarySelected.size} 个`;
  const addProject = document.getElementById("klib-add-project");
  if (addProject) addProject.disabled = keywordLibrarySelected.size < 1;
  const all = document.getElementById("klib-check-all");
  if (all) {
    const checks = [...document.querySelectorAll(".klib-check")];
    all.checked = checks.length > 0 && checks.every((cb) => cb.checked);
  }
}

function keywordLibrarySelectedIds() {
  return [...keywordLibrarySelected].map((id) => Number(id)).filter(Boolean).slice(0, 100);
}

function keywordLibrarySelectedRows() {
  return keywordLibrarySelectedIds().map((id) => {
    const row = keywordLibraryRows.get(String(id)) || {};
    return { keyword_id: id, keyword: row.keyword || "", marketplace: row.marketplace || "" };
  }).filter((row) => row.keyword);
}

function addSelectedKeywordsToResearchProject() {
  const rows = keywordLibrarySelectedRows();
  if (!rows.length) return notice("请先勾选关键词", "bad");
  return openResearchAssociationDialog({
    assetType: "keyword",
    items: rows.map((row) => ({
      key: row.keyword,
      label: row.keyword,
      marketplace: row.marketplace || keywordLibraryState.marketplace || "US",
    })),
    marketplace: keywordLibraryState.marketplace || "US",
  });
}

async function createTrackingFromKeywordLibrary() {
  const ids = keywordLibrarySelectedIds();
  if (!ids.length) return notice("请先勾选关键词", "bad");
  if (!confirm(`确认给 ${ids.length} 个关键词创建追踪任务？\n已有 active 追踪任务的关键词不会重复创建；不会立即联网采集。`)) return;
  try {
    const result = await apiSend("/api/keyword-library/keywords/create-tracking", "POST", {
      ids,
      marketplace: keywordLibraryState.marketplace || "US",
      target_snapshots: 3,
    });
    keywordLibrarySelected.clear();
    await loadKeywordLibrary();
    const warning = (result.warnings || [])[0];
    notice(`追踪任务处理完成：${fmt.int(result.created_or_existing)} 个${warning ? `；${warning}` : ""}`, warning ? "bad" : "ok");
  } catch (err) {
    notice(err.message, "bad");
  }
}

window.keywordLibraryDetail = (keywordId) => {
  const row = keywordLibraryRows.get(String(keywordId));
  if (row) showKeywordLibraryDetail(row);
};

window.keywordLibraryOpportunity = (keywordId) => {
  const row = keywordLibraryRows.get(String(keywordId));
  if (!row) return;
  keywordState.keyword = row.keyword || "";
  keywordState.minProducts = "";
  keywordState.offset = 0;
  persistState(keywordState);
  location.hash = "#/keywords";
};

window.keywordLibraryCopy = async (keywordId) => {
  const row = keywordLibraryRows.get(String(keywordId));
  if (!row) return;
  try {
    await navigator.clipboard.writeText(row.keyword || "");
    notice("关键词已复制", "ok");
  } catch {
    notice(`关键词：${row.keyword || ""}`, "ok");
  }
};

async function showKeywordLibraryDetail(row) {
  const box = document.getElementById("klib-detail");
  if (!box) return;
  box.innerHTML = `<div class="panel"><div class="state"><div class="spinner"></div>加载关键词详情…</div></div>`;
  try {
    const detail = await api(`/api/keyword-library/keywords/${encodeURIComponent(row.keyword_id)}`);
    const asset = detail.asset || row;
    const products = detail.products || [];
    box.innerHTML = `
      <div class="panel">
        <div class="table-toolbar">
          <h2 style="margin:0;font-size:14px">关键词「${escapeHtml(asset.keyword)}」资产详情</h2>
          <div class="actions">
            <button class="btn btn-sm" onclick="window.keywordLibraryResearch(${Number(asset.keyword_id)})">加入研究项目</button>
            <button class="btn btn-sm" onclick="window.keywordLibraryOpportunity(${Number(asset.keyword_id)})">看机会</button>
            <button class="btn btn-sm" onclick="window.keywordLibraryCopy(${Number(asset.keyword_id)})">复制关键词</button>
          </div>
        </div>
        <div class="kv">
          <div><span>站点</span>${escapeHtml(asset.marketplace || "—")}</div>
          <div><span>关联商品</span>${fmt.int(asset.product_count)}</div>
          <div><span>快照时间点</span>${fmt.int(asset.snapshot_time_count)}</div>
          <div><span>最近采集</span>${fmt.text(asset.latest_snapshot_at)}</div>
          <div><span>机会信号</span>${scoreBadge(asset.avg_total_score)}</div>
          <div><span>自然序位估算</span>${fmt.num(asset.avg_organic_rank, 0)}</div>
          <div><span>来源</span>${keywordLibrarySourceBadges(asset)}</div>
          <div><span>追踪状态</span>${keywordLibraryTrackingBadge(asset.tracking_status)}</div>
        </div>
      </div>
      <div class="panel">
        <h2 style="margin:0 0 10px;font-size:14px">相关商品（最新快照）</h2>
        <div id="klib-products"></div>
      </div>`;
    renderKeywordLibraryProducts(products);
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    box.innerHTML = `<div class="panel"><div class="state error">⚠ ${escapeHtml(err.message)}</div></div>`;
  }
}

window.keywordLibraryResearch = (keywordId) => {
  const row = keywordLibraryRows.get(String(keywordId));
  if (!row) return;
  openResearchAssociationDialog({
    assetType: "keyword",
    items: [{ key: row.keyword, label: row.keyword, marketplace: row.marketplace || keywordLibraryState.marketplace || "US" }],
    marketplace: row.marketplace || keywordLibraryState.marketplace || "US",
  });
};

function renderKeywordLibraryProducts(products) {
  const box = document.getElementById("klib-products");
  if (!box) return;
  if (!products.length) {
    box.innerHTML = `<div class="state">该关键词暂无商品快照。可先创建追踪任务，后续采集仍需单独确认。</div>`;
    return;
  }
  renderSortableTable(box, [
    { key: "title", label: "标题", render: (item) => amazonProductLink(item, displayTitle(item, item.asin), 68), sortVal: (item) => displayTitle(item, item.asin) },
    { key: "total_score", label: "得分", align: "num", numeric: true, render: (item) => scoreBadge(item.total_score), sortVal: (item) => item.total_score },
    { key: "price", label: "价格", align: "num", numeric: true, render: (item) => fmt.money(item.price), sortVal: (item) => item.price },
    { key: "product_size", label: "尺寸/规格", render: (item) => escapeHtml(truncate(fmt.text(item.product_size), 28)), sortVal: (item) => item.product_size || "" },
    { key: "rating", label: "评分", align: "num", numeric: true, render: (item) => fmt.num(item.rating), sortVal: (item) => item.rating },
    { key: "review_count", label: "评论", align: "num", numeric: true, render: (item) => fmt.int(item.review_count), sortVal: (item) => item.review_count },
    { key: "monthly_bought", label: "近月购买", align: "num", numeric: true, render: (item) => fmt.int(item.monthly_bought), sortVal: (item) => item.monthly_bought },
    { key: "organic_rank", label: "序位估算", align: "num", numeric: true, render: (item) => fmt.int(item.organic_rank), sortVal: (item) => item.organic_rank },
  ], products, { rowHash: (item) => productDetailHash(item.asin, item.keyword), defaultSort: { key: "total_score", dir: -1 }, exportName: "关键词资产相关商品" });
}

/* ---------- 视图：关键词创意工坊 ---------- */
async function viewKeywordWorkshop() {
  content.innerHTML = `
    <div class="panel kw-workshop-run">
      <div class="table-toolbar">
        <h2 style="margin:0;font-size:14px">生成候选关键词</h2>
        <div class="actions">
          <button class="btn" id="kws-run">生成候选</button>
          <button class="btn" id="kws-refresh">刷新候选池</button>
        </div>
      </div>
      <div class="kw-workshop-form">
        <label class="kw-workshop-seeds">种子词
          <textarea id="kws-seeds" rows="4" placeholder="例如：squishy&#10;fidget toys"></textarea>
        </label>
        <div class="kw-workshop-options">
          <label class="check-inline"><input id="kws-use-suggest" type="checkbox" checked /> Amazon 联想</label>
          <label class="check-inline"><input id="kws-use-titles" type="checkbox" checked /> 标题抽词</label>
          <label class="check-inline"><input id="kws-expand" type="checkbox" checked /> a-z / 0-9 扩展</label>
          <label>单种子联想请求上限<input id="kws-max-query" type="number" min="1" max="37" value="16" /></label>
          <label>标题样本上限<input id="kws-title-rows" type="number" min="20" max="2000" value="300" /></label>
        </div>
      </div>
      <div id="kws-run-result"></div>
    </div>
    <details class="panel kw-runs-panel" open>
      <summary>最近生成批次</summary>
      <div class="kw-runs-head">
        <span id="kws-run-filter-label" class="result-meta">未按批次筛选</span>
        <span class="queue-spacer"></span>
        <button class="btn btn-sm" id="kws-clear-run">清除批次筛选</button>
        <button class="btn btn-sm" id="kws-runs-refresh">刷新批次</button>
      </div>
      <div id="kws-runs"></div>
    </details>
    <div class="panel kw-workshop-results">
      <div class="table-toolbar">
        <h2 style="margin:0;font-size:14px">候选池</h2>
        <span id="kws-selected" class="result-meta">已选 0 个</span>
      </div>
      <div class="filters">
        <input id="kws-filter" placeholder="候选词过滤" />
        <select id="kws-status" class="sel">
          <option value="all">全部状态</option>
          <option value="candidate">候选</option>
          <option value="promoted">已入库</option>
          <option value="tracking">追踪中</option>
          <option value="ignored">已忽略</option>
        </select>
        <select id="kws-source" class="sel">
          <option value="all">全部来源</option>
          <option value="amazon_suggest">Amazon 联想</option>
          <option value="title_ngram">标题抽词</option>
          <option value="existing_keyword">已有数据</option>
        </select>
        <button class="btn" id="kws-apply">筛选</button>
        <button class="btn" id="kws-reset">重置</button>
      </div>
      <div class="queue-bar">
        <button class="btn btn-sm" id="kws-promote">加入关键词库</button>
        <button class="btn btn-sm" id="kws-track">创建追踪</button>
        <button class="btn btn-sm" id="kws-ignore">忽略</button>
        <button class="btn btn-sm" id="kws-restore">恢复候选</button>
      </div>
      <div id="kws-meta" class="result-meta"></div>
      <div id="kws-table"></div>
      <div id="kws-pager-wrap"></div>
    </div>`;

  document.getElementById("kws-filter").value = keywordWorkshopState.keyword;
  document.getElementById("kws-status").value = keywordWorkshopState.status;
  document.getElementById("kws-source").value = keywordWorkshopState.source;
  document.getElementById("kws-run").onclick = runKeywordWorkshop;
  document.getElementById("kws-refresh").onclick = () => loadKeywordIdeas();
  document.getElementById("kws-runs-refresh").onclick = () => loadKeywordRuns();
  document.getElementById("kws-clear-run").onclick = () => keywordWorkshopSelectRun(null);
  document.getElementById("kws-apply").onclick = () => loadKeywordIdeas(true);
  document.getElementById("kws-reset").onclick = async () => {
    keywordWorkshopState.keyword = "";
    keywordWorkshopState.status = "candidate";
    keywordWorkshopState.source = "all";
    keywordWorkshopState.runId = null;
    keywordWorkshopState.selected.clear();
    persistState(keywordWorkshopState);
    document.getElementById("kws-filter").value = "";
    document.getElementById("kws-status").value = "candidate";
    document.getElementById("kws-source").value = "all";
    await loadKeywordRuns();
    await loadKeywordIdeas(true);
  };
  document.getElementById("kws-promote").onclick = () => keywordWorkshopBulk("promote");
  document.getElementById("kws-track").onclick = () => keywordWorkshopBulk("track");
  document.getElementById("kws-ignore").onclick = () => keywordWorkshopBulk("ignored");
  document.getElementById("kws-restore").onclick = () => keywordWorkshopBulk("candidate");
  content.querySelectorAll(".filters input, .filters select").forEach((inp) => {
    inp.addEventListener("keydown", (e) => { if (e.key === "Enter") loadKeywordIdeas(true); });
  });
  await loadKeywordRuns();
  await loadKeywordIdeas();
}

async function runKeywordWorkshop() {
  const box = document.getElementById("kws-run-result");
  const btn = document.getElementById("kws-run");
  const seedText = document.getElementById("kws-seeds").value.trim();
  const useSuggest = document.getElementById("kws-use-suggest").checked;
  const useTitles = document.getElementById("kws-use-titles").checked;
  if (!seedText) return notice("请填写至少一个种子词", "bad");
  if (!useSuggest && !useTitles) return notice("请至少选择一个来源", "bad");
  if (useSuggest && !confirm("将访问 Amazon Suggest 获取搜索联想，不会打开商品页或自动采集。继续？")) return;
  btn.disabled = true;
  box.innerHTML = `<div class="state"><div class="spinner"></div>正在生成候选…</div>`;
  try {
    const result = await apiSend("/api/keyword-workshop/runs", "POST", {
      seed_text: seedText,
      marketplace: "US",
      use_suggest: useSuggest,
      use_titles: useTitles,
      expand_suggest: document.getElementById("kws-expand").checked,
      max_suggest_queries_per_seed: Number(document.getElementById("kws-max-query").value || 16),
      max_title_rows: Number(document.getElementById("kws-title-rows").value || 300),
    });
    keywordWorkshopState.lastRunId = result.run_id;
    box.innerHTML = renderKeywordWorkshopRunResult(result);
    keywordWorkshopState.offset = 0;
    keywordWorkshopState.status = "candidate";
    keywordWorkshopState.runId = result.run_id;
    keywordWorkshopState.selected.clear();
    persistState(keywordWorkshopState);
    const status = document.getElementById("kws-status");
    if (status) status.value = "candidate";
    await loadKeywordRuns();
    await loadKeywordIdeas();
    notice(`候选生成完成，保存/合并 ${fmt.int(result.total_saved)} 个`, "ok");
  } catch (err) {
    box.innerHTML = `<div class="state error">⚠ ${escapeHtml(err.message)}</div>`;
    notice(err.message, "bad");
  } finally {
    const b = document.getElementById("kws-run");
    if (b) b.disabled = false;
  }
}

function renderKeywordWorkshopRunResult(result) {
  const warnings = (result.warnings || []).slice(0, 3).map((w) => `<div class="sample">${escapeHtml(w)}</div>`).join("");
  return `<div class="kw-run-summary">
    <span>运行 #${escapeHtml(result.run_id)}</span>
    <span>种子 ${fmt.int((result.seed_keywords || []).length)}</span>
    <span>原始候选 ${fmt.int(result.total_found)}</span>
    <span>保存/合并 ${fmt.int(result.total_saved)}</span>
    <button class="btn btn-sm" type="button" onclick="window.keywordWorkshopRunStatus(${Number(result.run_id)}, 'ignored')">忽略本轮候选</button>
    <button class="btn btn-sm" type="button" onclick="window.keywordWorkshopRunStatus(${Number(result.run_id)}, 'candidate')">恢复本轮候选</button>
    ${warnings ? `<div class="kw-run-warnings">${warnings}</div>` : ""}
  </div>`;
}

async function loadKeywordIdeas(resetPage = false) {
  if (resetPage) {
    keywordWorkshopState.offset = 0;
    keywordWorkshopState.keyword = document.getElementById("kws-filter").value.trim();
    keywordWorkshopState.status = document.getElementById("kws-status").value;
    keywordWorkshopState.source = document.getElementById("kws-source").value;
    keywordWorkshopState.selected.clear();
  }
  persistState(keywordWorkshopState);
  const table = document.getElementById("kws-table");
  const meta = document.getElementById("kws-meta");
  const pager = document.getElementById("kws-pager-wrap");
  if (!table) return;
  table.innerHTML = `<div class="state"><div class="spinner"></div>加载中…</div>`;
  meta.textContent = "";
  pager.innerHTML = "";
  updateKeywordWorkshopSelected();
  const q = new URLSearchParams({
    limit: String(keywordWorkshopState.limit),
    offset: String(keywordWorkshopState.offset),
    marketplace: "US",
    sort_by: keywordWorkshopState.sortBy,
    sort_dir: keywordWorkshopState.sortDir,
  });
  if (keywordWorkshopState.keyword) q.set("keyword", keywordWorkshopState.keyword);
  if (keywordWorkshopState.status && keywordWorkshopState.status !== "all") q.set("status", keywordWorkshopState.status);
  if (keywordWorkshopState.source && keywordWorkshopState.source !== "all") q.set("source", keywordWorkshopState.source);
  if (keywordWorkshopState.runId) q.set("run_id", String(keywordWorkshopState.runId));
  try {
    const page = normalizePage(await api(`/api/keyword-workshop/ideas?${q.toString()}`), keywordWorkshopState.limit);
    syncRemoteSortState(keywordWorkshopState, page);
    const rows = page.rows || [];
    meta.textContent = pageSummary(page, "候选词")
      + (keywordWorkshopState.runId ? ` · 当前批次 #${keywordWorkshopState.runId}` : "")
      + " · 创意分是早期参考分，不替代关键词机会分";
    if (!rows.length) {
      keywordWorkshopIdeaRows.clear();
      table.innerHTML = `<div class="state">暂无关键词创意候选。</div>`;
      pager.innerHTML = renderPager("kws-pager", page, [20, 50, 100]);
      bindPager("kws-pager", keywordWorkshopState, page, loadKeywordIdeas);
      updateKeywordWorkshopSelected();
      return;
    }
    keywordWorkshopIdeaRows.clear();
    rows.forEach((row) => keywordWorkshopIdeaRows.set(String(row.id), row));
    const visibleIds = new Set(rows.map((r) => Number(r.id)));
    keywordWorkshopState.selected = new Set([...keywordWorkshopState.selected].filter((id) => visibleIds.has(id)));
    renderSortableTable(table, [
      { key: "select", label: `<input type="checkbox" id="kws-check-all" onclick="window.keywordWorkshopToggleAll(this)" />`, align: "check", sortable: false, csv: false,
        render: (r) => `<input type="checkbox" class="kw-idea-check" value="${escapeHtml(r.id)}"${keywordWorkshopState.selected.has(Number(r.id)) ? " checked" : ""} onclick="event.stopPropagation()" onchange="window.keywordWorkshopToggle(this)" />` },
      { key: "keyword", label: "候选关键词", render: (r) => escapeHtml(r.keyword), sortVal: (r) => r.keyword },
      { key: "recommendation_level", label: "建议", render: (r) => keywordIdeaLevelBadge(r.recommendation_level), sortVal: (r) => r.recommendation_level },
      { key: "idea_score", label: "创意分", align: "num", numeric: true, render: (r) => scoreBadge(r.idea_score), sortVal: (r) => r.idea_score },
      { key: "confidence_score", label: "置信度", align: "num", numeric: true, render: (r) => scoreBadge(r.confidence_score), sortVal: (r) => r.confidence_score },
      { key: "source_types", label: "来源", render: (r) => keywordIdeaSources(r.source_types), csv: (r) => (r.source_types || []).map(keywordIdeaSourceLabel).join(" / ") },
      { key: "status", label: "状态", render: (r) => keywordIdeaStatusBadge(r.status), sortVal: (r) => r.status },
      { key: "occurrence_count", label: "当前证据量", align: "num", numeric: true, render: (r) => fmt.int(r.occurrence_count), sortVal: (r) => r.occurrence_count },
      { key: "reason", label: "原因", render: (r) => `<span class="kw-reason" title="${escapeHtml(r.reason || "—")}">${escapeHtml(truncate(r.reason || "—", 42))}</span>`, csv: (r) => r.reason || "" },
      { key: "evidence", label: "证据", sortable: false, csv: false,
        render: (r) => `<button type="button" class="btn btn-sm" onclick="event.stopPropagation();window.openKeywordIdeaEvidence('${escapeHtml(r.id)}')">查看</button>` },
      { key: "updated_at", label: "更新时间", render: (r) => fmt.text(r.updated_at), sortVal: (r) => r.updated_at },
    ], rows, { remoteSort: remoteSortOptions(keywordWorkshopState, loadKeywordIdeas), exportName: "关键词创意候选" });
    pager.innerHTML = renderPager("kws-pager", page, [20, 50, 100]);
    bindPager("kws-pager", keywordWorkshopState, page, loadKeywordIdeas);
    updateKeywordWorkshopSelected();
  } catch (err) {
    keywordWorkshopIdeaRows.clear();
    table.innerHTML = `<div class="state error">⚠ ${escapeHtml(err.message)}</div>`;
  }
}

async function loadKeywordRuns() {
  const box = document.getElementById("kws-runs");
  const label = document.getElementById("kws-run-filter-label");
  if (!box) return;
  if (label) label.textContent = keywordWorkshopState.runId ? `正在查看批次 #${keywordWorkshopState.runId}` : "未按批次筛选";
  box.innerHTML = `<div class="state"><div class="spinner"></div>加载批次…</div>`;
  const q = new URLSearchParams({ limit: String(keywordWorkshopState.runsLimit), offset: "0", marketplace: "US" });
  try {
    const page = normalizePage(await api(`/api/keyword-workshop/runs?${q.toString()}`), keywordWorkshopState.runsLimit);
    const rows = page.rows || [];
    if (!rows.length) {
      box.innerHTML = `<div class="state">暂无生成批次。</div>`;
      return;
    }
    box.innerHTML = `<div class="kw-runs-list">${rows.map(renderKeywordRunItem).join("")}</div>`;
    box.querySelectorAll("[data-run-action]").forEach((btn) => {
      btn.onclick = () => {
        const action = btn.dataset.runAction;
        const runId = Number(btn.dataset.runId);
        if (action === "view") keywordWorkshopSelectRun(runId);
        else if (action === "ignored" || action === "candidate") keywordWorkshopRunStatus(runId, action);
      };
    });
  } catch (err) {
    box.innerHTML = `<div class="state error">批次加载失败：${escapeHtml(err.message)}</div>`;
  }
}

function renderKeywordRunItem(run) {
  const active = Number(keywordWorkshopState.runId) === Number(run.id) ? " active" : "";
  const seeds = Array.isArray(run.seed_keywords) ? run.seed_keywords.join("、") : "—";
  const sources = keywordRunSources(run.sources);
  const warning = run.warning_message ? `<div class="kw-run-warning">${escapeHtml(truncate(run.warning_message, 90))}</div>` : "";
  return `<div class="kw-run-item${active}">
    <div class="kw-run-main">
      <button type="button" class="chip kw-run-id" data-run-action="view" data-run-id="${escapeHtml(run.id)}">#${escapeHtml(run.id)}</button>
      <b title="${escapeHtml(seeds)}">${escapeHtml(truncate(seeds, 36))}</b>
      <span>${escapeHtml(fmt.text(run.created_at))}</span>
      <span>${sources}</span>
    </div>
    <div class="kw-run-stats">
      <span>保存 ${fmt.int(run.total_saved)}</span>
      <span>候选 ${fmt.int(run.candidate_count)}</span>
      <span>忽略 ${fmt.int(run.ignored_count)}</span>
      <span>入库 ${fmt.int(run.promoted_count)}</span>
      <span>追踪 ${fmt.int(run.tracking_count)}</span>
    </div>
    <div class="kw-run-actions">
      <button type="button" class="btn btn-sm" data-run-action="view" data-run-id="${escapeHtml(run.id)}">查看本轮</button>
      <button type="button" class="btn btn-sm" data-run-action="ignored" data-run-id="${escapeHtml(run.id)}">忽略候选</button>
      <button type="button" class="btn btn-sm" data-run-action="candidate" data-run-id="${escapeHtml(run.id)}">恢复候选</button>
    </div>
    ${warning}
  </div>`;
}

function keywordRunSources(sources) {
  const data = sources || {};
  const labels = [];
  if (data.amazon_suggest) labels.push("Amazon 联想");
  if (data.title_ngram) labels.push("标题抽词");
  return labels.length ? labels.join(" / ") : "—";
}

async function keywordWorkshopSelectRun(runId) {
  keywordWorkshopState.runId = runId ? Number(runId) : null;
  keywordWorkshopState.offset = 0;
  keywordWorkshopState.selected.clear();
  persistState(keywordWorkshopState);
  await loadKeywordRuns();
  await loadKeywordIdeas();
}

function keywordIdeaSourceLabel(source) {
  return ({ amazon_suggest: "Amazon 联想", title_ngram: "标题抽词", existing_keyword: "已有数据" })[source] || source || "—";
}
function keywordIdeaSources(sources) {
  const items = Array.isArray(sources) ? sources : [];
  return items.length ? items.map((s) => `<span class="badge badge-dim">${escapeHtml(keywordIdeaSourceLabel(s))}</span>`).join(" ") : "—";
}

window.openKeywordIdeaEvidence = (ideaId) => {
  const idea = keywordWorkshopIdeaRows.get(String(ideaId));
  if (!idea) {
    notice("当前页没有找到这条候选证据，请刷新候选池后再试", "bad");
    return;
  }
  openKeywordIdeaDialog(idea);
};

function openKeywordIdeaDialog(idea) {
  closeKeywordIdeaDialog();
  const modal = document.createElement("div");
  modal.id = "keyword-idea-dialog";
  modal.className = "modal-backdrop";
  modal.innerHTML = `
    <section class="modal-panel keyword-idea-modal" role="dialog" aria-modal="true" aria-label="候选关键词证据">
      <div class="modal-head">
        <h2>${escapeHtml(idea.keyword || "候选关键词")} <span class="badge badge-dim">#${escapeHtml(idea.id)}</span></h2>
        <button type="button" class="btn btn-sm" data-close-idea>关闭</button>
      </div>
      ${renderKeywordIdeaEvidence(idea)}
    </section>`;
  modal.addEventListener("click", (event) => {
    if (event.target === modal) closeKeywordIdeaDialog();
  });
  document.body.appendChild(modal);
  const closeButton = modal.querySelector("[data-close-idea]");
  if (closeButton) closeButton.onclick = closeKeywordIdeaDialog;
  const body = modal.querySelector(".kw-evidence-body");
  if (body) body.focus();
}

function closeKeywordIdeaDialog() {
  const modal = document.getElementById("keyword-idea-dialog");
  if (!modal) return false;
  modal.remove();
  return true;
}

function renderKeywordIdeaEvidence(idea) {
  const evidence = idea.evidence || {};
  const sourceCards = renderKeywordIdeaEvidenceSources(evidence.sources || {});
  const seedText = Array.isArray(idea.seed_keywords) && idea.seed_keywords.length
    ? idea.seed_keywords.join("、")
    : (Array.isArray(evidence.seeds) ? evidence.seeds.join("、") : "—");
  return `<div class="kw-evidence-body" tabindex="0">
    <div class="kw-evidence-summary">
      <span>状态 ${keywordIdeaStatusBadge(idea.status)}</span>
      <span>建议 ${keywordIdeaLevelBadge(idea.recommendation_level)}</span>
      <span>创意分 ${scoreBadge(idea.idea_score)}</span>
      <span>置信度 ${scoreBadge(idea.confidence_score)}</span>
      <span>当前证据量 ${fmt.int(idea.occurrence_count)}</span>
      <span>来源 ${keywordIdeaSources(idea.source_types)}</span>
    </div>
    <div class="kw-evidence-reason">
      <h3>推荐理由</h3>
      <p>${escapeHtml(idea.reason || "暂无推荐理由。")}</p>
    </div>
    <div class="kw-evidence-meta">
      <div><span>种子词</span><b>${escapeHtml(seedText || "—")}</b></div>
      <div><span>最近批次</span><b>${fmt.text(idea.last_run_id ? `#${idea.last_run_id}` : "")}</b></div>
      <div><span>更新时间</span><b>${escapeHtml(fmt.text(idea.updated_at))}</b></div>
    </div>
    <h3>来源证据</h3>
    <div class="kw-evidence-grid">${sourceCards || `<div class="kw-evidence-card">暂无来源明细。</div>`}</div>
  </div>`;
}

function renderKeywordIdeaEvidenceSources(sources) {
  const cards = [];
  const suggest = sources.amazon_suggest;
  if (suggest) {
    const queries = Array.isArray(suggest.queries) ? suggest.queries : [];
    cards.push(`<div class="kw-evidence-card">
      <h4>Amazon 联想</h4>
      <div class="kw-evidence-line"><span>最高位置</span><b>${fmt.text(suggest.best_rank ? `第 ${suggest.best_rank} 位` : "")}</b></div>
      <div class="kw-evidence-line"><span>触发查询</span><b>${escapeHtml(queries.slice(0, 8).join("、") || "—")}</b></div>
    </div>`);
  }
  const title = sources.title_ngram;
  if (title) {
    const examples = Array.isArray(title.examples) ? title.examples : [];
    cards.push(`<div class="kw-evidence-card">
      <h4>标题抽词</h4>
      <div class="kw-evidence-line"><span>出现次数</span><b>${fmt.int(title.count)}</b></div>
      ${renderKeywordIdeaExamples(examples)}
    </div>`);
  }
  const existing = sources.existing_keyword;
  if (existing) {
    cards.push(`<div class="kw-evidence-card">
      <h4>已有数据</h4>
      <div class="kw-evidence-line"><span>关键词 ID</span><b>${fmt.text(existing.keyword_id)}</b></div>
      <div class="kw-evidence-line"><span>关联商品</span><b>${fmt.int(existing.product_count)}</b></div>
      <div class="kw-evidence-line"><span>快照数</span><b>${fmt.int(existing.snapshot_count)}</b></div>
      <div class="kw-evidence-line"><span>机会均分</span><b>${fmt.num(existing.avg_total_score, 1)}</b></div>
    </div>`);
  }
  Object.entries(sources).forEach(([key, value]) => {
    if (["amazon_suggest", "title_ngram", "existing_keyword"].includes(key)) return;
    cards.push(`<div class="kw-evidence-card">
      <h4>${escapeHtml(keywordIdeaSourceLabel(key))}</h4>
      <pre>${escapeHtml(JSON.stringify(value, null, 2))}</pre>
    </div>`);
  });
  return cards.join("");
}

function renderKeywordIdeaExamples(examples) {
  if (!examples.length) return `<div class="kw-evidence-empty">暂无标题样例。</div>`;
  return `<ul class="kw-evidence-list">${
    examples.slice(0, 6).map((item) => `<li>${escapeHtml(item)}</li>`).join("")
  }</ul>`;
}

function keywordIdeaStatusBadge(status) {
  const map = {
    candidate: ["候选", "badge-warn"],
    promoted: ["已入库", "badge-good"],
    tracking: ["追踪中", "badge-good"],
    ignored: ["已忽略", "badge-dim"],
  };
  const item = map[status] || [status || "—", "badge-dim"];
  return `<span class="badge ${item[1]}">${escapeHtml(item[0])}</span>`;
}
function keywordIdeaLevelBadge(level) {
  const cls = level === "优先验证" ? "badge-good" : level === "可观察" ? "badge-warn" : level === "暂不建议" ? "badge-bad" : "badge-dim";
  return `<span class="badge ${cls}">${escapeHtml(level || "仅作灵感")}</span>`;
}
function keywordWorkshopSelectedIds() {
  return [...keywordWorkshopState.selected].map((id) => Number(id)).filter(Boolean);
}
function updateKeywordWorkshopSelected() {
  const el = document.getElementById("kws-selected");
  if (el) el.textContent = `已选 ${keywordWorkshopState.selected.size} 个`;
  const all = document.getElementById("kws-check-all");
  if (all) {
    const checks = [...document.querySelectorAll(".kw-idea-check")];
    all.checked = checks.length > 0 && checks.every((cb) => cb.checked);
  }
}
window.keywordWorkshopToggle = (checkbox) => {
  const id = Number(checkbox.value);
  if (!id) return;
  if (checkbox.checked) keywordWorkshopState.selected.add(id);
  else keywordWorkshopState.selected.delete(id);
  updateKeywordWorkshopSelected();
  rememberAgentBusinessContext();
};
window.keywordWorkshopToggleAll = (checkbox) => {
  document.querySelectorAll(".kw-idea-check").forEach((cb) => {
    cb.checked = checkbox.checked;
    const id = Number(cb.value);
    if (checkbox.checked) keywordWorkshopState.selected.add(id);
    else keywordWorkshopState.selected.delete(id);
  });
  updateKeywordWorkshopSelected();
  rememberAgentBusinessContext();
};
async function keywordWorkshopBulk(action) {
  const ids = keywordWorkshopSelectedIds();
  if (!ids.length) return notice("请先勾选候选词", "bad");
  const labels = { promote: "加入关键词库", track: "创建追踪任务", ignored: "忽略", candidate: "恢复为候选" };
  if (!confirm(`确认对 ${ids.length} 个候选词执行「${labels[action] || action}」？`)) return;
  try {
    if (action === "promote") {
      await apiSend("/api/keyword-workshop/ideas/promote", "POST", { ids, marketplace: "US" });
    } else if (action === "track") {
      await apiSend("/api/keyword-workshop/ideas/create-tracking", "POST", { ids, marketplace: "US", target_snapshots: 3 });
    } else {
      await apiSend("/api/keyword-workshop/ideas/status", "POST", { ids, marketplace: "US", status: action });
    }
    keywordWorkshopState.selected.clear();
    await loadKeywordRuns();
    await loadKeywordIdeas();
    notice("操作完成", "ok");
  } catch (err) {
    notice(err.message, "bad");
  }
}

window.keywordWorkshopRunStatus = async (runId, status) => {
  const labels = { ignored: "忽略本轮候选", candidate: "恢复本轮候选" };
  const detail = status === "ignored"
    ? "只会把本轮仍处于“候选”的记录标记为已忽略；已入库、追踪中的候选不会受影响。"
    : "只会把本轮“已忽略”的记录恢复为候选。";
  if (!confirm(`确认执行「${labels[status] || status}」？\n${detail}`)) return;
  try {
    const result = await apiSend(`/api/keyword-workshop/runs/${encodeURIComponent(runId)}/ideas/status`, "POST", {
      marketplace: "US",
      status,
    });
    keywordWorkshopState.selected.clear();
    await loadKeywordRuns();
    await loadKeywordIdeas();
    notice(`${labels[status] || "操作"}完成：更新 ${fmt.int(result.updated)} 条`, "ok");
  } catch (err) {
    notice(err.message, "bad");
  }
};

/* ---------- 视图：关键词机会 ---------- */
async function viewKeywords() {
  content.innerHTML = `
    <details id="kw-primary-panel" class="panel kw-primary-panel"${keywordState.primaryOpen ? " open" : ""}>
      <summary>一级关键词分组（点击展开）</summary>
      <p class="kw-primary-tip">把相同中心词的关键词归为一组，如 mini squishy / cow squishy → <b>squishy</b>，gift for man / man → <b>man</b>。基于机会列表前 500 条聚合，一级带组内平均机会分（≥70 标「蓝海赛道」、按机会排序），点一级看二级关键词，再点二级看详情。</p>
      <div class="kw-primary-controls">
        <span class="kw-ctrl-label">归类方式</span>
        <button class="chip kw-mode${keywordGroupMode === "tail" ? " active" : ""}" data-mode="tail">词尾</button>
        <button class="chip kw-mode${keywordGroupMode === "first" ? " active" : ""}" data-mode="first">词首</button>
        <button class="chip kw-mode${keywordGroupMode === "shared" ? " active" : ""}" data-mode="shared">共享词</button>
      </div>
      <div id="kw-primary-chips" class="kw-primary-chips"></div>
      <div id="kw-secondary" class="kw-secondary"></div>
    </details>
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
  const primaryPanel = document.getElementById("kw-primary-panel");
  primaryPanel.addEventListener("toggle", () => {
    keywordState.primaryOpen = primaryPanel.open;
    persistState(keywordState);
    if (primaryPanel.open && !primaryPanel.dataset.loaded) {
      primaryPanel.dataset.loaded = "1";
      keywordAllRows = null; // 每次进入页面首开重拉，避免陈旧
      renderKeywordPrimaryGroups();
    }
  });
  primaryPanel.querySelectorAll(".kw-mode").forEach((b) => {
    b.onclick = () => {
      keywordGroupMode = b.dataset.mode;
      keywordState.groupMode = keywordGroupMode;
      persistState(keywordState);
      primaryPanel.querySelectorAll(".kw-mode").forEach((x) => x.classList.toggle("active", x === b));
      renderKeywordPrimaryGroups();
    };
  });
  document.getElementById("kw-filter").value = keywordState.keyword;
  document.getElementById("kw-min-products").value = keywordState.minProducts;
  document.getElementById("kw-apply").onclick = () => loadKeywords(true);
  document.getElementById("kw-reset").onclick = () => {
    keywordState.keyword = "";
    keywordState.minProducts = "";
    keywordProductState.keyword = "";
    keywordProductState.scope = "current";
    keywordProductState.offset = 0;
    persistState(keywordProductState);
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
  persistState(keywordState);
  const table = document.getElementById("kw-table");
  const meta = document.getElementById("kw-meta");
  const pager = document.getElementById("kw-pager-wrap");
  table.innerHTML = `<div class="state"><div class="spinner"></div>加载中…</div>`;
  meta.textContent = "";
  pager.innerHTML = "";
  const q = new URLSearchParams({
    limit: String(keywordState.limit),
    offset: String(keywordState.offset),
    sort_by: keywordState.sortBy,
    sort_dir: keywordState.sortDir,
  });
  if (keywordState.keyword) q.set("keyword", keywordState.keyword);
  if (keywordState.minProducts) q.set("min_products", keywordState.minProducts);
  try {
    const page = normalizePage(await api(`/api/keywords/opportunities?${q.toString()}`), keywordState.limit);
    syncRemoteSortState(keywordState, page);
    const rows = page.rows;
    meta.textContent = pageSummary(page, "关键词") + " · 当前指标按各关键词最新完整采集批次";
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
    { key: "product_count", label: "当前商品数", align: "num", numeric: true, render: (r) => fmt.int(r.product_count), sortVal: (r) => r.product_count },
    { key: "avg_monthly_bought", label: "需求", align: "num", numeric: true, render: (r) => fmt.int(r.avg_monthly_bought), sortVal: (r) => r.avg_monthly_bought },
    { key: "avg_review_count", label: "竞争", align: "num", numeric: true, render: (r) => fmt.int(r.avg_review_count), sortVal: (r) => r.avg_review_count },
    { key: "avg_price", label: "价格带", align: "num", numeric: true, render: (r) => fmt.money(r.avg_price), sortVal: (r) => r.avg_price },
    { key: "avg_organic_rank", label: "自然序位估算", align: "num", numeric: true, render: (r) => fmt.num(r.avg_organic_rank, 0), sortVal: (r) => r.avg_organic_rank },
  ], rows, { remoteSort: remoteSortOptions(keywordState, loadKeywords), exportName: "关键词机会", onRowClick: showKeywordDetail });
    pager.innerHTML = renderPager("kw-pager", page, [20, 50, 100]);
    bindPager("kw-pager", keywordState, page, loadKeywords);
    const selectedKeyword = String(keywordProductState.keyword || "").trim();
    const selectedRow = selectedKeyword
      ? rows.find((row) => String(row.keyword || "").trim() === selectedKeyword)
      : null;
    if (selectedRow) {
      showKeywordDetail(selectedRow, { resetProductPage: false, scrollBehavior: "auto" });
    }
  } catch (err) {
    table.innerHTML = `<div class="state error">⚠ ${escapeHtml(err.message)}</div>`;
  }
}

/* ---------- 一级关键词分组（聚合机会 + 多种字符串归类规则） ---------- */
let keywordGroupMode = ["tail", "first", "shared"].includes(keywordState.groupMode) ? keywordState.groupMode : "tail";   // tail 词尾 / first 词首 / shared 共享词
let keywordAllRows = null;       // 缓存全量行，切换归类方式不重拉（进入页面首开会清空重拉）
let keywordPrimaryGroups = new Map();

const KW_STOPWORDS = new Set(["for", "the", "a", "an", "of", "with", "and", "to", "in", "on", "by", "my", "your"]);

function kwTokens(kw) {
  return String(kw || "").trim().toLowerCase().split(/\s+/).filter(Boolean);
}
/* 简单单复数归一：去单个尾 s（不动 ss 结尾、长度>3），让 dog/dogs 等并组。 */
function kwNorm(t) {
  return (t.length > 3 && t.endsWith("s") && !t.endsWith("ss")) ? t.slice(0, -1) : t;
}
/* 归类键：词尾 / 词首 / 出现最多的非停用词。 */
function kwGroupKey(kw, mode, freq) {
  const toks = kwTokens(kw).map(kwNorm);
  if (!toks.length) return "";
  if (mode === "first") return toks[0];
  if (mode === "shared") {
    const pool = toks.filter((t) => !KW_STOPWORDS.has(t));
    const cand = pool.length ? pool : toks;
    let best = cand[0], bestF = -1;
    cand.forEach((t) => { const f = freq.get(t) || 0; if (f >= bestF) { bestF = f; best = t; } });
    return best;
  }
  return toks[toks.length - 1]; // tail
}
function kwGroupAgg(list) {
  const opp = list.map((r) => Number(r.opportunity_score) || 0);
  const avg = opp.length ? opp.reduce((a, b) => a + b, 0) / opp.length : 0;
  const max = opp.length ? Math.max(...opp) : 0;
  const products = list.reduce((a, r) => a + (Number(r.product_count) || 0), 0);
  return { count: list.length, avg, max, products };
}

async function renderKeywordPrimaryGroups() {
  const chips = document.getElementById("kw-primary-chips");
  const sec = document.getElementById("kw-secondary");
  if (!chips) return;
  chips.innerHTML = `<div class="state"><div class="spinner"></div>分组中…</div>`;
  if (sec) sec.innerHTML = "";
  try {
    if (!keywordAllRows) {
      const page = normalizePage(await api(`/api/keywords/opportunities?limit=500&offset=0`), 500);
      keywordAllRows = page.rows || [];
    }
    const rows = keywordAllRows;
    const freq = new Map();
    if (keywordGroupMode === "shared") {
      for (const r of rows) {
        for (const t of new Set(kwTokens(r.keyword).map(kwNorm))) freq.set(t, (freq.get(t) || 0) + 1);
      }
    }
    const groups = new Map();
    for (const r of rows) {
      const p = kwGroupKey(r.keyword, keywordGroupMode, freq);
      if (!p) continue;
      if (!groups.has(p)) groups.set(p, []);
      groups.get(p).push(r);
    }
    keywordPrimaryGroups = groups;
    if (!groups.size) { chips.innerHTML = `<div class="state">暂无关键词数据。</div>`; return; }
    // 按组平均机会分降序（赛道机会优先），并列按组大小
    const sorted = [...groups.entries()]
      .map(([p, list]) => [p, list, kwGroupAgg(list)])
      .sort((a, b) => b[2].avg - a[2].avg || b[2].count - a[2].count || a[0].localeCompare(b[0]));
    chips.innerHTML = sorted.map(([p, , agg]) =>
      `<button class="chip kw-primary-chip" data-primary="${escapeHtml(p)}">` +
        `${escapeHtml(p)} ${scoreBadge(Math.round(agg.avg))}` +
        ` <span class="kw-grp-count">${agg.count}词·${fmt.int(agg.products)}品</span>` +
        (agg.avg >= 70 ? ` <span class="badge badge-good">蓝海</span>` : "") +
      `</button>`
    ).join("");
    chips.querySelectorAll(".kw-primary-chip").forEach((b) => { b.onclick = () => selectPrimary(b.dataset.primary, b); });
    if (sec) sec.innerHTML = `<div class="state">点击上方一级关键词，查看其下二级关键词。</div>`;
  } catch (err) {
    chips.innerHTML = `<div class="state error">分组加载失败：${escapeHtml(err.message)}</div>`;
  }
}

function selectPrimary(primary, btn) {
  document.querySelectorAll(".kw-primary-chip").forEach((b) => b.classList.toggle("active", b === btn));
  const sec = document.getElementById("kw-secondary");
  if (!sec) return;
  const list = (keywordPrimaryGroups.get(primary) || []).slice()
    .sort((a, b) => Number(b.opportunity_score || 0) - Number(a.opportunity_score || 0));
  const agg = kwGroupAgg(list);
  sec.innerHTML =
    `<div class="kw-secondary-head">一级「${escapeHtml(primary)}」· 平均机会 ${Math.round(agg.avg)} · 最高 ${Math.round(agg.max)} · ${agg.count} 个二级词 · 共 ${fmt.int(agg.products)} 商品</div>` +
    `<div class="kw-secondary-chips">` + list.map((r) =>
      `<button class="chip kw-secondary-chip" data-kw="${escapeHtml(r.keyword)}">${escapeHtml(r.keyword)} ${scoreBadge(r.opportunity_score)}</button>`
    ).join("") + `</div>`;
  sec.querySelectorAll(".kw-secondary-chip").forEach((b) => {
    const r = list.find((x) => x.keyword === b.dataset.kw);
    b.onclick = () => {
      showKeywordDetail(r);
      const d = document.getElementById("kw-detail");
      if (d) d.scrollIntoView({ behavior: "smooth", block: "start" });
    };
  });
}

/* 关键词机会详情展开：机会原因 / 风险警告 / 进入策略 + 该词下商品下钻。 */
function showKeywordDetail(r, { resetProductPage = true, scrollBehavior = "smooth" } = {}) {
  const box = document.getElementById("kw-detail");
  if (!box) return;
  keywordProductState.keyword = r.keyword || "";
  if (resetProductPage) keywordProductState.offset = 0;
  keywordProductState.currentCount = Number(r.product_count) || 0;
  keywordProductState.historicalCount = Number(r.historical_product_count) || keywordProductState.currentCount;
  keywordProductState.currentSnapshotAt = r.latest_snapshot_at || "";
  keywordProductState.previousSnapshotAt = r.previous_snapshot_at || "";
  persistState(keywordProductState);
  const level = r.opportunity_level ? ` <span class="badge badge-dim">${escapeHtml(r.opportunity_level)}</span>` : "";
  const retention = r.retention_rate == null ? "—" : `${fmt.num(Number(r.retention_rate) * 100, 1)}%`;
  const rankChanges = r.comparable_rank_count == null
    ? "—"
    : `${fmt.int(r.rank_changed_count)} / ${fmt.int(r.comparable_rank_count)}`;
  box.innerHTML = `
    <div class="panel">
      <div class="table-toolbar">
        <h2 style="margin:0">关键词「${escapeHtml(r.keyword)}」机会详情${level}</h2>
        <button class="btn btn-sm" id="kw-opportunity-add-project" type="button">加入研究项目</button>
      </div>
      <div class="evidence-warning"><strong>当前口径</strong><span>${escapeHtml(r.scope_message || "当前指标仅使用该关键词最新完整采集批次。")}</span></div>
      <div class="advice-row"><span>机会原因</span><p>${escapeHtml(r.opportunity_reason || "—")}</p></div>
      <div class="advice-row"><span>风险警告</span><p>${escapeHtml(r.risk_warnings || "—")}</p></div>
      <div class="advice-row"><span>进入策略</span><p>${escapeHtml(r.entry_strategy || "—")}</p></div>
      <h3 style="margin:14px 0 8px;font-size:13px">维度细分</h3>
      <div class="kv">
        <div><span>机会分</span>${scoreBadge(r.opportunity_score)}</div>
        <div><span>当前商品数</span>${fmt.int(r.product_count)}</div>
        <div><span>需求分</span>${fmt.num(r.avg_demand_score, 0)}</div>
        <div><span>竞争分</span>${fmt.num(r.avg_competition_score, 0)}</div>
        <div><span>评分分</span>${fmt.num(r.avg_rating_score, 0)}</div>
        <div><span>价格分</span>${fmt.num(r.avg_price_score, 0)}</div>
        <div><span>序位分</span>${fmt.num(r.avg_rank_score, 0)}</div>
        <div><span>价格区间</span>${fmt.money(r.min_price)}–${fmt.money(r.max_price)}</div>
        <div><span>估算前10</span>${fmt.int(r.top10_count)}</div>
        <div><span>广告位</span>${fmt.int(r.sponsored_count)}</div>
        <div><span>当前批次</span>${fmt.text(r.latest_snapshot_at)}</div>
      </div>
      <h3 style="margin:14px 0 8px;font-size:13px">批次变化证据</h3>
      <div class="kv">
        <div><span>历史观察并集</span>${fmt.int(r.historical_product_count)}</div>
        <div><span>采集时点</span>${fmt.int(r.observation_count)}</div>
        <div><span>上一批次</span>${fmt.text(r.previous_snapshot_at)}</div>
        <div><span>上一批商品</span>${fmt.int(r.previous_product_count)}</div>
        <div><span>留存商品</span>${fmt.int(r.retained_product_count)}</div>
        <div><span>新进入</span>${fmt.int(r.entered_product_count)}</div>
        <div><span>退出</span>${fmt.int(r.exited_product_count)}</div>
        <div><span>批次留存率</span>${retention}</div>
        <div><span>序位变化</span>${rankChanges}</div>
      </div>
      <div class="result-meta" style="margin-top:10px">${escapeHtml(r.transition_summary || "尚无可比较的上一批次。")}</div>
    </div>
    <div class="panel">
      <div class="table-toolbar">
        <h2 style="margin:0;font-size:14px">该词下商品</h2>
        <div class="actions" role="group" aria-label="商品观察范围">
          <button type="button" class="chip kw-product-scope${keywordProductState.scope === "current" ? " active" : ""}" data-scope="current" title="只看最新完整采集批次">当前批次</button>
          <button type="button" class="chip kw-product-scope${keywordProductState.scope === "observed" ? " active" : ""}" data-scope="observed" title="查看历史上曾出现过的商品并集">历史观察</button>
        </div>
      </div>
      <div id="kw-products-meta" class="result-meta"></div>
      <div id="kw-products-scope" class="result-meta"></div>
      <div id="kw-products"></div>
      <div id="kw-products-pager-wrap"></div>
    </div>`;
  document.getElementById("kw-opportunity-add-project").onclick = () => openResearchAssociationDialog({
    assetType: "keyword",
    items: [{ key: r.keyword, label: r.keyword, marketplace: r.marketplace || "US" }],
    marketplace: r.marketplace || "US",
  });
  box.querySelectorAll(".kw-product-scope").forEach((button) => {
    button.onclick = () => {
      const scope = button.dataset.scope === "observed" ? "observed" : "current";
      if (keywordProductState.scope === scope) return;
      keywordProductState.scope = scope;
      keywordProductState.offset = 0;
      persistState(keywordProductState);
      box.querySelectorAll(".kw-product-scope").forEach((item) => item.classList.toggle("active", item === button));
      loadKeywordProducts();
      rememberAgentBusinessContext();
    };
  });
  box.scrollIntoView({ behavior: scrollBehavior, block: "nearest" });
  loadKeywordProducts();
}

async function loadKeywordProducts() {
  persistState(keywordProductState);
  const keyword = keywordProductState.keyword;
  const box = document.getElementById("kw-products");
  const meta = document.getElementById("kw-products-meta");
  const scopeNote = document.getElementById("kw-products-scope");
  const pager = document.getElementById("kw-products-pager-wrap");
  if (!box || !keyword) return;
  box.innerHTML = `<div class="state"><div class="spinner"></div>加载中…</div>`;
  meta.textContent = "";
  if (scopeNote) scopeNote.textContent = "";
  pager.innerHTML = "";
  const scope = keywordProductState.scope === "observed" ? "observed" : "current";
  const q = new URLSearchParams({
    keyword,
    keyword_exact: "true",
    keyword_scope: scope,
    limit: String(keywordProductState.limit),
    offset: String(keywordProductState.offset),
    sort_by: keywordProductState.sortBy,
    sort_dir: keywordProductState.sortDir,
  });
  try {
    const page = normalizePage(await api(`/api/products?${q.toString()}`), keywordProductState.limit);
    syncRemoteSortState(keywordProductState, page);
    const rows = page.rows;
    const scopeLabel = scope === "current"
      ? `当前批次 ${keywordProductState.currentSnapshotAt || ""}`.trim()
      : "历史观察并集";
    meta.textContent = pageSummary(page, "商品") + ` · 关键词：${keyword} · ${scopeLabel}`;
    if (scopeNote) scopeNote.textContent = page.scope_message || "";
    if (!rows.length) {
      box.innerHTML = `<div class="state">该关键词下暂无商品。</div>`;
      pager.innerHTML = renderPager("kw-products-pager", page, [10, 25, 50]);
      bindPager("kw-products-pager", keywordProductState, page, loadKeywordProducts);
      return;
    }
    renderSortableTable(box, [
      { key: "title", label: "标题", render: (item) => amazonProductLink(item, displayTitle(item, item.asin), 68), sortVal: (item) => displayTitle(item, item.asin) },
      { key: "total_score", label: "得分", align: "num", numeric: true, render: (item) => scoreBadge(item.total_score), sortVal: (item) => item.total_score },
      { key: "price", label: "价格", align: "num", numeric: true, render: (item) => fmt.money(item.price), sortVal: (item) => item.price },
      { key: "product_size", label: "尺寸/规格", render: (item) => escapeHtml(truncate(fmt.text(item.product_size), 28)), sortVal: (item) => item.product_size || "" },
      { key: "rating", label: "评分", align: "num", numeric: true, render: (item) => fmt.num(item.rating), sortVal: (item) => item.rating },
      { key: "review_count", label: "评论", align: "num", numeric: true, render: (item) => fmt.int(item.review_count), sortVal: (item) => item.review_count },
      { key: "monthly_bought", label: "近月购买", align: "num", numeric: true, render: (item) => fmt.int(item.monthly_bought), sortVal: (item) => item.monthly_bought },
      { key: "organic_rank", label: "序位估算", align: "num", numeric: true, render: (item) => fmt.int(item.organic_rank), sortVal: (item) => item.organic_rank },
      { key: "rank_snapshot_at", label: "观察批次", render: (item) => fmt.text(item.rank_snapshot_at), sortVal: (item) => item.rank_snapshot_at || "" },
    ], rows, { rowHash: (item) => productDetailHash(item.asin, keyword), remoteSort: remoteSortOptions(keywordProductState, loadKeywordProducts), exportName: `关键词-${keyword}-商品` });
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
  const untraceable = rows.filter((row) => row.evidence?.status !== "可追溯").length;
  content.innerHTML = `${untraceable ? `<div class="evidence-warning"><strong>评论证据提示</strong><span>${untraceable} 条洞察含不可核验或部分可追溯样本，不应直接用于高置信选品结论。</span></div>` : ""}<div class="result-meta">共 ${rows.length} 条 · 点列头排序</div><div id="rv-table"></div>`;
  renderSortableTable(document.getElementById("rv-table"), [
    { key: "title", label: "商品 / ASIN", render: (r) => amazonProductLink(r, displayTitle(r, r.asin || r.keyword || "—"), 80), sortVal: (r) => displayTitle(r, r.asin || r.keyword || "") },
    { key: "samples", label: "样本数", align: "num", numeric: true, render: (r) => fmt.int(r.evidence?.sample_count ?? r.review_count), sortVal: (r) => r.evidence?.sample_count ?? r.review_count },
    { key: "evidence", label: "证据状态", render: (r) => reviewEvidenceBadge(r.evidence), sortVal: (r) => r.evidence?.traceable_rate ?? 0 },
    { key: "neg", label: "低分占比", align: "num", numeric: true, render: (r) => formatNegativeRate(r), sortVal: (r) => negativeRateValue(r) },
    { key: "pain", label: "主要痛点", render: (r) => escapeHtml(truncate(formatPainPoints(r.top_pain_points || r.pain_points), 60)), csv: (r) => formatPainPoints(r.top_pain_points || r.pain_points) },
    { key: "opp", label: "改良机会", render: (r) => escapeHtml(truncate(r.opportunity_summary || r.improvement_opportunities || "—", 60)), csv: (r) => r.opportunity_summary || r.improvement_opportunities || "" },
  ], rows, {
    rowHash: (row) => /^[A-Z0-9]{10}$/i.test(String(row.asin || "")) ? productDetailHash(row.asin) : "",
    defaultSort: { key: "neg", dir: -1 },
    exportName: "评论洞察",
  });
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
function viewTasks() {
  content.innerHTML = `
    <div class="panel task-center-controls">
      <div class="table-toolbar">
        <div>
          <h2 style="margin:0">任务中心</h2>
          <div class="cell-sub">统一查看采集与本地 HTML 入库记录</div>
        </div>
        <div class="actions">
          <a class="btn btn-sm" href="#/crawl">手动采集</a>
          <a class="btn btn-sm" href="#/import">本地 HTML 入库</a>
        </div>
      </div>
      <form id="task-filters" class="filters compact task-center-filters">
        <input id="task-filter-keyword" placeholder="关键词筛选" />
        <select id="task-filter-type" class="sel">
          <option value="all">全部类型</option>
          <option value="crawl">采集</option>
          <option value="import">入库</option>
        </select>
        <select id="task-filter-status" class="sel">
          <option value="all">全部状态</option>
          <option value="运行中">运行中</option>
          <option value="完成">完成</option>
          <option value="异常停止">异常停止</option>
          <option value="失败">失败</option>
        </select>
        <button class="btn" type="submit">筛选</button>
        <button class="btn" id="task-filter-reset" type="button">重置</button>
      </form>
    </div>
    <div class="table-toolbar">
      <div id="task-meta" class="result-meta" style="margin:0"></div>
      <div class="actions">
        <span id="task-selected" class="selected-count">Agent 上下文：已选 0 个</span>
        <button class="btn btn-sm" id="task-clear-selected" disabled>清空选择</button>
      </div>
    </div>
    <div id="task-table"></div>
    <div id="task-pager"></div>`;
  document.getElementById("task-filter-keyword").value = taskState.keyword;
  document.getElementById("task-filter-type").value = taskState.jobType;
  document.getElementById("task-filter-status").value = taskState.status;
  document.getElementById("task-filters").onsubmit = (event) => {
    event.preventDefault();
    taskState.keyword = document.getElementById("task-filter-keyword").value.trim();
    taskState.jobType = document.getElementById("task-filter-type").value;
    taskState.status = document.getElementById("task-filter-status").value;
    loadTaskJobs(true);
  };
  document.getElementById("task-filter-reset").onclick = () => {
    taskState.keyword = "";
    taskState.jobType = "all";
    taskState.status = "all";
    taskState.offset = 0;
    persistState(taskState);
    viewTasks();
  };
  document.getElementById("task-clear-selected").onclick = () => {
    taskSelected.clear();
    document.querySelectorAll(".task-check").forEach((check) => { check.checked = false; });
    updateTaskSelected();
    rememberAgentBusinessContext();
  };
  updateTaskSelected();
  loadTaskJobs();
}

async function loadTaskJobs(resetPage = false) {
  if (resetPage) taskState.offset = 0;
  persistState(taskState);
  const table = document.getElementById("task-table");
  const meta = document.getElementById("task-meta");
  const pager = document.getElementById("task-pager");
  if (!table || !meta || !pager) return;
  table.innerHTML = `<div class="state"><div class="spinner"></div>加载任务记录…</div>`;
  meta.textContent = "";
  pager.innerHTML = "";
  const query = new URLSearchParams({
    limit: String(taskState.limit),
    offset: String(taskState.offset),
  });
  if (taskState.keyword) query.set("keyword", taskState.keyword);
  if (taskState.jobType !== "all") query.set("job_type", taskState.jobType);
  if (taskState.status !== "all") query.set("status", taskState.status);
  try {
    const page = normalizePage(await api(`/api/tasks/page?${query.toString()}`), taskState.limit);
    const rows = (page.rows || []).map((row, index) => ({
      ...row,
      _taskRowId: String(row.id ?? `${page.offset}-${index}`),
    }));
    taskErrorRows.clear();
    rows.forEach((row) => {
      taskErrorRows.set(row._taskRowId, row);
      taskRows.set(row._taskRowId, row);
    });
    meta.textContent = `${pageSummary(page, "任务")} · 默认按最新时间排列`;
    if (!rows.length) {
      table.innerHTML = `<div class="state">暂无符合当前条件的任务记录。</div>`;
    } else {
      renderSortableTable(table, [
        { key: "select", label: "", sortable: false, csv: false, align: "check", render: renderTaskCheckbox },
        { key: "time", label: "时间", sortable: false, render: (r) => fmt.text(r.created_at || r.started_at) },
        { key: "type", label: "类型", sortable: false, render: (r) => escapeHtml(taskDisplayType(r)) },
        { key: "keyword", label: "关键词", sortable: false, render: (r) => escapeHtml(r.keyword || "—") },
        { key: "status", label: "状态", sortable: false, render: (r) => statusBadge(r.status) },
        { key: "valid_count", label: "有效数", sortable: false, align: "num", numeric: true, render: (r) => taskMetric(r, "valid") },
        { key: "ingested_count", label: "入库数", sortable: false, align: "num", numeric: true, render: (r) => taskMetric(r, "ingested") },
        { key: "error", label: "错误日志", sortable: false, render: renderTaskErrorCell, csv: taskErrorText },
      ], rows, {
        exportName: "任务记录",
        onDraw: bindTaskErrorButtons,
      });
    }
    pager.innerHTML = renderPager("task-pager-inner", page, [10, 25, 50, 100]);
    bindPager("task-pager-inner", taskState, page, loadTaskJobs);
    updateTaskSelected();
  } catch (err) {
    table.innerHTML = `<div class="state error">⚠ ${escapeHtml(err.message)}</div>`;
    meta.textContent = "任务记录加载失败";
  }
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

function renderTaskCheckbox(row) {
  const rowId = String(row._taskRowId || "");
  const checked = taskSelected.has(rowId) ? " checked" : "";
  return `<input type="checkbox" class="prod-check task-check" value="${escapeHtml(rowId)}"${checked} onclick="event.stopPropagation()" onchange="window.taskToggle(this)" />`;
}

window.taskToggle = (checkbox) => {
  const rowId = String(checkbox.value || "");
  if (!rowId) return;
  if (checkbox.checked) taskSelected.add(rowId);
  else taskSelected.delete(rowId);
  updateTaskSelected();
  rememberAgentBusinessContext();
};

function updateTaskSelected() {
  const el = document.getElementById("task-selected");
  if (el) el.textContent = `Agent 上下文：已选 ${taskSelected.size} 个`;
  const clear = document.getElementById("task-clear-selected");
  if (clear) clear.disabled = taskSelected.size === 0;
}

function taskSelectedRowIds() {
  return [...taskSelected].map((id) => String(id || "").trim()).filter(Boolean).slice(0, 5);
}

function taskSelectedRows() {
  return taskSelectedRowIds().map((rowId) => {
    const row = taskRows.get(rowId) || {};
    return {
      row_id: rowId,
      id: row.id ?? null,
      type: taskDisplayType(row),
      status: row.status || "",
      keyword: row.keyword || "",
      has_error: !!taskErrorText(row),
      ingested_count: taskMetricValue(row, "ingested") ?? 0,
      valid_count: taskMetricValue(row, "valid") ?? 0,
      started_at: row.started_at || row.created_at || "",
      finished_at: row.finished_at || "",
    };
  });
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
      <div class="actions crawl-flow-links">
        <a class="btn btn-sm" href="#/tasks">查看任务中心</a>
        <a class="btn btn-sm" href="#/import">本地 HTML 入库</a>
      </div>
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
  document.getElementById("cr-keyword").value = crawlState.keyword;
  document.getElementById("cr-pages").value = crawlState.pages;
  modeBox.checked = crawlState.queueMode;
  modeBox.onchange = () => setQueueMode(modeBox.checked);
  bindStateInputs(["cr-keyword", "cr-pages"], () => {
    crawlState.keyword = document.getElementById("cr-keyword").value.trim();
    crawlState.pages = Math.max(1, Math.min(7, Number(document.getElementById("cr-pages").value) || 1));
    persistState(crawlState);
  });
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
  setQueueMode(modeBox.checked);
}

function setQueueMode(on) {
  crawlState.queueMode = !!on;
  persistState(crawlState);
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
  crawlState.keyword = keyword;
  crawlState.pages = pages;
  persistState(crawlState);
  crawlQueue.items.push({ keyword, pages, status: "待采", reason: "", collected_at: "" });
  kwEl.value = "";
  crawlState.keyword = "";
  persistState(crawlState);
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
      <td><button class="btn btn-sm btn-bad" onclick="window.queueRemove(${i})" ${crawlQueueRunning ? "disabled" : ""}>移除</button></td>
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
      const r = await runBrowserAction(
        () => apiSend("/api/crawl/run-import", "POST", { keyword: it.keyword, pages: it.pages })
      );
      it.status = r.outcome || "完成";
      it.reason = r["原因"] || "";
      if (it.status === "完成") {
        it.collected_at = r["采集时间"] || new Date().toISOString().slice(0, 19).replace("T", " ");
      }
      importedTotal += Number(r["入库商品数"]) || 0;
    } catch (err) {
      if (err?.code === "chrome_driver_download_cancelled") {
        it.status = "待采";
        it.reason = "已取消驱动下载";
      } else {
        it.status = "失败";
        it.reason = err.message;
      }
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
    const result = await runBrowserAction(() => apiSend("/api/crawl/open-amazon", "POST"));
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
  crawlState.keyword = keyword;
  crawlState.pages = pages;
  persistState(crawlState);
  if (!confirm(
    `立即联网采集「${keyword}」${pages} 页？\n\n会打开浏览器访问 Amazon 搜索页并保存 HTML；不自动写入数据库。遇到验证码、登录页或空页会停止。`
  )) return;
  btn.disabled = true;
  box.innerHTML = `<div class="state"><div class="spinner"></div>采集中…（浏览器会自动打开，请勿刷新页面）</div>`;
  try {
    const result = await runBrowserAction(
      () => apiSend("/api/crawl/run", "POST", { keyword, pages })
    );
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
  const visibleIds = new Set((tasks || []).map((task) => Number(task.id)).filter(Boolean));
  trackingTaskRows.clear();
  (tasks || []).forEach((task) => trackingTaskRows.set(String(task.id), task));
  [...trackingSelected].forEach((id) => { if (!visibleIds.has(Number(id))) trackingSelected.delete(id); });
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
      <div class="table-toolbar">
        <h2 style="margin:0;font-size:14px">追踪任务（${tasks.length}）</h2>
        <div class="actions">
          <span id="t-selected" class="selected-count">已选 0 个</span>
          <button class="btn btn-sm" id="t-clear-selected">清空选择</button>
        </div>
      </div>
      <div id="t-list">${trackingTable(tasks)}</div>
    </div>`;
  document.getElementById("t-keyword").value = trackingFormState.keyword;
  document.getElementById("t-target").value = trackingFormState.targetSnapshots;
  document.getElementById("t-pages").value = trackingFormState.pagesPerKeyword;
  document.getElementById("t-market").value = trackingFormState.marketplace;
  bindStateInputs(["t-keyword", "t-target", "t-pages", "t-market"], () => {
    trackingFormState.keyword = document.getElementById("t-keyword").value.trim();
    trackingFormState.targetSnapshots = Number(document.getElementById("t-target").value) || 3;
    trackingFormState.pagesPerKeyword = Number(document.getElementById("t-pages").value) || 2;
    trackingFormState.marketplace = document.getElementById("t-market").value.trim() || "US";
    persistState(trackingFormState);
  });
  document.getElementById("t-create").onclick = createTracking;
  document.getElementById("t-clear-selected").onclick = () => {
    trackingSelected.clear();
    document.querySelectorAll(".track-check").forEach((check) => { check.checked = false; });
    updateTrackingSelected();
    rememberAgentBusinessContext();
  };
  updateTrackingSelected();
}

function trackingTable(tasks) {
  if (!tasks || !tasks.length) return `<div class="state">暂无追踪任务，先在上方创建。</div>`;
  return tableHtml(
    ["", "关键词", "站点", "进度", "状态", "最近采集", "最近检查", "操作"],
    tasks.map((t) => {
      const cur = t.current_snapshots ?? t.achieved_snapshots ?? 0;
      const done = Number(cur) >= Number(t.target_snapshots);
      const toggle = t.status === "active"
        ? `<button class="btn btn-sm" onclick="window.trackToggle(${t.id},'paused')">暂停</button>`
        : `<button class="btn btn-sm" onclick="window.trackToggle(${t.id},'active')">恢复</button>`;
      return { _hash: `#/tracking/${t.id}`, _key: `tracking:${t.id}`, cells: [
        trackingCheckbox(t),
        escapeHtml(t.keyword),
        escapeHtml(t.marketplace),
        `<span class="badge ${done ? "badge-good" : "badge-dim"}">${cur} / ${escapeHtml(t.target_snapshots)}</span>`,
        statusBadge(t.status),
        fmt.text(t.last_collected_at),
        fmt.text(t.last_checked_at),
        `<div class="actions">
          <a class="btn btn-sm" href="#/tracking/${t.id}">证据复盘</a>
          <button class="btn btn-sm" onclick="window.trackPreview(${t.id})">检查</button>
          <button class="btn btn-sm btn-warn" onclick="window.trackCollect(${t.id})">执行采集</button>
          ${toggle}
          <button class="btn btn-sm btn-bad" onclick="window.trackDelete(${t.id})">删除</button>
        </div>`,
      ] };
    })
  );
}

async function viewTrackingEvidence(taskId) {
  loading();
  const evidence = await api(`/api/tracking/tasks/${encodeURIComponent(taskId)}/evidence`);
  const task = evidence.task || {};
  const schedule = evidence.schedule || {};
  const gate = evidence.trend_gate || {};
  const preliminary = gate.preliminary || {};
  const stable = gate.stable || {};
  const adjacent = evidence.adjacent_comparison || {};
  const watch = evidence.research_watch || {};
  const local = evidence.local_evidence || {};
  const boundaries = Array.isArray(evidence.boundaries) ? evidence.boundaries : [];
  const dueClass = schedule.due ? "badge-good" : schedule.state === "target_reached" ? "badge-good" : "badge-warn";
  content.innerHTML = `
    <section class="tracking-evidence-head">
      <div>
        <span class="tracking-evidence-kicker">追踪任务 #${fmt.int(task.id)} · ${escapeHtml(task.marketplace || "US")}</span>
        <h2>${escapeHtml(task.keyword || "未命名关键词")}</h2>
        <p>${escapeHtml(task.progress_note || "任务时点与趋势合格时点分开计算。")}</p>
      </div>
      <div class="actions">
        <button class="btn btn-sm" id="te-open-keyword" type="button">关键词机会</button>
        <button class="btn btn-sm" id="te-check" type="button">检查到期</button>
        <button class="btn btn-sm btn-warn" id="te-collect" type="button"${schedule.due ? "" : " disabled"}
          title="${escapeHtml(schedule.reason || "当前不可采集")}">执行采集</button>
      </div>
    </section>
    <div class="research-summary-grid tracking-evidence-summary">
      <div><span>任务进度</span><b>${fmt.int(task.current_snapshots)} / ${fmt.int(task.target_snapshots)}</b><small>原始入库时点</small></div>
      <div><span>趋势合格点</span><b>${fmt.int(gate.qualified_points)} / ${fmt.int(gate.raw_points)}</b><small>${escapeHtml(gate.timepoint_quality_label || "尚未评估")}</small></div>
      <div><span>合格跨度</span><b>${fmt.int(gate.span_days)} 天</b><small>${escapeHtml(gate.qualified_first_snapshot_at || "—")} 起</small></div>
      <div><span>采集状态</span><b><span class="badge ${dueClass}">${escapeHtml(schedule.label || "—")}</span></b><small>${escapeHtml(schedule.next_collectible_at ? `最早 ${schedule.next_collectible_at}` : schedule.reason || "—")}</small></div>
      <div><span>最近采集</span><b class="tracking-evidence-time">${escapeHtml(fmt.text(task.last_collected_at))}</b><small>每轮 ${fmt.int(task.pages_per_keyword)} 页</small></div>
    </div>
    <section class="panel">
      <div class="tracking-evidence-section-head">
        <div><h2>趋势门槛</h2><p>${escapeHtml(gate.scope_note || "")}</p></div>
        <span class="badge ${gate.timepoint_quality_status === "qualified" ? "badge-good" : "badge-warn"}">${escapeHtml(gate.timepoint_quality_label || "待评估")}</span>
      </div>
      <div class="tracking-gate-grid">
        ${trackingGateBlock(preliminary)}
        ${trackingGateBlock(stable)}
      </div>
      ${(gate.timepoint_quality_warnings || []).map((warning) => `<div class="evidence-warning"><strong>质量提示</strong><span>${escapeHtml(warning)}</span></div>`).join("")}
      <div class="result-meta">${escapeHtml(schedule.reason || "—")}</div>
    </section>
    <section class="panel">
      <div class="tracking-evidence-section-head">
        <div><h2>采集时间点</h2><p>逐批核对商品数、自然序位完整性、页面覆盖与原始卡片数量。</p></div>
        <span class="result-meta">${fmt.int((evidence.timepoints || []).length)} 个原始时点</span>
      </div>
      ${trackingTimepointTable(evidence.timepoints || [])}
    </section>
    <section class="panel">
      <div class="tracking-evidence-section-head">
        <div><h2>相邻批次变化</h2><p>${escapeHtml(adjacent.confidence_note || "")}</p></div>
        <span class="result-meta">${escapeHtml(adjacent.previous_snapshot_at || "—")} → ${escapeHtml(adjacent.current_snapshot_at || "—")}</span>
      </div>
      ${trackingAdjacentEvidence(adjacent, task.keyword)}
    </section>
    <section class="panel">
      <div class="tracking-evidence-section-head">
        <div><h2>研究对象观察</h2><p>${escapeHtml(watch.note || "")}</p></div>
        <span class="result-meta">${fmt.int(watch.project_count)} 个项目 · ${fmt.int(watch.product_count)} 个商品</span>
      </div>
      ${trackingResearchProjectLinks(watch.projects || [])}
      ${trackingResearchWatchTable(watch.products || [], task.keyword)}
    </section>
    <section class="panel">
      <div class="tracking-evidence-section-head">
        <div><h2>本地原始证据</h2><p>${escapeHtml(local.note || "")}</p></div>
        <span class="result-meta">${fmt.int((local.files || []).length)} 个文件 · ${fmt.int((local.jobs || []).length)} 条任务日志</span>
      </div>
      ${trackingSourceFilesTable(local.files || [])}
      <h3 class="tracking-evidence-subhead">采集与入库日志</h3>
      ${trackingJobsTable(local.jobs || [])}
    </section>
    <section class="tracking-boundaries" aria-label="证据边界">
      ${boundaries.map((item) => `<div><span>边界</span><p>${escapeHtml(item)}</p></div>`).join("")}
    </section>`;
  document.getElementById("te-open-keyword").onclick = () => openTrackingKeywordOpportunity(task.keyword || "");
  document.getElementById("te-check").onclick = async () => {
    try {
      const result = await apiSend("/api/tracking/check", "POST", { execute: false, task_id: Number(task.id) });
      notice("检查完成（未联网）：" + summarizeCheck(result), "ok");
      await viewTrackingEvidence(task.id);
    } catch (err) { notice(err.message, "bad"); }
  };
  const collect = document.getElementById("te-collect");
  if (collect && schedule.due) collect.onclick = () => runTrackingCollection(task.id, () => viewTrackingEvidence(task.id));
}

function trackingGateBlock(gate) {
  const ready = !!gate.ready;
  const requiredPoints = Math.max(1, Number(gate.required_points) || 1);
  const requiredDays = Math.max(1, Number(gate.required_days) || 1);
  const completedPoints = Math.max(0, requiredPoints - (Number(gate.remaining_points) || 0));
  const completedDays = Math.max(0, requiredDays - (Number(gate.remaining_days) || 0));
  const progress = ready
    ? 100
    : Math.min(100, Math.max(0, Math.min(completedPoints / requiredPoints, completedDays / requiredDays) * 100));
  return `<div class="tracking-gate ${ready ? "tracking-gate-ready" : ""}">
    <div><span>${escapeHtml(gate.label || "趋势门槛")}</span><b>${ready ? "已达到" : "积累中"}</b></div>
    <div class="tracking-gate-meter"><span style="width:${progress.toFixed(1)}%"></span></div>
    <p>${escapeHtml(gate.message || "—")}</p>
    <small>${fmt.int(gate.required_points)} 个合格点 / ${fmt.int(gate.required_days)} 天</small>
  </div>`;
}

function trackingQualityBadge(row) {
  const status = row.qualification;
  const cls = status === "qualified" ? "badge-good" : status === "near_duplicate" ? "badge-warn" : "badge-bad";
  return `<span class="badge ${cls}" title="${escapeHtml(row.exclusion_reason || "计入趋势")}">${escapeHtml(row.qualification_label || "未评估")}</span>`;
}

function trackingTimepointTable(rows) {
  if (!rows.length) return `<div class="state">该任务尚无已入库的关键词排名时点。</div>`;
  return tableHtml(
    ["采集时间", "趋势资格", "有效商品", "原始卡片", "自然 / 广告", "序位范围", "页数", "核心覆盖"],
    rows.map((row) => ({ cells: [
      escapeHtml(fmt.text(row.snapshot_at)),
      trackingQualityBadge(row),
      fmt.int(row.product_count),
      fmt.int(row.total_card_count),
      `${fmt.int(row.organic_row_count)} / ${fmt.int(row.sponsored_row_count)}`,
      row.min_organic_rank == null ? "—" : `${fmt.int(row.min_organic_rank)}–${fmt.int(row.max_organic_rank)}`,
      fmt.int(row.page_count ?? row.observed_page_count),
      row.data_coverage == null ? "—" : `${fmt.num(Number(row.data_coverage) * 100, 1)}%`,
    ] }))
  );
}

function trackingAdjacentEvidence(adjacent, keyword) {
  if (!adjacent.available) return `<div class="state">${escapeHtml(adjacent.summary || "暂无可比较批次。")}</div>`;
  const retention = adjacent.retention_rate == null ? "—" : `${fmt.num(Number(adjacent.retention_rate) * 100, 1)}%`;
  const delta = adjacent.average_rank_delta == null ? "—" : `${Number(adjacent.average_rank_delta) > 0 ? "+" : ""}${fmt.num(adjacent.average_rank_delta, 2)}`;
  return `
    <div class="research-summary-grid tracking-transition-summary">
      <div><span>留存</span><b>${fmt.int(adjacent.retained_product_count)}</b><small>留存率 ${retention}</small></div>
      <div><span>本批新观察到</span><b>${fmt.int(adjacent.entered_product_count)}</b><small>仅指相邻批次</small></div>
      <div><span>本批未观察到</span><b>${fmt.int(adjacent.exited_product_count)}</b><small>不等于下架</small></div>
      <div><span>序位提升 / 下降</span><b>${fmt.int(adjacent.improved_count)} / ${fmt.int(adjacent.worsened_count)}</b><small>${fmt.int(adjacent.unchanged_count)} 个不变</small></div>
      <div><span>平均序位差</span><b>${delta}</b><small>正数表示平均向后</small></div>
    </div>
    <div class="evidence-warning"><strong>短期证据</strong><span>${escapeHtml(adjacent.summary || "")}</span></div>
    <div class="tracking-evidence-columns">
      <div><h3>本批新观察到</h3>${trackingBatchMembersTable(adjacent.entered || [], "current_rank", keyword)}</div>
      <div><h3>本批未观察到</h3>${trackingBatchMembersTable(adjacent.exited || [], "previous_rank", keyword)}</div>
    </div>
    <h3 class="tracking-evidence-subhead">留存商品序位变化</h3>
    ${trackingMovementTable(adjacent.movements || [], keyword)}`;
}

function trackingBatchMembersTable(rows, rankKey, keyword) {
  if (!rows.length) return `<div class="state tracking-compact-state">无</div>`;
  return tableHtml(
    ["商品", rankKey === "current_rank" ? "本批序位" : "上批序位"],
    rows.map((row) => ({
      _hash: productDetailHash(row.asin, keyword),
      _key: `${rankKey}:${row.asin}`,
      cells: [amazonProductLink(row, displayTitle(row, row.asin || "—"), 48), fmt.int(row[rankKey])],
    }))
  );
}

function trackingMovementTable(rows, keyword) {
  if (!rows.length) return `<div class="state tracking-compact-state">暂无可比较的自然序位。</div>`;
  return tableHtml(
    ["商品", "上批", "本批", "变化", "判断"],
    rows.map((row) => ({
      _hash: productDetailHash(row.asin, keyword),
      _key: `movement:${row.asin}`,
      cells: [
        amazonProductLink(row, displayTitle(row, row.asin || "—"), 64),
        fmt.int(row.previous_rank),
        fmt.int(row.current_rank),
        `${Number(row.rank_delta) > 0 ? "+" : ""}${fmt.int(row.rank_delta)}`,
        `<span class="badge ${row.movement === "improved" ? "badge-good" : row.movement === "worsened" ? "badge-warn" : "badge-dim"}">${escapeHtml(row.movement_label || "—")}</span>`,
      ],
    }))
  );
}

function trackingResearchProjectLinks(projects) {
  if (!projects.length) return `<div class="result-meta">该关键词尚未关联研究项目，暂不生成候选/对标观察。</div>`;
  return `<div class="tracking-project-links">${projects.map((project) => `
    <span><b>${escapeHtml(project.name || `项目 #${project.project_id}`)}</b>
      <a class="link" href="${escapeHtml(project.detail_route)}">项目</a>
      <a class="link" href="${escapeHtml(project.report_route)}">动态报告</a>
    </span>`).join("")}</div>`;
}

function trackingResearchWatchTable(rows, keyword) {
  if (!rows.length) return `<div class="state">暂无关联研究商品。</div>`;
  return tableHtml(
    ["角色 / 商品", "出现时点", "上批 / 本批", "历史序位", "证据解释"],
    rows.map((row) => {
      const roles = (row.projects || []).map((item) => item.role_label || item.role).filter(Boolean).join("、") || "未分类";
      const history = (row.history || []).map((item) => `${String(item.snapshot_at || "").slice(5, 10)} #${item.organic_rank ?? "—"}`).join(" → ") || "未观察到";
      return {
        _hash: row.detail_route || productDetailHash(row.asin, keyword),
        _key: `watch:${row.asin}`,
        cells: [
          `<div class="tracking-watch-product"><span class="badge badge-dim">${escapeHtml(roles)}</span>${amazonProductLink(row, displayTitle(row, row.asin || "—"), 64)}</div>`,
          `${fmt.int(row.observed_timepoint_count)} / ${fmt.int(row.total_timepoint_count)}`,
          `${fmt.int(row.previous_rank)} / ${fmt.int(row.current_rank)}`,
          `<span class="tracking-rank-history">${escapeHtml(history)}</span>`,
          `<span class="tracking-interpretation">${escapeHtml(row.interpretation || "—")}</span>`,
        ],
      };
    })
  );
}

function trackingSourceFilesTable(files) {
  if (!files.length) return `<div class="state tracking-compact-state">尚未找到可核验的本地原始文件。</div>`;
  return tableHtml(
    ["对应时点", "来源", "文件", "大小", "SHA-256", "状态"],
    files.map((file) => ({ cells: [
      escapeHtml(fmt.text(file.snapshot_at)),
      escapeHtml(fmt.text(file.source_type)),
      `<code class="tracking-file-path" title="${escapeHtml(file.path || "")}">${escapeHtml(file.name || "—")}</code>`,
      file.size_bytes == null ? "—" : escapeHtml(formatFileBytes(file.size_bytes)),
      file.sha256 ? `<code title="${escapeHtml(file.sha256)}">${escapeHtml(String(file.sha256).slice(0, 16))}…</code>` : "—",
      `<span class="badge ${file.exists ? "badge-good" : "badge-bad"}">${file.exists ? "可核验" : "文件缺失"}</span>`,
    ] }))
  );
}

function trackingJobsTable(jobs) {
  if (!jobs.length) return `<div class="state tracking-compact-state">暂无相关任务日志。</div>`;
  return tableHtml(
    ["任务", "类型", "关联时点", "执行时间", "状态", "解析 / 有效 / 入库", "关联依据"],
    jobs.map((job) => ({ cells: [
      `#${fmt.int(job.id)}`,
      escapeHtml(fmt.text(job.job_type)),
      escapeHtml(fmt.text(job.related_snapshot_at)),
      escapeHtml(fmt.text(job.started_at)),
      statusBadge(job.status),
      `${fmt.int(job.total_found)} / ${fmt.int(job.total_valid)} / ${fmt.int(job.total_inserted)}`,
      escapeHtml(fmt.text(job.association_basis)),
    ] }))
  );
}

function openTrackingKeywordOpportunity(keyword) {
  keywordState.keyword = String(keyword || "").trim();
  keywordState.offset = 0;
  keywordProductState.keyword = keywordState.keyword;
  keywordProductState.scope = "current";
  keywordProductState.offset = 0;
  persistState(keywordState);
  persistState(keywordProductState);
  location.hash = "#/keywords";
}

function trackingCheckbox(task) {
  const id = Number(task.id);
  const checked = trackingSelected.has(id) ? " checked" : "";
  return `<input type="checkbox" class="prod-check track-check" value="${escapeHtml(id)}"${checked} onclick="event.stopPropagation()" onchange="window.trackingToggle(this)" />`;
}

window.trackingToggle = (checkbox) => {
  const id = Number(checkbox.value);
  if (!id) return;
  if (checkbox.checked) trackingSelected.add(id);
  else trackingSelected.delete(id);
  updateTrackingSelected();
  rememberAgentBusinessContext();
};

function updateTrackingSelected() {
  const el = document.getElementById("t-selected");
  if (el) el.textContent = `已选 ${trackingSelected.size} 个`;
}

function trackingSelectedIds() {
  return [...trackingSelected].map((id) => Number(id)).filter(Boolean).slice(0, 5);
}

function trackingSelectedTasks() {
  return trackingSelectedIds().map((id) => {
    const task = trackingTaskRows.get(String(id)) || {};
    return {
      id,
      keyword: task.keyword || "",
      status: task.status || "",
      current_snapshots: task.current_snapshots ?? task.achieved_snapshots ?? null,
      target_snapshots: task.target_snapshots ?? null,
      last_collected_at: task.last_collected_at || "",
      last_checked_at: task.last_checked_at || "",
    };
  });
}

async function createTracking() {
  const kw = document.getElementById("t-keyword").value.trim();
  if (!kw) return notice("请填写关键词", "bad");
  trackingFormState.keyword = kw;
  trackingFormState.targetSnapshots = Number(document.getElementById("t-target").value) || 3;
  trackingFormState.pagesPerKeyword = Number(document.getElementById("t-pages").value) || 2;
  trackingFormState.marketplace = document.getElementById("t-market").value.trim() || "US";
  persistState(trackingFormState);
  try {
    await apiSend("/api/tracking/tasks", "POST", {
      keyword: kw,
      target_snapshots: trackingFormState.targetSnapshots,
      pages_per_keyword: trackingFormState.pagesPerKeyword,
      marketplace: trackingFormState.marketplace,
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

async function runTrackingCollection(id, onComplete = viewTracking) {
  // 联网采集是危险操作：必须二次确认后才传 execute=true（Lead 硬要求）。
  if (!confirm(
    "⚠ 立即执行联网采集？\n\n会打开浏览器实际抓取 Amazon，受限频 / 被拦即停 / 72h 边界约束，可能耗时。\n\n确认继续？"
  )) return;
  notice("联网采集执行中…（串行，请稍候）", "ok");
  try {
    const r = await runBrowserAction(
      () => apiSend("/api/tracking/check", "POST", { execute: true, task_id: id })
    );
    notice("采集完成：" + summarizeCheck(r), "ok");
    await onComplete();
  } catch (err) { notice(err.message, "bad"); }
}

window.trackCollect = (id) => runTrackingCollection(id, viewTracking);

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
    <tbody>${rows.map((r) => `<tr${r._hash ? selectableEntryAttrs(r._hash, r._key || r._hash) : ""}>${
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

function remoteSortOptions(state, loadFn) {
  return {
    key: state.sortBy,
    dir: state.sortDir === "asc" ? 1 : -1,
    onChange: (key, dir) => {
      state.sortBy = key;
      state.sortDir = dir > 0 ? "asc" : "desc";
      state.offset = 0;
      persistState(state);
      loadFn();
    },
  };
}

function syncRemoteSortState(state, page) {
  if (!page) return;
  if (page.sort_by) state.sortBy = page.sort_by;
  if (page.sort_dir) state.sortDir = page.sort_dir;
  persistState(state);
}

/* 通用可排序表格：点列头切换升/降序，数值列按数字排，可选行跳转、CSV 导出。
   columns: [{key,label,render?(item),sortVal?(item),csv?(item),numeric?,align?:'num'}]
   opts: {rowHash?(item)=>hash, defaultSort?:{key,dir}, exportName?:string} */
function renderSortableTable(container, columns, data, opts = {}) {
  const remote = opts.remoteSort || null;
  const state = remote
    ? { key: remote.key || null, dir: remote.dir > 0 ? 1 : -1 }
    : { key: null, dir: 1, ...(opts.defaultSort || {}) };
  function draw() {
    const rows = data.slice();
    if (state.key && !remote) {
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
      return `<tr>${tds}</tr>`;
    }).join("");
    const bar = opts.exportName
      ? `<div class="table-bar"><button class="btn btn-sm" data-csv="1">导出 CSV</button></div>` : "";
    container.innerHTML = bar + wrapTable(`<table><thead><tr>${thead}</tr></thead><tbody>${tbody}</tbody></table>`);
    container.querySelectorAll("th.sortable").forEach((th) => {
      th.onclick = () => {
        const k = th.dataset.key;
        const nextDir = state.key === k ? -state.dir : 1;
        if (remote) {
          if (typeof remote.onChange === "function") remote.onChange(k, nextDir);
          return;
        }
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
    if (opts.rowHash || opts.onRowClick) {
      container.querySelectorAll("tbody tr").forEach((tr, i) => {
        const item = rows[i];
        const hash = opts.rowHash ? opts.rowHash(item) : "";
        if (!hash && !opts.onRowClick) return;
        const key = opts.rowKey
          ? opts.rowKey(item)
          : (hash || item.id || item.keyword_id || item.asin || item.keyword || String(i));
        prepareSelectableEntry(tr, {
          hash,
          key,
          activate: opts.onRowClick ? () => opts.onRowClick(item) : null,
        });
      });
      restoreInteractiveSelection(container);
    }
    if (opts.onDraw) opts.onDraw(container, rows);
    scheduleClientTranslate();
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
        从应用数据目录 <code>html/</code> 选择已保存的 Amazon 搜索结果页，先核对有效候选与数据库影响，再确认写入 MySQL。
        仅允许该目录下文件（白名单）；预览只解析不写入数据库，写入需二次确认。
      </p>
      ${files.length ? `
      <div class="filters" style="margin-top:10px">
        <input id="imp-keyword" placeholder="关键词（留空时按目录推断）" style="width:220px" />
        <button class="btn" id="imp-preview">预览入库</button>
        <button class="btn btn-warn" id="imp-commit" disabled>请先预览</button>
      </div>
      <div id="imp-confirmation" class="import-confirmation" aria-live="polite"></div>
      <div id="imp-files" class="file-list file-tree">${renderHtmlFileTree(files)}</div>`
      : `<div class="state"><code>html/</code> 目录暂无可入库 HTML 文件。</div>`}
    </div>
    <div id="imp-result"></div>`;
  if (!files.length) return;
  document.getElementById("imp-keyword").value = htmlImportState.keyword;
  restoreCheckedValues(".imp-file", htmlImportState.selectedFiles);
  bindStateInputs(["imp-keyword"], () => {
    saveHtmlImportFormState();
    invalidateHtmlImportPreview();
  });
  document.querySelectorAll(".imp-file").forEach((checkbox) => checkbox.addEventListener("change", () => {
    saveHtmlImportFormState();
    invalidateHtmlImportPreview();
  }));
  document.getElementById("imp-preview").onclick = () => runImport(false);
  document.getElementById("imp-commit").onclick = () => runImport(true);
  if (htmlImportPreview && !htmlImportPreviewMatches()) htmlImportPreview = null;
  updateHtmlImportConfirmationState();
  if (htmlImportPreview?.summary && htmlImportPreviewMatches()) {
    document.getElementById("imp-result").innerHTML = renderImportResult(htmlImportPreview.summary, false);
  }
}

function restoreCheckedValues(selector, savedValues) {
  const boxes = [...document.querySelectorAll(selector)];
  if (!boxes.length || !Array.isArray(savedValues) || !savedValues.length) return;
  const available = new Set(boxes.map((box) => String(box.value || "")));
  const selected = savedValues.filter((value) => available.has(String(value)));
  if (!selected.length) return;
  boxes.forEach((box) => { box.checked = selected.includes(String(box.value || "")); });
}

function saveHtmlImportFormState() {
  const keyword = document.getElementById("imp-keyword");
  htmlImportState.keyword = keyword ? keyword.value.trim() : "";
  htmlImportState.selectedFiles = [...document.querySelectorAll(".imp-file:checked")].map((c) => c.value);
  persistState(htmlImportState);
}

function currentHtmlImportRequest() {
  const keyword = document.getElementById("imp-keyword")?.value.trim() || "";
  const files = [...document.querySelectorAll(".imp-file:checked")].map((checkbox) => checkbox.value);
  return { files, keyword };
}

function sameHtmlImportRequest(left, right) {
  if (!left || !right || left.keyword !== right.keyword || left.files.length !== right.files.length) return false;
  return left.files.every((file, index) => file === right.files[index]);
}

function htmlImportPreviewMatches() {
  return Boolean(htmlImportPreview && sameHtmlImportRequest(currentHtmlImportRequest(), htmlImportPreview.request));
}

function invalidateHtmlImportPreview() {
  if (!htmlImportPreview) {
    updateHtmlImportConfirmationState();
    return;
  }
  htmlImportPreview = null;
  const box = document.getElementById("imp-result");
  if (box) box.innerHTML = `<div class="state">文件选择或关键词已变化，本次预览已失效。</div>`;
  updateHtmlImportConfirmationState();
}

function updateHtmlImportConfirmationState() {
  const button = document.getElementById("imp-commit");
  const status = document.getElementById("imp-confirmation");
  if (!button || !status) return;
  const matched = htmlImportPreviewMatches();
  const valid = matched ? Number(htmlImportPreview.expectedValid || 0) : 0;
  button.disabled = !matched || valid < 1;
  button.textContent = matched && valid > 0 ? `确认写入 ${valid} 条` : "请先预览";
  if (!matched) {
    const selected = currentHtmlImportRequest().files.length;
    status.innerHTML = selected
      ? `<span>已选择 ${selected} 个文件，尚未形成可确认批次。</span>`
      : `<span>尚未选择 HTML 文件。</span>`;
    return;
  }
  if (valid < 1) {
    status.innerHTML = `<strong>批次 ${escapeHtml(htmlImportPreview.fingerprint)}</strong><span>没有符合完整性要求的候选，不能写入。</span>`;
    return;
  }
  status.innerHTML = `
    <strong>批次 ${escapeHtml(htmlImportPreview.fingerprint)} 已锁定</strong>
    <span>有效候选 ${fmt.int(valid)} 条；只有当前文件、关键词和候选集合保持一致时才能写入。</span>`;
}

function renderHtmlFileTree(files) {
  const root = createTreeNode();
  files.forEach((file) => addTreeFile(root, file));
  return renderTreeNode(root, 0);
}

function createTreeNode() {
  return { dirs: new Map(), files: [] };
}

function addTreeFile(root, file) {
  const parts = String(file || "").split("/").filter(Boolean);
  let node = root;
  while (parts.length > 1) {
    const dir = parts.shift();
    if (!node.dirs.has(dir)) node.dirs.set(dir, createTreeNode());
    node = node.dirs.get(dir);
  }
  node.files.push({ path: file, name: parts[0] || file });
}

function renderTreeNode(node, depth) {
  const folders = [...node.dirs.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([name, child]) => `
    <details class="tree-folder" open style="--depth:${depth}">
      <summary><span class="tree-folder-name">${escapeHtml(name)}</span><span class="tree-count">${countTreeFiles(child)}</span></summary>
      ${renderTreeNode(child, depth + 1)}
    </details>`).join("");
  const files = node.files.sort((a, b) => a.name.localeCompare(b.name)).map((file) => `
    <label class="file-row tree-file" style="--depth:${depth}">
      <input type="checkbox" class="imp-file" value="${escapeHtml(file.path)}"/>
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
  const request = currentHtmlImportRequest();
  const files = request.files;
  const box = document.getElementById("imp-result");
  if (!files.length) { notice("请至少勾选一个 HTML 文件", "bad"); return; }
  if (commit && !htmlImportPreviewMatches()) {
    notice("当前选择没有有效预览，请重新预览后再确认写入", "bad");
    updateHtmlImportConfirmationState();
    return;
  }
  if (commit) {
    const preview = htmlImportPreview;
    const summary = preview.summary || {};
    const impact = [
      `有效候选：${fmt.int(preview.expectedValid)} 条`,
      `预计新增商品：${fmt.int(summary["预计新增商品"] || 0)} 个`,
      `预计更新已有商品：${fmt.int(summary["预计更新已有商品"] || 0)} 个`,
      `预计新增时间序列：${fmt.int(summary["预计新增时间序列"] || 0)} 条`,
    ].join("\n");
    if (!confirm(`确认写入批次 ${preview.fingerprint}？\n\n${impact}\n\n写入后仍需单独同步分析仓库。`)) return;
  }
  box.innerHTML = `<div class="state"><div class="spinner"></div>${commit ? "写入中…" : "预览入库中…"}</div>`;
  const keyword = request.keyword || null;
  saveHtmlImportFormState();
  try {
    const body = { files, keyword };
    if (commit) {
      body.confirmed = true;
      body.confirmation_token = htmlImportPreview.token;
      body.expected_valid = htmlImportPreview.expectedValid;
    }
    const r = await apiSend(`/api/import/html/${commit ? "commit" : "preview"}`, "POST", body);
    if (!commit) {
      if (!sameHtmlImportRequest(currentHtmlImportRequest(), request)) {
        htmlImportPreview = null;
        box.innerHTML = `<div class="state">预览期间选择发生变化，本次结果未用于确认写入。</div>`;
        updateHtmlImportConfirmationState();
        notice("选择已变化，请重新预览", "bad");
        return;
      }
      const token = String(r?.["确认令牌"] || "");
      const expectedValid = Number(r?.["有效入库候选"] || 0);
      if (!token) throw new Error("后端未返回批次确认令牌，请刷新后重试");
      htmlImportPreview = {
        request: { files: [...files], keyword: request.keyword },
        token,
        expectedValid,
        fingerprint: String(r?.["批次指纹"] || token.slice(-12).toUpperCase()),
        summary: r,
      };
    }
    box.innerHTML = renderImportResult(r, commit);
    if (commit) htmlImportPreview = null;
    updateHtmlImportConfirmationState();
    notice(commit ? "写入完成" : "预览入库完成", "ok");
  } catch (err) {
    if (commit) htmlImportPreview = null;
    updateHtmlImportConfirmationState();
    box.innerHTML = `<div class="state error">⚠ ${escapeHtml(err.message)}</div>`;
  }
}

function renderImportResult(r, commit) {
  return renderSummaryPanel(commit ? "写入结果" : "预览入库结果", r);
}

/* 通用：把 controller 返回的中文摘要 dict 渲染成面板（过滤原因单列、列表值用顿号拼接）。 */
function renderSummaryPanel(title, r) {
  const hiddenKeys = new Set(["过滤原因", "确认令牌"]);
  const main = Object.entries(r || {}).filter(([key]) => !hiddenKeys.has(key))
    .map(([k, v]) => {
      const display = Array.isArray(v) ? (v.join("、") || "—") : (v == null || v === "" ? "—" : v);
      return `<div class="row"><span>${escapeHtml(k)}</span> <b>${escapeHtml(display)}</b></div>`;
    }).join("");
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
      <div class="file-list">${htmls.map((f) => `<label class="file-row"><input type="checkbox" class="rh-file" value="${escapeHtml(f)}"/> ${escapeHtml(f)}</label>`).join("")}</div>`
      : `<div class="state"><code>2_1/reviews/</code> 目录暂无评论 HTML。</div>`}
      <div id="rv-html-result"></div>
    </div>`;
  if (imp.length) {
    const fileEl = document.getElementById("rv-file");
    if (reviewImportState.file && imp.includes(reviewImportState.file)) fileEl.value = reviewImportState.file;
    document.getElementById("rv-asin").value = reviewImportState.defaultAsin;
    bindStateInputs(["rv-file", "rv-asin"], saveReviewImportFormState);
    document.getElementById("rv-preview").onclick = () => runReviewImport(false);
    document.getElementById("rv-commit").onclick = () => runReviewImport(true);
  }
  if (htmls.length) {
    document.getElementById("rh-asin").value = reviewImportState.htmlDefaultAsin;
    document.getElementById("rh-fmt").value = reviewImportState.outputFormat || "csv";
    restoreCheckedValues(".rh-file", reviewImportState.selectedHtmlFiles);
    bindStateInputs(["rh-asin", "rh-fmt"], saveReviewImportFormState);
    document.querySelectorAll(".rh-file").forEach((checkbox) => checkbox.addEventListener("change", saveReviewImportFormState));
    document.getElementById("rh-parse").onclick = runReviewParse;
  }
}

function saveReviewImportFormState() {
  const fileEl = document.getElementById("rv-file");
  const asinEl = document.getElementById("rv-asin");
  const htmlAsinEl = document.getElementById("rh-asin");
  const fmtEl = document.getElementById("rh-fmt");
  reviewImportState.file = fileEl ? fileEl.value : reviewImportState.file;
  reviewImportState.defaultAsin = asinEl ? asinEl.value.trim() : reviewImportState.defaultAsin;
  reviewImportState.htmlDefaultAsin = htmlAsinEl ? htmlAsinEl.value.trim() : reviewImportState.htmlDefaultAsin;
  reviewImportState.outputFormat = fmtEl ? fmtEl.value : reviewImportState.outputFormat;
  reviewImportState.selectedHtmlFiles = [...document.querySelectorAll(".rh-file:checked")].map((c) => c.value);
  persistState(reviewImportState);
}

async function runReviewImport(commit) {
  const file = document.getElementById("rv-file").value;
  const box = document.getElementById("rv-imp-result");
  if (!file) { notice("请选择评论文件", "bad"); return; }
  if (commit && !confirm(`确认把「${file}」的评论写入数据库（MySQL）？建议先预览导入，确认有效候选与过滤情况。`)) return;
  box.innerHTML = `<div class="state"><div class="spinner"></div>${commit ? "导入中…" : "预览导入中…"}</div>`;
  const default_asin = document.getElementById("rv-asin").value.trim() || null;
  saveReviewImportFormState();
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
  saveReviewImportFormState();
  try {
    const r = await apiSend("/api/import/reviews/parse-html", "POST", { files, default_asin, output_format });
    box.innerHTML = renderSummaryPanel("解析结果", r);
    notice("解析完成，点右上「刷新」后可导入生成文件", "ok");
  } catch (err) { box.innerHTML = `<div class="state error">⚠ ${escapeHtml(err.message)}</div>`; }
}

/* ---------- 视图：分析仓库手动同步（阶段1 单元③） ---------- */
async function viewWarehouseSync() {
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
      <div id="wh-status"><div class="state"><div class="spinner"></div>正在核对仓库状态…</div></div>
      <div id="wh-result"></div>
    </div>`;
  document.getElementById("wh-sync").onclick = runWarehouseSync;
  await loadWarehouseStatus();
}

async function loadWarehouseStatus() {
  const box = document.getElementById("wh-status");
  if (!box) return;
  try {
    box.innerHTML = renderWarehouseStatus(await api("/api/warehouse/status"));
  } catch (err) {
    box.innerHTML = `<div class="state error">⚠ ${escapeHtml(err.message)}</div>`;
  }
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
    await loadWarehouseStatus();
    notice("仓库同步完成", "ok");
  } catch (err) {
    box.innerHTML = `<div class="state error">⚠ ${escapeHtml(err.message)}</div>`;
  } finally {
    btn.disabled = false;
  }
}

function renderWarehouseResult(r) {
  const top = ["总行数", "同步时间", "DuckDB", "Parquet", "清单"].filter((k) => r && r[k] != null)
    .map((k) => `<div class="row"><span>${k}</span> <b>${escapeHtml(r[k])}</b></div>`).join("");
  const tables = (r && r["同步表"]) || {};
  const tableRows = Object.entries(tables)
    .map(([t, n]) => `<div class="row"><span>${escapeHtml(t)}</span> <b>${escapeHtml(n)}</b></div>`).join("");
  return `<div class="panel"><h2>同步结果</h2>${top}${tableRows ? `<h3 style="margin:14px 0 6px;font-size:13px">各表行数</h3>${tableRows}` : ""}</div>`;
}

function renderWarehouseStatus(data) {
  const stateLabel = { current: "最新", stale: "需要同步", missing: "尚未完整建立" };
  const stateTone = { current: "badge-good", stale: "badge-warn", missing: "badge-bad" };
  const state = String((data && data.status) || "missing");
  const rows = Array.isArray(data && data.tables) ? data.tables : [];
  const tableRows = rows.map((row) => `
    <tr>
      <td>${escapeHtml(row.name)}</td>
      <td>${escapeHtml(row.source_table)}</td>
      <td><span class="badge ${stateTone[row.state] || ""}">${escapeHtml(stateLabel[row.state] || row.state)}</span></td>
      <td>${fmt.int(row.rows)}</td>
      <td>${fmt.int(row.source_rows)}</td>
    </tr>`).join("");
  return `
    <div class="warehouse-status warehouse-status-${escapeHtml(state)}">
      <div class="warehouse-status-heading">
        <h3>当前状态</h3>
        <span class="badge ${stateTone[state] || ""}">${escapeHtml(stateLabel[state] || state)}</span>
      </div>
      <p class="muted">${escapeHtml((data && data.message) || "--")} 最近同步：${escapeHtml((data && data.last_synced_at) || "--")}</p>
      <div class="table-wrap">
        <table>
          <thead><tr><th>分析表</th><th>MySQL 来源</th><th>状态</th><th>副本行数</th><th>来源行数</th></tr></thead>
          <tbody>${tableRows || `<tr><td colspan="5">暂无状态信息</td></tr>`}</tbody>
        </table>
      </div>
    </div>`;
}

/* ---------- 视图：设置（S4 文案复核，透出 services.settings 三层护栏） ---------- */
let settingsPreflight = null;
let settingsPreflightError = "";

async function viewSettings() {
  loading();
  let data;
  try {
    const [settingsResult, preflightResult] = await Promise.allSettled([
      api("/api/settings"),
      api("/api/system/deployment-preflight"),
    ]);
    if (settingsResult.status !== "fulfilled") throw settingsResult.reason;
    data = settingsResult.value;
    settingsPreflight = preflightResult.status === "fulfilled" ? preflightResult.value : null;
    settingsPreflightError = preflightResult.status === "rejected"
      ? preflightResult.reason.message
      : "";
  }
  catch (err) { return errorState(err); }
  renderSettings((data && data.settings) || {}, settingsPreflight, settingsPreflightError);
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
  "analytics.custom_scoring.weights.rank": "序位权重",
  "analytics.custom_scoring.weights.growth": "增长权重",
};

function settingsFieldLabel(path) {
  return SETTINGS_FIELD_LABELS[path] || path;
}

function renderSettings(s, preflight = settingsPreflight, preflightError = settingsPreflightError) {
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
          `<label>${({demand:"需求权重",competition:"竞争权重",rating:"评分权重",price:"价格权重",rank:"序位权重",growth:"增长权重"})[k]}<input id="set-w-${k}" type="number" min="0" max="1" step="0.05" value="${escapeHtml(w[k])}" /></label>`
        ).join("")}
      </div>
      <div class="set-hint">权重范围 0-1；增长权重在趋势第二步接入真实值前建议保持 0。</div>
    </div>
    <div class="panel" id="set-deployment-panel">
      ${renderDeploymentPreflight(preflight, preflightError)}
    </div>
    <div class="set-actions">
      <button class="btn" id="set-save">保存设置</button>
      <button class="btn" id="set-reload">放弃改动并重新载入</button>
    </div>
    <div id="set-result"></div>`;
  document.getElementById("set-save").onclick = saveSettings;
  document.getElementById("set-reload").onclick = viewSettings;
  bindDeploymentActions();
}

function renderDeploymentPreflight(data, error = "") {
  if (!data) {
    return `
      <div class="deployment-heading">
        <h2>部署与迁移</h2>
        <button class="btn" id="set-preflight-refresh">重新检查</button>
      </div>
      <div class="state error">⚠ ${escapeHtml(error || "暂未取得部署预检结果")}</div>`;
  }
  const stateLabel = { ready: "已就绪", warning: "有提示", blocked: "未就绪" };
  const stateClass = { ready: "badge-good", warning: "badge-warn", blocked: "badge-bad" };
  const checks = (data.checks || []).map((item) => `
    <div class="deployment-check">
      <div class="deployment-check-title">
        <b>${escapeHtml(item.label)}</b>
        <span class="badge ${stateClass[item.status] || "badge-dim"}">${escapeHtml(stateLabel[item.status] || item.status)}</span>
      </div>
      <span>${escapeHtml(item.message || "--")}</span>
    </div>`).join("");
  return `
    <div class="deployment-heading">
      <div>
        <h2>部署与迁移 <span class="badge ${stateClass[data.state] || "badge-dim"}">${escapeHtml(stateLabel[data.state] || data.state)}</span></h2>
        <p>${escapeHtml(data.message || "--")}</p>
      </div>
      <div class="deployment-actions">
        <button class="btn" id="set-preflight-refresh">重新检查</button>
        <button class="btn" id="set-local-backup">导出本地证据迁移包</button>
      </div>
    </div>
    <div class="deployment-capabilities">
      <span>分析功能 ${data.ready_for_analysis ? "已就绪" : "未就绪"}</span>
      <span>联网采集 ${data.ready_for_collection ? "已就绪" : "未就绪"}</span>
      <span>检查时间 ${escapeHtml(data.checked_at || "--")}</span>
    </div>
    <div class="deployment-boundary">
      <b>重要边界：</b>迁移包不包含 MySQL 业务数据、数据库密码、Agent 密钥或浏览器登录状态。MySQL 必须单独备份与恢复。
    </div>
    <div class="deployment-checks">${checks}</div>
    <div id="set-backup-result"></div>`;
}

function bindDeploymentActions() {
  const refresh = document.getElementById("set-preflight-refresh");
  const backup = document.getElementById("set-local-backup");
  if (refresh) refresh.onclick = refreshDeploymentPreflight;
  if (backup) backup.onclick = backupLocalEvidence;
}

async function refreshDeploymentPreflight() {
  const panel = document.getElementById("set-deployment-panel");
  if (!panel) return;
  panel.innerHTML = `<div class="state"><div class="spinner"></div>正在只读检查部署环境…</div>`;
  try {
    settingsPreflight = await api("/api/system/deployment-preflight", { allowStale: true });
    settingsPreflightError = "";
    panel.innerHTML = renderDeploymentPreflight(settingsPreflight);
  } catch (err) {
    settingsPreflight = null;
    settingsPreflightError = err.message;
    panel.innerHTML = renderDeploymentPreflight(null, err.message);
  }
  bindDeploymentActions();
}

async function backupLocalEvidence() {
  const boundary = [
    "确认生成本地证据迁移包？",
    "",
    "包含：HTML、评论文件、导出、分析结果、分析仓库和安全偏好。",
    "不包含：MySQL 业务数据、数据库/Agent 私有配置、日志、缓存和浏览器登录状态。",
  ].join("\n");
  if (!confirm(boundary)) return;
  const button = document.getElementById("set-local-backup");
  const box = document.getElementById("set-backup-result");
  if (button) button.disabled = true;
  if (box) box.innerHTML = `<div class="state"><div class="spinner"></div>正在校验并打包本地证据…</div>`;
  try {
    const result = await apiSend(
      "/api/system/local-data-backup",
      "POST",
      { confirmed: true },
    );
    if (box) {
      box.innerHTML = `
        <div class="deployment-backup-result">
          <b>迁移包已生成</b>
          <span>${escapeHtml(result.archive_path)}</span>
          <span>${fmt.int(result.file_count)} 个文件，${escapeHtml(formatFileBytes(result.total_bytes))}</span>
          <span>SHA-256：${escapeHtml(result.archive_sha256)}</span>
          <strong>MySQL 业务数据未包含，迁移设备前仍需单独备份 MySQL。</strong>
        </div>`;
    }
    notice("本地证据迁移包已生成", "ok");
  } catch (err) {
    if (box) box.innerHTML = `<div class="state error">⚠ ${escapeHtml(err.message)}</div>`;
    notice(err.message, "bad");
  } finally {
    if (button) button.disabled = false;
  }
}

function formatFileBytes(value) {
  const bytes = Number(value || 0);
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
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
    renderSettings(r.settings || {}, settingsPreflight, settingsPreflightError);
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
  { re: /^#\/model-replay$/, title: "模型实验室", run: viewScoringReplay },
  { re: /^#\/products$/, title: "商品池 · 筛选", run: viewProducts },
  { re: /^#\/compare(?:\/(.+))?$/, title: "商品对比", run: (m) => viewCompare(m[1] ? decodeURIComponent(m[1]) : "") },
  { re: /^#\/product\/([^?]+)(?:\?score_keyword=(.*))?$/, title: "商品详情 · 趋势", run: (m) => viewProductDetail(decodeURIComponent(m[1]), m[2] ? decodeURIComponent(m[2]) : "") },
  { re: /^#\/metrics$/, title: "指标与估算", run: viewMetricCenter },
  { re: /^#\/metrics\/evidence$/, title: "指标证据队列", run: viewMetricEvidence },
  { re: /^#\/metrics\/(.+)$/, title: "商品指标 · 估算", run: (m) => viewMetricProduct(decodeURIComponent(m[1])) },
  { re: /^#\/keyword-workshop$/, title: "关键词创意工坊", run: viewKeywordWorkshop },
  { re: /^#\/keyword-library$/, title: "关键词资产库", run: viewKeywordLibrary },
  { re: /^#\/keywords$/, title: "关键词机会", run: viewKeywords },
  { re: /^#\/market-niches$/, title: "市场与利基", run: viewMarketNiches },
  { re: /^#\/market-niches\/(\d+)$/, title: "市场利基 · 证据", run: (m) => viewMarketNicheDetail(m[1]) },
  { re: /^#\/competitive-graph$/, title: "竞品图谱", run: viewCompetitiveGraph },
  { re: /^#\/competitive-graph\/(\d+)$/, title: "竞品图谱 · 覆盖与缺口", run: (m) => viewCompetitiveGraphDetail(m[1]) },
  { re: /^#\/research-projects$/, title: "研究项目", run: viewResearchProjects },
  { re: /^#\/research-review-queue$/, title: "研究项目 · 复核队列", run: viewResearchReviewQueue },
  { re: /^#\/research-projects\/(\d+)\/observation-plan$/, title: "研究项目 · 观察计划", run: (m) => viewResearchObservationPlan(m[1]) },
  { re: /^#\/research-projects\/(\d+)\/report-live-compare\/(\d+)$/, title: "研究项目 · 冻结后变化", run: (m) => viewResearchLiveReportComparison(m[1], m[2]) },
  { re: /^#\/research-projects\/(\d+)\/report-compare\/(\d+)\/(\d+)$/, title: "研究项目 · 报告差异", run: (m) => viewResearchReportComparison(m[1], m[2], m[3]) },
  { re: /^#\/research-projects\/(\d+)\/report-versions\/(\d+)$/, title: "研究项目 · 冻结报告", run: (m) => viewResearchReportVersion(m[1], m[2]) },
  { re: /^#\/research-projects\/(\d+)\/report-versions$/, title: "研究项目 · 报告版本", run: (m) => viewResearchReportVersions(m[1]) },
  { re: /^#\/research-projects\/(\d+)\/report$/, title: "研究项目 · 决策报告", run: (m) => viewResearchDecisionReport(m[1]) },
  { re: /^#\/research-projects\/(\d+)$/, title: "研究项目 · 验证", run: (m) => viewResearchProjectDetail(m[1]) },
  { re: /^#\/reviews$/, title: "评论痛点", run: viewReviews },
  { re: /^#\/agent$/, title: "AI 助手", run: viewAgent },
  { re: /^#\/tasks$/, title: "任务中心", run: viewTasks },
  { re: /^#\/tracking\/(\d+)$/, title: "关键词追踪 · 证据复盘", run: (m) => viewTrackingEvidence(m[1]) },
  { re: /^#\/tracking$/, title: "关键词追踪", run: viewTracking },
  { re: /^#\/import$/, title: "本地 HTML 入库", run: viewImport },
  { re: /^#\/import-reviews$/, title: "评论导入", run: viewReviewImport },
  { re: /^#\/warehouse$/, title: "仓库同步", run: viewWarehouseSync },
  { re: /^#\/settings$/, title: "设置", run: viewSettings },
];

async function router() {
  const routeEpoch = ++viewRequestEpoch;
  closeResearchAssociationDialog();
  closeResearchReportFreezeDialog();
  const hash = location.hash || "#/recommendations";
  const route = routes.find((r) => r.re.test(hash));
  if (!route) {
    location.hash = "#/recommendations";
    return;
  }
  const previousHash = renderedRouteHash;
  if (previousHash) {
    saveRouteScroll(previousHash);
  }
  if (isProductDetailRoute(hash)) {
    if (previousHash && previousHash !== hash && !isProductDetailRoute(previousHash)) {
      rememberDetailOrigin(hash, previousHash);
      detailEnteredFromInApp = true;
    } else if (!previousHash) {
      detailEnteredFromInApp = false;
    }
  } else {
    detailEnteredFromInApp = false;
  }
  renderedRouteHash = hash;
  const m = hash.match(route.re);
  viewTitle.textContent = route.title;
  document.title = `${route.title} · 选品助手`;
  document.querySelectorAll(".nav-item").forEach((a) => {
    a.classList.toggle("active", hash.startsWith(a.getAttribute("href")));
  });
  try {
    await route.run(m);
    if (routeEpoch !== viewRequestEpoch) return;
    mountResearchEvidenceContextBar();
    if (hash !== "#/agent") rememberAgentBusinessContext();
    restoreInteractiveSelection(content);
    restoreRouteScroll(hash);
    scheduleClientTranslate();
  } catch (err) {
    if (err?.name === "StaleViewError" || routeEpoch !== viewRequestEpoch) return;
    errorState(err);
    scheduleClientTranslate();
  }
}

/* ---------- 健康指示 ---------- */
async function pingHealth() {
  const dot = document.getElementById("api-status");
  const txt = document.getElementById("api-status-text");
  try {
    const readiness = await api("/api/ready", { allowStale: true });
    dot.className = "dot dot-ok"; txt.textContent = "系统就绪";
    txt.title = readiness.message || "数据库连接与迁移状态正常";
  } catch (err) {
    dot.className = "dot dot-bad";
    txt.textContent = err?.status === 503 ? "数据库未就绪" : "API 不可达";
    txt.title = err?.message || "无法连接本地服务";
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
  initClientTranslationControls();
  window.addEventListener("resize", syncSidebarForViewport);
  document.addEventListener("keydown", handleGlobalShortcuts);
  content.addEventListener("click", handleSelectableClick);
  content.addEventListener("dblclick", handleSelectableDoubleClick);
  content.addEventListener("keydown", handleSelectableKeydown);
  syncSidebarForViewport();
}

document.getElementById("refresh-btn").onclick = router;
document.getElementById("back-btn").onclick = () => history.back();
document.getElementById("open-web-btn").onclick = openCurrentPageInBrowser;
window.addEventListener("hashchange", router);
window.addEventListener("beforeunload", () => saveRouteScroll(renderedRouteHash));
window.addEventListener("DOMContentLoaded", () => { initShellControls(); pingHealth(); router(); });
if (document.readyState !== "loading") { initShellControls(); pingHealth(); router(); }
