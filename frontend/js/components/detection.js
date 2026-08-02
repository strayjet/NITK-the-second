import { el, icon, modal, toast, fmtDuration, fmtPct, badge, clear } from "../ui.js";
import { api } from "../api.js";

function densityBadge(density) {
  const kind = density === "HIGH" ? "danger" : density === "MEDIUM" ? "warn" : "neutral";
  return badge(`${density} traffic`, kind);
}

function incidentCard(incident) {
  const confidencePct = Math.round((incident.max_confidence || 0) * 100);
  return el("div", { class: "result-card" }, [
    el("div", { class: "result-card-head" }, [
      el("strong", {}, incident.incident_type || "ACCIDENT"),
      badge(incident.status, incident.status === "ACTIVE" ? "warn" : "neutral"),
      incident.emergency_vehicle_present ? badge("Emergency vehicle", "danger") : null,
    ]),
    el("div", { class: "result-card-body" }, [
      el("div", { class: "result-stat" }, [el("span", {}, "Camera"), el("strong", {}, incident.camera_id)]),
      el("div", { class: "result-stat" }, [el("span", {}, "Duration"), el("strong", {}, fmtDuration(incident.duration_seconds))]),
      el("div", { class: "result-stat" }, [el("span", {}, "Peak vehicles"), el("strong", {}, String(incident.peak_vehicle_count ?? 0))]),
      el("div", { class: "result-stat" }, [el("span", {}, "Frames"), el("strong", {}, String(incident.frame_count ?? 0))]),
    ]),
    el("div", { class: "confidence-bar" }, [
      el("div", { class: "confidence-fill", style: `width:${confidencePct}%` }),
    ]),
    el("div", { class: "result-card-foot" }, [
      el("span", { class: "muted" }, `Confidence ${fmtPct(incident.max_confidence)}`),
      densityBadge(incident.traffic_density_at_peak || "LOW"),
    ]),
  ].filter(Boolean));
}

function aggregationSummary(result, container) {
  clear(container);
  const closed = result.incidents || [];
  const active = result.active_incidents || [];
  container.appendChild(
    el("div", { class: "detect-summary" }, [
      el("span", {}, `${result.frames_processed} frame(s) processed`),
      el("span", {}, `${closed.length} incident(s) closed`),
      el("span", {}, `${active.length} still active`),
    ])
  );
  if (!closed.length && !active.length) {
    container.appendChild(el("p", { class: "muted" }, "No accident-level incidents were detected in this upload."));
    return;
  }
  const grid = el("div", { class: "result-grid" });
  [...closed, ...active].forEach((inc) => grid.appendChild(incidentCard(inc)));
  container.appendChild(grid);
}

function bboxColor(label) {
  const map = { car: "#5b8cff", truck: "#ffb545", bus: "#22d3ee", motorcycle: "#a78bfa", bicycle: "#33d17a", person: "#f472b6" };
  return map[label?.toLowerCase()] || "#ff5d5d";
}

function drawQuickPreview(file, raw, canvasWrap) {
  clear(canvasWrap);
  const img = new Image();
  img.onload = () => {
    const canvas = el("canvas", { class: "detect-canvas" });
    const maxW = 520;
    const scale = Math.min(1, maxW / img.width);
    canvas.width = img.width * scale;
    canvas.height = img.height * scale;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    const boxes = raw.bounding_boxes || [];
    boxes.forEach((b) => {
      const color = bboxColor(b.label);
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.strokeRect(b.x1 * scale, b.y1 * scale, (b.x2 - b.x1) * scale, (b.y2 - b.y1) * scale);
      ctx.fillStyle = color;
      const text = `${b.label} ${(b.confidence * 100).toFixed(0)}%`;
      const tw = ctx.measureText(text).width + 8;
      ctx.fillRect(b.x1 * scale, Math.max(0, b.y1 * scale - 16), tw, 16);
      ctx.fillStyle = "#0b1020";
      ctx.font = "11px sans-serif";
      ctx.fillText(text, b.x1 * scale + 4, Math.max(11, b.y1 * scale - 4));
    });
    canvasWrap.appendChild(canvas);
    const v = raw.vehicles || {};
    canvasWrap.appendChild(
      el("div", { class: "detect-summary" }, [
        el("span", {}, `${boxes.length} object(s) detected`),
        el("span", {}, `${v.total ?? 0} vehicle(s)`),
        raw.accident ? badge(`Accident (${fmtPct(raw.confidence)})`, "danger") : badge("No accident detected", "neutral"),
        raw.emergency_vehicle ? badge("Emergency vehicle", "warn") : null,
      ].filter(Boolean))
    );
  };
  img.src = URL.createObjectURL(file);
}

