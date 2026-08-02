import { el, icon, toast, loadingState, errorState, clear, badge, fmtPct } from "../ui.js";
import { api } from "../api.js";
import { onLiveEvent } from "../ws.js";

const LAYER_COLORS = {
  population: "#5b8cff",
  accidents: "#ff5d5d",
  closures: "#ffb545",
  facilities: "#33d17a",
  incidents: "#f472b6",
  road_suggestions: "#22d3ee",
  route: "#a78bfa",
  closure_hover: "#fde047",
  closure_selected: "#f59e0b",
  closure_closed: "#ef4444",
  closure_before: "#3b82f6",
  closure_after: "#22c55e",
  facility_recommendation: "#a855f7",
  blocked_road: "#ef4444",
};

// Human-readable legend labels — keys mirror LAYER_COLORS above.
const LAYER_LABELS = {
  population: "Population density",
  accidents: "Accident hotspots",
  closures: "Closure impact",
  facilities: "Facilities",
  incidents: "Recorded incidents",
  road_suggestions: "Road-gap suggestions",
  route: "Planned route",
  closure_hover: "Road (hover preview)",
  closure_selected: "Road (selected)",
  closure_closed: "Road (closed)",
  closure_before: "Route before closure",
  closure_after: "Route after closure",
  facility_recommendation: "Facility recommendation",
  blocked_road: "Blocked road (persistent)",
};

let map = null;
let groups = {};
let clickMode = null; // "origin" | "destination" | "closure_edge" | null
let originLatLng = null;
let destinationLatLng = null;
let originMarker = null;
let destinationMarker = null;
let closureEdge = null; // { u, v, geometry } — the road segment selected for closure
let unsubscribeLive = null;
let lastHoverTs = 0;
let hoverToken = 0;

// -- Block Road (persistent closures) ----------------------------------------
// A road blocked here is removed from routing for every user until it is
// unblocked or all closures are cleared - unlike `closureEdge` above, which
// only stages a one-off "Simulate Closure" comparison.
let blockedEdges = {}; // "u-v" -> { node_u, node_v, geometry }

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

function setGroupVisible(name, visible) {
  const g = groups[name];
  if (!g) return;
  if (visible && !map.hasLayer(g)) map.addLayer(g);
  if (!visible && map.hasLayer(g)) map.removeLayer(g);
}

