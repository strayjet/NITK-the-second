// ui.js — small reusable UI building blocks shared by every page.

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null) node.setAttribute(k, v);
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined) continue;
    node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

export function icon(name, cls = "icon") {
  const paths = {
    overview: '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
    incidents: '<path d="M12 3 2 20h20L12 3Z"/><path d="M12 10v4" stroke-linecap="round"/><circle cx="12" cy="17" r="0.5" fill="currentColor"/>',
    map: '<path d="M9 3 3 6v15l6-3 6 3 6-3V3l-6 3-6-3Z"/><path d="M9 3v15M15 6v15" />',
    planning: '<path d="M4 19h16M7 19V9m5 10V5m5 14v-7"/>',
    copilot: '<rect x="3" y="5" width="18" height="13" rx="3"/><path d="M8 21h8M9 9h.01M15 9h.01"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1 1.55V21a2 2 0 1 1-4 0v-.09A1.7 1.7 0 0 0 9 19.4a1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-1.55-1H3a2 2 0 1 1 0-4h.09A1.7 1.7 0 0 0 4.6 9a1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-1.55V3a2 2 0 1 1 4 0v.09a1.7 1.7 0 0 0 1 1.55 1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.7 1.7 0 0 0 19.4 9c.1.38.32.71.63.93.29.2.65.32 1.02.32H21a2 2 0 1 1 0 4h-.09a1.7 1.7 0 0 0-1.55 1Z"/>',
    close: '<path d="M18 6 6 18M6 6l12 12" stroke-linecap="round"/>',
    check: '<path d="M20 6 9 17l-5-5" stroke-linecap="round" stroke-linejoin="round"/>',
    alert: '<path d="M12 9v4M12 17h.01M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z"/>',
    upload: '<path d="M12 3v12M6 9l6-6 6 6M4 21h16" stroke-linecap="round" stroke-linejoin="round"/>',
    refresh: '<path d="M23 4v6h-6M1 20v-6h6" stroke-linecap="round"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10M23 14l-4.64 4.36A9 9 0 0 1 3.51 15" stroke-linecap="round"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3" stroke-linecap="round"/>',
    send: '<path d="m22 2-7 20-4-9-9-4 20-7Z" stroke-linejoin="round"/>',
    empty: '<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M3 7l2-4h14l2 4"/><path d="M9 12h6"/>',
    truck: '<rect x="1" y="7" width="15" height="10" rx="1"/><path d="M16 10h3l3 3v4h-6z"/><circle cx="5.5" cy="18.5" r="1.5"/><circle cx="17.5" cy="18.5" r="1.5"/>',
    hospital: '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M12 8v6M9 11h6" stroke-linecap="round"/>',
    route: '<circle cx="6" cy="19" r="2"/><circle cx="18" cy="5" r="2"/><path d="M8 19h7a4 4 0 0 0 4-4v-1a4 4 0 0 0-4-4H9a4 4 0 0 1-4-4V5"/>',
    road: '<path d="M4 21 10 3h4l6 18M9 21l1-4M15 21l-1-4"/>',
    layers: '<path d="m12 2 9 5-9 5-9-5 9-5Z"/><path d="m3 12 9 5 9-5M3 17l9 5 9-5"/>',
    chevronDown: '<path d="m6 9 6 6 6-6" stroke-linecap="round" stroke-linejoin="round"/>',
    menu: '<path d="M3 12h18M3 6h18M3 18h18" stroke-linecap="round"/>',
    plus: '<path d="M12 5v14M5 12h14" stroke-linecap="round"/>',
    video: '<rect x="2" y="6" width="14" height="12" rx="2"/><path d="m22 8-6 4 6 4V8Z"/>',
    image: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/>',
    trash: '<path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0-1 14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2L4 6" stroke-linecap="round" stroke-linejoin="round"/>',
    bot: '<rect x="3" y="8" width="18" height="12" rx="3"/><circle cx="9" cy="14" r="1.2"/><circle cx="15" cy="14" r="1.2"/><path d="M12 8V4M9 4h6"/>',
    user: '<circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 5-6 8-6s6.5 2 8 6"/>',
  };
  const svg = `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">${paths[name] || paths.alert}</svg>`;
  const wrap = document.createElement("span");
  wrap.innerHTML = svg;
  return wrap.firstElementChild;
}

// -- state placeholders --------------------------------------------------------

export function loadingState(message = "Loading…") {
  return el("div", { class: "state-block state-loading" }, [
    el("div", { class: "spinner" }),
    el("p", {}, message),
  ]);
}

export function skeletonRows(n = 4, cls = "skeleton-row") {
  const wrap = el("div", { class: "skeleton-wrap" });
  for (let i = 0; i < n; i++) wrap.appendChild(el("div", { class: cls }));
  return wrap;
}

export function emptyState(title, subtitle, action) {
  const box = el("div", { class: "state-block state-empty" }, [
    icon("empty", "icon icon-lg"),
    el("h3", {}, title),
    el("p", {}, subtitle || ""),
  ]);
  if (action) box.appendChild(action);
  return box;
}

export function errorState(message, onRetry) {
  const box = el("div", { class: "state-block state-error" }, [
    icon("alert", "icon icon-lg"),
    el("h3", {}, "Something went wrong"),
    el("p", {}, message || "Unable to reach the CityMind backend."),
  ]);
  if (onRetry) {
    box.appendChild(el("button", { class: "btn btn-secondary", onclick: onRetry }, "Retry"));
  }
  return box;
}

// -- toasts --------------------------------------------------------

let toastRoot = null;
export function toast(message, kind = "info", timeout = 4200) {
  if (!toastRoot) {
    toastRoot = document.getElementById("toast-root");
  }
  if (!toastRoot) return;
  const node = el("div", { class: `toast toast-${kind}` }, [
    icon(kind === "error" ? "alert" : kind === "success" ? "check" : "bot", "icon"),
    el("span", {}, message),
  ]);
  toastRoot.appendChild(node);
  requestAnimationFrame(() => node.classList.add("toast-show"));
  setTimeout(() => {
    node.classList.remove("toast-show");
    setTimeout(() => node.remove(), 250);
  }, timeout);
}

// -- formatting --------------------------------------------------------

export function fmtNumber(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return "–";
  return new Intl.NumberFormat().format(n);
}

export function fmtDate(iso) {
  if (!iso) return "–";
  const d = new Date(iso);
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function fmtDuration(seconds) {
  if (seconds === null || seconds === undefined) return "–";
  if (seconds < 60) return `${seconds.toFixed(0)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}m ${s}s`;
}

export function fmtPct(f) {
  if (f === null || f === undefined) return "–";
  return `${(f * 100).toFixed(0)}%`;
}

export function debounce(fn, ms = 300) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

export function badge(text, kind = "neutral") {
  return el("span", { class: `badge badge-${kind}` }, text);
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

export function modal(titleText, bodyNode, { onClose } = {}) {
  const overlay = el("div", { class: "modal-overlay" });
  const closeFn = () => {
    overlay.classList.remove("modal-show");
    setTimeout(() => overlay.remove(), 180);
    onClose && onClose();
  };
  const box = el("div", { class: "modal-box" }, [
    el("div", { class: "modal-header" }, [
      el("h3", {}, titleText),
      el("button", { class: "icon-btn", onclick: closeFn, "aria-label": "Close" }, icon("close")),
    ]),
    el("div", { class: "modal-body" }, bodyNode),
  ]);
  overlay.appendChild(box);
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) closeFn();
  });
  document.body.appendChild(overlay);
  requestAnimationFrame(() => overlay.classList.add("modal-show"));
  return { overlay, close: closeFn };
}
