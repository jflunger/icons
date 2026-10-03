"use strict";
/* Icon Search frontend: vanilla JS, no build step. */

const $ = (sel, root = document) => root.querySelector(sel);
const PAGE = 300;

// ------------------------------------------------------------------ storage
const store = {
  get(key, fallback) {
    try { const v = localStorage.getItem("iconsearch." + key); return v == null ? fallback : JSON.parse(v); }
    catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem("iconsearch." + key, JSON.stringify(value)); } catch { /* private mode etc. */ }
  },
};

// ------------------------------------------------------------------ state
const prefs = Object.assign(
  { size: 128, stroke: 0, pngSize: 512 },
  store.get("prefs.v2", {}),
);
const S = {
  sets: [],            // from /api/sets
  providers: new Set(),
  q: "", variant: "", category: "",
  view: "search",      // search | favs | recent | similar
  similarTo: null,
  items: [], total: 0, loading: false, done: false,
  sel: -1,             // selected index in items
  detail: null,        // detail JSON of the open icon
  favs: store.get("favs", []),
  recent: store.get("recent", []),
  coll: store.get("coll", []),     // [{id, prefix, name, width, height, body}]
  ctrl: null,
};

// ------------------------------------------------------------------ helpers
function savePrefs() { store.set("prefs.v2", prefs); }
// Icons are black on the light theme and white on the dark one (theme.js sets data-theme).
function theme() { return document.documentElement.dataset.theme === "dark" ? "dark" : "light"; }
function iconColor() { return theme() === "dark" ? "#ffffff" : "#000000"; }
function hex() { return iconColor().slice(1); }

function svgUrl(icon, { size = prefs.size, dl = false, absolute = false, ext = "svg" } = {}) {
  const p = new URLSearchParams({ color: hex(), size: String(size) });
  if (prefs.stroke > 0 && icon.is_stroke) p.set("stroke", String(prefs.stroke));
  if (dl) p.set("dl", "1");
  const path = `/icon/${encodeURIComponent(icon.prefix)}/${encodeURIComponent(icon.name)}.${ext}?${p}`;
  return absolute ? new URL(path, location.origin).href : path;
}

function inlineSvg(icon, cls = "") {
  // Bodies come from our own DB (Iconify packages), built for inline use.
  const sw = prefs.stroke > 0 && icon.is_stroke ? ` style="--sw:${(prefs.stroke * icon.width / 24).toFixed(3)}"` : "";
  const strokeCls = icon.is_stroke ? " stroke" : "";
  return `<svg class="${cls}${strokeCls}" viewBox="0 0 ${icon.width} ${icon.height}"${sw} aria-hidden="true">${icon.body}</svg>`;
}

function setName(prefix) {
  const s = S.sets.find(x => x.prefix === prefix);
  return s ? s.name : prefix;
}

const BADGE = { lucide: "LU", tabler: "TB", ph: "PH", heroicons: "HI", bi: "BI", iconoir: "IO", fluent: "FL", carbon: "CB" };
function badge(prefix) { return BADGE[prefix] || prefix.slice(0, 2).toUpperCase(); }

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function toast(msg, err = false) {
  const el = document.createElement("div");
  el.className = "toast" + (err ? " err" : "");
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), err ? 5000 : 2200);
}

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = `${r.status}`;
    try { msg = (await r.json()).detail || msg; } catch { /* not JSON */ }
    throw new Error(msg);
  }
  return r.json();
}

function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

function pushRecent(icon) {
  S.recent = [icon.id, ...S.recent.filter(id => id !== icon.id)].slice(0, 30);
  store.set("recent", S.recent);
}

// ------------------------------------------------------------------ URL state
function readUrl() {
  const p = new URLSearchParams(location.search);
  S.q = p.get("q") || "";
  S.variant = p.get("v") || "";
  S.category = p.get("cat") || "";
  const provs = p.get("p");
  S.providers = new Set(provs ? provs.split(",").filter(Boolean) : S.sets.map(s => s.prefix));
}

function writeUrl() {
  const p = new URLSearchParams();
  if (S.q) p.set("q", S.q);
  if (S.providers.size && S.providers.size < S.sets.length) p.set("p", [...S.providers].join(","));
  if (S.variant) p.set("v", S.variant);
  if (S.category) p.set("cat", S.category);
  const qs = p.toString();
  history.replaceState(null, "", qs ? `?${qs}` : location.pathname);
}

