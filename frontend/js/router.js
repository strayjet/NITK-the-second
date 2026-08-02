// router.js — tiny hash router. No history API server config needed since
// this is a static, no-build single page served by nginx try_files.
import { store } from "./state.js";

const routes = new Map();
let mountEl = null;
let current = null;

export function registerPage(name, { title, render, onLeave }) {
  routes.set(name, { title, render, onLeave });
}

export function initRouter(root) {
  mountEl = root;
  window.addEventListener("hashchange", () => navigate(parseHash(), false));
  navigate(parseHash(), false);
}

function parseHash() {
  const raw = (window.location.hash || "#/overview").replace(/^#\/?/, "");
  return raw.split("?")[0] || "overview";
}

export function navigate(name, updateHash = true) {
  if (!routes.has(name)) name = "overview";
  if (updateHash) window.location.hash = `/${name}`;
  if (current && current !== name) {
    routes.get(current)?.onLeave?.();
  }
  current = name;
  store.set("route", name);
  const page = routes.get(name);
  document.title = `CityMind — ${page.title}`;
  mountEl.innerHTML = "";
  page.render(mountEl);
}

export function currentRoute() {
  return current;
}
