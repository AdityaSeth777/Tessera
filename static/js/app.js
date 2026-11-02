import * as L from './layout.js';

const $ = (s, r = document) => r.querySelector(s);
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'html') el.innerHTML = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(kid));
  return el;
}

const STORE_KEY = 'local-collage.state.v1';
const SWATCHES = ['#ffffff', '#f5efe6', '#ece4d8', '#e9e9ee', '#111111', '#000000', '#2b2b30', '#ffd6e0', '#cde8ff', '#d7f2d0', '#fff1b8', '#ff5a36', '#0a84ff', '#1f3b2d'];

const defaults = () => ({
  canvas: { format: '4:5', aspect: [4, 5], bg: '#ffffff' },
  style: { shape: 'rounded', radius: 0.025, gap: 0.012, margin: 0.025 },
  keepOrder: false, tiles: {}, tree: null, zTop: 0,
});

let library = [];
const mediaById = {};
let state = loadState();
let undoStack = [], redoStack = [];
let selected = null;
let liveBefore = null;
const transport = { playing: false, t: 0, base: 0, perf: 0, raf: 0 };

// ------------------------------------------------------------ persistence & history

function loadState() {
  try {
    const s = JSON.parse(localStorage.getItem(STORE_KEY));
    if (s && s.canvas && s.style) return { ...defaults(), ...s };
  } catch {}
  return null;
}
let persistTimer = 0;
function persist() {
  clearTimeout(persistTimer);
  persistTimer = setTimeout(() => {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(state)); } catch {}
  }, 150);
}
const snap = () => JSON.stringify(state);
function pushHistory(before) {
  if (before !== snap()) {
    undoStack.push(before);
    if (undoStack.length > 150) undoStack.shift();
    redoStack = [];
  }
  persist();
  updateUndo();
}
function change(fn, opts = {}) {
  const before = snap();
  fn();
  pushHistory(before);
  render(opts);
}
function begin() { if (liveBefore === null) liveBefore = snap(); }
function end() { if (liveBefore !== null) { pushHistory(liveBefore); liveBefore = null; } }
function restore(s) {
  state = JSON.parse(s);
  if (selected && !state.tiles[selected]) selected = null;
  persist();
  render();
}
function undo() { if (undoStack.length) { redoStack.push(snap()); restore(undoStack.pop()); updateUndo(); } }
function redo() { if (redoStack.length) { undoStack.push(snap()); restore(redoStack.pop()); updateUndo(); } }
function updateUndo() {
  $('#btnUndo').disabled = !undoStack.length;
  $('#btnRedo').disabled = !redoStack.length;
}

// ------------------------------------------------------------ helpers

const canvasAspect = () => state.canvas.aspect[0] / state.canvas.aspect[1];
const mediaOf = (tile) => mediaById[tile?.mediaId];
const aspectOf = (id) => mediaOf(state.tiles[id])?.aspect || 1;
const dockedIds = () => L.leaves(state.tree).filter((id) => state.tiles[id]);
const tileCount = () => Object.keys(state.tiles).length;
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const fmtTime = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
const pct = (v) => `${(v * 100).toFixed(1)}%`;
const uid = () => 't' + Math.random().toString(36).slice(2, 9);

function toast(msg, err = false) {
  const el = h('div', { class: 'toast' + (err ? ' err' : '') }, msg);
  $('#toasts').append(el);
  setTimeout(() => el.remove(), err ? 6000 : 2800);
}

function relayout() {
  const ids = dockedIds();
  for (const t of Object.values(state.tiles)) if (!t.floating && !ids.includes(t.id)) ids.push(t.id);
  state.tree = L.autoLayout(ids, aspectOf, canvasAspect(), state.keepOrder);
}

function newTile(mediaId) {
  return { id: uid(), mediaId, fit: 'cover', zoom: 1, panX: 0, panY: 0, floating: false, rect: null, rotation: 0, z: 0, start: 0, audio: true };
}

function addMedia(mediaIds) {
  const room = L.MAX_TILES - tileCount();
  if (room <= 0) { toast(`A collage holds up to ${L.MAX_TILES} tiles`, true); return; }
  if (mediaIds.length > room) toast(`Added ${room} — a collage holds up to ${L.MAX_TILES} tiles`);
  change(() => {
    let last;
    for (const mid of mediaIds.slice(0, room)) {
      const t = newTile(mid);
      state.tiles[t.id] = t;
      last = t.id;
    }
    relayout();
    selected = last;
  });
}

function removeTile(id) {
  change(() => {
    const floating = state.tiles[id]?.floating;
    delete state.tiles[id];
    if (!floating) relayout();
    if (selected === id) selected = null;
  });
}

const CONTENT_KEYS = ['mediaId', 'fit', 'zoom', 'panX', 'panY', 'start', 'audio'];
function swapContent(a, b) {
  if (a === b) return;
  change(() => {
    const A = state.tiles[a], B = state.tiles[b];
    for (const k of CONTENT_KEYS) [A[k], B[k]] = [B[k], A[k]];
    selected = b;
  });
}

function replaceMedia(id, mediaId) {
  change(() => Object.assign(state.tiles[id], { mediaId, zoom: 1, panX: 0, panY: 0, start: 0 }));
}

function floatTile(id) {
  const it = lastLayout?.items.find((x) => x.tile.id === id);
  if (!it) return;
  const { W, H } = lastLayout;
  change(() => {
    const t = state.tiles[id];
    const r = it.cell;
    t.floating = true;
    t.rect = { x: r.x / W, y: r.y / H, w: r.w / W, h: r.h / H };
    t.z = ++state.zTop;
    state.tree = L.removeLeaf(state.tree, id);
  });
}

function dockTile(id) {
  change(() => {
    const t = state.tiles[id];
    t.floating = false;
    t.rotation = 0;
    relayout();
  });
}

function setMode(mode) {
  if (!lastLayout) return;
  const { W, H } = lastLayout;
  change(() => {
    if (mode === 'free') {
      for (const it of lastLayout.items) {
        const t = state.tiles[it.tile.id];
        if (t.floating) continue;
        t.floating = true;
        t.rect = { x: it.cell.x / W, y: it.cell.y / H, w: it.cell.w / W, h: it.cell.h / H };
        t.z = ++state.zTop;
      }
      state.tree = null;
    } else {
      const ids = dockedIds();
      Object.values(state.tiles).filter((t) => t.floating).sort((a, b) => a.z - b.z)
        .forEach((t) => { t.floating = false; t.rotation = 0; ids.push(t.id); });
      state.tree = L.autoLayout(ids, aspectOf, canvasAspect(), true);
    }
  });
}

// ------------------------------------------------------------ stage