// ------------------------------------------------------------------ controls
function applyVisualPrefs() {
  document.body.classList.toggle("custom-stroke", prefs.stroke > 0);
  $("#size").value = prefs.size;
  $("#stroke").value = prefs.stroke;
  $("#stroke-out").textContent = prefs.stroke > 0 ? prefs.stroke.toFixed(2).replace(/\.?0+$/, "") : "auto";
  $("#png-size").value = String(prefs.pngSize);
  for (const b of document.querySelectorAll("#theme-mode button")) b.classList.toggle("on", b.dataset.theme === theme());
}

// The icon colour follows the theme, so server-rendered URLs (drag images, downloads) change too.
function setTheme(t, save = true) {
  document.documentElement.dataset.theme = t;
  if (save) store.set("theme", t);
  applyVisualPrefs();
  refreshDownloadLinks();
}

// Stroke only needs re-rendering of inline --sw values.
function rerenderStroke() {
  for (const tile of document.querySelectorAll(".tile")) {
    const icon = S.items[+tile.dataset.i];
    if (icon && icon.is_stroke) tile.querySelector("svg").outerHTML = inlineSvg(icon);
  }
  if (S.detail) renderPreview();
  renderCollection();
}

function bindControls() {
  $("#size").addEventListener("change", e => {
    prefs.size = Math.max(16, Math.min(2048, parseInt(e.target.value, 10) || 128));
    savePrefs(); applyVisualPrefs(); refreshDownloadLinks();
  });
  $("#stroke").addEventListener("input", e => {
    prefs.stroke = parseFloat(e.target.value) || 0;
    savePrefs(); applyVisualPrefs(); rerenderStroke(); refreshDownloadLinks();
  });
  for (const b of document.querySelectorAll("#theme-mode button")) {
    b.addEventListener("click", () => setTheme(b.dataset.theme));
  }
  // Without an explicit choice, keep following the OS setting.
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", e => {
    if (store.get("theme", null) == null) setTheme(e.matches ? "dark" : "light", false);
  });
  $("#png-size").addEventListener("change", e => { prefs.pngSize = +e.target.value; savePrefs(); });

  const onQ = debounce(() => { S.q = $("#q").value.trim(); setView("search"); }, 120);
  $("#q").addEventListener("input", onQ);
  $("#variant").addEventListener("change", e => { S.variant = e.target.value; newSearch(); });
  $("#category").addEventListener("change", e => { S.category = e.target.value; newSearch(); });
  for (const b of document.querySelectorAll("#view-mode button")) {
    b.addEventListener("click", () => setView(b.dataset.view));
  }
}

function renderProviders() {
  const box = $("#providers");
  box.innerHTML = "";
  for (const s of S.sets) {
    const chip = document.createElement("button");
    chip.className = "chip" + (S.providers.has(s.prefix) ? " on" : "");
    chip.innerHTML = `${escapeHtml(s.name)} <span class="n">${s.groups.toLocaleString()}</span>`;
    chip.title = "Click to toggle, Shift-click to show only this set";
    chip.addEventListener("click", e => {
      if (e.shiftKey || e.metaKey) {
        const only = S.providers.size === 1 && S.providers.has(s.prefix);
        S.providers = only ? new Set(S.sets.map(x => x.prefix)) : new Set([s.prefix]);
      } else if (S.providers.has(s.prefix)) {
        S.providers.delete(s.prefix);
        if (!S.providers.size) S.providers = new Set(S.sets.map(x => x.prefix));
      } else {
        S.providers.add(s.prefix);
      }
      renderProviders();
      renderVariants();
      loadCategories();
      newSearch();
    });
    box.appendChild(chip);
  }
}

// Variant options follow the selected providers (Lucide has no "fill", etc.).
function renderVariants() {
  const order = S.allVariants || [];
  const avail = new Set(S.sets.filter(s => S.providers.has(s.prefix)).flatMap(s => s.variants));
  if (S.variant && !avail.has(S.variant)) S.variant = "";
  $("#variant").innerHTML = `<option value="">any</option>` +
    order.filter(v => avail.has(v)).map(v => `<option>${escapeHtml(v)}</option>`).join("");
  $("#variant").value = S.variant;
}

async function loadCategories() {
  const sel = $("#category");
  try {
    const cats = await api(`/api/categories?providers=${encodeURIComponent([...S.providers].join(","))}`);
    sel.innerHTML = `<option value="">any</option>` + cats.map(c =>
      `<option value="${escapeHtml(c.category)}">${escapeHtml(c.category)} (${c.count})</option>`).join("");
    if (S.category && !cats.some(c => c.category === S.category)) S.category = "";
    sel.value = S.category;
  } catch { /* index not ready; leave as is */ }
}

