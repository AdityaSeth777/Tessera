// Layout engine. A docked layout is a guillotine tree:
//   leaf:  { t: 'leaf', id }
//   split: { t: 'split', dir: 'row' | 'col', sizes: [...], children: [...] }
// 'row' places children side by side, 'col' stacks them.

export const FORMATS = [
  { id: '1:1', label: 'Square', sub: '1:1 · Instagram post', a: [1, 1] },
  { id: '4:5', label: 'Portrait', sub: '4:5 · Instagram post', a: [4, 5] },
  { id: '9:16', label: 'Story / Reel', sub: '9:16 · Stories, TikTok', a: [9, 16] },
  { id: '1.91:1', label: 'Landscape', sub: '1.91:1 · Instagram', a: [1.91, 1] },
  { id: '16:9', label: 'Widescreen', sub: '16:9 · YouTube, TV', a: [16, 9] },
  { id: '3:4', label: 'Photo portrait', sub: '3:4 · iPhone photo', a: [3, 4] },
  { id: '4:3', label: 'Photo landscape', sub: '4:3 · iPhone photo', a: [4, 3] },
  { id: '2:3', label: 'Print portrait', sub: '2:3 · 4×6 print', a: [2, 3] },
  { id: '3:2', label: 'Print landscape', sub: '3:2 · 6×4 print', a: [3, 2] },
  { id: 'a4', label: 'A4 / A-series', sub: '1:1.414 · paper', a: [1, 1.4142] },
];

export const MAX_TILES = 10;

const leaf = (id) => ({ t: 'leaf', id });
const split = (dir, children) =>
  children.length === 1 ? children[0] : { t: 'split', dir, sizes: children.map(() => 1), children };
const other = (dir) => (dir === 'row' ? 'col' : 'row');

export function leaves(node, out = []) {
  if (!node) return out;
  if (node.t === 'leaf') out.push(node.id);
  else node.children.forEach((c) => leaves(c, out));
  return out;
}

export function clone(node) {
  return node ? JSON.parse(JSON.stringify(node)) : null;
}

export function removeLeaf(node, id) {
  if (!node) return null;
  if (node.t === 'leaf') return node.id === id ? null : node;
  const kids = [], sizes = [];
  node.children.forEach((c, i) => {
    const r = removeLeaf(c, id);
    if (r) { kids.push(r); sizes.push(node.sizes[i]); }
  });
  if (!kids.length) return null;
  if (kids.length === 1) return kids[0];
  return { ...node, children: kids, sizes };
}

export function relabel(node, ids) {
  let k = 0;
  const walk = (n) => (n.t === 'leaf' ? leaf(ids[k++]) : { ...n, sizes: [...n.sizes], children: n.children.map(walk) });
  return walk(node);
}

// ------------------------------------------------------------ templates

function compositions(n, maxPart, maxParts) {
  const out = [];
  const rec = (left, acc) => {
    if (left === 0) { out.push([...acc]); return; }
    if (acc.length >= maxParts) return;
    for (let p = 1; p <= Math.min(maxPart, left); p++) rec(left - p, [...acc, p]);
  };
  rec(n, []);
  return out;
}

function groups(comp, outer, start = 0) {
  let k = start;
  return split(outer, comp.map((c) => split(other(outer), Array.from({ length: c }, () => leaf(k++)))));
}

function sideComp(m) {
  if (m <= 3) return Array(m).fill(1);
  const out = [];
  for (let left = m; left > 0; left -= 2) out.push(Math.min(2, left));
  return out;
}

function balancedComp(m) {
  return ({ 1: [1], 2: [2], 3: [3], 4: [2, 2], 5: [2, 3], 6: [3, 3], 7: [3, 4], 8: [4, 4], 9: [3, 3, 3] })[m] || sideComp(m);
}

