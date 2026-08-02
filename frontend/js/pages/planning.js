// planning.js — Planning page as a true 3-panel, map-first smart-city
// workspace: LEFT tools (Road Closure / Facility Recommendation / Analysis
// Controls), CENTER the interactive map, RIGHT purpose-built result panels
// (Road Closure Impact, Facility Recommendation, Network Analysis).
// Every input is either a map click or a numeric tuning parameter — there is
// no coordinate-entry UX anywhere in this workflow. All API calls and
// payload shapes are unchanged from previous versions.
import { el, icon, toast, loadingState, errorState, clear, badge, fmtPct } from "../ui.js";
import { api } from "../api.js";
import { onLiveEvent } from "../ws.js";

const COLORS = {
  route: "#a78bfa",
  closure_hover: "#fde047",
  closure_selected: "#f59e0b",
  closure_closed: "#ef4444",
  closure_before: "#3b82f6",
  closure_after: "#22c55e",
  network_edge: "#64748b",
  network_hover: "#fde047",
  network_selected: "#f59e0b",
  network_blocked: "#ef4444",
  blocked_road: "#ef4444",
  facility_recommendation: "#a855f7",
  road_suggestions: "#22d3ee",
  scenario_location: "#4ade80",
};

// -- module state (one Leaflet map instance per Planning page visit) --------
let map = null;
let groups = {};
let clickMode = null; // "origin" | "destination" | "closure_edge" | "scenario_location" | null
let originLatLng = null;
let destinationLatLng = null;
let originMarker = null;
let destinationMarker = null;
let closureEdge = null; // { u, v, geometry }
let scenarioLocationLatLng = null;
let facilitiesCache = null; // lazily loaded for the scenario "existing facility" picker
let lastHoverTs = 0;
let hoverToken = 0;

// -- Road Closure Simulation mode (whole-network vector layer) --------------
// Separate from the existing single-click "Select road to close" flow above:
// this renders every edge as its own hoverable/clickable Leaflet polyline so
// road segments can be selected and inspected directly, with no origin or
// destination required. Selecting a segment here also stages it in
// `closureEdge` so the existing Simulate Closure button keeps working
// unchanged if an origin/destination happen to be set.
let networkMode = false;
let allEdgesCache = null; // full /city/edges result, fetched once and reused
let edgeLayerIndex = {}; // edge_id -> { layer, data }
let selectedNetworkEdge = null; // the raw edge item from /city/edges, or null

// -- Block Road (persistent closures) ----------------------------------------
// A road blocked here is removed from routing for every user until it is
// unblocked or all closures are cleared - unlike `closureEdge` above, which
// only stages a one-off "Simulate Closure" comparison. Keyed by "u-v" so it
// can be cross-referenced against both `closureEdge` selections and the
// whole-network vector layer.
let blockedEdges = {}; // "u-v" -> { node_u, node_v, geometry }
let unsubscribeLive = null;

function edgeKey(u, v) {
  return `${u}-${v}`;
}
function isEdgeBlocked(u, v) {
  return !!(blockedEdges[edgeKey(u, v)] || blockedEdges[edgeKey(v, u)]);
}

function ensureGroup(name) {
  if (!groups[name]) groups[name] = L.layerGroup().addTo(map);
  return groups[name];
}
function clearGroup(name) {
  if (groups[name]) groups[name].clearLayers();
}

function field(labelText, inputNode) {
  return el("label", { class: "form-field" }, [el("span", {}, labelText), inputNode]);
}

function stat(label, value) {
  return el("div", { class: "result-stat" }, [el("span", {}, label), el("strong", {}, value)]);
}

function statRow(label, before, after, fmt = (v) => v) {
  const delta = typeof before === "number" && typeof after === "number" ? after - before : null;
  const deltaNode = delta === null ? null : el("span", { class: `delta ${delta <= 0 ? "delta-good" : "delta-bad"}` }, `${delta > 0 ? "+" : ""}${fmt(delta)}`);
  return el("div", { class: "compare-row" }, [
    el("span", { class: "muted" }, label),
    el("span", {}, fmt(before)),
    icon("chevronDown", "icon compare-arrow"),
    el("span", {}, fmt(after)),
    deltaNode,
  ].filter(Boolean));
}

function confidenceMeter(score) {
  const pct = Math.round((score || 0) * 100);
  return el("div", { class: "confidence-meter" }, [
    el("div", { class: "confidence-meter-fill", style: `width:${pct}%` }),
  ]);
}

// -- right column: purpose-built, always-present result panels --------------
function makePanel(iconName, title, emptyText) {
  const content = el("div", { class: "results-panel-empty" }, emptyText);
  const root = el("div", { class: "panel results-panel" }, [
    el("div", { class: "panel-header" }, [icon(iconName), el("h2", {}, title)]),
    content,
  ]);
  return {
    root,
    showEmpty() {
      clear(content);
      content.className = "results-panel-empty";
      content.appendChild(document.createTextNode(emptyText));
    },
    showLoading(msg) {
      clear(content);
      content.className = "";
      content.appendChild(loadingState(msg));
    },
    showError(msg, retry) {
      clear(content);
      content.className = "";
      content.appendChild(errorState(msg, retry));
    },
    setNodes(nodes) {
      clear(content);
      content.className = "";
      content.append(...[].concat(nodes));
    },
  };
}