// ------------------------------------------------------------------ search / views
function setView(view) {
  S.view = view;
  for (const b of document.querySelectorAll("#view-mode button")) b.classList.toggle("on", b.dataset.view === view);
  newSearch();
}

function newSearch() {
  if (S.ctrl) S.ctrl.abort();
  S.ctrl = null; S.loading = false;
  S.items = []; S.total = 0; S.done = false; S.sel = -1;
  $("#grid").innerHTML = "";
  $("#grid-wrap").scrollTop = 0;
  if (S.view === "search") writeUrl();
  loadMore();
}

async function loadMore() {
  if (S.loading || S.done) return;
  const ctrl = new AbortController();
  S.ctrl = ctrl; S.loading = true;
  try {
    let items, total;
    if (S.view === "search") {
      const p = new URLSearchParams({ q: S.q, limit: PAGE, offset: S.items.length });
      if (S.providers.size < S.sets.length) p.set("providers", [...S.providers].join(","));
      if (S.variant) p.set("variant", S.variant);
      if (S.category) p.set("category", S.category);
      const d = await api(`/api/search?${p}`, { signal: ctrl.signal });
      items = d.items; total = d.total;
    } else if (S.view === "similar") {
      const d = await api(`/api/similar/${encodeURIComponent(S.similarTo.id)}`, { signal: ctrl.signal });
      items = d.items; total = items.length;
    } else {
      const ids = S.view === "favs" ? S.favs : S.recent;
      const d = ids.length ? await api(`/api/icons?ids=${encodeURIComponent(ids.join(","))}`, { signal: ctrl.signal }) : { items: [] };
      items = d.items; total = items.length;
    }
    if (ctrl.signal.aborted) return;
    appendTiles(items);
    S.total = total;
    S.done = S.view !== "search" || S.items.length >= total || !items.length;
    renderStatus();
  } catch (e) {
    if (e.name === "AbortError") return;
    $("#status").textContent = "";
    showEmpty(e.message.includes("being built") ? "The icon index is being built. This takes about a minute on first start…" : `Error: ${e.message}`);
    if (e.message.includes("being built")) setTimeout(() => boot(), 5000);
  } finally {
    if (S.ctrl === ctrl) { S.loading = false; S.ctrl = null; }
  }
}

function renderStatus() {
  const n = S.total.toLocaleString();
  const label = {
    search: S.q ? `${n} results` : `${n} icons`,
    favs: `${n} favourites`,
    recent: `${n} recent`,
    similar: `${n} similar to ${S.similarTo ? S.similarTo.name : ""}`,
  }[S.view];
  $("#status").textContent = label;
  if (!S.items.length) {
    showEmpty({
      search: (S.q ? `No icons match “${S.q}”.` : "No icons with these filters.") +
        (S.variant || S.category ? " Try setting Variant and Category back to “any”." : " Try a broader word, or turn more providers on."),
      favs: "No favourites yet. Press f or ☆ in the detail panel to add one.",
      recent: "Icons you copy, drag or download show up here.",
      similar: "No similar icons found in other sets.",
    }[S.view]);
  } else {
    $("#empty").hidden = true;
  }
}

function showEmpty(msg) {
  const el = $("#empty");
  el.textContent = msg;
  el.hidden = false;
}

function appendTiles(items) {
  const grid = $("#grid");
  const favs = new Set(S.favs);
  const frag = document.createDocumentFragment();
  for (const icon of items) {
    const i = S.items.length;
    S.items.push(icon);
    const t = document.createElement("div");
    t.className = "tile";
    t.dataset.i = i;
    t.setAttribute("role", "option");
    t.title = `${icon.name} · ${setName(icon.prefix)}`;
    t.innerHTML = inlineSvg(icon) + `<span class="badge">${badge(icon.prefix)}</span>` +
      (favs.has(icon.id) ? `<span class="star">★</span>` : "") +
      `<span class="name">${escapeHtml(icon.base_name)}</span>`;
    frag.appendChild(t);
  }
  grid.appendChild(frag);
}

