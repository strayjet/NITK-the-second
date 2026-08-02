import { el, icon, fmtNumber, fmtDate, loadingState, errorState, emptyState, badge, clear } from "../ui.js";
import { api } from "../api.js";
import { store } from "../state.js";
import { donutChart, barChart, legendFor } from "../charts.js";
import { navigate } from "../router.js";
import { onLiveEvent } from "../ws.js";
import { openDetectionModal } from "../components/detection.js";

let unsubscribeFeed = null;
let unsubscribeLive = null;
let pollTimer = null;

function kpiCard(label, value, iconName, hint) {
  return el("div", { class: "card kpi-card" }, [
    el("div", { class: "kpi-icon" }, icon(iconName)),
    el("div", {}, [
      el("div", { class: "card-label" }, label),
      el("div", { class: "card-value" }, fmtNumber(value)),
      hint ? el("div", { class: "card-hint" }, hint) : null,
    ].filter(Boolean)),
  ]);
}

function feedItemNode(item) {
  const { type, data, time } = item;
  let text = "";
  let kind = "neutral";
  if (type === "incident.closed") {
    text = `Camera ${data.camera_id} — incident closed (conf ${Number(data.max_confidence).toFixed(2)}, ${data.duration_seconds.toFixed(1)}s)`;
    kind = "warn";
  } else if (type === "camera.active_incidents") {
    text = `Camera ${data.camera_id} — ${data.incidents.length} active incident(s)`;
    kind = "danger";
  } else if (type === "dashboard.invalidated") {
    text = `Dashboard data updated (${data.reason})`;
    kind = "neutral";
  } else if (type === "aggregation.poll") {
    text = `Camera ${data.camera_id} — polled ${data.frames_processed} frame(s)`;
  } else {
    text = JSON.stringify(data);
  }
  return el("li", { class: "feed-item" }, [
    badge(type, kind),
    el("span", { class: "feed-time" }, time.toLocaleTimeString()),
    el("p", {}, text),
  ]);
}

