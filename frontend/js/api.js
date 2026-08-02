// api.js — thin REST client for citymind-backend. Every call is a plain
// fetch wrapper; no framework, no build step (loaded as a native ES module).
import { store } from "./state.js";

class ApiError extends Error {
  constructor(message, status, data) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

function base() {
  return store.get("apiBase").replace(/\/$/, "");
}

async function request(path, { method = "GET", body, isForm = false, query } = {}) {
  let url = `${base()}${path}`;
  if (query) {
    const qs = new URLSearchParams(
      Object.entries(query).filter(([, v]) => v !== undefined && v !== null && v !== "")
    ).toString();
    if (qs) url += `?${qs}`;
  }
  let res;
  try {
    res = await fetch(url, {
      method,
      headers: body && !isForm ? { "Content-Type": "application/json" } : undefined,
      body: body ? (isForm ? body : JSON.stringify(body)) : undefined,
    });
  } catch (err) {
    throw new ApiError(`Network error reaching ${path}: ${err.message}`, 0, null);
  }
  const text = await res.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { raw: text };
    }
  }
  if (!res.ok) {
    const detail = data && data.detail ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : res.statusText;
    throw new ApiError(detail || `HTTP ${res.status}`, res.status, data);
  }
  return data;
}

export const api = {
  ApiError,
  // -- core / health / detection ------------------------------------------------
  health: () => request("/health"),
  detectImage: (file, cameraId) => {
    const form = new FormData();
    form.append("file", file);
    return request("/detect", { method: "POST", body: form, isForm: true, query: { camera_id: cameraId || undefined } });
  },
  aggregateImage: (file, cameraId) => {
    const form = new FormData();
    form.append("file", file);
    return request("/aggregate/image", { method: "POST", body: form, isForm: true, query: { camera_id: cameraId || undefined } });
  },
  aggregateVideo: (file, cameraId, maxFrames) => {
    const form = new FormData();
    form.append("file", file);
    return request("/aggregate/video", {
      method: "POST",
      body: form,
      isForm: true,
      query: { camera_id: cameraId || undefined, max_frames: maxFrames || undefined },
    });
  },

  // -- dashboard ------------------------------------------------------------
  dashboardSummary: () => request("/dashboard/summary"),
  dashboardIncidents: (params) => request("/dashboard/incidents", { query: params }),
  dashboardFacilities: (params) => request("/dashboard/facilities", { query: params }),
  dashboardCongestion: (params) => request("/dashboard/congestion", { query: params }),
  dashboardRoutes: (params) => request("/dashboard/routes", { query: params }),

  // -- heatmap ------------------------------------------------------------
  heatmap: (layers, limit) => request("/heatmap", { query: { layers: layers.join(","), limit } }),

  // -- city logic ------------------------------------------------------------
  computeRoute: (origin, destination) => request("/city/route", { method: "POST", body: { origin, destination } }),
  simulateClosure: (origin, destination, edge) =>
    request("/city/simulate-closure", { method: "POST", body: { origin, destination, edge_to_close: edge } }),
  nearestEdge: (lat, lon) => request("/city/nearest-edge", { method: "POST", body: { lat, lon } }),
  cityEdges: () => request("/city/edges"),
  blockRoad: (edge) => request("/city/block-road", { method: "POST", body: { edge } }),
  unblockRoad: (edge) => request("/city/unblock-road", { method: "POST", body: { edge } }),
  clearClosures: () => request("/city/clear-closures", { method: "POST" }),
  closedRoads: () => request("/city/closed-roads"),
  recommendFacility: (payload) => request("/city/recommend-facility", { method: "POST", body: payload }),
  facilityTypes: () => request("/city/facility-types"),
  suggestRoads: (payload) => request("/city/suggest-roads", { method: "POST", body: payload }),

  // -- scenario ------------------------------------------------------------
  simulateScenario: (payload) => request("/simulate/scenario", { method: "POST", body: payload }),

  // -- copilot ------------------------------------------------------------
  copilotChat: (message, sessionId, context) =>
    request("/copilot/chat", { method: "POST", body: { message, session_id: sessionId, context } }),
  copilotTools: () => request("/copilot/tools"),
};