const stage = $('#stage');
const stageWrap = $('#stageWrap');
const clip = h('div', { class: 'clip' });
stage.append(clip);
const tileEls = new Map();
let dividerEls = [];
let lastLayout = null;
const selBox = h('div', { class: 'sel-box hidden' },
  h('div', { class: 'stem' }),
  h('div', { class: 'h rotate', title: 'Rotate (⇧ snaps 15°)' }),
  h('div', { class: 'h resize', title: 'Resize (⇧ free aspect)' }));
stage.append(selBox);

function stageSize() {
  const r = stageWrap.getBoundingClientRect();
  const [aw, ah] = state.canvas.aspect;
  const s = Math.min((r.width - 64) / aw, (r.height - 64) / ah);
  return [Math.max(50, aw * s), Math.max(50, ah * s)];
}

function mediaEl(m) {
  if (m.kind === 'video') {
    const v = h('video', { class: 'media', src: m.proxy, muted: true, loop: true, playsinline: true, preload: 'auto' });
    v.muted = true;
    v.addEventListener('loadedmetadata', () => syncVideo(v));
    return v;
  }
  return h('img', { class: 'media', src: m.preview, draggable: 'false' });
}

function createTileEl(id) {
  const el = h('div', { class: 'tile', 'data-id': id });
  const tools = h('div', { class: 'tile-tools' },
    h('button', { class: 'grip', draggable: 'true', title: 'Drag onto another tile to swap', ondragstart: (e) => {
      e.dataTransfer.setData('application/x-tile', id);
      e.dataTransfer.effectAllowed = 'move';
    } }, '⠿'),
    h('button', { class: 'flt', title: 'Float / dock', onclick: (e) => { e.stopPropagation(); state.tiles[id].floating ? dockTile(id) : floatTile(id); } }, '⧉'),
    h('button', { title: 'Remove', onclick: (e) => { e.stopPropagation(); removeTile(id); } }, '✕'));
  el.append(tools);
  el.addEventListener('pointerdown', (e) => onTilePointerDown(e, id));
  el.addEventListener('wheel', (e) => onTileWheel(e, id), { passive: false });
  el.addEventListener('dragover', (e) => {
    const types = e.dataTransfer.types;
    if (types.includes('application/x-tile') || types.includes('application/x-media')) {
      e.preventDefault(); e.stopPropagation(); el.classList.add('drop-target');
    }
  });
  el.addEventListener('dragleave', () => el.classList.remove('drop-target'));
  el.addEventListener('drop', (e) => {
    el.classList.remove('drop-target');
    const t = e.dataTransfer.getData('application/x-tile');
    const m = e.dataTransfer.getData('application/x-media');
    if (t || m) { e.preventDefault(); e.stopPropagation(); }
    if (t) swapContent(t, id);
    else if (m) replaceMedia(id, m);
  });
  clip.append(el);
  tileEls.set(id, el);
  return el;
}

function renderStage() {
  const [W, H] = stageSize();
  Object.assign(stage.style, { width: W + 'px', height: H + 'px', background: state.canvas.bg });
  const lay = L.computeLayout(state, W, H);
  lastLayout = { ...lay, W, H };
  const seen = new Set();

  lay.items.forEach((it, i) => {
    const t = it.tile, m = mediaOf(t);
    if (!m) return;
    seen.add(t.id);
    const el = tileEls.get(t.id) || createTileEl(t.id);
    let media = el.querySelector('.media');
    if (!media || media.dataset.mediaId !== m.id) {
      media?.remove();
      el.querySelector('.vbadge')?.remove();
      media = mediaEl(m);
      media.dataset.mediaId = m.id;
      el.prepend(media);
      if (m.kind === 'video') el.append(h('div', { class: 'vbadge' }, '▶ ' + fmtTime(m.duration)));
    }
    const r = it.rect;
    Object.assign(el.style, {
      left: r.x + 'px', top: r.y + 'px', width: r.w + 'px', height: r.h + 'px',
      borderRadius: it.radius + 'px', zIndex: t.floating ? 100 + t.z : 1 + i,
      transform: t.floating && t.rotation ? `rotate(${t.rotation}deg)` : '',
    });
    el.classList.toggle('floating', !!t.floating);
    el.querySelector('.flt').textContent = t.floating ? '⊞' : '⧉';
    el.querySelector('.flt').title = t.floating ? 'Dock back into the grid' : 'Float (move & rotate freely)';
    const p = L.placement(r.w, r.h, m.width, m.height, t.fit, t.zoom, t.panX, t.panY);
    Object.assign(media.style, { left: p.left + 'px', top: p.top + 'px', width: p.dw + 'px', height: p.dh + 'px' });
  });
  for (const [id, el] of tileEls) if (!seen.has(id)) { el.remove(); tileEls.delete(id); }

  // dividers are reused by index so a drag keeps its pointer capture
  while (dividerEls.length > lay.dividers.length) dividerEls.pop().remove();
  lay.dividers.forEach((d, i) => {
    let el = dividerEls[i];
    if (!el) {
      el = h('div', { class: 'divider' });
      el.addEventListener('pointerdown', (e) => onDividerDown(e, i));
      stage.append(el);
      dividerEls[i] = el;
    }
    const thick = Math.max(12, state.style.gap * Math.min(W, H));
    el.className = 'divider ' + d.dir + (el.classList.contains('active') ? ' active' : '');
    Object.assign(el.style, d.dir === 'row'
      ? { left: d.at - thick / 2 + 'px', top: d.rect.y + 'px', width: thick + 'px', height: d.rect.h + 'px' }
      : { left: d.rect.x + 'px', top: d.at - thick / 2 + 'px', width: d.rect.w + 'px', height: thick + 'px' });
  });

  const sel = lay.items.find((it) => it.tile.id === selected);
  selBox.classList.toggle('hidden', !sel);
  if (sel) {
    const r = sel.rect, fl = sel.tile.floating;
    Object.assign(selBox.style, {
      left: r.x + 'px', top: r.y + 'px', width: r.w + 'px', height: r.h + 'px',
      borderRadius: sel.radius + 'px', transform: fl && sel.tile.rotation ? `rotate(${sel.tile.rotation}deg)` : '',
    });
    selBox.querySelectorAll('.h, .stem').forEach((x) => x.classList.toggle('hidden', !fl));
  }
  $('#emptyHint').classList.toggle('hidden', tileCount() > 0);
  $('#emptyHint').style.color = luminance(state.canvas.bg) > 0.5 ? 'rgba(0,0,0,.45)' : 'rgba(255,255,255,.55)';
}

function luminance(hex) {
  const n = parseInt(hex.slice(1), 16);
  return (0.2126 * (n >> 16) + 0.7152 * ((n >> 8) & 255) + 0.0722 * (n & 255)) / 255;
}

function stagePoint(e) {
  const r = stage.getBoundingClientRect();
  return { x: e.clientX - r.left, y: e.clientY - r.top };
}

