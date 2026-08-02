import { el, icon, clear, emptyState, toast, badge } from "../ui.js";
import { api } from "../api.js";
import { store } from "../state.js";

function uid() {
  return `sess_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

function newSession() {
  return { id: uid(), title: "New conversation", createdAt: new Date().toISOString(), messages: [] };
}

export function renderCopilot(root) {
  const page = el("div", { class: "page page-copilot" });
  page.appendChild(
    el("div", { class: "page-header" }, [
      el("div", {}, [el("h1", {}, "Copilot"), el("p", { class: "muted" }, "Natural-language access to routing, facility recommendation, closures, and incidents.")]),
    ])
  );

  let sessions = { ...store.get("copilotSessions") };
  let activeId = store.get("copilotActiveSession");
  if (!activeId || !sessions[activeId]) {
    const s = newSession();
    sessions[s.id] = s;
    activeId = s.id;
    persist();
  }

  const layout = el("div", { class: "copilot-layout" });
  const historyPanel = el("div", { class: "panel copilot-history" }, [
    el("div", { class: "panel-header" }, [el("h2", {}, "Conversations"), el("button", { class: "icon-btn", onclick: startNew, title: "New conversation" }, icon("plus"))]),
    el("div", { class: "history-list", id: "history-list" }),
  ]);

  const chatPanel = el("div", { class: "panel copilot-chat" }, [
    el("div", { class: "copilot-log", id: "copilot-log" }),
    el("form", { class: "copilot-input-row", id: "copilot-form" }, [
      el("input", { type: "text", id: "copilot-input", class: "input", placeholder: "Ask CityMind: e.g. \"route from 19.07,72.87 to 19.10,72.90\"", autocomplete: "off" }),
      el("button", { class: "btn btn-primary", type: "submit" }, [icon("send")]),
    ]),
  ]);

  layout.appendChild(historyPanel);
  layout.appendChild(chatPanel);
  page.appendChild(layout);
  root.appendChild(page);

  function persist() {
    store.saveCopilotSessions(sessions, activeId);
  }

  function renderHistory() {
    const list = document.getElementById("history-list");
    clear(list);
    const ordered = Object.values(sessions).sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt));
    if (!ordered.length) {
      list.appendChild(emptyState("No conversations yet", "Start chatting to build history."));
      return;
    }
    ordered.forEach((s) => {
      const row = el("div", { class: "history-item" + (s.id === activeId ? " active" : "") }, [
        el("div", { class: "history-item-main", onclick: () => switchSession(s.id) }, [
          el("strong", {}, s.title || "Conversation"),
          el("span", { class: "muted small" }, `${s.messages.length} message(s)`),
        ]),
        el("button", { class: "icon-btn", onclick: () => deleteSession(s.id), title: "Delete" }, icon("trash")),
      ]);
      list.appendChild(row);
    });
  }

  function switchSession(id) {
    activeId = id;
    persist();
    renderHistory();
    renderLog();
  }

  function startNew() {
    const s = newSession();
    sessions[s.id] = s;
    activeId = s.id;
    persist();
    renderHistory();
    renderLog();
  }

  function deleteSession(id) {
    delete sessions[id];
    if (activeId === id) {
      const remaining = Object.values(sessions);
      if (remaining.length) {
        activeId = remaining[0].id;
      } else {
        const s = newSession();
        sessions[s.id] = s;
        activeId = s.id;
      }
    }
    persist();
    renderHistory();
    renderLog();
  }

  function currentSession() {
    return sessions[activeId];
  }

  function toolCallsNode(toolCalls) {
    if (!toolCalls || !toolCalls.length) return null;
    const wrap = el("details", { class: "tool-calls" }, [
      el("summary", {}, `${toolCalls.length} tool call(s)`),
    ]);
    toolCalls.forEach((tc) => {
      wrap.appendChild(
        el("div", { class: "tool-call" }, [
          badge(tc.tool, tc.error ? "danger" : "neutral"),
          el("pre", {}, JSON.stringify(tc.arguments, null, 2)),
          tc.error ? el("p", { class: "text-error small" }, tc.error) : null,
        ].filter(Boolean))
      );
    });
    return wrap;
  }

  function renderLog() {
    const log = document.getElementById("copilot-log");
    clear(log);
    const session = currentSession();
    if (!session || !session.messages.length) {
      log.appendChild(emptyState("Ask CityMind anything", "Try: \"what's the best route from 19.07,72.87 to 19.10,72.90?\""));
      return;
    }
    session.messages.forEach((m) => {
      const bubble = el("div", { class: `copilot-msg ${m.role}` }, [
        el("div", { class: "copilot-avatar" }, icon(m.role === "user" ? "user" : "bot")),
        el("div", { class: "copilot-bubble" }, [
          m.intent ? badge(m.intent, "neutral") : null,
          el("p", {}, m.text),
          m.toolCalls ? toolCallsNode(m.toolCalls) : null,
          m.warnings && m.warnings.length ? el("p", { class: "text-warn small" }, m.warnings.join(" · ")) : null,
        ].filter(Boolean)),
      ]);
      log.appendChild(bubble);
    });
    log.scrollTop = log.scrollHeight;
  }

  function addMessage(role, text, extra = {}) {
    const session = currentSession();
    session.messages.push({ role, text, ...extra });
    if (role === "user" && session.messages.length === 1) {
      session.title = text.slice(0, 42) + (text.length > 42 ? "…" : "");
    }
    persist();
    renderHistory();
    renderLog();
  }

  document.getElementById("copilot-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = document.getElementById("copilot-input");
    const message = input.value.trim();
    if (!message) return;
    addMessage("user", message);
    input.value = "";

    const log = document.getElementById("copilot-log");
    const typing = el("div", { class: "copilot-msg assistant typing" }, [
      el("div", { class: "copilot-avatar" }, icon("bot")),
      el("div", { class: "copilot-bubble" }, [el("span", { class: "typing-dots" }, [el("i"), el("i"), el("i")])]),
    ]);
    log.appendChild(typing);
    log.scrollTop = log.scrollHeight;

    try {
      const res = await api.copilotChat(message, activeId);
      typing.remove();
      addMessage("assistant", res.reply, { intent: res.intent, toolCalls: res.tool_calls, warnings: res.warnings });
    } catch (err) {
      typing.remove();
      addMessage("assistant", `Error: ${err.message}`);
      toast(`Copilot request failed: ${err.message}`, "error");
    }
  });

  renderHistory();
  renderLog();
}
