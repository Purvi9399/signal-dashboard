/* Deterministic layout for the COP–metric network.
 *
 * Positions only; no semantics. Metrics sit in one column, grouped by their id
 * prefix (the category the export gives). COPs sit in columns by dependency depth
 * in the executed case graph (cop.consumes, from cops/specs.py CONSUMES). The
 * columns are a drawing convenience, not a COP hierarchy: the documented hierarchy
 * is shown separately and only from the export's hierarchy contract.
 */

export const G = { metricX: 16, metricW: 200, rowH: 22, catGap: 14, top: 36,
  copX0: 290, colW: 140, copW: 122, copH: 46, copGap: 14 };

export function depths(cops) {
  const by = Object.fromEntries(cops.map((c) => [c.id, c]));
  const d = {};
  const visit = (id, seen = new Set()) => {
    if (d[id] != null) return d[id];
    if (seen.has(id)) return 0;
    seen.add(id);
    const up = (by[id] && by[id].consumes) || [];
    d[id] = up.length ? 1 + Math.max(...up.map((u) => visit(u, seen))) : 0;
    return d[id];
  };
  cops.forEach((c) => visit(c.id));
  return d;
}

export function layout(net) {
  const pos = {};
  let y = G.top;
  let cat = null;
  const metrics = [...net.metrics].sort((a, b) => a.category.localeCompare(b.category) || a.id.localeCompare(b.id));
  const cats = [];
  metrics.forEach((m) => {
    if (m.category !== cat) {
      if (cat !== null) y += G.catGap;
      cat = m.category;
      cats.push({ name: cat, y: y });
      y += G.rowH;
    }
    pos["m:" + m.id] = { x: G.metricX, y, w: G.metricW, h: G.rowH - 4 };
    y += G.rowH;
  });
  const mH = y;
  const d = depths(net.cops);
  const cols = {};
  [...net.cops].sort((a, b) => a.name.localeCompare(b.name)).forEach((c) => {
    (cols[d[c.id]] ||= []).push(c.id);
  });
  let cH = 0;
  const span = Math.max(mH - G.top - 30, 4 * (G.copH + G.copGap));
  Object.entries(cols).forEach(([k, ids]) => {
    const slot = span / ids.length;
    ids.forEach((id, i) => {
      const yy = Math.round(G.top + 30 + (i + 0.5) * slot - G.copH / 2);
      pos["c:" + id] = { x: G.copX0 + Number(k) * G.colW, y: yy, w: G.copW, h: G.copH };
      cH = Math.max(cH, yy + G.copH);
    });
  });
  const maxDepth = Math.max(...Object.values(d));
  return { pos, cats, depth: d, maxDepth, width: G.copX0 + (maxDepth + 1) * G.colW + 20,
    height: Math.max(mH, cH) + 30 };
}

/* Split a COP name over at most two lines for the node box. */
export function nameLines(name, max = 18) {
  if (name.length <= max) return [name];
  const words = name.split(" ");
  let a = "";
  while (words.length && (a + " " + words[0]).trim().length <= max) a = (a + " " + words.shift()).trim();
  return [a || words.shift(), words.join(" ")];
}

export function metricEdgePath(p, m, c) {
  const a = p["m:" + m], b = p["c:" + c];
  if (!a || !b) return null;
  const x1 = a.x + a.w, y1 = a.y + a.h / 2, x2 = b.x, y2 = b.y + b.h / 2;
  const mx = (x1 + x2) / 2;
  return `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`;
}

export function copEdgePath(p, from, to, kind) {
  const a = p["c:" + from], b = p["c:" + to];
  if (!a || !b) return null;
  if (a.x < b.x) {                               // forward: right side to left side
    const x1 = a.x + a.w, y1 = a.y + a.h / 2 + (kind === "consumes" ? 0 : 6), x2 = b.x, y2 = b.y + b.h / 2;
    const mx = (x1 + x2) / 2;
    return `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`;
  }
  // backward or same column (referrals, support): arc over the top
  const x1 = a.x + a.w / 2, y1 = a.y, x2 = b.x + b.w / 2, y2 = b.y;
  const lift = 30 + Math.abs(x1 - x2) * 0.08;
  return `M${x1},${y1} C${x1},${y1 - lift} ${x2},${y2 - lift} ${x2},${y2}`;
}