function drag(e, onMove, onUp) {
  const target = e.currentTarget;
  target.setPointerCapture(e.pointerId);
  const move = (ev) => onMove(ev);
  const up = (ev) => {
    target.removeEventListener('pointermove', move);
    target.removeEventListener('pointerup', up);
    target.removeEventListener('pointercancel', up);
    onUp?.(ev);
  };
  target.addEventListener('pointermove', move);
  target.addEventListener('pointerup', up);
  target.addEventListener('pointercancel', up);
}

function onTilePointerDown(e, id) {
  if (e.button !== 0 || e.target.closest('.tile-tools')) return;
  e.preventDefault();
  const t = state.tiles[id];
  const before = snap();
  const selChanged = selected !== id;
  selected = id;
  if (t.floating && t.z !== state.zTop) t.z = ++state.zTop;
  const start = stagePoint(e);
  const { W, H } = lastLayout;
  const it = lastLayout.items.find((x) => x.tile.id === id);
  const m = mediaOf(t);
  const p0 = L.placement(it.rect.w, it.rect.h, m.width, m.height, t.fit, t.zoom, t.panX, t.panY);
  const mode = t.floating && !e.altKey ? 'move' : 'pan';
  const r0 = t.rect ? { ...t.rect } : null;
  const pan0 = { x: t.panX, y: t.panY };
  renderStage();
  if (selChanged) renderInspector();
  drag(e, (ev) => {
    const pt = stagePoint(ev);
    let dx = pt.x - start.x, dy = pt.y - start.y;
    if (mode === 'move') {
      t.rect.x = r0.x + dx / W;
      t.rect.y = r0.y + dy / H;
    } else {
      if (t.floating && t.rotation) {
        const a = (-t.rotation * Math.PI) / 180;
        [dx, dy] = [dx * Math.cos(a) - dy * Math.sin(a), dx * Math.sin(a) + dy * Math.cos(a)];
      }
      if (p0.maxX > 0.5) t.panX = clamp(pan0.x + dx / p0.maxX, -1, 1);
      if (p0.maxY > 0.5) t.panY = clamp(pan0.y + dy / p0.maxY, -1, 1);
    }
    renderStage();
  }, () => { pushHistory(before); renderInspector(); });
}

let wheelBefore = null, wheelTimer = 0;
function onTileWheel(e, id) {
  e.preventDefault();
  const t = state.tiles[id];
  if (wheelBefore === null) wheelBefore = snap();
  selected = id;
  t.zoom = clamp(t.zoom * Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0018)), 0.3, 8);
  renderStage();
  clearTimeout(wheelTimer);
  wheelTimer = setTimeout(() => { pushHistory(wheelBefore); wheelBefore = null; renderInspector(); }, 400);
}

function onDividerDown(e, idx) {
  e.preventDefault();
  e.stopPropagation();
  const d = lastLayout.dividers[idx];
  const before = snap();
  const start = stagePoint(e);
  const s0 = [...d.node.sizes];
  const pair = s0[d.i] + s0[d.i + 1];
  const minS = Math.min(pair * 0.45, d.total * 0.06);
  const el = e.currentTarget;
  el.classList.add('active');
  drag(e, (ev) => {
    const pt = stagePoint(ev);
    const delta = ((d.dir === 'row' ? pt.x - start.x : pt.y - start.y) / d.avail) * d.total;
    const a = clamp(s0[d.i] + delta, minS, pair - minS);
    d.node.sizes[d.i] = a;
    d.node.sizes[d.i + 1] = pair - a;
    renderStage();
  }, () => { el.classList.remove('active'); pushHistory(before); });
}

selBox.querySelector('.resize').addEventListener('pointerdown', (e) => {
  e.preventDefault(); e.stopPropagation();
  const t = state.tiles[selected];
  if (!t?.floating) return;
  const before = snap();
  const { W, H } = lastLayout;
  const cx = (t.rect.x + t.rect.w / 2) * W, cy = (t.rect.y + t.rect.h / 2) * H;
  const a = (-(t.rotation || 0) * Math.PI) / 180;
  const local = (pt) => {
    const dx = pt.x - cx, dy = pt.y - cy;
    return { x: dx * Math.cos(a) - dy * Math.sin(a), y: dx * Math.sin(a) + dy * Math.cos(a) };
  };
  const l0 = local(stagePoint(e));
  const len0 = Math.hypot(l0.x, l0.y) || 1;
  const w0 = t.rect.w * W, h0 = t.rect.h * H;
  drag(e, (ev) => {
    const l = local(stagePoint(ev));
    let w, hh;
    if (ev.shiftKey && state.style.shape !== 'circle') { w = 2 * Math.abs(l.x); hh = 2 * Math.abs(l.y); }
    else { const k = Math.hypot(l.x, l.y) / len0; w = w0 * k; hh = h0 * k; }
    w = Math.max(24, w); hh = Math.max(24, hh);
    Object.assign(t.rect, { x: (cx - w / 2) / W, y: (cy - hh / 2) / H, w: w / W, h: hh / H });
    renderStage();
  }, () => { pushHistory(before); renderInspector(); });
});

selBox.querySelector('.rotate').addEventListener('pointerdown', (e) => {
  e.preventDefault(); e.stopPropagation();
  const t = state.tiles[selected];
  if (!t?.floating) return;
  const before = snap();
  const { W, H } = lastLayout;
  const cx = (t.rect.x + t.rect.w / 2) * W, cy = (t.rect.y + t.rect.h / 2) * H;
  drag(e, (ev) => {
    const pt = stagePoint(ev);
    let deg = (Math.atan2(pt.y - cy, pt.x - cx) * 180) / Math.PI + 90;
    if (ev.shiftKey) deg = Math.round(deg / 15) * 15;
    else if (Math.abs(deg - Math.round(deg / 90) * 90) < 3) deg = Math.round(deg / 90) * 90;
    deg = ((deg + 540) % 360) - 180;
    t.rotation = Math.round(deg * 10) / 10;
    renderStage();
  }, () => { pushHistory(before); renderInspector(); });
});

stage.addEventListener('pointerdown', (e) => {
  if (e.target === stage || e.target === clip) { selected = null; renderStage(); renderInspector(); }
});

// ------------------------------------------------------------ drop & upload

stageWrap.addEventListener('dragover', (e) => {
  const types = e.dataTransfer.types;
  if (types.includes('Files') || types.includes('application/x-media')) { e.preventDefault(); stageWrap.classList.add('drop'); }
});
stageWrap.addEventListener('dragleave', (e) => { if (e.target === stageWrap) stageWrap.classList.remove('drop'); });
stageWrap.addEventListener('drop', (e) => {
  stageWrap.classList.remove('drop');
  const m = e.dataTransfer.getData('application/x-media');
  if (m) { e.preventDefault(); addMedia([m]); return; }
  if (e.dataTransfer.files.length) { e.preventDefault(); uploadFiles([...e.dataTransfer.files], true); }
});
window.addEventListener('dragover', (e) => { if (e.dataTransfer.types.includes('Files')) e.preventDefault(); });
window.addEventListener('drop', (e) => {
  if (e.dataTransfer.files.length && !e.defaultPrevented) { e.preventDefault(); uploadFiles([...e.dataTransfer.files], false); }
});
$('#fileInput').addEventListener('change', (e) => { uploadFiles([...e.target.files], true); e.target.value = ''; });

