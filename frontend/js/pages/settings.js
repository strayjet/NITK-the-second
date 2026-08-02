import { el, icon, toast, clear, badge, loadingState } from "../ui.js";
import { api } from "../api.js";
import { store } from "../state.js";
import { connectWebSocket } from "../ws.js";

export function renderSettings(root) {
  const page = el("div", { class: "page" });
  page.appendChild(
    el("div", { class: "page-header" }, [
      el("div", {}, [el("h1", {}, "Settings"), el("p", { class: "muted" }, "Connection, appearance, and local data.")]),
    ])
  );

  const grid = el("div", { class: "grid-2" });

  // -- connection --------------------------------------------------------
  const apiInput = el("input", { type: "text", class: "input", value: store.get("apiBase") });
  const healthBox = el("div", { class: "health-box" });

  async function checkHealth() {
    clear(healthBox);
    healthBox.appendChild(loadingState("Checking backend health…"));
    try {
      const h = await api.health();
      clear(healthBox);
      healthBox.appendChild(
        el("div", { class: "health-grid" }, [
          el("div", { class: "detail-row" }, [el("span", { class: "muted" }, "Service"), badge(h.status, h.status === "ok" ? "success" : "warn")]),
          el("div", { class: "detail-row" }, [el("span", { class: "muted" }, "Service name"), el("strong", {}, h.service)]),
          el("div", { class: "detail-row" }, [el("span", { class: "muted" }, "YOLO service reachable"), badge(h.yolo_service_reachable ? "Yes" : "No", h.yolo_service_reachable ? "success" : "danger")]),
          h.yolo_service ? el("div", { class: "detail-row" }, [el("span", { class: "muted" }, "YOLO model loaded"), badge(h.yolo_service.yolo_model_loaded ? "Yes" : "No", h.yolo_service.yolo_model_loaded ? "success" : "danger")]) : null,
          h.yolo_service ? el("div", { class: "detail-row" }, [el("span", { class: "muted" }, "Accident model loaded"), badge(h.yolo_service.accident_model_loaded ? "Yes" : "No", h.yolo_service.accident_model_loaded ? "success" : "danger")]) : null,
          h.yolo_service_error ? el("div", { class: "detail-row" }, [el("span", { class: "muted" }, "Error"), el("strong", { class: "text-error" }, h.yolo_service_error)]) : null,
        ].filter(Boolean))
      );
    } catch (err) {
      clear(healthBox);
      healthBox.appendChild(el("p", { class: "text-error" }, `Unable to reach backend: ${err.message}`));
    }
  }

  const connectionPanel = el("div", { class: "panel" }, [
    el("div", { class: "panel-header" }, [el("h2", {}, "Backend connection")]),
    el("div", { class: "form-grid" }, [el("label", { class: "form-field" }, [el("span", {}, "API base URL"), apiInput])]),
    el("div", { class: "row-actions", style: "margin:12px 0" }, [
      el(
        "button",
        {
          class: "btn btn-primary",
          onclick: () => {
            const val = apiInput.value.trim().replace(/\/$/, "");
            if (!val) return;
            store.setApiBase(val);
            connectWebSocket();
            checkHealth();
            toast("API connection updated", "success");
          },
        },
        "Connect"
      ),
      el("button", { class: "btn btn-secondary", onclick: checkHealth }, [icon("refresh"), " Check health"]),
    ]),
    healthBox,
  ]);

  // -- appearance --------------------------------------------------------
  const themeSelect = el(
    "select",
    {
      class: "input",
      onchange: (e) => {
        store.setTheme(e.target.value);
        toast(`Theme set to ${e.target.value}`, "success");
      },
    },
    [
      el("option", { value: "dark", selected: store.get("theme") === "dark" ? "selected" : undefined }, "Dark"),
      el("option", { value: "light", selected: store.get("theme") === "light" ? "selected" : undefined }, "Light"),
    ]
  );

  const refreshInput = el("input", { type: "number", class: "input", min: "10", value: String(store.get("refreshIntervalSec")) });

  const appearancePanel = el("div", { class: "panel" }, [
    el("div", { class: "panel-header" }, [el("h2", {}, "Appearance & behavior")]),
    el("div", { class: "form-grid" }, [
      el("label", { class: "form-field" }, [el("span", {}, "Theme"), themeSelect]),
      el("label", { class: "form-field" }, [
        el("span", {}, "Overview refresh interval (seconds)"),
        refreshInput,
      ]),
    ]),
    el(
      "button",
      {
        class: "btn btn-secondary",
        onclick: () => {
          store.setRefreshInterval(Math.max(10, Number(refreshInput.value) || 30));
          toast("Refresh interval saved", "success");
        },
      },
      "Save"
    ),
  ]);

  grid.appendChild(connectionPanel);
  grid.appendChild(appearancePanel);
  page.appendChild(grid);

  // -- facility catalog --------------------------------------------------------
  const catalogPanel = el("div", { class: "panel" }, [
    el("div", { class: "panel-header" }, [el("h2", {}, "Facility catalog")]),
    el("div", { id: "catalog-box" }, [loadingState("Loading facility types…")]),
  ]);
  page.appendChild(catalogPanel);

  api
    .facilityTypes()
    .then((res) => {
      const box = document.getElementById("catalog-box");
      clear(box);
      const table = el(
        "table",
        { class: "data-table" },
        [
          el("thead", {}, [el("tr", {}, [el("th", {}, "Value"), el("th", {}, "Label"), el("th", {}, "Color"), el("th", {}, "Min spacing (km)")])]),
          el(
            "tbody",
            {},
            res.facility_types.map((ft) =>
              el("tr", {}, [
                el("td", {}, ft.value),
                el("td", {}, ft.label),
                el("td", {}, [el("span", { class: "legend-dot", style: `background:${ft.color}` }), ` ${ft.color}`]),
                el("td", {}, String(ft.min_spacing_km)),
              ])
            )
          ),
        ]
      );
      box.appendChild(table);
    })
    .catch((err) => {
      const box = document.getElementById("catalog-box");
      clear(box);
      box.appendChild(el("p", { class: "text-error" }, `Unable to load facility catalog: ${err.message}`));
    });

  // -- local data --------------------------------------------------------
  const dataPanel = el("div", { class: "panel" }, [
    el("div", { class: "panel-header" }, [el("h2", {}, "Local dashboard data")]),
    el("p", { class: "muted small" }, "Incident triage status and copilot conversation history are stored in this browser only."),
    el("div", { class: "row-actions" }, [
      el(
        "button",
        {
          class: "btn btn-secondary",
          onclick: () => {
            store.clearIncidentWorkflow();
            toast("Incident triage state cleared", "success");
          },
        },
        "Clear incident triage state"
      ),
      el(
        "button",
        {
          class: "btn btn-secondary",
          onclick: () => {
            store.clearCopilotSessions();
            toast("Copilot conversation history cleared", "success");
          },
        },
        "Clear copilot history"
      ),
    ]),
  ]);
  page.appendChild(dataPanel);

  root.appendChild(page);
  checkHealth();
}