export function renderMap(root) {
  const page = el("div", { class: "page page-map" });

  const header = el("div", { class: "page-header" }, [
    el("div", {}, [el("h1", {}, "Map"), el("p", { class: "muted" }, "Accidents, closures, facilities, routing, and network-gap suggestions.")]),
    el("div", { class: "page-actions" }, [
      el("button", { class: "btn btn-secondary", id: "map-refresh" }, [icon("refresh"), " Refresh layers"]),
    ]),
  ]);
  page.appendChild(header);

  const layout = el("div", { class: "map-layout" });

  const controls = el("div", { class: "map-controls panel" }, [
    el("div", { class: "panel-header" }, [
      icon("layers"),
      el("h2", {}, "Layers"),
      el("div", { class: "layers-header-actions" }, [
        el("button", { class: "btn btn-tiny", id: "layers-show-all", type: "button" }, "Show all"),
        el("button", { class: "btn btn-tiny", id: "layers-hide-all", type: "button" }, "Hide all"),
      ]),
    ]),
    layerToggle("population", "Population density", true),
    layerToggle("accidents", "Accident hotspots", true),
    layerToggle("closures", "Closure impact", true),
    layerToggle("facilities", "Facilities", true),
    layerToggle("incidents", "Recorded incidents", true),
    layerToggle("road_suggestions", "Road-gap suggestions", false),

    el("div", { class: "panel-header", style: "margin-top:16px" }, [el("h2", {}, "Route planner")]),
    el("p", { class: "muted small" }, "Click a mode below, then click the map."),
    el("div", { class: "route-btns" }, [
      el("button", { class: "btn btn-tiny", id: "set-origin" }, "Set origin"),
      el("button", { class: "btn btn-tiny", id: "set-destination" }, "Set destination"),
      el("button", { class: "btn btn-tiny", id: "clear-route" }, "Clear"),
    ]),
    el("div", { id: "route-status", class: "muted small" }, "No points selected."),
    el("button", { class: "btn btn-primary btn-block", id: "compute-route" }, "Compute route"),
    el("div", { id: "route-result", class: "route-result" }),

    el("div", { class: "panel-header", style: "margin-top:16px" }, [el("h2", {}, "Road suggestions")]),
    el("button", { class: "btn btn-secondary btn-block", id: "fetch-suggestions" }, "Suggest missing roads"),
    el("div", { id: "suggestions-result" }),

    el("div", { class: "panel-header", style: "margin-top:16px" }, [icon("hospital"), el("h2", {}, "Facility recommendation")]),
    el("label", { class: "form-field" }, [
      el("span", {}, "Facility type"),
      el("select", { class: "input", id: "facility-reco-type" }, [el("option", { value: "" }, "Loading types…")]),
    ]),
    el("label", { class: "toggle-row" }, [
      el("input", { type: "checkbox", checked: "checked", id: "facility-heatmap-toggle" }),
      el("span", { class: "legend-dot", style: `background:${LAYER_COLORS.population}` }),
      el("span", {}, "Population heatmap"),
    ]),
    el("label", { class: "toggle-row" }, [
      el("input", { type: "checkbox", checked: "checked", id: "facility-radius-toggle" }),
      el("span", { class: "legend-dot", style: `background:${LAYER_COLORS.facility_recommendation}` }),
      el("span", {}, "Coverage radius"),
    ]),
    el("button", { class: "btn btn-primary btn-block", id: "find-best-location-btn" }, "Find Best Location"),
    el("div", { id: "facility-reco-result" }),

    el("div", { class: "panel-header", style: "margin-top:16px" }, [icon("road"), el("h2", {}, "Road closure")]),
    el("p", { class: "muted small" }, "Uses the origin/destination set above. Click \"Select road to close\", hover to preview, then click a road below."),
    el("div", { class: "route-btns" }, [
      el("button", { class: "btn btn-tiny", id: "closure-select-btn" }, "Select road to close"),
      el("button", { class: "btn btn-tiny", id: "closure-clear-btn" }, "Clear selection"),
    ]),
    el("div", { id: "closure-status", class: "muted small" }, "No road selected."),
    el("p", { id: "closure-hint", class: "muted small", style: "color:var(--warn); display:none" }, "Set an origin and destination above to enable simulation."),
    el("button", { class: "btn btn-primary btn-block", id: "simulate-closure-btn", style: "display:none" }, "Simulate Closure"),
    el("div", { id: "closure-result", class: "route-result" }),

    el("h3", { class: "muted small", style: "text-transform:uppercase;letter-spacing:.04em;margin:12px 0 0" }, "Block Road"),
    el("p", { class: "muted small" }, "Select a road above, then block it — blocked roads are removed from routing for everyone until unblocked."),
    el("div", { class: "route-btns" }, [
      el("button", { class: "btn btn-tiny btn-danger", id: "block-road-btn" }, "Block Road"),
      el("button", { class: "btn btn-tiny", id: "unblock-road-btn" }, "Unblock Road"),
    ]),
    el("div", { class: "route-btns" }, [
      el("button", { class: "btn btn-tiny", id: "clear-closures-btn" }, "Clear All Closures"),
    ]),
    el("div", { id: "closures-status", class: "muted small" }, "0 roads currently blocked."),

    el("div", { class: "panel-header", style: "margin-top:16px" }, [el("h2", {}, "Legend")]),
    el("div", { class: "map-legend", id: "map-legend" }),
  ]);

  const mapWrap = el("div", { class: "map-wrap panel" }, [el("div", { id: "map-canvas" })]);

  layout.appendChild(controls);
  layout.appendChild(mapWrap);
  page.appendChild(layout);
  root.appendChild(page);

  function layerToggle(key, label, checked) {
    return el("label", { class: "toggle-row" }, [
      el("input", { type: "checkbox", checked: checked ? "checked" : undefined, "data-layer": key, onchange: onLayerToggle }),
      el("span", { class: "legend-dot", style: `background:${LAYER_COLORS[key]}` }),
      el("span", {}, label),
    ]);
  }

  buildLegend();

  function buildLegend() {
    const legend = document.getElementById("map-legend");
    clear(legend);
    Object.entries(LAYER_COLORS).forEach(([key, color]) => {
      legend.appendChild(
        el("div", { class: "chart-legend-item" }, [el("span", { class: "legend-dot", style: `background:${color}` }), el("span", {}, LAYER_LABELS[key] || key.replace(/_/g, " "))])
      );
    });
  }

  function buildLegendInto(container) {
    Object.entries(LAYER_COLORS).forEach(([key, color]) => {
      container.appendChild(
        el("div", { class: "chart-legend-item" }, [el("span", { class: "legend-dot", style: `background:${color}` }), el("span", {}, LAYER_LABELS[key] || key.replace(/_/g, " "))])
      );
    });
  }

  // -- map init --------------------------------------------------------
  requestAnimationFrame(() => {
    map = L.map("map-canvas", { zoomControl: true }).setView([19.076, 72.877], 12);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; OpenStreetMap contributors",
      maxZoom: 18,
    }).addTo(map);
    map.on("click", onMapClick);
    map.on("mousemove", onMapHover);
    map.on("mouseout", () => clearGroup("closure_hover"));
    addFullscreenControl();
    addLegendControl();
    loadHeatmapLayers();
    loadFacilities();
    loadIncidentsLayer();
    loadFacilityRecoTypes();
    loadBlockedRoads();
  });

  // -- fullscreen support --------------------------------------------------------
  function addFullscreenControl() {
    const FullscreenControl = L.Control.extend({
      options: { position: "topright" },
      onAdd: function () {
        const btn = L.DomUtil.create("button", "map-fullscreen-btn leaflet-bar");
        btn.type = "button";
        btn.title = "Toggle fullscreen";
        btn.setAttribute("aria-label", "Toggle fullscreen");
        btn.innerHTML = fullscreenIconSvg(false);
        L.DomEvent.disableClickPropagation(btn);
        L.DomEvent.on(btn, "click", () => toggleFullscreen(btn));
        return btn;
      },
    });
    map.addControl(new FullscreenControl());

    document.addEventListener("fullscreenchange", () => {
      const active = document.fullscreenElement === mapWrap;
      mapWrap.classList.toggle("map-fullscreen-active", active);
      const btn = mapWrap.querySelector(".map-fullscreen-btn");
      if (btn) btn.innerHTML = fullscreenIconSvg(active);
      setTimeout(() => map && map.invalidateSize(), 120);
    });
  }

  function fullscreenIconSvg(active) {
    return active
      ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16"><path d="M9 3H5a2 2 0 0 0-2 2v4M15 3h4a2 2 0 0 1 2 2v4M9 21H5a2 2 0 0 1-2-2v-4M15 21h4a2 2 0 0 0 2-2v-4" stroke-linecap="round" stroke-linejoin="round"/></svg>'
      : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16"><path d="M3 9V5a2 2 0 0 1 2-2h4M15 3h4a2 2 0 0 1 2 2v4M21 15v4a2 2 0 0 1-2 2h-4M9 21H5a2 2 0 0 1-2-2v-4" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  }

  function toggleFullscreen() {
    if (!document.fullscreenElement) {
      const req = mapWrap.requestFullscreen || mapWrap.webkitRequestFullscreen;
      if (req) req.call(mapWrap);
      else toast("Fullscreen isn't supported in this browser", "error");
    } else {
      (document.exitFullscreen || document.webkitExitFullscreen)?.call(document);
    }
  }

  // -- on-map legend control --------------------------------------------------------
  function addLegendControl() {
    const LegendControl = L.Control.extend({
      options: { position: "bottomleft" },
      onAdd: function () {
        const box = L.DomUtil.create("div", "map-legend-control collapsed");
        const heading = L.DomUtil.create("h4", "", box);
        heading.innerHTML = 'Legend <span class="legend-caret">▸</span>';
        const body = L.DomUtil.create("div", "chart-legend", box);
        buildLegendInto(body);
        L.DomEvent.disableClickPropagation(box);
        L.DomEvent.on(heading, "click", () => {
          box.classList.toggle("collapsed");
          heading.querySelector(".legend-caret").textContent = box.classList.contains("collapsed") ? "▸" : "▾";
        });
        return box;
      },
    });
    map.addControl(new LegendControl());
  }

  async function loadFacilityRecoTypes() {
    const select = document.getElementById("facility-reco-type");
    try {
      const res = await api.facilityTypes();
      clear(select);
      res.facility_types.forEach((ft) => {
        select.appendChild(el("option", { value: ft.value, style: `color:${ft.color}` }, `${ft.label} (min spacing ${ft.min_spacing_km}km)`));
      });
    } catch {
      clear(select);
      select.appendChild(el("option", { value: "hospital" }, "hospital"));
    }
  }

  function onLayerToggle(e) {
    const key = e.target.getAttribute("data-layer");
    if (!e.target.checked) {
      clearGroup(key);
      return;
    }
    if (key === "population" || key === "accidents" || key === "closures") loadHeatmapLayers();
    if (key === "facilities") loadFacilities();
    if (key === "incidents") loadIncidentsLayer();
    if (key === "road_suggestions") fetchSuggestions();
  }

  function activeHeatmapLayers() {
    return ["population", "accidents", "closures"].filter(
      (k) => controls.querySelector(`[data-layer="${k}"]`).checked
    );
  }

  async function loadHeatmapLayers() {
    const layers = activeHeatmapLayers();
    ["population", "accidents", "closures"].forEach(clearGroup);
    if (!layers.length) return;
    try {
      const data = await api.heatmap(layers, 500);
      for (const [name, collection] of Object.entries(data.layers)) {
        const group = ensureGroup(name);
        collection.features.forEach((feature) => {
          const [lon, lat] = feature.geometry.coordinates;
          L.circleMarker([lat, lon], { radius: 5, color: LAYER_COLORS[name], weight: 1, fillOpacity: 0.65 })
            .bindPopup(`<b>${name}</b><br/>${Object.entries(feature.properties).map(([k, v]) => `${k}: ${v}`).join("<br/>")}`)
            .addTo(group);
        });
      }
    } catch (err) {
      toast(`Heatmap unavailable: ${err.message}`, "error");
    }
  }

  async function loadFacilities() {
    if (!controls.querySelector('[data-layer="facilities"]').checked) return;
    clearGroup("facilities");
    try {
      const page = await api.dashboardFacilities({ page: 1, page_size: 200 });
      const group = ensureGroup("facilities");
      page.items.forEach((f) => {
        L.circleMarker([f.lat, f.lon], { radius: 7, color: LAYER_COLORS.facilities, weight: 2, fillOpacity: 0.5 })
          .bindPopup(`<b>${f.name || f.facility_type}</b><br/>type: ${f.facility_type}<br/>status: ${f.status}<br/>cost: ${f.cost.toFixed(1)}`)
          .addTo(group);
      });
    } catch (err) {
      console.warn("facilities layer failed", err.message);
    }
  }

  async function loadIncidentsLayer() {
    if (!controls.querySelector('[data-layer="incidents"]').checked) return;
    clearGroup("incidents");
    try {
      const page = await api.dashboardIncidents({ page: 1, page_size: 100 });
      const group = ensureGroup("incidents");
      page.items.forEach((inc) => {
        if (inc.lat == null || inc.lng == null) return;
        L.circleMarker([inc.lat, inc.lng], { radius: 6, color: LAYER_COLORS.incidents, weight: 2, fillOpacity: 0.5 })
          .bindPopup(`<b>${inc.incident_type}</b><br/>camera: ${inc.camera_id}<br/>status: ${inc.status}`)
          .addTo(group);
      });
    } catch (err) {
      console.warn("incidents layer failed", err.message);
    }
  }

  // -- routing --------------------------------------------------------
  function onMapClick(e) {
    if (clickMode === "origin") {
      originLatLng = e.latlng;
      if (originMarker) map.removeLayer(originMarker);
      originMarker = L.marker(e.latlng, { title: "Origin" }).addTo(map);
      clickMode = null;
    } else if (clickMode === "destination") {
      destinationLatLng = e.latlng;
      if (destinationMarker) map.removeLayer(destinationMarker);
      destinationMarker = L.marker(e.latlng, { title: "Destination" }).addTo(map);
      clickMode = null;
    } else if (clickMode === "closure_edge") {
      selectClosureEdge(e.latlng);
      clickMode = null;
    }
    updateRouteStatus();
  }

  // -- hover preview for the road about to be selected for closure --------
  function onMapHover(e) {
    if (clickMode !== "closure_edge") return;
    const now = performance.now();
    if (now - lastHoverTs < 120) return; // throttle backend calls
    lastHoverTs = now;
    const token = ++hoverToken;
    api
      .nearestEdge(e.latlng.lat, e.latlng.lng)
      .then((res) => {
        if (token !== hoverToken || clickMode !== "closure_edge") return; // stale / mode changed meanwhile
        clearGroup("closure_hover");
        const group = ensureGroup("closure_hover");
        L.polyline(res.geometry, {
          color: LAYER_COLORS.closure_hover,
          weight: 5,
          opacity: 0.85,
          className: "hover-path",
        }).addTo(group);
      })
      .catch(() => {});
  }

  function updateRouteStatus() {
    const el2 = document.getElementById("route-status");
    const o = originLatLng ? `${originLatLng.lat.toFixed(4)}, ${originLatLng.lng.toFixed(4)}` : "not set";
    const d = destinationLatLng ? `${destinationLatLng.lat.toFixed(4)}, ${destinationLatLng.lng.toFixed(4)}` : "not set";
    el2.textContent = `Origin: ${o} · Destination: ${d}`;
    updateClosureUI();
  }

  // -- road closure (map-first: hover to preview, click a road, no coordinates typed) --------
  async function selectClosureEdge(latlng) {
    try {
      const res = await api.nearestEdge(latlng.lat, latlng.lng);
      closureEdge = { u: res.node_u, v: res.node_v, geometry: res.geometry };
      clearGroup("closure_hover");
      clearGroup("closure_selected");
      clearGroup("closure_closed");
      clearGroup("closure_before");
      clearGroup("closure_after");
      clear(document.getElementById("closure-result"));
      const group = ensureGroup("closure_selected");
      L.polyline(closureEdge.geometry, {
        color: LAYER_COLORS.closure_selected,
        weight: 6,
        dashArray: "8 8",
        className: "pulse-path",
      }).addTo(group);
      updateClosureUI();
      toast("Road segment selected — ready to simulate", "success");
    } catch (err) {
      toast(`Could not select a road there: ${err.message}`, "error");
    }
  }

  function updateClosureUI() {
    const statusEl = document.getElementById("closure-status");
    const hintEl = document.getElementById("closure-hint");
    const simBtn = document.getElementById("simulate-closure-btn");
    const blockBtn = document.getElementById("block-road-btn");
    const unblockBtn = document.getElementById("unblock-road-btn");
    const hasEdge = !!closureEdge;
    const hasRoute = !!(originLatLng && destinationLatLng);
    const isBlocked = hasEdge && isEdgeBlocked(closureEdge.u, closureEdge.v);

    if (statusEl) {
      statusEl.textContent = hasEdge
        ? `Selected road: OSM edge ${closureEdge.u} → ${closureEdge.v}${isBlocked ? " — currently blocked" : ""}`
        : "No road selected.";
      statusEl.classList.toggle("status-blocked", isBlocked);
    }
    if (hintEl) hintEl.style.display = hasEdge && !hasRoute ? "block" : "none";
    if (simBtn) simBtn.style.display = hasEdge && hasRoute ? "block" : "none";
    if (blockBtn) blockBtn.disabled = !hasEdge || isBlocked;
    if (unblockBtn) unblockBtn.disabled = !hasEdge || !isBlocked;
  }

  // ==========================================================================
  // Block Road — persistent closures. A blocked road is removed from every
  // routing calculation until it is unblocked or all closures are cleared,
  // and is always shown in red on the map.
  // ==========================================================================
  async function loadBlockedRoads() {
    try {
      const res = await api.closedRoads();
      blockedEdges = {};
      (res.blocked_edges || []).forEach((e) => {
        blockedEdges[edgeKey(e.node_u, e.node_v)] = e;
      });
      renderBlockedRoadsLayer();
      updateClosuresStatus();
      updateClosureUI();
    } catch (err) {
      console.warn("could not load blocked roads", err.message);
    }
  }

  function renderBlockedRoadsLayer() {
    if (!map) return;
    clearGroup("blocked_road");
    const group = ensureGroup("blocked_road");
    Object.values(blockedEdges).forEach((e) => {
      L.polyline(e.geometry, {
        color: LAYER_COLORS.blocked_road,
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
          updateClosureUI();
          toast("Blocked road selected — click Unblock Road to reopen it", "info");
        })
        .addTo(group);
    });
  }

  function updateClosuresStatus() {
    const statusEl = document.getElementById("closures-status");
    const clearBtn = document.getElementById("clear-closures-btn");
    const count = Object.keys(blockedEdges).length;
    if (statusEl) statusEl.textContent = `${count} road${count === 1 ? "" : "s"} currently blocked.`;
    if (clearBtn) clearBtn.disabled = count === 0;
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
      updateClosuresStatus();
      updateClosureUI();
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
      updateClosuresStatus();
      updateClosureUI();
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
      updateClosuresStatus();
      updateClosureUI();
      toast("All road closures cleared", "success");
    } catch (err) {
      toast(`Could not clear closures: ${err.message}`, "error");
    }
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

  // Draws a polyline that traces itself in over `duration` ms (optionally
  // after `startDelay` ms), instead of snapping in instantly — this is the
  // "route drawing itself" transition used for before/after closure routes,
  // and for the closed-road segment itself.
  function animatedPolyline(latlngs, options, group, startDelay = 0, duration = 700) {
    const line = L.polyline([], options).addTo(group);
    const total = latlngs.length;
    if (total < 2) {
      line.setLatLngs(latlngs);
      return line;
    }
    const start = performance.now() + startDelay;
    function step(now) {
      const elapsed = now - start;
      if (elapsed < 0) {
        requestAnimationFrame(step);
        return;
      }
      const t = Math.min(1, elapsed / duration);
      const count = Math.max(2, Math.round(t * total));
      line.setLatLngs(latlngs.slice(0, count));
      if (t < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
    return line;
  }

  // Small marker that travels along `latlngs` over `duration` ms — used to
  // read as "traffic rerouting" onto the new alternate path once it's drawn.
  function animateMarkerAlong(latlngs, group, startDelay = 0, duration = 900) {
    if (!latlngs || latlngs.length < 2) return;
    const marker = L.circleMarker(latlngs[0], {
      radius: 5,
      color: "#ffffff",
      weight: 2,
      fillColor: LAYER_COLORS.closure_after,
      fillOpacity: 1,
      className: "reroute-marker",
    }).addTo(group);
    const total = latlngs.length;
    const start = performance.now() + startDelay;
    function step(now) {
      const elapsed = now - start;
      if (elapsed < 0) {
        requestAnimationFrame(step);
        return;
      }
      const t = Math.min(1, elapsed / duration);
      const idx = Math.min(total - 1, Math.floor(t * (total - 1)));
      marker.setLatLng(latlngs[idx]);
      if (t < 1) requestAnimationFrame(step);
      else setTimeout(() => group.removeLayer(marker), 350);
    }
    requestAnimationFrame(step);
  }

  async function simulateClosure() {
    const resultBox = document.getElementById("closure-result");
    if (!closureEdge || !originLatLng || !destinationLatLng) {
      toast("Select a road, plus an origin and destination, first", "error");
      return;
    }
    clear(resultBox);
    resultBox.appendChild(loadingState("Simulating road closure…"));
    try {
      const res = await api.simulateScenario({
        scenario_type: "road_closure",
        origin: [originLatLng.lat, originLatLng.lng],
        destination: [destinationLatLng.lat, destinationLatLng.lng],
        edge_to_close: [closureEdge.u, closureEdge.v],
        coverage_radius_km: 2.5,
      });
      clear(resultBox);
      renderClosureResult(res);
      toast("Closure simulated", "success");
    } catch (err) {
      clear(resultBox);
      resultBox.appendChild(errorState(err.message, simulateClosure));
    }
  }

  function renderClosureResult(res) {
    const resultBox = document.getElementById("closure-result");
    clearGroup("closure_hover");
    clearGroup("closure_selected");
    clearGroup("closure_closed");
    clearGroup("closure_before");
    clearGroup("closure_after");

    // The closed road segment itself: traces in quickly, then holds a red
    // pulsing glow so it visibly reads as "now closed".
    const closedGroup = ensureGroup("closure_closed");
    animatedPolyline(closureEdge.geometry, { color: LAYER_COLORS.closure_closed, weight: 6, className: "closure-flash" }, closedGroup, 0, 450);

    const rc = res.route_change;
    const cc = res.coverage_change;

    resultBox.appendChild(el("p", { class: "scenario-summary" }, res.summary));

    if (!rc || rc.status !== "OK") {
      resultBox.appendChild(
        el("div", { class: "result-card" }, [
          el("div", { class: "result-card-head" }, [el("strong", {}, "No alternative route"), badge("Critical", "danger")]),
        ])
      );
      map.fitBounds(L.polyline(closureEdge.geometry).getBounds(), { padding: [60, 60] });
      return;
    }

    // Original route (blue) draws first, then the alternate route (green)
    // traces in right after — a smooth before/after transition — followed by
    // a small marker that travels the new route, reading as "rerouting".
    const beforeGroup = ensureGroup("closure_before");
    const afterGroup = ensureGroup("closure_after");
    animatedPolyline(rc.route_coordinates_before, { color: LAYER_COLORS.closure_before, weight: 5 }, beforeGroup, 0, 700);
    animatedPolyline(rc.route_coordinates_after, { color: LAYER_COLORS.closure_after, weight: 5, dashArray: "10 6" }, afterGroup, 500, 700);
    animateMarkerAlong(rc.route_coordinates_after, afterGroup, 1250, 900);

    const bounds = L.latLngBounds([...rc.route_coordinates_before, ...rc.route_coordinates_after, ...closureEdge.geometry]);
    map.fitBounds(bounds, { padding: [40, 40] });

    const severity = severityFor(rc, cc);

    resultBox.appendChild(
      el("div", { class: "result-card" }, [
        el("div", { class: "result-card-head" }, [el("strong", {}, "Closure impact"), badge(severity.label, severity.kind)]),
        el("div", { class: "result-card-body" }, [
          el("div", { class: "result-stat" }, [el("span", {}, "Original distance"), el("strong", {}, `${rc.distance_km_before.toFixed(2)} km`)]),
          el("div", { class: "result-stat" }, [el("span", {}, "New distance"), el("strong", {}, `${rc.distance_km_after.toFixed(2)} km`)]),
          el("div", { class: "result-stat" }, [el("span", {}, "Added distance"), el("strong", {}, `${rc.delay_km >= 0 ? "+" : ""}${rc.delay_km.toFixed(2)} km`)]),
          el("div", { class: "result-stat" }, [el("span", {}, "Estimated delay"), el("strong", {}, `${rc.eta_delay_minutes >= 0 ? "+" : ""}${rc.eta_delay_minutes.toFixed(1)} min`)]),
        ]),
      ])
    );

    if (cc) {
      resultBox.appendChild(
        el("div", { class: "panel-subsection" }, [
          el("h3", {}, "Citywide coverage impact"),
          el(
            "p",
            { class: "muted small" },
            `Facility coverage ${cc.coverage_fraction_delta <= 0 ? "drops" : "rises"} from ${fmtPct(cc.coverage_fraction_before)} to ${fmtPct(cc.coverage_fraction_after)}.`
          ),
        ])
      );
    }
  }

  document.addEventListener("click", (e) => {
    if (e.target.id === "set-origin") {
      clickMode = "origin";
      toast("Click the map to place the origin", "info");
    }
    if (e.target.id === "set-destination") {
      clickMode = "destination";
      toast("Click the map to place the destination", "info");
    }
    if (e.target.id === "clear-route") {
      originLatLng = destinationLatLng = null;
      if (originMarker) map.removeLayer(originMarker);
      if (destinationMarker) map.removeLayer(destinationMarker);
      clearGroup("route");
      document.getElementById("route-result").innerHTML = "";
      updateRouteStatus();
    }
    if (e.target.id === "compute-route" || e.target.closest?.("#compute-route")) computeRoute();
    if (e.target.id === "fetch-suggestions" || e.target.closest?.("#fetch-suggestions")) fetchSuggestions();
    if (e.target.id === "map-refresh" || e.target.closest?.("#map-refresh")) {
      loadHeatmapLayers();
      loadFacilities();
      loadIncidentsLayer();
    }
    if (e.target.id === "closure-select-btn" || e.target.closest?.("#closure-select-btn")) {
      clickMode = "closure_edge";
      toast("Move over the map to preview roads, click one to select it for closure", "info");
    }
    if (e.target.id === "closure-clear-btn" || e.target.closest?.("#closure-clear-btn")) {
      closureEdge = null;
      clearGroup("closure_hover");
      clearGroup("closure_selected");
      clearGroup("closure_closed");
      clearGroup("closure_before");
      clearGroup("closure_after");
      clear(document.getElementById("closure-result"));
      updateClosureUI();
    }
    if (e.target.id === "simulate-closure-btn" || e.target.closest?.("#simulate-closure-btn")) simulateClosure();
    if (e.target.id === "block-road-btn" || e.target.closest?.("#block-road-btn")) blockCurrentRoad();
    if (e.target.id === "unblock-road-btn" || e.target.closest?.("#unblock-road-btn")) unblockCurrentRoad();
    if (e.target.id === "clear-closures-btn" || e.target.closest?.("#clear-closures-btn")) clearAllClosures();
    if (e.target.id === "find-best-location-btn" || e.target.closest?.("#find-best-location-btn")) findBestLocation();
    if (e.target.id === "layers-show-all") setAllLayers(true);
    if (e.target.id === "layers-hide-all") setAllLayers(false);
  });

  document.getElementById("facility-radius-toggle").addEventListener("change", (e) => {
    setGroupVisible("facility_coverage_radius", e.target.checked);
  });
  document.getElementById("facility-heatmap-toggle").addEventListener("change", (e) => {
    const popCheckbox = controls.querySelector('[data-layer="population"]');
    if (e.target.checked) {
      if (popCheckbox && !popCheckbox.checked) popCheckbox.checked = true;
      loadHeatmapLayers();
    } else {
      clearGroup("population");
    }
  });

  function setAllLayers(visible) {
    controls.querySelectorAll("[data-layer]").forEach((cb) => {
      if (cb.checked === visible) return;
      cb.checked = visible;
      cb.dispatchEvent(new Event("change"));
    });
  }

  // -- facility recommendation --------------------------------------------------------
  async function findBestLocation() {
    const box = document.getElementById("facility-reco-result");
    const typeSelect = document.getElementById("facility-reco-type");
    const facilityType = typeSelect.value || "hospital";
    clear(box);
    box.appendChild(loadingState("Evaluating candidate sites…"));
    clearGroup("facility_recommendation");
    clearGroup("facility_coverage_radius");
    try {
      const res = await api.recommendFacility({ facility_type: facilityType, persist: false });
      clear(box);
      if (res.status === "no_valid_site") {
        box.appendChild(el("p", { class: "muted small" }, res.detail || "No valid site found for this facility type."));
        return;
      }
      renderFacilityRecommendation(res);
      toast("Facility recommendation ready", "success");
    } catch (err) {
      clear(box);
      box.appendChild(errorState(err.message, findBestLocation));
    }
  }

  // Animates a circle marker's radius growing in with an ease-out curve,
  // instead of the marker just appearing at full size.
  function animatedCircleMarker(latlng, targetRadius, options, group, duration = 500) {
    const marker = L.circleMarker(latlng, { ...options, radius: 0 }).addTo(group);
    const start = performance.now();
    function step(now) {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      marker.setRadius(Math.max(0.1, targetRadius * eased));
      if (t < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
    return marker;
  }

  // A brief expanding, fading ring — a "ping" that draws the eye to the
  // spot a facility marker is being placed on.
  function placementPing(latlng, color, group, duration = 900, maxRadiusM = 900) {
    const ring = L.circle(latlng, { radius: 30, color, weight: 2, fillOpacity: 0, opacity: 0.9, className: "facility-ping" }).addTo(group);
    const start = performance.now();
    function step(now) {
      const t = Math.min(1, (now - start) / duration);
      ring.setRadius(30 + t * maxRadiusM);
      ring.setStyle({ opacity: 0.9 * (1 - t) });
      if (t < 1) requestAnimationFrame(step);
      else group.removeLayer(ring);
    }
    requestAnimationFrame(step);
  }

  function renderFacilityRecommendation(res) {
    const box = document.getElementById("facility-reco-result");
    const group = ensureGroup("facility_recommendation");
    const radiusGroup = ensureGroup("facility_coverage_radius");
    const color = res.color || LAYER_COLORS.facility_recommendation;
    const radiusKm = res.min_spacing_km || 2.5;
    const latlng = [res.lat, res.lon];

    // Animated marker placement: a ping ring plus the marker growing in.
    placementPing(latlng, color, group);
    animatedCircleMarker(latlng, 10, { color, weight: 3, fillOpacity: 0.85 }, group).bindPopup(`<b>Recommended ${res.label || res.facility_type}</b>`);

    // Coverage radius circle lives in its own group so it can be toggled
    // independently of the recommendation marker itself.
    L.circle(latlng, { radius: radiusKm * 1000, color, weight: 1, fillOpacity: 0.08, dashArray: "4 4" }).addTo(radiusGroup);
    const radiusToggle = document.getElementById("facility-radius-toggle");
    if (radiusToggle && !radiusToggle.checked) setGroupVisible("facility_coverage_radius", false);

    // Nearby existing facilities of the same type
    (res.nearby_facilities || []).forEach((f) => {
      L.circleMarker([f.lat, f.lon], { radius: 5, color: "#94a3b8", weight: 1, fillOpacity: 0.5 })
        .bindPopup(`Existing ${res.label || res.facility_type}<br/>${f.distance_km.toFixed(2)} km away`)
        .addTo(group);
    });

    // Respect the facility panel's own heatmap toggle for turning on
    // population density context (defaults on).
    const heatmapToggle = document.getElementById("facility-heatmap-toggle");
    const popCheckbox = controls.querySelector('[data-layer="population"]');
    if (heatmapToggle && heatmapToggle.checked && popCheckbox && !popCheckbox.checked) {
      popCheckbox.checked = true;
      loadHeatmapLayers();
    }

    map.setView([res.lat, res.lon], Math.max(map.getZoom(), 14));

    const explanationText =
      res.coverage_improvement_pct != null && res.population_served != null
        ? `This location is recommended because population density is high and current coverage is low. It brings roughly ${Math.round(res.population_served).toLocaleString()} people into coverage, ${res.coverage_improvement_pct.toFixed(0)}% of whom are not currently served by a nearby ${(res.label || res.facility_type || "").toLowerCase()}.`
        : "This location minimizes population-weighted access cost across the demand area.";

    box.appendChild(
      el("div", { class: "result-card" }, [
        el("div", { class: "result-card-head" }, [el("strong", {}, res.label || res.facility_type), badge("Preview only", "neutral")]),
        el("div", { class: "result-card-body" }, [
          el("div", { class: "result-stat" }, [el("span", {}, "Coverage radius"), el("strong", {}, `${radiusKm} km`)]),
          el("div", { class: "result-stat" }, [el("span", {}, "Nearby existing facilities"), el("strong", {}, String((res.nearby_facilities || []).length))]),
          el("div", { class: "result-stat" }, [el("span", {}, "Coverage improvement"), el("strong", {}, res.coverage_improvement_pct != null ? `${res.coverage_improvement_pct.toFixed(0)}%` : "–")]),
          el("div", { class: "result-stat" }, [el("span", {}, "Population served"), el("strong", {}, res.population_served != null ? Math.round(res.population_served).toLocaleString() : "–")]),
          el("div", { class: "result-stat" }, [el("span", {}, "Confidence score"), el("strong", {}, res.confidence_score != null ? `${Math.round(res.confidence_score * 100)}%` : "–")]),
        ]),
        el("p", { class: "muted small facility-reco-explanation" }, explanationText),
        el("details", { class: "facility-reco-coords" }, [
          el("summary", {}, "Coordinates"),
          el("p", { class: "muted small" }, `${res.lat.toFixed(5)}, ${res.lon.toFixed(5)}`),
        ]),
      ])
    );
  }

  async function computeRoute() {
    const resultBox = document.getElementById("route-result");
    if (!originLatLng || !destinationLatLng) {
      toast("Set both an origin and destination first", "error");
      return;
    }
    clear(resultBox);
    resultBox.appendChild(loadingState("Computing route…"));
    try {
      const res = await api.computeRoute([originLatLng.lat, originLatLng.lng], [destinationLatLng.lat, destinationLatLng.lng]);
      clearGroup("route");
      const group = ensureGroup("route");
      const line = L.polyline(res.route_coordinates, { color: LAYER_COLORS.route, weight: 4 }).addTo(group);
      map.fitBounds(line.getBounds(), { padding: [30, 30] });
      clear(resultBox);
      resultBox.appendChild(el("p", {}, [el("strong", {}, `${res.distance_km.toFixed(2)} km`), " shortest population-aware route"]));
    } catch (err) {
      clear(resultBox);
      resultBox.appendChild(el("p", { class: "text-error small" }, err.message));
    }
  }

  async function fetchSuggestions() {
    const box = document.getElementById("suggestions-result");
    clear(box);
    box.appendChild(loadingState("Scanning road network…"));
    clearGroup("road_suggestions");
    try {
      const res = await api.suggestRoads({ sample_size: 150, min_straight_line_m: 200, max_straight_line_m: 2000, top_n: 8, persist: false });
      clear(box);
      const group = ensureGroup("road_suggestions");
      if (!res.suggestions.length) {
        box.appendChild(el("p", { class: "muted small" }, "No high-impact road gaps found."));
        return;
      }
      res.suggestions.forEach((s, i) => {
        L.polyline([s.point_u, s.point_v], { color: LAYER_COLORS.road_suggestions, weight: 3, dashArray: "6 6" })
          .bindPopup(`Gap ratio ${s.gap_ratio.toFixed(2)}<br/>Saves ${(s.distance_saved_m / 1000).toFixed(2)} km<br/>Impact score ${s.impact_score.toFixed(1)}`)
          .addTo(group);
        box.appendChild(
          el("div", { class: "suggestion-row" }, [
            badge(`#${i + 1}`, "neutral"),
            el("span", {}, `Impact ${s.impact_score.toFixed(1)} · saves ${(s.distance_saved_m / 1000).toFixed(2)} km`),
          ])
        );
      });
      const layerCheckbox = controls.querySelector('[data-layer="road_suggestions"]');
      layerCheckbox.checked = true;
    } catch (err) {
      clear(box);
      box.appendChild(el("p", { class: "text-error small" }, err.message));
    }
  }

  unsubscribeLive = onLiveEvent((type, data) => {
    if (type === "incident.closed" || type === "dashboard.invalidated") {
      loadIncidentsLayer();
      loadFacilities();
    }
    if (type === "dashboard.invalidated" && ["road_blocked", "road_unblocked", "closures_cleared"].includes(data?.reason)) {
      loadBlockedRoads();
    }
  });
}

export function leaveMap() {
  unsubscribeLive && unsubscribeLive();
  if (map) {
    map.remove();
    map = null;
  }
  groups = {};
  clickMode = null;
  originLatLng = destinationLatLng = null;
  originMarker = destinationMarker = null;
  closureEdge = null;
  blockedEdges = {};
  lastHoverTs = 0;
  hoverToken = 0;
}