const pending = new Map();
async function uploadFiles(files, addToCollage) {
  if (!files.length) return;
  const queue = files.map((f) => ({ f, key: uid() }));
  queue.forEach(({ f, key }) => pending.set(key, f.name));
  renderLibrary();
  const added = [];
  const worker = async () => {
    while (queue.length) {
      const { f, key } = queue.shift();
      const fd = new FormData();
      fd.append('files', f, f.name);
      try {
        const res = await fetch('/api/media', { method: 'POST', body: fd });
        const data = await res.json();
        for (const m of data.items) { library.push(m); mediaById[m.id] = m; added.push(m.id); }
        for (const er of data.errors) toast(`Couldn't read ${er.name}: ${er.error}`, true);
      } catch (err) {
        toast(`Upload failed for ${f.name}: ${err.message}`, true);
      }
      pending.delete(key);
      renderLibrary();
    }
  };
  await Promise.all([worker(), worker()]);
  if (addToCollage && added.length) addMedia(added);
}

async function deleteMedia(id) {
  const used = Object.values(state.tiles).some((t) => t.mediaId === id);
  if (!confirm(used ? 'Remove this file from the library and the collage?' : 'Remove this file from the library?')) return;
  await fetch('/api/media/' + id, { method: 'DELETE' });
  library = library.filter((m) => m.id !== id);
  delete mediaById[id];
  if (used) {
    change(() => {
      for (const t of Object.values(state.tiles)) if (t.mediaId === id) delete state.tiles[t.id];
      relayout();
    });
  }
  renderLibrary();
}

// ------------------------------------------------------------ library panel

function badgesFor(m) {
  const out = [h('span', { class: 'badge' }, m.ext.replace('.', '').toUpperCase())];
  if (m.kind === 'video') {
    out.push(h('span', { class: 'badge' }, fmtTime(m.duration)));
    if ((m.space || '').startsWith('HDR')) out.push(h('span', { class: 'badge hdr' }, 'HDR'));
  }
  if (m.wide && !(m.space || '').startsWith('HDR')) out.push(h('span', { class: 'badge p3' }, 'P3'));
  return out;
}

function renderLibrary() {
  const grid = $('#libGrid');
  grid.innerHTML = '';
  const used = new Set(Object.values(state.tiles).map((t) => t.mediaId));
  for (const m of library) {
    grid.append(h('div', {
      class: 'lib-item' + (used.has(m.id) ? ' used' : ''), draggable: 'true', title: `${m.name}\n${m.width}×${m.height} · ${m.space || ''}`,
      onclick: () => addMedia([m.id]),
      ondragstart: (e) => { e.dataTransfer.setData('application/x-media', m.id); e.dataTransfer.effectAllowed = 'copy'; },
    },
      h('img', { src: m.thumb, loading: 'lazy', draggable: 'false' }),
      h('div', { class: 'badges' }, badgesFor(m)),
      h('button', { class: 'del', title: 'Remove from library', onclick: (e) => { e.stopPropagation(); deleteMedia(m.id); } }, '✕')));
  }
  for (const name of pending.values()) grid.append(h('div', { class: 'lib-item loading' }, 'Processing', h('br'), name));
  if (!library.length && !pending.size) grid.append(h('p', { class: 'hint', style: { gridColumn: '1 / -1' } }, 'No files yet.'));
}

// ------------------------------------------------------------ controls

function slider({ label, min, max, step, value, fmt = (v) => v, onInput, onBegin = begin, onEnd = end }) {
  const out = h('span', {}, fmt(value));
  const input = h('input', { type: 'range', min, max, step, value });
  input.addEventListener('input', () => { onBegin(); onInput(+input.value); out.textContent = fmt(+input.value); });
  input.addEventListener('change', () => onEnd());
  return h('div', { class: 'slider' }, h('div', { class: 'lbl' }, h('span', {}, label), out), input);
}

function colorPicker(get, set, onBegin = begin, onEnd = end) {
  const hex = h('input', { class: 'hex', value: get(), maxlength: 7, spellcheck: 'false' });
  const pick = h('input', { type: 'color', value: get() });
  const sw = h('div', { class: 'swatches' });
  const refresh = () => {
    pick.value = get();
    if (document.activeElement !== hex) hex.value = get();
    sw.querySelectorAll('.swatch').forEach((s) => s.classList.toggle('on', s.dataset.c === get()));
  };
  pick.addEventListener('input', () => { onBegin(); set(pick.value); refresh(); });
  pick.addEventListener('change', () => onEnd());
  hex.addEventListener('input', () => {
    let v = hex.value.trim();
    if (!v.startsWith('#')) v = '#' + v;
    const ok = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(v);
    hex.classList.toggle('bad', !ok);
    if (ok) {
      if (v.length === 4) v = '#' + [...v.slice(1)].map((c) => c + c).join('');
      onBegin(); set(v.toLowerCase()); refresh();
    }
  });
  hex.addEventListener('change', () => { onEnd(); hex.value = get(); hex.classList.remove('bad'); });
  for (const c of SWATCHES) {
    sw.append(h('button', { class: 'swatch', 'data-c': c, style: { background: c }, title: c,
      onclick: () => { onBegin(); set(c); onEnd(); refresh(); } }));
  }
  refresh();
  return h('div', {}, h('div', { class: 'color-row' }, pick, hex, h('span', { class: 'muted' }, 'hex')), sw);
}