// ------------------------------------------------------------------ selection + detail
function select(i, { open = true, scroll = true } = {}) {
  if (i < 0 || i >= S.items.length) return;
  const prev = $(".tile.sel");
  if (prev) prev.classList.remove("sel");
  S.sel = i;
  const tile = $(`.tile[data-i="${i}"]`);
  if (tile) {
    tile.classList.add("sel");
    if (scroll) tile.scrollIntoView({ block: "nearest" });
  }
  if (open) openDetail(S.items[i]);
}

async function openDetail(icon) {
  try {
    S.detail = await api(`/api/icon/${encodeURIComponent(icon.id)}`);
  } catch (e) { toast(e.message, true); return; }
  renderDetail();
}

function renderPreview() {
  $("#preview").innerHTML = inlineSvg(S.detail);
}

function renderDetail() {
  const d = S.detail;
  $("#detail").hidden = false;
  renderPreview();
  $("#d-name").textContent = d.name;
  $("#d-set").textContent = `${setName(d.prefix)} · ${d.variant}${d.category ? " · " + d.category : ""} · ${d.width}×${d.height}`;
  const vbox = $("#d-variants");
  vbox.innerHTML = "";
  if (d.siblings.length > 1) {
    for (const s of d.siblings) {
      const c = document.createElement("button");
      c.className = "chip" + (s.id === d.id ? " on" : "");
      c.textContent = s.variant + (d.siblings.filter(x => x.variant === s.variant).length > 1 ? ` ${s.width}` : "");
      c.title = s.name;
      c.onclick = () => openDetail(s);
      vbox.appendChild(c);
    }
  }
  const tbox = $("#d-tags");
  tbox.innerHTML = "";
  for (const t of d.tags.slice(0, 40)) {
    const b = document.createElement("button");
    b.className = "tag";
    b.textContent = t;
    b.title = `Search for “${t}”`;
    b.onclick = () => { $("#q").value = t; S.q = t; setView("search"); };
    tbox.appendChild(b);
  }
  const lic = d.set || {};
  $("#d-license").innerHTML = lic.license_title
    ? `License: <a href="${escapeHtml(lic.license_url || "/licenses")}" target="_blank" rel="noopener">${escapeHtml(lic.license_title)}</a> · v${escapeHtml(lic.version || "")}`
    : "";
  $("#fav").textContent = S.favs.includes(d.id) ? "★ Favourited" : "☆ Favourite";
  $("#collect").textContent = S.coll.some(c => c.id === d.id) ? "✓ In collection" : "＋ Collection";
  refreshDownloadLinks();
}

function closeDetail() {
  $("#detail").hidden = true;
  S.detail = null;
}

function refreshDownloadLinks() {
  if (S.detail) $("#download").href = svgUrl(S.detail, { dl: true });
  renderCollection();
}

// ------------------------------------------------------------------ drag
// PowerPoint only inserts a picture for a native <img> drag: the browser then supplies the
// image itself plus an HTML <img src> that Office downloads. Custom setData (URL text,
// Chrome's DownloadURL) lands as a hyperlink. So on hover each tile/preview gets an invisible
// SVG <img> on top, loaded before the drag starts, and dragstart leaves the data alone.
// SVG (not PNG) so Finder/Figma get a vector file; PowerPoint may not accept an SVG <img>.
function armDrag(el, icon) {
  const src = svgUrl(icon, { absolute: true });
  let img = el.querySelector("img.drag-src");
  if (!img) {
    img = document.createElement("img");
    img.className = "drag-src";
    img.alt = icon.name;
    el.appendChild(img);
  }
  if (img.src !== src) img.src = src;
}

function setDrag(e, icon) {
  const dt = e.dataTransfer;
  dt.effectAllowed = "copy";
  const ghost = $("#drag-ghost");
  ghost.innerHTML = inlineSvg(icon);
  ghost.style.color = iconColor();
  dt.setDragImage(ghost, 32, 32);
  pushRecent(icon);
}

// ------------------------------------------------------------------ clipboard
async function fetchSvgText(icon, size) {
  const r = await fetch(svgUrl(icon, { size }));
  if (!r.ok) throw new Error(`SVG request failed (${r.status})`);
  return r.text();
}

async function renderPng(icon, px) {
  const text = await fetchSvgText(icon, px);
  const url = URL.createObjectURL(new Blob([text], { type: "image/svg+xml" }));
  try {
    const img = new Image();
    img.src = url;
    await img.decode();
    const scale = px / Math.max(icon.width, icon.height);
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(icon.width * scale);
    canvas.height = Math.round(icon.height * scale);
    canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
    return await new Promise((res, rej) => canvas.toBlob(b => b ? res(b) : rej(new Error("PNG encode failed")), "image/png"));
  } finally {
    URL.revokeObjectURL(url);
  }
}

