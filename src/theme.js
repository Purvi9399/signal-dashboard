export const C = {
  ink: "#12212E", body: "#46566A", mute: "#8695A6",
  rule: "#E3E8EC", ground: "#F6F8F9", panel: "#FFFFFF",
  accent: "#0B6E6E", soft: "#E6F1F0",
  warn: "#B4690E", danger: "#9B2C2C", good: "#2F6B4F",
};

export const AGENT = {
  "claude-code": "Claude Code", cursor: "Cursor", codex: "Codex",
  copilot: "GitHub Copilot", antigravity: "Antigravity",
};

export const has = (v) =>
  v !== null && v !== undefined && v !== "" && !(Array.isArray(v) && !v.length);
export const uniq = (r, k) => new Set(r.map((x) => x[k]).filter(Boolean)).size;
export const sum = (r, k) => r.reduce((a, x) => a + (Number(x[k]) || 0), 0);
export const cnt = (r, f) => r.filter(f).length;
export const num = (n) =>
  n == null || Number.isNaN(n) ? "\u2014"
  : n >= 1e6 ? (n / 1e6).toFixed(1) + "M"
  : n >= 1e4 ? Math.round(n / 1e3) + "k"
  : Math.round(n).toLocaleString();
