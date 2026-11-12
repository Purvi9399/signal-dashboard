/* Hash routing shared by both pages. */
export const PAGES = [["network", "COP–Metric Network"], ["decisions", "COP Decision Explorer"]];

export function parseHash() {
  const h = window.location.hash.replace(/^#\/?/, "");
  const [page, q] = h.split("?");
  return { page: PAGES.some(([p]) => p === page) ? page : "network",
    params: Object.fromEntries(new URLSearchParams(q || "")) };
}

export function go(page, params = {}) {
  const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v != null && v !== "")).toString();
  window.location.hash = `/${page}${q ? "?" + q : ""}`;
}