function legacyCopyText(text) {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed"; ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  const ok = document.execCommand("copy");
  ta.remove();
  return ok;
}

async function copyPng(icon = S.detail) {
  if (!icon) return;
  if (!window.isSecureContext || !navigator.clipboard || !window.ClipboardItem) {
    toast("Copy PNG needs HTTPS or localhost. Drag or Download instead.", true);
    return;
  }
  const px = prefs.pngSize;
  try {
    // Pass a promise so Safari keeps the user gesture while the PNG renders.
    await navigator.clipboard.write([new ClipboardItem({ "image/png": renderPng(icon, px) })]);
    pushRecent(icon);
    toast(`Copied PNG ${px}px`);
  } catch (e) {
    toast(`Copy PNG failed: ${e.message}`, true);
  }
}

async function copySvg(icon = S.detail) {
  if (!icon) return;
  const textP = fetchSvgText(icon, prefs.size);
  try {
    if (window.isSecureContext && navigator.clipboard && window.ClipboardItem) {
      const types = { "text/plain": textP.then(t => new Blob([t], { type: "text/plain" })) };
      if (!ClipboardItem.supports || ClipboardItem.supports("image/svg+xml")) {
        types["image/svg+xml"] = textP.then(t => new Blob([t], { type: "image/svg+xml" }));
      }
      try {
        await navigator.clipboard.write([new ClipboardItem(types)]);
      } catch {
        await navigator.clipboard.writeText(await textP);
      }
    } else if (!legacyCopyText(await textP)) {
      throw new Error("the browser refused");
    }
    pushRecent(icon);
    toast("Copied SVG");
  } catch (e) {
    toast(`Copy SVG failed: ${e.message}`, true);
  }
}

// ------------------------------------------------------------------ favourites, collection, similar
function toggleFav(icon = S.detail) {
  if (!icon) return;
  const on = S.favs.includes(icon.id);
  S.favs = on ? S.favs.filter(id => id !== icon.id) : [icon.id, ...S.favs];
  store.set("favs", S.favs);
  $("#fav-count").textContent = S.favs.length ? S.favs.length : "";
  toast(on ? "Removed from favourites" : "Added to favourites");
  const idx = S.items.findIndex(x => x.id === icon.id);
  const tile = idx >= 0 && $(`.tile[data-i="${idx}"]`);
  if (tile) {
    const star = tile.querySelector(".star");
    if (on && star) star.remove();
    if (!on && !star) tile.insertAdjacentHTML("beforeend", `<span class="star">★</span>`);
  }
  if (S.detail && S.detail.id === icon.id) renderDetail();
}

function toggleCollect(icon = S.detail) {
  if (!icon) return;
  const on = S.coll.some(c => c.id === icon.id);
  if (on) S.coll = S.coll.filter(c => c.id !== icon.id);
  else {
    if (S.coll.length >= 200) { toast("A collection holds up to 200 icons", true); return; }
    const { id, prefix, name, width, height, body, is_stroke } = icon;
    S.coll.push({ id, prefix, name, width, height, body, is_stroke });
  }
  store.set("coll", S.coll);
  renderCollection();
  if (S.detail && S.detail.id === icon.id) renderDetail();
}

function renderCollection() {
  const bar = $("#collection-bar");
  bar.hidden = !S.coll.length;
  if (!S.coll.length) return;
  $("#coll-label").textContent = `Collection · ${S.coll.length}`;
  const box = $("#coll-items");
  box.innerHTML = "";
  for (const icon of S.coll) {
    const b = document.createElement("button");
    b.title = `${icon.name} (click to remove)`;
    b.innerHTML = inlineSvg(icon);
    b.onclick = () => toggleCollect(icon);
    box.appendChild(b);
  }
  const p = new URLSearchParams({ ids: S.coll.map(c => c.id).join(","), color: hex(), size: prefs.size });
  if (prefs.stroke > 0) p.set("stroke", prefs.stroke);
  $("#coll-zip").href = `/api/zip?${p}`;
}

function showSimilar(icon = S.detail) {
  if (!icon) return;
  S.similarTo = icon;
  setView("similar");
}

