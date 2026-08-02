import { el, icon } from "../ui.js";
import { store } from "../state.js";
import { navigate } from "../router.js";

const NAV_ITEMS = [
  { id: "overview", label: "Overview", icon: "overview" },
  { id: "incidents", label: "Incidents", icon: "incidents" },
  { id: "map", label: "Map", icon: "map" },
  { id: "planning", label: "Planning", icon: "planning" },
  { id: "copilot", label: "Copilot", icon: "copilot" },
  { id: "settings", label: "Settings", icon: "settings" },
];

export function renderSidebar(root) {
  const nav = el("nav", { class: "sidebar-nav" });
  const links = {};

  NAV_ITEMS.forEach((item) => {
    const link = el(
      "a",
      {
        href: `#/${item.id}`,
        class: "sidebar-link",
        onclick: (e) => {
          e.preventDefault();
          navigate(item.id);
          root.classList.remove("sidebar-open");
        },
      },
      [icon(item.icon), el("span", { class: "sidebar-label" }, item.label)]
    );
    links[item.id] = link;
    nav.appendChild(link);
  });

  const statusDot = el("span", { class: "dot dot-offline" });
  const statusLabel = el("span", {}, "connecting…");
  const statusRow = el("div", { class: "sidebar-status" }, [statusDot, statusLabel]);

  const sidebar = el("aside", { class: "sidebar", id: "sidebar" }, [
    el("div", { class: "sidebar-brand" }, [
      el("span", { class: "brand-dot" }),
      el("span", { class: "brand-text" }, "CityMind"),
    ]),
    nav,
    el("div", { class: "sidebar-footer" }, [statusRow]),
  ]);

  root.appendChild(sidebar);

  function setActive(routeName) {
    Object.entries(links).forEach(([id, node]) => {
      node.classList.toggle("active", id === routeName);
    });
  }
  setActive(store.get("route"));
  store.subscribe("route", setActive);

  store.subscribe("wsStatus", (s) => {
    statusDot.className = `dot ${s === "online" ? "dot-online" : s === "connecting" ? "dot-connecting" : "dot-offline"}`;
  });
  store.subscribe("wsLabel", (l) => (statusLabel.textContent = l));

  return sidebar;
}