// Shared by the panel and the start dialog. ctx: { get, set, begin, end, refresh }
function styleControls(container, ctx) {
  const build = () => {
    const s = ctx.get();
    container.innerHTML = '';
    container.append(h('h3', {}, 'Tile shape'));
    const shapes = h('div', { class: 'shape-opts' });
    for (const [id, label, rad] of [['sharp', 'Sharp', '0'], ['rounded', 'Rounded', '7px'], ['circle', 'Circle', '50%']]) {
      shapes.append(h('button', { class: 'shape-opt' + (s.shape === id ? ' on' : ''), onclick: () => {
        ctx.begin(); ctx.set({ shape: id }); ctx.end(); ctx.refresh(); build();
      } }, h('i', { style: { borderRadius: rad } }), label));
    }
    container.append(shapes);
    if (s.shape === 'rounded') {
      container.append(slider({ label: 'Corner roundness', min: 0, max: 0.2, step: 0.0025, value: s.radius, fmt: pct,
        onBegin: ctx.begin, onEnd: ctx.end, onInput: (v) => { ctx.set({ radius: v }); ctx.refresh(); } }));
    }
    if (s.shape === 'circle') container.append(h('p', { class: 'hint' }, 'Each cell shows the largest circle that fits in it.'));
    container.append(h('h3', { style: { marginTop: '16px' } }, 'Padding'));
    container.append(slider({ label: 'Spacing between tiles', min: 0, max: 0.08, step: 0.001, value: s.gap, fmt: pct,
      onBegin: ctx.begin, onEnd: ctx.end, onInput: (v) => { ctx.set({ gap: v }); ctx.refresh(); } }));
    container.append(slider({ label: 'Outer border', min: 0, max: 0.15, step: 0.001, value: s.margin, fmt: pct,
      onBegin: ctx.begin, onEnd: ctx.end, onInput: (v) => { ctx.set({ margin: v }); ctx.refresh(); } }));
    container.append(h('div', { class: 'slider' }, h('div', { class: 'lbl' }, h('span', {}, 'Padding color'))));
    container.append(colorPicker(() => ctx.get().bg, (c) => { ctx.set({ bg: c }); ctx.refresh(); }, ctx.begin, ctx.end));
  };
  build();
  return build;
}

const panelStyleCtx = {
  get: () => ({ ...state.style, bg: state.canvas.bg }),
  set: (p) => {
    if ('bg' in p) state.canvas.bg = p.bg;
    for (const k of ['shape', 'radius', 'gap', 'margin']) if (k in p) state.style[k] = p[k];
  },
  begin, end, refresh: () => { renderStage(); persist(); },
};
let rebuildStylePanel = () => {};

function setFormat(fmt, aspect) {
  change(() => { state.canvas.format = fmt; state.canvas.aspect = aspect; });
}

function renderFormat() {
  const chips = $('#formatChips');
  chips.innerHTML = '';
  for (const f of L.FORMATS) {
    const s = 14 / Math.max(f.a[0], f.a[1]);
    chips.append(h('button', { class: 'chip' + (state.canvas.format === f.id ? ' on' : ''), title: f.sub, onclick: () => setFormat(f.id, [...f.a]) },
      h('i', { class: 'shape', style: { width: f.a[0] * s + 'px', height: f.a[1] * s + 'px' } }), f.id === 'a4' ? 'A4' : f.id));
  }
  $('#ratioW').value = +state.canvas.aspect[0].toFixed(3);
  $('#ratioH').value = +state.canvas.aspect[1].toFixed(3);
}
const onRatio = () => {
  const w = +$('#ratioW').value, hh = +$('#ratioH').value;
  if (w > 0 && hh > 0) setFormat('custom', [w, hh]);
};
$('#ratioW').addEventListener('change', onRatio);
$('#ratioH').addEventListener('change', onRatio);
$('#btnSwapRatio').addEventListener('click', () => {
  const [w, hh] = state.canvas.aspect;
  const f = L.FORMATS.find((x) => x.a[0] === hh && x.a[1] === w);
  setFormat(f ? f.id : 'custom', [hh, w]);
});
$('#btnFitCanvas').addEventListener('click', () => {
  if (!state.tree) { toast('Fit canvas works on the grid layout'); return; }
  change(() => {
    const natural = L.fitSizes(state.tree, aspectOf);
    state.canvas.aspect = [+natural.toFixed(4), 1];
    state.canvas.format = 'custom';
  });
});

$('#modeSeg').addEventListener('click', (e) => {
  const b = e.target.closest('button');
  if (b) setMode(b.dataset.mode);
});
$('#btnAuto').addEventListener('click', () => {
  if (!dockedIds().length) return;
  change(() => { state.tree = L.autoLayout(dockedIds(), aspectOf, canvasAspect(), state.keepOrder); });
});
$('#btnShuffle').addEventListener('click', () => {
  if (dockedIds().length < 2) return;
  change(() => { state.tree = L.relabel(state.tree, L.shuffle(dockedIds())); });
});
$('#btnBalance').addEventListener('click', () => {
  if (!state.tree) return;
  change(() => L.fitSizes(state.tree, aspectOf));
});
$('#keepOrder').addEventListener('change', (e) => change(() => { state.keepOrder = e.target.checked; }));

let tplKey = '';
function renderTemplates() {
  const ids = dockedIds();
  const key = ids.map((i) => state.tiles[i].mediaId).join() + '|' + state.canvas.aspect.join(':');
  const box = $('#templates');
  const isFree = tileCount() > 0 && !ids.length;
  $('#modeSeg').querySelectorAll('button').forEach((b) => b.classList.toggle('on', (b.dataset.mode === 'free') === isFree));
  if (key === tplKey) return;
  tplKey = key;
  box.innerHTML = '';
  if (!ids.length) {
    box.append(h('p', { class: 'hint', style: { gridColumn: '1 / -1' } }, isFree ? 'Freeform mode — switch to Grid for templates.' : 'Add media to see layouts.'));
    return;
  }
  const ranked = L.rankTemplates(ids, aspectOf, canvasAspect()).slice(0, 28);
  const [aw, ah] = state.canvas.aspect;
  const s = 50 / Math.max(aw, ah);
  const W = aw * s, H = ah * s;
  ranked.forEach(({ tpl }, i) => {
    const tree = L.relabel(tpl, ids);
    const fake = { tree, tiles: Object.fromEntries(ids.map((id) => [id, { id }])), style: { gap: 0.04, margin: 0.04, shape: 'sharp', radius: 0 } };
    const lay = L.computeLayout(fake, W, H);
    const mini = h('div', { class: 'mini', style: { width: W + 'px', height: H + 'px' } },
      lay.items.map((it) => h('div', { style: { left: it.rect.x + 'px', top: it.rect.y + 'px', width: it.rect.w + 'px', height: it.rect.h + 'px' } })));
    box.append(h('div', { class: 'tpl', title: 'Apply layout', onclick: () => change(() => { state.tree = L.relabel(tpl, dockedIds()); }) },
      mini, i === 0 ? h('span', { class: 'best' }, 'BEST') : null));
  });
}

