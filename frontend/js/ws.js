// ws.js — manages the single /ws/live connection and re-broadcasts events
// into the store's feed + a lightweight event bus pages can subscribe to.
import { store } from "./state.js";

const listeners = new Set();
let socket = null;
let retryTimer = null;

function wsUrlFor(apiBase) {
  const url = new URL(apiBase);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = "/ws/live";
  return url.toString();
}

export function onLiveEvent(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function connectWebSocket() {
  if (socket) {
    try {
      socket.onclose = null;
      socket.close();
    } catch {}
  }
  clearTimeout(retryTimer);
  store.set("wsStatus", "connecting");
  store.set("wsLabel", "connecting…");

  let ws;
  try {
    ws = new WebSocket(wsUrlFor(store.get("apiBase")));
  } catch {
    store.set("wsStatus", "offline");
    store.set("wsLabel", "invalid API URL");
    return;
  }
  socket = ws;

  ws.onopen = () => {
    store.set("wsStatus", "online");
    store.set("wsLabel", "Live");
  };
  ws.onclose = () => {
    store.set("wsStatus", "offline");
    store.set("wsLabel", "Reconnecting…");
    retryTimer = setTimeout(() => {
      if (socket === ws) connectWebSocket();
    }, 3000);
  };
  ws.onerror = () => {
    store.set("wsStatus", "offline");
    store.set("wsLabel", "Connection error");
  };
  ws.onmessage = (evt) => {
    let msg;
    try {
      msg = JSON.parse(evt.data);
    } catch {
      return;
    }
    if (msg.type === "connected") return;
    store.pushFeedItem({ type: msg.type, data: msg.data, time: new Date() });
    listeners.forEach((fn) => fn(msg.type, msg.data));
  };
}
