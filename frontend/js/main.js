import { store } from "./state.js";
import { initRouter, registerPage, navigate } from "./router.js";
import { renderSidebar } from "./components/sidebar.js";
import { connectWebSocket } from "./ws.js";
import { el, icon } from "./ui.js";
import { openDetectionModal } from "./components/detection.js";

import { renderOverview, leaveOverview } from "./pages/overview.js";
import { renderIncidents, leaveIncidents } from "./pages/incidents.js";
import { renderMap, leaveMap } from "./pages/map.js";
import { renderPlanning, leavePlanning } from "./pages/planning.js";
import { renderCopilot } from "./pages/copilot.js";
import { renderSettings } from "./pages/settings.js";

const PAGE_TITLES = {
  overview: "Overview",
  incidents: "Incidents",
  map: "Map",
  planning: "Planning",
  copilot: "Copilot",
  settings: "Settings",
};

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
}

function initTopbar() {
  const topbar = document.getElementById("topbar");
  const menuBtn = el("button", { class: "icon-btn sidebar-toggle", onclick: () => document.getElementById("sidebar").classList.toggle("sidebar-open") }, icon("menu"));
  const titleEl = el("h1", { class: "topbar-title", id: "topbar-title" }, "Overview");
  const newDetectionBtn = el("button", { class: "btn btn-primary btn-topbar", onclick: () => openDetectionModal({}) }, [icon("upload"), " New detection"]);

  topbar.appendChild(menuBtn);
  topbar.appendChild(titleEl);
  topbar.appendChild(el("div", { class: "topbar-spacer" }));
  topbar.appendChild(newDetectionBtn);

  store.subscribe("route", (r) => {
    titleEl.textContent = PAGE_TITLES[r] || "CityMind";
  });
}

function boot() {
  applyTheme(store.get("theme"));

  renderSidebar(document.getElementById("app-shell"));
  initTopbar();

  registerPage("overview", { title: "Overview", render: renderOverview, onLeave: leaveOverview });
  registerPage("incidents", { title: "Incidents", render: renderIncidents, onLeave: leaveIncidents });
  registerPage("map", { title: "Map", render: renderMap, onLeave: leaveMap });
  registerPage("planning", { title: "Planning", render: renderPlanning, onLeave: leavePlanning });
  registerPage("copilot", { title: "Copilot", render: renderCopilot });
  registerPage("settings", { title: "Settings", render: renderSettings });

  initRouter(document.getElementById("page-root"));
  connectWebSocket();
}

document.addEventListener("DOMContentLoaded", boot);