function renderInspector() {
  const box = $('#inspector');
  box.innerHTML = '';
  box.append(h('h3', {}, 'Selected tile'));
  const t = state.tiles[selected];
  const m = mediaOf(t);
  if (!t || !m) {
    box.append(h('p', { class: 'insp-empty' }, 'Click a tile to adjust it. Drag inside a tile to reposition the photo, scroll / pinch to zoom, drag ⠿ onto another tile to swap.'));
    return;
  }
  box.append(h('div', { class: 'kv' }, 'File', h('b', { title: m.name }, m.name)));
  box.append(h('div', { class: 'kv' }, 'Size', h('b', {}, `${m.width} × ${m.height}`)));
  box.append(h('div', { class: 'kv' }, 'Color', h('b', {}, m.space || '—')));
  if (m.gainmap) box.append(h('p', { class: 'hint' }, 'This HEIC has an HDR gain map. Its Display P3 color is kept; the extra HDR brightness boost is not carried into the export.'));

  const fit = h('div', { class: 'seg', style: { marginTop: '10px' } });
  for (const [id, label] of [['cover', 'Fill cell'], ['contain', 'Show whole']]) {
    fit.append(h('button', { class: t.fit === id ? 'on' : '', onclick: () => change(() => { t.fit = id; t.panX = 0; t.panY = 0; }) }, label));
  }
  box.append(fit);
  box.append(slider({ label: 'Zoom', min: 0.3, max: 5, step: 0.01, value: t.zoom, fmt: (v) => `${Math.round(v * 100)}%`,
    onInput: (v) => { t.zoom = v; renderStage(); } }));
  box.append(h('div', { class: 'row' },
    h('button', { class: 'ghost small grow', onclick: () => change(() => Object.assign(t, { zoom: 1, panX: 0, panY: 0 })) }, 'Reset crop'),
    h('button', { class: 'ghost small grow', onclick: () => (t.floating ? dockTile(t.id) : floatTile(t.id)) }, t.floating ? 'Dock into grid' : 'Float freely')));
  if (t.floating) {
    box.append(slider({ label: 'Rotation', min: -180, max: 180, step: 0.5, value: t.rotation || 0, fmt: (v) => `${v}°`,
      onInput: (v) => { t.rotation = v; renderStage(); } }));
  }
  if (m.kind === 'video') {
    box.append(slider({ label: 'Start at', min: 0, max: Math.max(0, m.duration - 0.1), step: 0.05, value: t.start, fmt: (v) => `${v.toFixed(2)}s`,
      onInput: (v) => { t.start = v; syncAllVideos(); renderTransport(); } }));
    if (m.hasAudio) {
      box.append(h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: t.audio, onchange: (e) => change(() => { t.audio = e.target.checked; }) }), 'Include this clip\'s audio in video export'));
    }
  }
  box.append(h('div', { class: 'row' }, h('button', { class: 'ghost small grow', onclick: () => removeTile(t.id) }, 'Remove from collage')));
}

// ------------------------------------------------------------ video transport

function videoTiles() {
  return Object.values(state.tiles).filter((t) => mediaOf(t)?.kind === 'video');
}
function timelineDuration() {
  const lens = videoTiles().map((t) => Math.max(0.1, mediaOf(t).duration - t.start));
  return lens.length ? Math.max(...lens) : 0;
}
function syncVideo(v) {
  const el = v.closest('.tile');
  const t = el && state.tiles[el.dataset.id];
  const m = mediaOf(t);
  if (!t || !m || !v.duration) return;
  const target = L.clipTime(v.duration, Math.min(t.start, v.duration - 0.05), transport.t);
  if (Math.abs(v.currentTime - target) > 0.08) v.currentTime = target;
  if (transport.playing) v.play().catch(() => {}); else v.pause();
}
function syncAllVideos() { stage.querySelectorAll('video').forEach(syncVideo); }
function tick() {
  if (!transport.playing) return;
  const D = timelineDuration();
  transport.t = transport.base + (performance.now() - transport.perf) / 1000;
  if (transport.t >= D) { transport.base = 0; transport.perf = performance.now(); transport.t = 0; syncAllVideos(); }
  renderTransport();
  transport.raf = requestAnimationFrame(tick);
}
function setPlaying(p) {
  transport.playing = p;
  if (p) { transport.base = transport.t; transport.perf = performance.now(); tick(); }
  syncAllVideos();
  renderTransport();
}
$('#btnPlay').addEventListener('click', () => setPlaying(!transport.playing));
$('#scrub').addEventListener('input', (e) => {
  transport.playing = false;
  transport.t = (+e.target.value / 1000) * timelineDuration();
  syncAllVideos();
  renderTransport();
});
function renderTransport() {
  const D = timelineDuration();
  $('#transport').classList.toggle('hidden', !D);
  if (!D) return;
  if (transport.t > D) transport.t = 0;
  $('#btnPlay').textContent = transport.playing ? '❚❚' : '▶';
  $('#scrub').value = Math.round((transport.t / D) * 1000);
  $('#timeLabel').textContent = `${fmtTime(transport.t)} / ${fmtTime(D)}`;
}

// ------------------------------------------------------------ modals

function modal(content, { closable = true } = {}) {
  const back = h('div', { class: 'modal-back' }, content);
  if (closable) back.addEventListener('pointerdown', (e) => { if (e.target === back) close(); });
  const onKey = (e) => { if (e.key === 'Escape' && closable) close(); };
  const close = () => { back.remove(); document.removeEventListener('keydown', onKey); };
  document.addEventListener('keydown', onKey);
  $('#modalRoot').append(back);
  return close;
}