export function renderPlanning(root) {
  // Declared up front (rather than near their owning section, further down)
  // because roadClosureSection() below is invoked synchronously via
  // sidebar.append(...) before this function's later statements run — a
  // `let` declared after that call point would still be in its temporal
  // dead zone when roadClosureSection() tries to assign to it.
  let updateNetworkModeStatusRef = null;
  let networkStatusNode = null;
  let updateClosuresStatusRef = null;

  const page = el("div", { class: "page page-planning" });
  page.appendChild(
    el("div", { class: "page-header" }, [
      el("div", {}, [el("h1", {}, "Planning"), el("p", { class: "muted" }, "Click the map to plan — road closure, facility recommendation, and network analysis, all without typing a coordinate.")]),
    ])
  );

  const layout = el("div", { class: "planning-layout" });
  const sidebar = el("div", { class: "planning-sidebar" });
  const mapWrap = el("div", { class: "planning-map-wrap panel" }, [el("div", { id: "planning-map-canvas" })]);
  const results = el("div", { class: "planning-results" });

  // -- right column: three purpose-built panels, one per workflow ----------
  const roadInfoPanel = makePanel("road", "Selected Road", "Turn on Road Closure Mode and click a road segment to inspect it — no origin or destination needed.");
  const closureImpactPanel = makePanel("road", "Road Closure Impact", "Select a road and simulate a closure to see its impact here.");
  const facilityPanel = makePanel("hospital", "Facility Recommendation", "Find a best-location recommendation to see it here.");
  const analysisPanel = makePanel("layers", "Network Analysis", "Suggest missing roads or run a scenario comparison to see results here.");

  results.append(roadInfoPanel.root, closureImpactPanel.root, facilityPanel.root, analysisPanel.root);

  sidebar.append(roadClosureSection(), facilityRecommendationSection(), analysisControlsSection());
  layout.append(sidebar, mapWrap, results);
  page.appendChild(layout);
  root.appendChild(page);

  // -- map init --------------------------------------------------------
  requestAnimationFrame(() => {
    map = L.map("planning-map-canvas", { zoomControl: true }).setView([19.076, 72.877], 12);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; OpenStreetMap contributors",
      maxZoom: 18,
    }).addTo(map);
    map.on("click", onMapClick);
    map.on("mousemove", onMapHover);
    map.on("mouseout", () => clearGroup("closure_hover"));
    loadBlockedRoads();
  });

  // Another page/user may block, unblock, or clear closures while this page
  // is open - keep the red "blocked roads" layer in sync.
  unsubscribeLive = onLiveEvent((type, data) => {
    if (type === "dashboard.invalidated" && ["road_blocked", "road_unblocked", "closures_cleared"].includes(data?.reason)) {
      loadBlockedRoads();
    }
  });

  function onMapClick(e) {
    if (clickMode === "origin") {
      originLatLng = e.latlng;
      if (originMarker) map.removeLayer(originMarker);
      originMarker = L.marker(e.latlng, { title: "Origin" }).addTo(map);
      clickMode = null;
      updateRoadClosureStatus();
    } else if (clickMode === "destination") {
      destinationLatLng = e.latlng;
      if (destinationMarker) map.removeLayer(destinationMarker);
      destinationMarker = L.marker(e.latlng, { title: "Destination" }).addTo(map);
      clickMode = null;
      updateRoadClosureStatus();
    } else if (clickMode === "closure_edge") {
      selectClosureEdge(e.latlng);
      clickMode = null;
    } else if (clickMode === "scenario_location") {
      scenarioLocationLatLng = e.latlng;
      clearGroup("scenario_location");
      L.circleMarker(e.latlng, { radius: 8, color: COLORS.scenario_location, weight: 3, fillOpacity: 0.7 }).addTo(ensureGroup("scenario_location"));
      clickMode = null;
      updateScenarioLocationChip();
      toast("Location picked on map", "success");
    }
  }

  // Hover preview of the nearest road while in "select road to close" mode —
  // mirrors the Map page's hover-to-preview / click-to-select flow.
  function onMapHover(e) {
    if (clickMode !== "closure_edge") return;
    const now = performance.now();
    if (now - lastHoverTs < 120) return;
    lastHoverTs = now;
    const token = ++hoverToken;
    api
      .nearestEdge(e.latlng.lat, e.latlng.lng)
      .then((res) => {
        if (token !== hoverToken || clickMode !== "closure_edge") return;
        clearGroup("closure_hover");
        L.polyline(res.geometry, { color: COLORS.closure_hover, weight: 5, opacity: 0.85, className: "hover-path" }).addTo(ensureGroup("closure_hover"));
      })
      .catch(() => {});
  }

  // ==========================================================================
  // LEFT — Road Closure
  // ==========================================================================
  function roadClosureSection() {
    const statusOrigin = el("span", { class: "planning-tool-status" }, "Origin: not set");
    const statusDest = el("span", { class: "planning-tool-status" }, "Destination: not set");
    const closureStatus = el("div", { class: "planning-tool-status" }, "No road selected.");
    const closureHint = el("p", { class: "muted small", style: "color:var(--warn); display:none" }, "Set an origin and destination to enable simulation.");
    const simulateBtn = el("button", { class: "btn btn-primary btn-block", style: "display:none" }, "Simulate Closure");

    // -- Block Road (persistent closure) ---------------------------------
    const blockBtn = el("button", { class: "btn btn-tiny btn-danger" }, "Block Road");
    const unblockBtn = el("button", { class: "btn btn-tiny" }, "Unblock Road");
    const clearClosuresBtn = el("button", { class: "btn btn-tiny" }, "Clear All Closures");
    const closuresStatus = el("div", { class: "planning-tool-status" }, "0 roads currently blocked.");
    blockBtn.onclick = blockCurrentRoad;
    unblockBtn.onclick = unblockCurrentRoad;
    clearClosuresBtn.onclick = clearAllClosures;

    function refreshStatus() {
      statusOrigin.textContent = originLatLng ? "Origin: set ✓" : "Origin: not set";
      statusDest.textContent = destinationLatLng ? "Destination: set ✓" : "Destination: not set";
      const hasEdge = !!closureEdge;
      const hasRoute = !!(originLatLng && destinationLatLng);
      const isBlocked = hasEdge && isEdgeBlocked(closureEdge.u, closureEdge.v);
      closureStatus.textContent = hasEdge
        ? `Road segment selected ✓${isBlocked ? " — currently blocked" : ""}`
        : "No road selected.";
      closureStatus.classList.toggle("status-blocked", isBlocked);
      closureHint.style.display = hasEdge && !hasRoute ? "block" : "none";
      simulateBtn.style.display = hasEdge && hasRoute ? "block" : "none";
      blockBtn.disabled = !hasEdge || isBlocked;
      unblockBtn.disabled = !hasEdge || !isBlocked;
    }
    updateRoadClosureStatusRef = refreshStatus;
    refreshStatus();

    updateClosuresStatusRef = () => {
      const count = Object.keys(blockedEdges).length;
      closuresStatus.textContent = `${count} road${count === 1 ? "" : "s"} currently blocked.`;
      clearClosuresBtn.disabled = count === 0;
    };
    updateClosuresStatusRef();

    simulateBtn.onclick = simulateClosure;

    // -- Road Closure Simulation mode: whole network shown + selectable ----
    const networkModeBtn = el("button", { class: "btn btn-tiny" }, "Road Closure Mode: Off");
    const networkStatus = el("div", { class: "planning-tool-status" }, "Off — showing only what you click.");
    networkModeBtn.onclick = toggleNetworkMode;
    networkStatusNode = networkStatus;
    updateNetworkModeStatusRef = () => {
      networkModeBtn.textContent = networkMode ? "Road Closure Mode: On" : "Road Closure Mode: Off";
      networkModeBtn.classList.toggle("btn-primary", networkMode);
    };
    updateNetworkModeStatusRef();

    return el("div", { class: "panel planning-tool-section" }, [
      el("div", { class: "panel-header" }, [icon("road"), el("h2", {}, "Road Closure")]),
      el("p", { class: "muted small" }, "Click the map to set points, then hover to preview and click a road — nothing to type."),
      el("div", { class: "route-btns" }, [
        el("button", { class: "btn btn-tiny", onclick: () => { clickMode = "origin"; toast("Click the map to place the origin", "info"); } }, "Set origin"),
        el("button", { class: "btn btn-tiny", onclick: () => { clickMode = "destination"; toast("Click the map to place the destination", "info"); } }, "Set destination"),
        el("button", { class: "btn btn-tiny", onclick: clearRoute }, "Clear"),
      ]),
      statusOrigin,
      statusDest,
      el("hr", { class: "planning-tool-divider" }),
      el("div", { class: "route-btns" }, [
        el("button", { class: "btn btn-tiny", onclick: () => { clickMode = "closure_edge"; toast("Move over the map to preview roads, click one to select it", "info"); } }, "Select road to close"),
        el("button", { class: "btn btn-tiny", onclick: clearClosureSelection }, "Clear selection"),
      ]),
      closureStatus,
      closureHint,
      simulateBtn,
      el("div", { class: "route-btns" }, [blockBtn, unblockBtn]),
      el("div", { class: "route-btns" }, [clearClosuresBtn]),
      closuresStatus,
      el("hr", { class: "planning-tool-divider" }),
      el("h3", { class: "muted small", style: "text-transform:uppercase;letter-spacing:.04em;margin:0" }, "Road Closure Simulation"),
      el("p", { class: "muted small" }, "Shows every road on the map. Hover to preview, click any segment to select and inspect it — no origin or destination needed."),
      el("div", { class: "route-btns" }, [networkModeBtn]),
      networkStatus,
    ]);
  }

  function setNetworkStatus(text) {
    if (networkStatusNode) networkStatusNode.textContent = text;
  }

  function clearRoute() {
    originLatLng = destinationLatLng = null;
    if (originMarker) map.removeLayer(originMarker);
    if (destinationMarker) map.removeLayer(destinationMarker);
    clearGroup("route");
    updateRoadClosureStatus();
  }

  function clearClosureSelection() {
    closureEdge = null;
    clearGroup("closure_hover");
    clearGroup("closure_selected");
    clearGroup("closure_closed");
    clearGroup("closure_before");
    clearGroup("closure_after");
    closureImpactPanel.showEmpty();
    clearNetworkSelection();
    updateRoadClosureStatus();
  }

  let updateRoadClosureStatusRef = null;
  function updateRoadClosureStatus() {
    updateRoadClosureStatusRef && updateRoadClosureStatusRef();
  }

  // ==========================================================================
  // Block Road — persistent closures. A blocked road is removed from every
  // routing calculation until it is unblocked or all closures are cleared,
  // and is always shown in red on the map, independent of Road Closure
  // Simulation (network) mode being on or off.
  // ==========================================================================
  async function loadBlockedRoads() {
    try {
      const res = await api.closedRoads();
      blockedEdges = {};
      (res.blocked_edges || []).forEach((e) => {
        blockedEdges[edgeKey(e.node_u, e.node_v)] = e;
      });
      renderBlockedRoadsLayer();
      refreshNetworkEdgeColors();
      updateClosuresStatus();
      updateRoadClosureStatus();
    } catch (err) {
      console.warn("could not load blocked roads", err.message);
    }
  }

  function renderBlockedRoadsLayer() {
    if (!map) return;
    clearGroup("blocked_roads");
    const group = ensureGroup("blocked_roads");
    Object.values(blockedEdges).forEach((e) => {
      L.polyline(e.geometry, {
        color: COLORS.blocked_road,
        weight: 6,
        opacity: 0.95,
        className: "blocked-road-path",
      })
        .bindPopup(`<b>Road blocked</b><br/>OSM edge ${e.node_u} \u2192 ${e.node_v}`)
        .on("click", (ev) => {
          L.DomEvent.stopPropagation(ev);
          closureEdge = { u: e.node_u, v: e.node_v, geometry: e.geometry };
          clearGroup("closure_hover");
          clearGroup("closure_selected");
          updateRoadClosureStatus();
          toast("Blocked road selected — click Unblock Road to reopen it", "info");
        })
        .addTo(group);
    });
  }

  // Re-colors the whole-network vector layer (if currently shown) so
  // blocked roads read as red there too, not just in the dedicated layer.
  function refreshNetworkEdgeColors() {
    if (!networkMode) return;
    Object.entries(edgeLayerIndex).forEach(([edgeId, entry]) => {
      if (selectedNetworkEdge && selectedNetworkEdge.edge_id === edgeId) return; // selection style wins
      const blocked = isEdgeBlocked(entry.data.node_u, entry.data.node_v);
      entry.layer.setStyle({
        color: blocked ? COLORS.network_blocked : COLORS.network_edge,
        weight: blocked ? 5 : 3,
        opacity: blocked ? 0.9 : 0.55,
      });
    });
  }

  function updateClosuresStatus() {
    updateClosuresStatusRef && updateClosuresStatusRef();
  }

  async function blockCurrentRoad() {
    if (!closureEdge) return;
    try {
      const res = await api.blockRoad([closureEdge.u, closureEdge.v]);
      blockedEdges = {};
      (res.blocked_edges || []).forEach((e) => {
        blockedEdges[edgeKey(e.node_u, e.node_v)] = e;
      });
      renderBlockedRoadsLayer();
      refreshNetworkEdgeColors();
      updateClosuresStatus();
      updateRoadClosureStatus();
      if (selectedNetworkEdge) selectNetworkEdge(selectedNetworkEdge);
      toast("Road blocked", "success");
    } catch (err) {
      toast(`Could not block road: ${err.message}`, "error");
    }
  }

  async function unblockCurrentRoad() {
    if (!closureEdge) return;
    try {
      const res = await api.unblockRoad([closureEdge.u, closureEdge.v]);
      blockedEdges = {};
      (res.blocked_edges || []).forEach((e) => {
        blockedEdges[edgeKey(e.node_u, e.node_v)] = e;
      });
      renderBlockedRoadsLayer();
      refreshNetworkEdgeColors();
      updateClosuresStatus();
      updateRoadClosureStatus();
      if (selectedNetworkEdge) selectNetworkEdge(selectedNetworkEdge);
      toast("Road unblocked", "success");
    } catch (err) {
      toast(`Could not unblock road: ${err.message}`, "error");
    }
  }

  async function clearAllClosures() {
    try {
      await api.clearClosures();
      blockedEdges = {};
      renderBlockedRoadsLayer();
      refreshNetworkEdgeColors();
      updateClosuresStatus();
      updateRoadClosureStatus();
      if (selectedNetworkEdge) selectNetworkEdge(selectedNetworkEdge);
      toast("All road closures cleared", "success");
    } catch (err) {
      toast(`Could not clear closures: ${err.message}`, "error");
    }
  }

  async function selectClosureEdge(latlng) {
    try {
      const res = await api.nearestEdge(latlng.lat, latlng.lng);
      closureEdge = { u: res.node_u, v: res.node_v, geometry: res.geometry };
      clearGroup("closure_hover");
      clearGroup("closure_selected");
      clearGroup("closure_closed");
      clearGroup("closure_before");
      clearGroup("closure_after");
      L.polyline(closureEdge.geometry, { color: COLORS.closure_selected, weight: 6, dashArray: "8 8", className: "pulse-path" }).addTo(ensureGroup("closure_selected"));
      updateRoadClosureStatus();
      toast("Road segment selected — ready to simulate", "success");
    } catch (err) {
      toast(`Could not select a road there: ${err.message}`, "error");
    }
  }

  // ==========================================================================
  // Road Closure Simulation mode — entire network rendered as a vector
  // layer; every segment is independently hoverable/clickable. No origin or
  // destination is required to select and inspect a segment here.
  // ==========================================================================
  async function toggleNetworkMode() {
    networkMode = !networkMode;
    updateNetworkModeStatusRef && updateNetworkModeStatusRef();

    if (networkMode) {
      await loadRoadNetwork();
    } else {
      clearGroup("network_edges");
      edgeLayerIndex = {};
      clearNetworkSelection();
      setNetworkStatus("Off — showing only what you click.");
    }
  }

  async function loadRoadNetwork() {
    if (!allEdgesCache) {
      setNetworkStatus("Loading road network…");
      try {
        const res = await api.cityEdges();
        allEdgesCache = res.edges;
      } catch (err) {
        toast(`Could not load road network: ${err.message}`, "error");
        networkMode = false;
        updateNetworkModeStatusRef && updateNetworkModeStatusRef();
        setNetworkStatus("Failed to load road network.");
        return;
      }
    }
    renderRoadNetwork();
    setNetworkStatus(`${allEdgesCache.length} road segments shown — hover to preview, click to select.`);
  }

  function renderRoadNetwork() {
    clearGroup("network_edges");
    const group = ensureGroup("network_edges");
    edgeLayerIndex = {};

    allEdgesCache.forEach((edgeData) => {
      const isSelected = selectedNetworkEdge && selectedNetworkEdge.edge_id === edgeData.edge_id;
      const isBlocked = isEdgeBlocked(edgeData.node_u, edgeData.node_v);
      const layer = L.polyline(edgeData.geometry, {
        color: isSelected ? COLORS.network_selected : isBlocked ? COLORS.network_blocked : COLORS.network_edge,
        weight: isSelected ? 6 : isBlocked ? 5 : 3,
        opacity: isSelected ? 1 : isBlocked ? 0.9 : 0.55,
      }).addTo(group);
      edgeLayerIndex[edgeData.edge_id] = { layer, data: edgeData };

      layer.on("mouseover", () => {
        if (selectedNetworkEdge && selectedNetworkEdge.edge_id === edgeData.edge_id) return;
        layer.setStyle({ color: COLORS.network_hover, weight: 5, opacity: 0.9 });
        layer.bringToFront();
      });
      layer.on("mouseout", () => {
        if (selectedNetworkEdge && selectedNetworkEdge.edge_id === edgeData.edge_id) return;
        const stillBlocked = isEdgeBlocked(edgeData.node_u, edgeData.node_v);
        layer.setStyle({
          color: stillBlocked ? COLORS.network_blocked : COLORS.network_edge,
          weight: stillBlocked ? 5 : 3,
          opacity: stillBlocked ? 0.9 : 0.55,
        });
      });
      layer.on("click", (e) => {
        L.DomEvent.stopPropagation(e); // select the road, don't also fire the map's own click handler
        selectNetworkEdge(edgeData);
      });
    });
  }

  function selectNetworkEdge(edgeData) {
    if (selectedNetworkEdge && edgeLayerIndex[selectedNetworkEdge.edge_id]) {
      const prev = edgeLayerIndex[selectedNetworkEdge.edge_id];
      const prevBlocked = isEdgeBlocked(prev.data.node_u, prev.data.node_v);
      prev.layer.setStyle({
        color: prevBlocked ? COLORS.network_blocked : COLORS.network_edge,
        weight: prevBlocked ? 5 : 3,
        opacity: prevBlocked ? 0.9 : 0.55,
      });
    }
    selectedNetworkEdge = edgeData;
    const entry = edgeLayerIndex[edgeData.edge_id];
    if (entry) {
      entry.layer.setStyle({ color: COLORS.network_selected, weight: 6, opacity: 1 });
      entry.layer.bringToFront();
    }

    // Also stage this segment in the existing single-edge closure flow, so
    // Simulate Closure works immediately if an origin/destination are
    // already set — without requiring either to just select and inspect it.
    closureEdge = { u: edgeData.node_u, v: edgeData.node_v, geometry: edgeData.geometry };
    clearGroup("closure_hover");
    clearGroup("closure_before");
    clearGroup("closure_after");
    clearGroup("closure_selected");
    updateRoadClosureStatus();

    const isBlocked = isEdgeBlocked(edgeData.node_u, edgeData.node_v);
    const panelBlockBtn = el("button", { class: "btn btn-tiny btn-danger", disabled: isBlocked ? "disabled" : undefined }, "Block Road");
    const panelUnblockBtn = el("button", { class: "btn btn-tiny", disabled: isBlocked ? undefined : "disabled" }, "Unblock Road");
    panelBlockBtn.onclick = blockCurrentRoad;
    panelUnblockBtn.onclick = unblockCurrentRoad;

    roadInfoPanel.setNodes([
      el("div", { class: "result-card" }, [
        el("div", { class: "result-card-head" }, [
          el("strong", {}, edgeData.name || "Unnamed road"),
          badge(edgeData.highway || "road", "neutral"),
          isBlocked ? badge("Blocked", "danger") : null,
        ].filter(Boolean)),
        el("div", { class: "result-card-body" }, [
          stat("Road ID", edgeData.edge_id),
          stat("Connected intersections", `${edgeData.node_u} \u2192 ${edgeData.node_v}`),
          stat("Length", `${(edgeData.length_m / 1000).toFixed(3)} km`),
        ]),
      ]),
      el("div", { class: "route-btns" }, [panelBlockBtn, panelUnblockBtn]),
      el("p", { class: "muted small" }, "Staged for closure — set an origin and destination in Road Closure above, then Simulate Closure to see its impact."),
    ]);
    toast("Road segment selected", "success");
  }

  function clearNetworkSelection() {
    if (selectedNetworkEdge && edgeLayerIndex[selectedNetworkEdge.edge_id]) {
      const prev = edgeLayerIndex[selectedNetworkEdge.edge_id];
      const prevBlocked = isEdgeBlocked(prev.data.node_u, prev.data.node_v);
      prev.layer.setStyle({
        color: prevBlocked ? COLORS.network_blocked : COLORS.network_edge,
        weight: prevBlocked ? 5 : 3,
        opacity: prevBlocked ? 0.9 : 0.55,
      });
    }
    selectedNetworkEdge = null;
    roadInfoPanel.showEmpty();
  }

  function severityFor(routeChange, coverageChange) {
    if (!routeChange || routeChange.status !== "OK") return { label: "Critical — no alternative", kind: "danger" };
    const delay = routeChange.eta_delay_minutes ?? 0;
    const covDelta = coverageChange ? Math.abs(coverageChange.coverage_fraction_delta * 100) : 0;
    if (delay >= 15 || covDelta >= 5) return { label: "Severe", kind: "danger" };
    if (delay >= 5 || covDelta >= 2) return { label: "High", kind: "warn" };
    if (delay >= 2 || covDelta >= 0.5) return { label: "Moderate", kind: "warn" };
    return { label: "Low", kind: "success" };
  }

  async function simulateClosure() {
    if (!originLatLng || !destinationLatLng || !closureEdge) return;
    closureImpactPanel.showLoading("Simulating closure…");
    try {
      const res = await api.simulateClosure(
        [originLatLng.lat, originLatLng.lng],
        [destinationLatLng.lat, destinationLatLng.lng],
        [closureEdge.u, closureEdge.v]
      );
      clearGroup("closure_before");
      clearGroup("closure_after");

      const rc = res.route_change;
      const cc = res.coverage_change;

      if (!rc || rc.status !== "OK") {
        closureImpactPanel.setNodes([
          el("p", { class: "scenario-summary" }, res.summary),
          el("div", { class: "result-card" }, [el("div", { class: "result-card-head" }, [el("strong", {}, "No alternative route"), badge("Critical", "danger")])]),
        ]);
        map.fitBounds(L.polyline(closureEdge.geometry).getBounds(), { padding: [60, 60] });
        toast("Closure simulated", "success");
        return;
      }

      L.polyline(rc.route_coordinates_before, { color: COLORS.closure_before, weight: 5 }).addTo(ensureGroup("closure_before"));
      L.polyline(rc.route_coordinates_after, { color: COLORS.closure_after, weight: 5, dashArray: "10 6" }).addTo(ensureGroup("closure_after"));
      map.fitBounds(L.latLngBounds([...rc.route_coordinates_before, ...rc.route_coordinates_after, ...closureEdge.geometry]), { padding: [40, 40] });

      const severity = severityFor(rc, cc);
      const nodes = [
        el("p", { class: "scenario-summary" }, res.summary),
        el("div", { class: "result-card" }, [
          el("div", { class: "result-card-head" }, [el("strong", {}, "Impact"), badge(severity.label, severity.kind)]),
          el("div", { class: "result-card-body" }, [
            stat("Original distance", `${rc.distance_km_before.toFixed(2)} km`),
            stat("Rerouted distance", `${rc.distance_km_after.toFixed(2)} km`),
            stat("Added distance", `${rc.delay_km >= 0 ? "+" : ""}${rc.delay_km.toFixed(2)} km`),
            stat("Delay estimate", `${rc.eta_delay_minutes >= 0 ? "+" : ""}${rc.eta_delay_minutes.toFixed(1)} min`),
          ]),
        ]),
      ];
      if (cc) {
        nodes.push(
          el("div", { class: "panel-subsection" }, [
            el("h3", {}, "Citywide coverage impact"),
            el("p", { class: "muted small" }, `Facility coverage ${cc.coverage_fraction_delta <= 0 ? "drops" : "rises"} from ${fmtPct(cc.coverage_fraction_before)} to ${fmtPct(cc.coverage_fraction_after)}.`),
          ])
        );
      }
      closureImpactPanel.setNodes(nodes);
      toast("Closure simulated", "success");
    } catch (err) {
      closureImpactPanel.showError(err.message, simulateClosure);
    }
  }

  // ==========================================================================
  // LEFT — Facility Recommendation
  // ==========================================================================
  function facilityRecommendationSection() {
    const typeSelect = el("select", { class: "input" }, [el("option", { value: "" }, "Loading types…")]);
    api
      .facilityTypes()
      .then((res) => {
        clear(typeSelect);
        res.facility_types.forEach((ft) => {
          typeSelect.appendChild(el("option", { value: ft.value, style: `color:${ft.color}` }, `${ft.label} (min spacing ${ft.min_spacing_km}km)`));
        });
      })
      .catch(() => {
        clear(typeSelect);
        typeSelect.appendChild(el("option", { value: "hospital" }, "hospital"));
      });

    const findBtn = el("button", { class: "btn btn-primary btn-block" }, "Find Best Location");
    findBtn.onclick = () => findBestLocation(typeSelect.value || "hospital");

    return el("div", { class: "panel planning-tool-section" }, [
      el("div", { class: "panel-header" }, [icon("hospital"), el("h2", {}, "Facility Recommendation")]),
      field("Facility type", typeSelect),
      findBtn,
    ]);
  }

  async function findBestLocation(facilityType) {
    facilityPanel.showLoading("Evaluating candidate sites…");
    clearGroup("facility_recommendation");
    try {
      const res = await api.recommendFacility({ facility_type: facilityType, persist: false });
      if (res.status === "no_valid_site") {
        facilityPanel.setNodes([el("p", { class: "muted small" }, res.detail || "No valid site found for this facility type.")]);
        return;
      }
      renderFacilityRecommendation(res);
      toast("Facility recommendation ready", "success");
    } catch (err) {
      facilityPanel.showError(err.message, () => findBestLocation(facilityType));
    }
  }

  function renderFacilityRecommendation(res) {
    const group = ensureGroup("facility_recommendation");
    const color = res.color || COLORS.facility_recommendation;
    const radiusKm = res.min_spacing_km || 2.5;

    L.circleMarker([res.lat, res.lon], { radius: 10, color, weight: 3, fillOpacity: 0.85 }).bindPopup(`<b>Recommended ${res.label || res.facility_type}</b>`).addTo(group);
    L.circle([res.lat, res.lon], { radius: radiusKm * 1000, color, weight: 1, fillOpacity: 0.08, dashArray: "4 4" }).addTo(group);
    (res.nearby_facilities || []).forEach((f) => {
      L.circleMarker([f.lat, f.lon], { radius: 5, color: "#94a3b8", weight: 1, fillOpacity: 0.5 })
        .bindPopup(`Existing ${res.label || res.facility_type}<br/>${f.distance_km.toFixed(2)} km away`)
        .addTo(group);
    });
    map.setView([res.lat, res.lon], Math.max(map.getZoom(), 14));

    const explanationText =
      res.coverage_improvement_pct != null && res.population_served != null
        ? `This location is recommended because population density is high and current coverage is low. It brings roughly ${Math.round(res.population_served).toLocaleString()} people into coverage, ${res.coverage_improvement_pct.toFixed(0)}% of whom are not currently served by a nearby ${(res.label || res.facility_type || "").toLowerCase()}.`
        : "This location minimizes population-weighted access cost across the demand area.";

    facilityPanel.setNodes([
      el("div", { class: "result-card" }, [
        el("div", { class: "result-card-head" }, [el("strong", {}, res.label || res.facility_type), badge("Preview only", "neutral")]),
        el("div", { class: "result-card-body" }, [
          stat("Confidence score", res.confidence_score != null ? `${Math.round(res.confidence_score * 100)}%` : "–"),
          stat("Population served", res.population_served != null ? Math.round(res.population_served).toLocaleString() : "–"),
          stat("Coverage improvement", res.coverage_improvement_pct != null ? `${res.coverage_improvement_pct.toFixed(0)}%` : "–"),
          stat("Coverage radius", `${radiusKm} km`),
        ]),
      ]),
      confidenceMeter(res.confidence_score),
      el("p", { class: "muted small facility-reco-explanation" }, explanationText),
      el("details", { class: "facility-reco-coords" }, [el("summary", {}, "Coordinates"), el("p", { class: "muted small" }, `${res.lat.toFixed(5)}, ${res.lon.toFixed(5)}`)]),
    ]);
  }

  // ==========================================================================
  // LEFT — Analysis Controls (road-gap suggestions + scenario comparison)
  // ==========================================================================
  function analysisControlsSection() {
    // -- road-gap suggestions --
    const sampleSize = el("input", { type: "number", class: "input", value: "150", min: "2" });
    const minDist = el("input", { type: "number", class: "input", value: "200", min: "0" });
    const maxDist = el("input", { type: "number", class: "input", value: "2000", min: "1" });
    const topN = el("input", { type: "number", class: "input", value: "10", min: "1" });
    const suggestBtn = el("button", { class: "btn btn-secondary btn-block" }, "Suggest missing roads");
    suggestBtn.onclick = () => suggestRoads({ sampleSize, minDist, maxDist, topN });

    // -- scenario comparison --
    const scenarioType = el(
      "select",
      { class: "input", onchange: () => refreshScenarioFields() },
      [
        el("option", { value: "facility_add" }, "Add facility"),
        el("option", { value: "facility_remove" }, "Remove facility"),
        el("option", { value: "facility_relocate" }, "Relocate facility"),
      ]
    );
    const facilityTypeInput = el("input", { type: "text", class: "input", placeholder: "GENERIC", value: "GENERIC" });
    const existingFacilitySelect = el("select", { class: "input" }, [el("option", { value: "" }, "Loading facilities…")]);
    const coverageRadiusInput = el("input", { type: "number", class: "input", value: "2.5", step: "0.5" });
    const pickLocationBtn = el("button", { class: "btn btn-tiny" }, "Pick location on map");
    const locationChip = el("span", { class: "planning-tool-status" }, "No location picked.");
    pickLocationBtn.onclick = () => {
      clickMode = "scenario_location";
      toast("Click the map to set the facility location", "info");
    };
    updateScenarioLocationChipRef = () => {
      clear(locationChip);
      locationChip.appendChild(
        scenarioLocationLatLng
          ? el("span", { class: "facility-picked-chip" }, [icon("check"), " Location picked"])
          : document.createTextNode("No location picked.")
      );
    };
    updateScenarioLocationChipRef();

    const facilityTypeField = field("Facility type", facilityTypeInput);
    const existingFacilityField = field("Existing facility", existingFacilitySelect);
    const locationField = el("div", { class: "form-field" }, [el("span", {}, "Facility location"), pickLocationBtn, locationChip]);

    function refreshScenarioFields() {
      const t = scenarioType.value;
      locationField.style.display = t === "facility_add" || t === "facility_relocate" ? "flex" : "none";
      facilityTypeField.style.display = t === "facility_add" || t === "facility_relocate" ? "flex" : "none";
      existingFacilityField.style.display = t === "facility_remove" || t === "facility_relocate" ? "flex" : "none";
      if ((t === "facility_remove" || t === "facility_relocate") && !facilitiesCache) loadFacilitiesIntoSelect(existingFacilitySelect);
    }
    refreshScenarioFields();

    const runScenarioBtn = el("button", { class: "btn btn-primary btn-block" }, "Run scenario");
    runScenarioBtn.onclick = () => runScenario({ scenarioType, facilityTypeInput, existingFacilitySelect, coverageRadiusInput });

    return el("div", { class: "panel planning-tool-section" }, [
      el("div", { class: "panel-header" }, [icon("planning"), el("h2", {}, "Analysis Controls")]),

      el("h3", { class: "muted small", style: "text-transform:uppercase;letter-spacing:.04em;margin:0" }, "Road-gap suggestions"),
      el("div", { class: "form-grid" }, [field("Sample size", sampleSize), field("Top N", topN), field("Min straight-line (m)", minDist), field("Max straight-line (m)", maxDist)]),
      suggestBtn,

      el("hr", { class: "planning-tool-divider" }),

      el("h3", { class: "muted small", style: "text-transform:uppercase;letter-spacing:.04em;margin:0" }, "Scenario comparison"),
      el("div", { class: "form-grid" }, [field("Scenario type", scenarioType), locationField, facilityTypeField, existingFacilityField, field("Coverage radius (km)", coverageRadiusInput)]),
      runScenarioBtn,
    ]);
  }

  let updateScenarioLocationChipRef = null;
  function updateScenarioLocationChip() {
    updateScenarioLocationChipRef && updateScenarioLocationChipRef();
  }

  async function loadFacilitiesIntoSelect(selectNode) {
    try {
      const page = await api.dashboardFacilities({ page: 1, page_size: 200 });
      facilitiesCache = page.items;
      clear(selectNode);
      if (!facilitiesCache.length) {
        selectNode.appendChild(el("option", { value: "" }, "No facilities yet"));
        return;
      }
      facilitiesCache.forEach((f) => {
        selectNode.appendChild(el("option", { value: String(f.id) }, `${f.name || f.facility_type} (#${f.id})`));
      });
    } catch {
      clear(selectNode);
      selectNode.appendChild(el("option", { value: "" }, "Could not load facilities"));
    }
  }

  async function suggestRoads({ sampleSize, minDist, maxDist, topN }) {
    analysisPanel.showLoading("Scanning for high-impact road gaps…");
    clearGroup("road_suggestions");
    try {
      const res = await api.suggestRoads({
        sample_size: Number(sampleSize.value) || 100,
        min_straight_line_m: Number(minDist.value) || 200,
        max_straight_line_m: Number(maxDist.value) || 2000,
        top_n: Number(topN.value) || 10,
        persist: false,
      });
      if (!res.suggestions.length) {
        analysisPanel.setNodes([el("p", { class: "muted small" }, "No suggestions — try widening the distance range or sample size.")]);
        return;
      }
      const group = ensureGroup("road_suggestions");
      const bounds = [];
      res.suggestions.forEach((s) => {
        L.polyline([s.point_u, s.point_v], { color: COLORS.road_suggestions, weight: 3, dashArray: "6 6" })
          .bindPopup(`Gap ratio ${s.gap_ratio.toFixed(2)}<br/>Saves ${(s.distance_saved_m / 1000).toFixed(2)} km<br/>Impact score ${s.impact_score.toFixed(1)}`)
          .addTo(group);
        bounds.push(s.point_u, s.point_v);
      });
      map.fitBounds(L.latLngBounds(bounds), { padding: [40, 40] });

      const totalSaved = res.suggestions.reduce((sum, s) => sum + s.distance_saved_m, 0) / 1000;
      const top = res.suggestions[0];
      analysisPanel.setNodes([
        el("p", { class: "muted small" }, `${res.suggestions.length} road-gap suggestions found and drawn on the map.`),
        el("div", { class: "panel-subsection" }, [
          el("h3", {}, "Top suggestions"),
          ...res.suggestions.slice(0, 5).map((s, i) => stat(`#${i + 1} impact score`, s.impact_score.toFixed(1))),
        ]),
        el("div", { class: "panel-subsection" }, [
          el("h3", {}, "Combined potential savings"),
          el("p", { class: "muted small" }, `Roughly ${totalSaved.toFixed(1)} km of network distance.`),
        ]),
        el("div", { class: "panel-subsection" }, [
          el("h3", {}, "Top priority"),
          el("p", { class: "muted small" }, `Saves ${(top.distance_saved_m / 1000).toFixed(2)} km with a gap ratio of ${top.gap_ratio.toFixed(2)} — worth investigating first.`),
        ]),
      ]);
      toast(`${res.suggestions.length} road suggestions generated`, "success");
    } catch (err) {
      analysisPanel.showError(err.message, () => suggestRoads({ sampleSize, minDist, maxDist, topN }));
    }
  }

  async function runScenario({ scenarioType, facilityTypeInput, existingFacilitySelect, coverageRadiusInput }) {
    const t = scenarioType.value;
    const payload = { scenario_type: t, coverage_radius_km: Number(coverageRadiusInput.value) || 2.5 };
    if (t === "facility_add" || t === "facility_relocate") {
      if (!scenarioLocationLatLng) {
        toast("Pick a facility location on the map first", "error");
        return;
      }
      payload.facility_location = [scenarioLocationLatLng.lat, scenarioLocationLatLng.lng];
      payload.facility_type = facilityTypeInput.value.trim() || "GENERIC";
    }
    if (t === "facility_remove" || t === "facility_relocate") {
      const id = Number(existingFacilitySelect.value);
      if (!id) {
        toast("Choose an existing facility first", "error");
        return;
      }
      payload.facility_id = id;
    }

    analysisPanel.showLoading("Running scenario comparison…");
    try {
      const res = await api.simulateScenario(payload);
      const nodes = [el("p", { class: "scenario-summary" }, res.summary)];

      if (res.route_change) {
        const rc = res.route_change;
        nodes.push(
          el("div", { class: "panel-subsection" }, [
            el("h3", {}, "Route change"),
            statRow("Distance (km)", rc.distance_km_before, rc.distance_km_after, (v) => (v == null ? "–" : v.toFixed(2))),
            statRow("ETA (min)", rc.eta_minutes_before, rc.eta_minutes_after, (v) => (v == null ? "–" : v.toFixed(1))),
          ])
        );
      }
      if (res.coverage_change) {
        const cc = res.coverage_change;
        nodes.push(
          el("div", { class: "panel-subsection" }, [
            el("h3", {}, "Coverage change"),
            statRow("Population covered", cc.population_covered_before, cc.population_covered_after, (v) => Math.round(v).toLocaleString()),
            statRow("Coverage fraction", cc.coverage_fraction_before, cc.coverage_fraction_after, fmtPct),
          ])
        );
      }
      analysisPanel.setNodes(nodes);
      toast("Scenario simulated", "success");
    } catch (err) {
      analysisPanel.showError(err.message, () => runScenario({ scenarioType, facilityTypeInput, existingFacilitySelect, coverageRadiusInput }));
    }
  }
}

export function leavePlanning() {
  unsubscribeLive && unsubscribeLive();
  unsubscribeLive = null;
  if (map) {
    map.remove();
    map = null;
  }
  groups = {};
  clickMode = null;
  originLatLng = destinationLatLng = null;
  originMarker = destinationMarker = null;
  closureEdge = null;
  scenarioLocationLatLng = null;
  facilitiesCache = null;
  lastHoverTs = 0;
  hoverToken = 0;
  networkMode = false;
  allEdgesCache = null;
  edgeLayerIndex = {};
  selectedNetworkEdge = null;
  blockedEdges = {};
}