export function openDetectionModal({ onComplete } = {}) {
  let mode = "image"; // image | video
  let previewMode = "aggregate"; // aggregate | quick (image-only)
  let selectedFile = null;

  const fileInput = el("input", { type: "file", accept: "image/*", id: "detect-file-input" });
  const cameraInput = el("input", { type: "text", placeholder: "Camera ID (optional)", class: "input" });
  const dropZone = el("div", { class: "dropzone" }, [
    icon("upload", "icon icon-lg"),
    el("p", {}, "Drag & drop a file here, or click to browse"),
    el("p", { class: "muted small" }, "Images: jpg/png. Video: mp4/mov."),
  ]);
  const fileNameLabel = el("p", { class: "muted small" }, "No file selected");
  const resultBox = el("div", { class: "detect-results" });
  const previewBox = el("div", { class: "detect-preview" });

  const imageTab = el("button", { class: "tab active", onclick: () => switchMode("image") }, [icon("image"), " Image"]);
  const videoTab = el("button", { class: "tab", onclick: () => switchMode("video") }, [icon("video"), " Video"]);
  const tabs = el("div", { class: "tabs" }, [imageTab, videoTab]);

  const quickToggle = el("label", { class: "toggle-row" }, [
    el("input", {
      type: "checkbox",
      checked: "checked",
      onchange: (e) => {
        previewMode = e.target.checked ? "quick" : "aggregate";
      },
    }),
    el("span", {}, "Quick bounding-box preview (image only, no incident record created)"),
  ]);

  function switchMode(next) {
    mode = next;
    imageTab.classList.toggle("active", mode === "image");
    videoTab.classList.toggle("active", mode === "video");
    quickToggle.style.display = mode === "image" ? "flex" : "none";
    fileInput.setAttribute("accept", mode === "image" ? "image/*" : "video/*");
    selectedFile = null;
    fileNameLabel.textContent = "No file selected";
    clear(resultBox);
    clear(previewBox);
  }

  dropZone.addEventListener("click", () => fileInput.click());
  ["dragover", "dragenter"].forEach((evt) =>
    dropZone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropZone.classList.add("dropzone-hover");
    })
  );
  ["dragleave", "drop"].forEach((evt) =>
    dropZone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropZone.classList.remove("dropzone-hover");
    })
  );
  dropZone.addEventListener("drop", (e) => {
    const file = e.dataTransfer.files[0];
    if (file) setFile(file);
  });
  fileInput.addEventListener("change", (e) => {
    if (e.target.files[0]) setFile(e.target.files[0]);
  });

  function setFile(file) {
    selectedFile = file;
    fileNameLabel.textContent = `Selected: ${file.name} (${(file.size / 1024).toFixed(0)} KB)`;
  }

  const submitBtn = el("button", { class: "btn btn-primary", onclick: submit }, [icon("upload"), " Run detection"]);

  async function submit() {
    if (!selectedFile) {
      toast("Choose a file first", "error");
      return;
    }
    clear(resultBox);
    clear(previewBox);
    submitBtn.disabled = true;
    submitBtn.textContent = "Running…";
    resultBox.appendChild(
      el("div", { class: "state-block state-loading" }, [el("div", { class: "spinner" }), el("p", {}, "Running detection through YOLO + incident aggregator…")])
    );
    try {
      let result;
      if (mode === "image" && previewMode === "quick") {
        const raw = await api.detectImage(selectedFile, cameraInput.value.trim());
        clear(resultBox);
        drawQuickPreview(selectedFile, raw, previewBox);
      } else if (mode === "image") {
        result = await api.aggregateImage(selectedFile, cameraInput.value.trim());
        clear(resultBox);
        aggregationSummary(result, resultBox);
        onComplete && onComplete(result);
      } else {
        result = await api.aggregateVideo(selectedFile, cameraInput.value.trim());
        clear(resultBox);
        aggregationSummary(result, resultBox);
        onComplete && onComplete(result);
      }
      toast("Detection complete", "success");
    } catch (err) {
      clear(resultBox);
      resultBox.appendChild(el("div", { class: "state-block state-error" }, [icon("alert", "icon icon-lg"), el("p", {}, err.message)]));
      toast(`Detection failed: ${err.message}`, "error");
    } finally {
      submitBtn.disabled = false;
      submitBtn.innerHTML = "";
      submitBtn.appendChild(icon("upload"));
      submitBtn.appendChild(document.createTextNode(" Run detection"));
    }
  }

  const body = el("div", { class: "detect-modal" }, [
    tabs,
    dropZone,
    fileInput,
    fileNameLabel,
    cameraInput,
    quickToggle,
    submitBtn,
    previewBox,
    resultBox,
  ]);
  fileInput.style.display = "none";

  modal("Run detection", body);
}