export function renderOverview(root) {
  const page = el("div", { class: "page" });

  const header = el("div", { class: "page-header" }, [
    el("div", {}, [el("h1", {}, "Overview"), el("p", { class: "muted" }, "Live snapshot of city traffic, incidents, and infrastructure planning.")]),
    el("div", { class: "page-actions" }, [
      el("button", { class: "btn btn-secondary", onclick: () => loadAll() }, [icon("refresh"), " Refresh"]),
    ]),
  ]);
  page.appendChild(header);

  const kpiRow = el("section", { class: "kpi-grid" }, [loadingState("Loading KPIs…")]);
  page.appendChild(kpiRow);

  const quickActions = el("section", { class: "panel quick-actions" }, [
    el("div", { class: "panel-header" }, [el("h2", {}, "Quick actions")]),
    el("div", { class: "action-grid" }, [
      actionButton("upload", "New detection", "Upload an image or video for YOLO analysis", () =>
        openDetectionModal({ onComplete: () => loadAll() })
      ),
      actionButton("route", "Plan a route", "Compute population-aware routing", () => navigate("planning")),
      actionButton("planning", "Recommend facility", "Find the best new facility location", () => navigate("planning")),
      actionButton("copilot", "Ask Copilot", "Natural-language access to every tool", () => navigate("copilot")),
    ]),
  ]);
  page.appendChild(quickActions);

  const grid2 = el("section", { class: "grid-2" });
  const feedPanel = el("div", { class: "panel" }, [
    el("div", { class: "panel-header" }, [el("h2", {}, "Live incident feed"), badge("0", "neutral")]),
    el("ul", { class: "feed" }, [emptyState("Waiting for events", "Live updates from /ws/live will appear here.")]),
  ]);
  const chartsPanel = el("div", { class: "panel" }, [
    el("div", { class: "panel-header" }, [el("h2", {}, "Incidents by status")]),
    el("div", { class: "chart-row" }, [loadingState("Loading chart…")]),
  ]);
  grid2.appendChild(feedPanel);
  grid2.appendChild(chartsPanel);
  page.appendChild(grid2);

  const grid2b = el("section", { class: "grid-2" });
  const facilitiesChartPanel = el("div", { class: "panel" }, [
    el("div", { class: "panel-header" }, [el("h2", {}, "Facilities by type")]),
    el("div", { class: "chart-row" }, [loadingState("Loading chart…")]),
  ]);
  const metricsPanel = el("div", { class: "panel" }, [
    el("div", { class: "panel-header" }, [el("h2", {}, "System metrics")]),
    el("div", {}, [loadingState("Loading metrics…")]),
  ]);
  grid2b.appendChild(facilitiesChartPanel);
  grid2b.appendChild(metricsPanel);
  page.appendChild(grid2b);

  root.appendChild(page);

  function actionButton(iconName, title, subtitle, onClick) {
    return el("button", { class: "action-card", onclick: onClick }, [
      icon(iconName, "icon icon-lg"),
      el("div", {}, [el("strong", {}, title), el("p", { class: "muted small" }, subtitle)]),
    ]);
  }

  async function loadAll() {
    clear(kpiRow);
    kpiRow.appendChild(loadingState("Loading KPIs…"));
    try {
      const s = await api.dashboardSummary();
      clear(kpiRow);
      kpiRow.appendChild(kpiCard("Total incidents", s.total_incidents, "incidents"));
      kpiRow.appendChild(kpiCard("Active", s.active_incidents, "alert", "currently open"));
      kpiRow.appendChild(kpiCard("Closed", s.closed_incidents, "check"));
      kpiRow.appendChild(kpiCard("Facilities", s.total_facilities, "hospital"));
      kpiRow.appendChild(kpiCard("Detection events", s.total_detection_events, "image"));
      kpiRow.appendChild(kpiCard("Route analyses", s.total_route_analyses, "route", `avg ${s.avg_route_distance_km.toFixed(2)} km`));

      const statusData = Object.entries(s.incidents_by_status).map(([label, value], i) => ({
        label,
        value,
        color: label === "ACTIVE" ? "#ff5d5d" : "#33d17a",
      }));
      const chartBody = chartsPanel.querySelector(".chart-row");
      clear(chartBody);
      if (statusData.length) {
        chartBody.appendChild(donutChart(statusData));
        chartBody.appendChild(legendFor(statusData));
      } else {
        chartBody.appendChild(emptyState("No incidents yet", "Run a detection to populate this chart."));
      }

      const facilityData = Object.entries(s.facilities_by_type).map(([label, value]) => ({ label, value }));
      const facilityRow = facilitiesChartPanel.querySelector(".chart-row");
      clear(facilityRow);
      if (facilityData.length) {
        facilityRow.appendChild(barChart(facilityData));
      } else {
        facilityRow.appendChild(emptyState("No facilities yet", "Recommend a facility from the Planning page."));
      }

      const metricsBody = metricsPanel.querySelector(":scope > div:last-child");
      clear(metricsBody);
      metricsBody.appendChild(
        el("dl", { class: "metrics-list" }, [
          el("dt", {}, "Generated at"), el("dd", {}, fmtDate(s.generated_at)),
          el("dt", {}, "Cached response"), el("dd", {}, s.cached ? "Yes" : "No"),
          el("dt", {}, "Avg route distance"), el("dd", {}, `${s.avg_route_distance_km.toFixed(2)} km`),
        ])
      );
    } catch (err) {
      clear(kpiRow);
      kpiRow.appendChild(errorState(err.message, loadAll));
    }
  }

  function renderFeed() {
    const feed = store.get("feed");
    const list = feedPanel.querySelector(".feed");
    const badgeEl = feedPanel.querySelector(".badge");
    badgeEl.textContent = String(store.get("feedTotal"));
    clear(list);
    if (!feed.length) {
      list.appendChild(emptyState("Waiting for events", "Live updates from /ws/live will appear here."));
      return;
    }
    feed.slice(0, 20).forEach((item) => list.appendChild(feedItemNode(item)));
  }

  renderFeed();
  unsubscribeFeed = store.subscribe("feed", renderFeed);
  unsubscribeLive = onLiveEvent((type) => {
    if (type === "incident.closed" || type === "dashboard.invalidated") loadAll();
  });

  loadAll();
  pollTimer = setInterval(loadAll, Math.max(10, store.get("refreshIntervalSec")) * 1000);
}

export function leaveOverview() {
  unsubscribeFeed && unsubscribeFeed();
  unsubscribeLive && unsubscribeLive();
  clearInterval(pollTimer);
}