function startDialog({ fresh, first = false }) {
  const base = state || defaults();
  const draft = { format: base.canvas.format, aspect: [...base.canvas.aspect], bg: base.canvas.bg, ...base.style };
  const preview = h('div', { class: 'pv' });
  const drawPreview = () => {
    const [aw, ah] = draft.aspect;
    const s = 220 / Math.max(aw, ah);
    const W = aw * s, H = ah * s;
    const ids = ['a', 'b', 'c', 'd'];
    const fake = {
      tree: { t: 'split', dir: 'col', sizes: [1.2, 1], children: [{ t: 'leaf', id: 'a' }, { t: 'split', dir: 'row', sizes: [1, 1, 1], children: ['b', 'c', 'd'].map((id) => ({ t: 'leaf', id })) }] },
      tiles: Object.fromEntries(ids.map((id) => [id, { id }])), style: draft,
    };
    const lay = L.computeLayout(fake, W, H);
    Object.assign(preview.style, { width: W + 'px', height: H + 'px', background: draft.bg });
    preview.innerHTML = '';
    lay.items.forEach((it) => preview.append(h('div', { style: { left: it.rect.x + 'px', top: it.rect.y + 'px', width: it.rect.w + 'px', height: it.rect.h + 'px', borderRadius: it.radius + 'px' } })));
  };

  const formats = h('div', { class: 'format-grid' });
  const cw = h('input', { type: 'number', min: 0.1, step: 0.1, value: draft.aspect[0], style: { width: '60px' } });
  const ch = h('input', { type: 'number', min: 0.1, step: 0.1, value: draft.aspect[1], style: { width: '60px' } });
  const drawFormats = () => {
    formats.innerHTML = '';
    for (const f of L.FORMATS) {
      const s = 34 / Math.max(f.a[0], f.a[1]);
      formats.append(h('button', { class: 'fmt' + (draft.format === f.id ? ' on' : ''), onclick: () => {
        draft.format = f.id; draft.aspect = [...f.a]; cw.value = f.a[0]; ch.value = f.a[1]; drawFormats(); drawPreview();
      } }, h('i', { class: 'box', style: { width: f.a[0] * s + 'px', height: f.a[1] * s + 'px' } }), h('b', {}, f.label), h('small', {}, f.sub)));
    }
  };
  const onCustom = () => {
    if (+cw.value > 0 && +ch.value > 0) { draft.format = 'custom'; draft.aspect = [+cw.value, +ch.value]; drawFormats(); drawPreview(); }
  };
  cw.addEventListener('input', onCustom);
  ch.addEventListener('input', onCustom);

  const styleBox = h('div');
  const noop = () => {};
  styleControls(styleBox, { get: () => draft, set: (p) => Object.assign(draft, p), begin: noop, end: noop, refresh: drawPreview });

  let close;
  const apply = () => {
    change(() => {
      if (!state || fresh) {
        state = { ...defaults(), keepOrder: state?.keepOrder || false };
        selected = null;
      }
      state.canvas = { format: draft.format, aspect: draft.aspect, bg: draft.bg };
      state.style = { shape: draft.shape, radius: draft.radius, gap: draft.gap, margin: draft.margin };
    });
    if (first) undoStack = [];
    updateUndo();
    rebuildStylePanel();
    close();
  };

  drawFormats();
  drawPreview();
  const hasTiles = state && tileCount() > 0;
  close = modal(h('div', { class: 'modal' },
    h('h1', {}, first ? 'Start a collage' : fresh ? 'New collage' : 'Collage format & style'),
    h('p', { class: 'sub' }, 'Pick a shape and look. Everything here can be changed later from the right-hand panel.'),
    h('div', { class: 'modal-cols' },
      h('div', {},
        h('h3', {}, 'Format'), formats,
        h('div', { class: 'row', style: { marginTop: '12px' } }, h('span', { class: 'muted' }, 'Custom ratio'), cw, ':', ch,
          h('button', { class: 'ghost small', title: 'Swap portrait / landscape', onclick: () => { [cw.value, ch.value] = [ch.value, cw.value]; onCustom(); } }, '⇄ Rotate'))),
      h('div', {}, h('div', { class: 'preview-box' }, preview), styleBox)),
    h('div', { class: 'modal-foot' },
      fresh && hasTiles ? h('span', { class: 'muted', style: { marginRight: 'auto', alignSelf: 'center' } }, 'Clears the current collage (your library stays). Undo brings it back.') : null,
      first ? null : h('button', { class: 'ghost', onclick: () => close() }, 'Cancel'),
      h('button', { class: 'primary', onclick: apply }, first || fresh ? 'Create collage' : 'Apply'))),
  { closable: !first });
}

const STILLS = [
  ['jpeg-p3', 'JPEG · Display P3', 'Keeps the iPhone "pop", opens almost everywhere. Recommended.'],
  ['heic-p3', 'HEIC · Display P3', 'Apple format, smallest files, full wide color.'],
  ['png-p3', 'PNG · Display P3', 'Lossless, wide color.'],
  ['tiff-p3', 'TIFF · Display P3', 'Lossless, for print / editing.'],
  ['jpeg-srgb', 'JPEG · sRGB', 'Maximum compatibility (colors outside sRGB are clipped).'],
  ['png-srgb', 'PNG · sRGB', 'Lossless, maximum compatibility.'],
];
const VIDEOS = [
  ['mp4-h264', 'MP4 · H.264 (sRGB)', 'Plays everywhere: Instagram, WhatsApp, web.'],
  ['mov-hevc-p3', 'MOV · HEVC 10-bit · Display P3', 'Wide color video for Apple devices.'],
];

function exportCanvasSize(longEdge) {
  const [aw, ah] = state.canvas.aspect;
  const even = (v) => Math.max(2, Math.round(v / 2) * 2);
  return aw >= ah ? [even(longEdge), even((longEdge * ah) / aw)] : [even((longEdge * aw) / ah), even(longEdge)];
}

function buildPayload() {
  const [aw, ah] = state.canvas.aspect;
  const k = 1000 / Math.max(aw, ah);
  const W = aw * k, H = ah * k;
  const lay = L.computeLayout(state, W, H);
  return {
    canvas: { aspect: [aw, ah], bg: state.canvas.bg },
    time: transport.t,
    tiles: lay.items.filter((it) => mediaOf(it.tile)).map(({ tile: t, rect, radius }) => ({
      mediaId: t.mediaId, x: rect.x / W, y: rect.y / H, w: rect.w / W, h: rect.h / H,
      radius: radius / Math.min(W, H), rotation: t.floating ? t.rotation || 0 : 0,
      fit: t.fit, zoom: t.zoom, panX: t.panX, panY: t.panY, start: t.start, audio: t.audio,
    })),
  };
}