// ------------------------------------------------------------------ events
function bindGrid() {
  const grid = $("#grid");
  grid.addEventListener("click", e => {
    const t = e.target.closest(".tile");
    if (t) select(+t.dataset.i, { scroll: false });
  });
  grid.addEventListener("dblclick", e => {
    const t = e.target.closest(".tile");
    if (t) copySvg(S.items[+t.dataset.i]);
  });
  grid.addEventListener("pointerover", e => {
    const t = e.target.closest(".tile");
    if (t) armDrag(t, S.items[+t.dataset.i]);
  });
  grid.addEventListener("dragstart", e => {
    const t = e.target.closest(".tile");
    if (t) setDrag(e, S.items[+t.dataset.i]);
  });
  $("#preview").addEventListener("pointerover", () => { if (S.detail) armDrag($("#preview"), S.detail); });
  $("#preview").addEventListener("dragstart", e => { if (S.detail) setDrag(e, S.detail); });

  const io = new IntersectionObserver(entries => {
    if (entries.some(en => en.isIntersecting) && !S.loading && !S.done && S.items.length) loadMore();
  }, { root: $("#grid-wrap"), rootMargin: "800px" });
  io.observe($("#sentinel"));

  $("#detail-close").onclick = closeDetail;
  $("#copy-svg").onclick = () => copySvg();
  $("#copy-png").onclick = () => copyPng();
  $("#download").addEventListener("click", () => S.detail && pushRecent(S.detail));
  $("#fav").onclick = () => toggleFav();
  $("#collect").onclick = () => toggleCollect();
  $("#similar").onclick = () => showSimilar();
  $("#coll-clear").onclick = () => { S.coll = []; store.set("coll", S.coll); renderCollection(); if (S.detail) renderDetail(); };
}

function gridColumns() {
  return getComputedStyle($("#grid")).gridTemplateColumns.split(" ").length || 1;
}

function bindKeys() {
  document.addEventListener("keydown", e => {
    const inField = e.target.matches("input, select, textarea");
    const q = $("#q");
    if (e.key === "/" && !inField) { e.preventDefault(); q.focus(); q.select(); return; }
    if (e.key === "Escape") {
      if (!$("#detail").hidden) { closeDetail(); return; }
      if (q.value) { q.value = ""; S.q = ""; setView("search"); }
      q.focus();
      return;
    }
    if (e.target === q && (e.key === "ArrowDown" || e.key === "Enter") && S.items.length) {
      e.preventDefault();
      q.blur();
      select(Math.max(S.sel, 0), { open: e.key === "Enter" });
      return;
    }
    if (inField || e.metaKey || e.ctrlKey || e.altKey) return;
    const cols = gridColumns();
    const moves = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: cols, ArrowUp: -cols };
    if (e.key in moves) {
      e.preventDefault();
      if (S.sel < 0) { select(0, { open: false }); return; }
      const next = S.sel + moves[e.key];
      if (next < 0 && e.key === "ArrowUp") { q.focus(); return; }
      select(Math.min(Math.max(next, 0), S.items.length - 1), { open: !$("#detail").hidden });
      if (S.sel > S.items.length - cols * 3 && !S.done) loadMore();
      return;
    }
    const cur = S.detail || S.items[S.sel];
    if (e.key === "Enter" && S.sel >= 0) { e.preventDefault(); select(S.sel); }
    else if (e.key === "c" && cur) copySvg(cur);
    else if (e.key === "p" && cur) copyPng(cur);
    else if (e.key === "f" && cur) toggleFav(cur);
  });
}

// ------------------------------------------------------------------ boot
async function boot() {
  let d;
  try {
    d = await api("/api/sets");
  } catch (e) {
    showEmpty(e.message.includes("being built")
      ? "The icon index is being built. This takes about a minute on first start…"
      : `Could not load icon sets: ${e.message}`);
    setTimeout(boot, 5000);
    return;
  }
  S.sets = d.sets;
  if (!store.get("prefs.v2", null)) prefs.pngSize = d.png_size || 512;
  readUrl();
  $("#q").value = S.q;
  S.allVariants = d.all_variants;
  renderProviders();
  renderVariants();
  await loadCategories();
  setView("search");
}

(function init() {
  $("#insecure-banner").hidden = window.isSecureContext;
  if (!window.isSecureContext) $("#copy-png").disabled = true;
  $("#fav-count").textContent = S.favs.length ? S.favs.length : "";
  applyVisualPrefs();
  bindControls();
  bindGrid();
  bindKeys();
  renderCollection();
  boot();
})();
