import { el, icon, fmtDate, fmtDuration, fmtPct, badge, loadingState, emptyState, errorState, clear, debounce, modal, toast } from "../ui.js";
import { api } from "../api.js";
import { store } from "../state.js";
import { openDetectionModal } from "../components/detection.js";
import { onLiveEvent } from "../ws.js";

const WORKFLOW_STAGES = ["NEW", "ACKNOWLEDGED", "INVESTIGATING", "RESOLVED"];
const WORKFLOW_KIND = { NEW: "neutral", ACKNOWLEDGED: "warn", INVESTIGATING: "warn", RESOLVED: "success" };

let sortKey = "start_time";
let sortDir = "desc";
let unsubscribeLive = null;

function workflowOf(incidentId) {
  return store.get("incidentWorkflow")[incidentId] || "NEW";
}

function workflowBadge(incidentId) {
  const stage = workflowOf(incidentId);
  return badge(stage, WORKFLOW_KIND[stage]);
}

export function renderIncidents(root) {
  const page = el("div", { class: "page" });

  const filters = {
    status: "",
    camera_id: "",
    incident_type: "",
    emergency_vehicle_present: "",
    start_after: "",
    start_before: "",
  };
  let pageNum = 1;
  let pageSize = 20;
  let lastPage = null;

  const header = el("div", { class: "page-header" }, [
    el("div", {}, [el("h1", {}, "Incidents"), el("p", { class: "muted" }, "Search, triage, and act on every recorded incident.")]),
    el("div", { class: "page-actions" }, [
      el("button", { class: "btn btn-primary", onclick: () => openDetectionModal({ onComplete: load }) }, [icon("plus"), " New detection"]),
    ]),
  ]);
  page.appendChild(header);

  const filterBar = el("div", { class: "panel filter-bar" });
  page.appendChild(filterBar);

  const tablePanel = el("div", { class: "panel" });
  const tableHeader = el("div", { class: "panel-header" }, [el("h2", {}, "Incident list"), el("span", { class: "muted small", id: "incident-count" }, "")]);
  const tableBody = el("div", { class: "table-scroll" }, [loadingState("Loading incidents…")]);
  const pager = el("div", { class: "pager" });
  tablePanel.appendChild(tableHeader);
  tablePanel.appendChild(tableBody);
  tablePanel.appendChild(pager);
  page.appendChild(tablePanel);

  root.appendChild(page);

  // -- filter bar --------------------------------------------------------
  function textInput(placeholder, key) {
    const input = el("input", {
      class: "input",
      placeholder,
      oninput: debounce((e) => {
        filters[key] = e.target.value.trim();
        pageNum = 1;
        load();
      }, 350),
    });
    return input;
  }
  function selectInput(options, key) {
    const select = el(
      "select",
      {
        class: "input",
        onchange: (e) => {
          filters[key] = e.target.value;
          pageNum = 1;
          load();
        },
      },
      options.map((o) => el("option", { value: o.value }, o.label))
    );
    return select;
  }

  filterBar.appendChild(
    el("div", { class: "filter-row" }, [
      selectInput(
        [
          { value: "", label: "All statuses" },
          { value: "ACTIVE", label: "Active" },
          { value: "CLOSED", label: "Closed" },
        ],
        "status"
      ),
      textInput("Camera ID", "camera_id"),
      textInput("Incident type", "incident_type"),
      selectInput(
        [
          { value: "", label: "Emergency vehicle: any" },
          { value: "true", label: "Emergency vehicle: yes" },
          { value: "false", label: "Emergency vehicle: no" },
        ],
        "emergency_vehicle_present"
      ),
      el("label", { class: "filter-date" }, [
        "From",
        el("input", { type: "datetime-local", class: "input", onchange: (e) => { filters.start_after = e.target.value ? new Date(e.target.value).toISOString() : ""; pageNum = 1; load(); } }),
      ]),
      el("label", { class: "filter-date" }, [
        "To",
        el("input", { type: "datetime-local", class: "input", onchange: (e) => { filters.start_before = e.target.value ? new Date(e.target.value).toISOString() : ""; pageNum = 1; load(); } }),
      ]),
      el("button", { class: "btn btn-secondary", onclick: () => { Object.keys(filters).forEach((k) => (filters[k] = "")); pageNum = 1; load(); filterBar.querySelectorAll("input,select").forEach((i) => (i.value = "")); } }, "Clear"),
    ])
  );

  function sortHeaderCell(label, key) {
    const cell = el("th", {
      class: "sortable" + (sortKey === key ? " sorted" : ""),
      onclick: () => {
        if (sortKey === key) sortDir = sortDir === "asc" ? "desc" : "asc";
        else {
          sortKey = key;
          sortDir = "desc";
        }
        renderTable(lastPage);
      },
    }, [label, sortKey === key ? el("span", { class: "sort-caret" }, sortDir === "asc" ? "▲" : "▼") : null].filter(Boolean));
    return cell;
  }

  function sortedItems(items) {
    const copy = [...items];
    copy.sort((a, b) => {
      let av = a[sortKey];
      let bv = b[sortKey];
      if (sortKey === "start_time") {
        av = new Date(av).getTime();
        bv = new Date(bv).getTime();
      }
      if (av === bv) return 0;
      const cmp = av > bv ? 1 : -1;
      return sortDir === "asc" ? cmp : -cmp;
    });
    return copy;
  }

  function renderTable(pageData) {
    clear(tableBody);
    if (!pageData || !pageData.items.length) {
      tableBody.appendChild(emptyState("No incidents found", "Try widening your filters, or run a new detection."));
      pager.innerHTML = "";
      document.getElementById("incident-count").textContent = "";
      return;
    }
    document.getElementById("incident-count").textContent = `${pageData.total_items} total`;

    const table = el("table", { class: "data-table" });
    const thead = el("thead", {}, [
      el("tr", {}, [
        sortHeaderCell("Camera", "camera_id"),
        sortHeaderCell("Status", "status"),
        el("th", {}, "Workflow"),
        sortHeaderCell("Start", "start_time"),
        sortHeaderCell("Duration", "duration_seconds"),
        sortHeaderCell("Confidence", "max_confidence"),
        el("th", {}, "Emergency"),
        el("th", {}, "Actions"),
      ]),
    ]);
    const tbody = el("tbody");
    sortedItems(pageData.items).forEach((inc) => {
      const tr = el("tr", { class: "clickable-row", onclick: () => openDetail(inc) }, [
        el("td", {}, inc.camera_id),
        el("td", {}, badge(inc.status, inc.status === "ACTIVE" ? "warn" : "neutral")),
        el("td", { id: `wf-${inc.incident_id}` }, workflowBadge(inc.incident_id)),
        el("td", {}, fmtDate(inc.start_time)),
        el("td", {}, fmtDuration(inc.duration_seconds)),
        el("td", {}, fmtPct(inc.max_confidence)),
        el("td", {}, inc.emergency_vehicle_present ? badge("Yes", "danger") : "–"),
        el("td", { onclick: (e) => e.stopPropagation() }, actionCell(inc)),
      ]);
      tbody.appendChild(tr);
    });
    table.appendChild(thead);
    table.appendChild(tbody);
    tableBody.appendChild(table);

    renderPager(pageData);
  }

  function actionCell(inc) {
    const wrap = el("div", { class: "row-actions" });
    const stage = workflowOf(inc.incident_id);
    const btn = (label, next) =>
      el("button", { class: "btn btn-tiny", disabled: stage === next ? "disabled" : undefined, onclick: () => setWorkflow(inc, next) }, label);
    wrap.appendChild(btn("Acknowledge", "ACKNOWLEDGED"));
    wrap.appendChild(btn("Investigate", "INVESTIGATING"));
    wrap.appendChild(btn("Resolve", "RESOLVED"));
    return wrap;
  }

  function setWorkflow(inc, stage) {
    store.setIncidentWorkflow(inc.incident_id, stage);
    const cell = document.getElementById(`wf-${inc.incident_id}`);
    if (cell) {
      clear(cell);
      cell.appendChild(workflowBadge(inc.incident_id));
    }
    toast(`Incident ${inc.incident_id.slice(0, 8)} marked ${stage.toLowerCase()}`, "success");
    if (lastPage) renderTable(lastPage);
  }

  function renderPager(pageData) {
    clear(pager);
    pager.appendChild(
      el("button", { class: "btn btn-secondary", disabled: !pageData.has_previous ? "disabled" : undefined, onclick: () => { pageNum -= 1; load(); } }, "Previous")
    );
    pager.appendChild(el("span", { class: "muted" }, `Page ${pageData.page} of ${Math.max(pageData.total_pages, 1)}`));
    pager.appendChild(
      el("button", { class: "btn btn-secondary", disabled: !pageData.has_next ? "disabled" : undefined, onclick: () => { pageNum += 1; load(); } }, "Next")
    );
    pager.appendChild(
      el(
        "select",
        {
          class: "input page-size-select",
          onchange: (e) => {
            pageSize = Number(e.target.value);
            pageNum = 1;
            load();
          },
        },
        [10, 20, 50, 100].map((n) => el("option", { value: n, selected: n === pageSize ? "selected" : undefined }, `${n} / page`))
      )
    );
  }

  function openDetail(inc) {
    const stage = workflowOf(inc.incident_id);
    const body = el("div", { class: "incident-detail" }, [
      el("div", { class: "detail-grid" }, [
        detailRow("Incident ID", inc.incident_id),
        detailRow("Type", inc.incident_type),
        detailRow("Camera", inc.camera_id),
        detailRow("Road", inc.road_id || "–"),
        detailRow("Location", inc.lat && inc.lng ? `${inc.lat.toFixed(5)}, ${inc.lng.toFixed(5)}` : "–"),
        detailRow("Status (backend)", inc.status),
        detailRow("Workflow", stage),
        detailRow("Start", fmtDate(inc.start_time)),
        detailRow("Last seen", fmtDate(inc.last_seen_time)),
        detailRow("End", inc.end_time ? fmtDate(inc.end_time) : "–"),
        detailRow("Duration", fmtDuration(inc.duration_seconds)),
        detailRow("Frame count", inc.frame_count),
        detailRow("Max confidence", fmtPct(inc.max_confidence)),
        detailRow("Avg confidence", fmtPct(inc.avg_confidence)),
        detailRow("Peak vehicle count", inc.peak_vehicle_count),
        detailRow("Traffic density at peak", inc.traffic_density_at_peak),
        detailRow("Emergency vehicle present", inc.emergency_vehicle_present ? "Yes" : "No"),
      ]),
      el("div", { class: "detail-actions" }, [
        el("button", { class: "btn btn-secondary", onclick: () => setWorkflow(inc, "ACKNOWLEDGED") }, "Acknowledge"),
        el("button", { class: "btn btn-secondary", onclick: () => setWorkflow(inc, "INVESTIGATING") }, "Investigate"),
        el("button", { class: "btn btn-primary", onclick: () => setWorkflow(inc, "RESOLVED") }, "Mark resolved"),
      ]),
    ]);
    modal(`Incident ${inc.incident_id.slice(0, 8)}`, body);
  }

  function detailRow(label, value) {
    return el("div", { class: "detail-row" }, [el("span", { class: "muted" }, label), el("strong", {}, String(value))]);
  }

  async function load() {
    clear(tableBody);
    tableBody.appendChild(loadingState("Loading incidents…"));
    try {
      const params = {
        page: pageNum,
        page_size: pageSize,
        status: filters.status || undefined,
        camera_id: filters.camera_id || undefined,
        incident_type: filters.incident_type || undefined,
        emergency_vehicle_present: filters.emergency_vehicle_present || undefined,
        start_after: filters.start_after || undefined,
        start_before: filters.start_before || undefined,
      };
      const pageData = await api.dashboardIncidents(params);
      lastPage = pageData;
      renderTable(pageData);
    } catch (err) {
      clear(tableBody);
      tableBody.appendChild(errorState(err.message, load));
    }
  }

  load();
  unsubscribeLive = onLiveEvent((type) => {
    if (type === "incident.closed" || type === "dashboard.invalidated") load();
  });
}

export function leaveIncidents() {
  unsubscribeLive && unsubscribeLive();
}