function exportDialog() {
  if (!tileCount()) { toast('Add some photos first'); return; }
  setPlaying(false);
  const hasVideo = videoTiles().length > 0;
  const opt = { format: hasVideo ? 'mp4-h264' : 'jpeg-p3', still: 4096, video: 1920, quality: 92, fps: 30, durationMode: 'longest', custom: 10, audio: true, crf: 18 };
  const body = h('div');
  const isVideo = () => opt.format.startsWith('mp4') || opt.format.startsWith('mov');

  const draw = () => {
    body.innerHTML = '';
    const list = (items) => h('div', { class: 'fmt-list' }, items.map(([id, label, sub]) =>
      h('label', { class: 'fmt-opt' + (opt.format === id ? ' on' : '') },
        h('input', { type: 'radio', name: 'fmt', checked: opt.format === id, onchange: () => { opt.format = id; draw(); } }),
        h('div', {}, h('b', {}, label), h('small', {}, sub)))));
    const left = h('div', {});
    if (hasVideo) left.append(h('h3', {}, 'Video (clips play)'), list(VIDEOS), h('h3', { style: { marginTop: '16px' } }, 'Still image'),
      h('p', { class: 'hint' }, `Videos are captured at the playhead (${fmtTime(transport.t)}).`));
    else left.append(h('h3', {}, 'Image format'));
    left.append(list(STILLS));

    const right = h('div', {});
    const sizes = isVideo() ? [1080, 1280, 1920, 2560, 3840] : [2048, 3072, 4096, 6000, 8192];
    const cur = isVideo() ? opt.video : opt.still;
    const [W, H] = exportCanvasSize(cur);
    right.append(h('div', { class: 'field' }, h('label', {}, `Size — ${W} × ${H} px`),
      h('select', { onchange: (e) => { opt[isVideo() ? 'video' : 'still'] = +e.target.value; draw(); } },
        sizes.map((s) => h('option', { value: s, selected: s === cur }, `${s}px long edge`)))));
    if (!isVideo() && !opt.format.startsWith('png') && !opt.format.startsWith('tiff')) {
      right.append(slider({ label: 'Quality', min: 60, max: 100, step: 1, value: opt.quality, onBegin: () => {}, onEnd: () => {}, onInput: (v) => { opt.quality = v; } }));
    }
    if (isVideo()) {
      right.append(h('div', { class: 'field' }, h('label', {}, 'Length'),
        h('select', { onchange: (e) => { opt.durationMode = e.target.value; draw(); } },
          [['longest', `Longest clip (${fmtTime(timelineDuration())}) — shorter clips loop`], ['shortest', 'Shortest clip'], ['custom', 'Custom seconds']]
            .map(([v, l]) => h('option', { value: v, selected: opt.durationMode === v }, l)))));
      if (opt.durationMode === 'custom') {
        right.append(h('div', { class: 'field' }, h('label', {}, 'Seconds'),
          h('input', { type: 'number', min: 1, max: 600, value: opt.custom, onchange: (e) => { opt.custom = +e.target.value; } })));
      }
      right.append(h('div', { class: 'field' }, h('label', {}, 'Frame rate'),
        h('select', { onchange: (e) => { opt.fps = +e.target.value; } }, [24, 25, 30, 60].map((f) => h('option', { value: f, selected: f === opt.fps }, `${f} fps`)))));
      right.append(h('div', { class: 'field' }, h('label', {}, 'Quality'),
        h('select', { onchange: (e) => { opt.crf = +e.target.value; } },
          [[16, 'Highest (big file)'], [18, 'High'], [22, 'Balanced'], [26, 'Small file']].map(([v, l]) => h('option', { value: v, selected: v === opt.crf }, l)))));
      right.append(h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: opt.audio, onchange: (e) => { opt.audio = e.target.checked; } }), 'Include audio (mixes clips that have it switched on)'));
    }
    right.append(status);
    body.append(h('div', { class: 'modal-cols' }, left, right));
  };

  const status = h('div');
  const go = h('button', { class: 'primary', onclick: async () => {
    go.disabled = true;
    status.innerHTML = '';
    const bar = h('div', { class: 'progress' }, h('div'));
    const label = h('div', { class: 'hint' }, 'Rendering…');
    status.append(bar, label);
    const req = {
      format: opt.format, collage: buildPayload(), longEdge: isVideo() ? opt.video : opt.still, quality: opt.quality,
      video: { fps: opt.fps, durationMode: opt.durationMode, customDuration: opt.custom, audio: opt.audio, crf: opt.crf },
    };
    try {
      const { id } = await (await fetch('/api/export', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(req) })).json();
      for (;;) {
        await new Promise((r) => setTimeout(r, 400));
        const job = await (await fetch('/api/export/' + id)).json();
        bar.firstChild.style.width = Math.round(job.progress * 100) + '%';
        label.textContent = `Rendering… ${Math.round(job.progress * 100)}%`;
        if (job.status === 'done') {
          status.innerHTML = '';
          const mb = (job.size / 1048576).toFixed(1);
          status.append(h('div', { class: 'done-box' },
            h('b', {}, `Done — ${job.width}×${job.height}, ${mb} MB, ${job.elapsed.toFixed(1)}s`),
            h('div', { class: 'row' },
              h('a', { class: 'btn small', href: `/exports/${encodeURIComponent(job.file)}?dl=1`, download: job.file }, 'Download'),
              h('a', { class: 'btn small', href: `/exports/${encodeURIComponent(job.file)}`, target: '_blank' }, 'Open')),
            h('p', { class: 'hint' }, 'Also saved to:'), h('code', {}, job.path)));
          break;
        }
        if (job.status === 'error') {
          status.innerHTML = '';
          status.append(h('div', { class: 'err-box' }, job.error));
          break;
        }
      }
    } catch (err) {
      status.append(h('div', { class: 'err-box' }, String(err)));
    }
    go.disabled = false;
  } }, 'Export');

  draw();
  modal(h('div', { class: 'modal' }, h('h1', {}, 'Export'),
    h('p', { class: 'sub' }, 'Rendered at full resolution on this Mac from the original files — HEIC stays HEIC until the final encode.'),
    body, h('div', { class: 'modal-foot' }, go)));
}

// ------------------------------------------------------------ wiring

function render() {
  renderFormat();
  renderStage();
  renderTemplates();
  renderInspector();
  renderLibrary();
  renderTransport();
  $('#keepOrder').checked = state.keepOrder;
  rebuildStylePanelIfStale();
}
let styleSig = '';
function rebuildStylePanelIfStale() {
  const sig = JSON.stringify([state.style, state.canvas.bg]);
  if (sig !== styleSig && liveBefore === null) { styleSig = sig; rebuildStylePanel(); }
}

$('#btnUndo').addEventListener('click', undo);
$('#btnRedo').addEventListener('click', redo);
$('#btnNew').addEventListener('click', () => startDialog({ fresh: true }));
$('#btnExport').addEventListener('click', exportDialog);
window.addEventListener('resize', () => renderStage());

document.addEventListener('keydown', (e) => {
  if (e.target.closest('input, select, textarea') || $('#modalRoot').children.length) return;
  const mod = e.metaKey || e.ctrlKey;
  if (mod && e.key.toLowerCase() === 'z') { e.preventDefault(); e.shiftKey ? redo() : undo(); return; }
  if (mod && e.key.toLowerCase() === 'y') { e.preventDefault(); redo(); return; }
  if ((e.key === 'Delete' || e.key === 'Backspace') && selected) { e.preventDefault(); removeTile(selected); return; }
  if (e.key === 'Escape') { selected = null; renderStage(); renderInspector(); return; }
  if (e.key === ' ' && videoTiles().length) { e.preventDefault(); setPlaying(!transport.playing); return; }
  const t = state.tiles[selected];
  if (t?.floating && e.key.startsWith('Arrow')) {
    e.preventDefault();
    const step = (e.shiftKey ? 10 : 1) / lastLayout.W;
    change(() => {
      if (e.key === 'ArrowLeft') t.rect.x -= step;
      if (e.key === 'ArrowRight') t.rect.x += step;
      if (e.key === 'ArrowUp') t.rect.y -= step * lastLayout.W / lastLayout.H;
      if (e.key === 'ArrowDown') t.rect.y += step * lastLayout.W / lastLayout.H;
    });
  }
});

async function init() {
  try {
    library = await (await fetch('/api/media')).json();
  } catch {
    toast('Could not reach the local server', true);
  }
  for (const m of library) mediaById[m.id] = m;
  const first = !state;
  if (first) state = defaults();
  rebuildStylePanel = styleControls($('#styleGroup'), panelStyleCtx);
  for (const t of Object.values(state.tiles)) if (!mediaById[t.mediaId]) delete state.tiles[t.id];
  if (state.tree) {
    const keep = L.leaves(state.tree).filter((id) => state.tiles[id]);
    if (keep.length !== L.leaves(state.tree).length) relayout();
  }
  updateUndo();
  render();
  if (first) startDialog({ fresh: true, first: true });
}
init();