function heroes(n) {
  if (n < 3) return [];
  const m = n - 1, out = [];
  // hero on the left / right with the rest stacked beside it
  const side = (start) => groups(sideComp(m), 'col', start);
  out.push({ ...split('row', [leaf(0), side(1)]), sizes: [1.3, 1] });
  out.push({ ...split('row', [side(0), leaf(m)]), sizes: [1, 1.3] });
  // hero on top / bottom with the rest in a row (or rows) underneath
  const strip = (start) => groups(m <= 4 ? Array(m).fill(1) : sideComp(m), 'row', start);
  out.push({ ...split('col', [leaf(0), strip(1)]), sizes: [1.3, 1] });
  out.push({ ...split('col', [strip(0), leaf(m)]), sizes: [1, 1.3] });
  if (m >= 4) {
    out.push({ ...split('row', [leaf(0), groups(balancedComp(m), 'col', 1)]), sizes: [1, 1] });
    out.push({ ...split('col', [leaf(0), groups(balancedComp(m), 'row', 1)]), sizes: [1, 1] });
  }
  return out;
}

const templateCache = new Map();

export function templates(n) {
  if (n <= 0) return [];
  if (templateCache.has(n)) return templateCache.get(n);
  const list = [];
  if (n === 1) list.push(leaf(0));
  else {
    const maxPart = n <= 5 ? n : 4;
    for (const comp of compositions(n, maxPart, 4)) {
      list.push(groups(comp, 'col'));
      list.push(groups(comp, 'row'));
    }
    list.push(...heroes(n));
  }
  const seen = new Set();
  const uniq = list.filter((t) => {
    const k = JSON.stringify(t);
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
  templateCache.set(n, uniq);
  return uniq;
}

// ------------------------------------------------------------ auto fit

// Sets split sizes so every leaf keeps its media aspect (a justified layout);
// returns the natural aspect of the whole tree.
export function fitSizes(node, aspectOf) {
  if (node.t === 'leaf') return aspectOf(node.id);
  const a = node.children.map((c) => fitSizes(c, aspectOf));
  if (node.dir === 'row') {
    node.sizes = a;
    return a.reduce((s, x) => s + x, 0);
  }
  node.sizes = a.map((x) => 1 / x);
  return 1 / node.sizes.reduce((s, x) => s + x, 0);
}

function leafAreas(node, area = 1, out = []) {
  if (node.t === 'leaf') { out.push(area); return out; }
  const total = node.sizes.reduce((s, x) => s + x, 0);
  node.children.forEach((c, i) => leafAreas(c, (area * node.sizes[i]) / total, out));
  return out;
}

function score(tree, natural, canvasAspect) {
  const logs = leafAreas(tree).map(Math.log);
  const mean = logs.reduce((s, x) => s + x, 0) / logs.length;
  const sd = Math.sqrt(logs.reduce((s, x) => s + (x - mean) ** 2, 0) / logs.length);
  return Math.abs(Math.log(natural / canvasAspect)) * 1.6 + sd * 0.45;
}

function permutations(arr) {
  if (arr.length <= 1) return [arr];
  const out = [];
  arr.forEach((x, i) => {
    for (const p of permutations([...arr.slice(0, i), ...arr.slice(i + 1)])) out.push([x, ...p]);
  });
  return out;
}

function shuffled(arr) {
  const a = [...arr];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

export function shuffle(arr) { return shuffled(arr); }

export function evaluate(tpl, ids, aspectOf, canvasAspect) {
  const tree = relabel(tpl, ids);
  const natural = fitSizes(tree, aspectOf);
  return { tree, cost: score(tree, natural, canvasAspect) };
}

export function autoLayout(ids, aspectOf, canvasAspect, keepOrder) {
  if (!ids.length) return null;
  let orders = [ids];
  if (!keepOrder && ids.length > 1) {
    if (ids.length <= 6) orders = permutations(ids);
    else {
      const byAspect = [...ids].sort((a, b) => aspectOf(a) - aspectOf(b));
      orders = [ids, byAspect, [...byAspect].reverse()];
      for (let i = 0; i < 150; i++) orders.push(shuffled(ids));
    }
  }
  let best = null;
  for (const tpl of templates(ids.length)) {
    for (const order of orders) {
      const r = evaluate(tpl, order, aspectOf, canvasAspect);
      if (!best || r.cost < best.cost) best = r;
    }
  }
  return best.tree;
}

export function rankTemplates(ids, aspectOf, canvasAspect) {
  return templates(ids.length)
    .map((tpl) => ({ tpl, cost: evaluate(tpl, ids, aspectOf, canvasAspect).cost }))
    .sort((a, b) => a.cost - b.cost);
}

// ------------------------------------------------------------ geometry

export function placement(tw, th, mw, mh, fit, zoom, panX, panY) {
  const base = fit === 'cover' ? Math.max(tw / mw, th / mh) : Math.min(tw / mw, th / mh);
  const s = base * zoom;
  const dw = mw * s, dh = mh * s;
  const maxX = Math.abs(dw - tw) / 2, maxY = Math.abs(dh - th) / 2;
  const cl = (v) => Math.max(-1, Math.min(1, v));
  return { left: (tw - dw) / 2 + cl(panX) * maxX, top: (th - dh) / 2 + cl(panY) * maxY, dw, dh, maxX, maxY };
}

function layoutTree(node, r, gap, rects, dividers) {
  if (node.t === 'leaf') { rects[node.id] = r; return; }
  const k = node.children.length;
  const horiz = node.dir === 'row';
  const span = horiz ? r.w : r.h;
  const avail = Math.max(1e-6, span - gap * (k - 1));
  const total = node.sizes.reduce((s, x) => s + x, 0);
  let pos = horiz ? r.x : r.y;
  node.children.forEach((c, i) => {
    const len = (avail * node.sizes[i]) / total;
    const cr = horiz ? { x: pos, y: r.y, w: len, h: r.h } : { x: r.x, y: pos, w: r.w, h: len };
    layoutTree(c, cr, gap, rects, dividers);
    pos += len;
    if (i < k - 1) {
      dividers.push({ node, i, dir: node.dir, avail, total, at: pos + gap / 2, rect: r });
      pos += gap;
    }
  });
}

// Resolves every tile to its final rect (after shape) in a W×H canvas.
export function computeLayout(state, W, H) {
  const S = Math.min(W, H);
  const { gap, margin, shape, radius } = state.style;
  const rects = {}, dividers = [];
  const m = margin * S;
  if (state.tree) layoutTree(state.tree, { x: m, y: m, w: W - 2 * m, h: H - 2 * m }, gap * S, rects, dividers);

  const docked = leaves(state.tree);
  const floating = Object.values(state.tiles).filter((t) => t.floating).sort((a, b) => a.z - b.z);
  const out = [];
  const push = (tile, r, cell) => {
    let rect = r, rad = 0;
    if (shape === 'circle') {
      const side = Math.min(r.w, r.h);
      rect = { x: r.x + (r.w - side) / 2, y: r.y + (r.h - side) / 2, w: side, h: side };
      rad = side / 2;
    } else if (shape === 'rounded') {
      rad = Math.min(radius * S, Math.min(r.w, r.h) / 2);
    }
    out.push({ tile, rect, radius: rad, cell });
  };
  docked.forEach((id) => state.tiles[id] && rects[id] && push(state.tiles[id], rects[id], rects[id]));
  floating.forEach((t) => {
    const r = { x: t.rect.x * W, y: t.rect.y * H, w: t.rect.w * W, h: t.rect.h * H };
    push(t, r, r);
  });
  return { items: out, dividers, rects };
}

// A clip plays start..end and then loops back to start (matches the exporter).
export function clipTime(duration, start, t) {
  const seg = duration - start;
  return seg > 0 ? start + (t % seg) : Math.max(0, start);
}
