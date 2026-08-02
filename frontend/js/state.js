// state.js — minimal pub/sub store. No framework: pages subscribe to the
// keys they care about and re-render themselves.

const DEFAULT_API_BASE = (() => {
  const { protocol, hostname } = window.location;
  return `${protocol}//${hostname}:9000`;
})();

function loadJSON(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

class Store {
  constructor() {
    this._data = {
      apiBase: localStorage.getItem("citymind.apiBase") || DEFAULT_API_BASE,
      theme: localStorage.getItem("citymind.theme") || "dark",
      wsStatus: "connecting",
      wsLabel: "connecting…",
      feed: [],
      feedTotal: 0,
      route: "overview",
      refreshIntervalSec: Number(localStorage.getItem("citymind.refreshInterval") || 30),
      // local-only operational workflow layered on top of backend ACTIVE/CLOSED status
      // (the backend models only ACTIVE/CLOSED; acknowledge/investigate/resolve are
      // dashboard-side triage state, tracked per incident_id in localStorage)
      incidentWorkflow: loadJSON("citymind.incidentWorkflow", {}),
      copilotSessions: loadJSON("citymind.copilotSessions", {}),
      copilotActiveSession: localStorage.getItem("citymind.copilotActiveSession") || null,
    };
    this._listeners = new Map();
  }

  get(key) {
    return this._data[key];
  }

  set(key, value) {
    this._data[key] = value;
    this._emit(key, value);
  }

  update(key, updater) {
    this.set(key, updater(this._data[key]));
  }

  subscribe(key, fn) {
    if (!this._listeners.has(key)) this._listeners.set(key, new Set());
    this._listeners.get(key).add(fn);
    return () => this._listeners.get(key)?.delete(fn);
  }

  _emit(key, value) {
    this._listeners.get(key)?.forEach((fn) => fn(value));
    this._listeners.get("*")?.forEach((fn) => fn(key, value));
  }

  // -- persisted setters ------------------------------------------------------
  setApiBase(value) {
    localStorage.setItem("citymind.apiBase", value);
    this.set("apiBase", value);
  }

  setTheme(value) {
    localStorage.setItem("citymind.theme", value);
    this.set("theme", value);
  }

  setRefreshInterval(sec) {
    localStorage.setItem("citymind.refreshInterval", String(sec));
    this.set("refreshIntervalSec", sec);
  }

  setIncidentWorkflow(incidentId, status) {
    const map = { ...this._data.incidentWorkflow, [incidentId]: status };
    localStorage.setItem("citymind.incidentWorkflow", JSON.stringify(map));
    this.set("incidentWorkflow", map);
  }

  clearIncidentWorkflow() {
    localStorage.removeItem("citymind.incidentWorkflow");
    this.set("incidentWorkflow", {});
  }

  saveCopilotSessions(sessions, activeId) {
    localStorage.setItem("citymind.copilotSessions", JSON.stringify(sessions));
    if (activeId) localStorage.setItem("citymind.copilotActiveSession", activeId);
    this.set("copilotSessions", sessions);
    this.set("copilotActiveSession", activeId ?? this._data.copilotActiveSession);
  }

  clearCopilotSessions() {
    localStorage.removeItem("citymind.copilotSessions");
    localStorage.removeItem("citymind.copilotActiveSession");
    this.set("copilotSessions", {});
    this.set("copilotActiveSession", null);
  }

  pushFeedItem(item) {
    const feed = [item, ...this._data.feed].slice(0, 100);
    this._data.feedTotal += 1;
    this.set("feed", feed);
    this.set("feedTotal", this._data.feedTotal);
  }
}

export const store = new Store();
export { DEFAULT_API_BASE };
