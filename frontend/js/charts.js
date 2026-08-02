// charts.js — small dependency-free SVG chart helpers. Kept intentionally
// simple: this dashboard doesn't need a full charting library for a
// handful of KPI visualizations.
import { el } from "./ui.js";

const PALETTE = ["#5b8cff", "#33d17a", "#ffb545", "#ff5d5d", "#a78bfa", "#22d3ee", "#f472b6"];

export function donutChart(data, { size = 160, thickness = 22 } = {}) {
  const total = data.reduce((s, d) => s + d.value, 0);
  const r = size / 2 - thickness / 2;
  const cx = size / 2;
  const cy = size / 2;
  const circumference = 2 * Math.PI * r;
  let offset = 0;

  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${size} ${size}`);
  svg.setAttribute("class", "chart-donut");

  if (total === 0) {
    const bg = document.createElementNS(svg.namespaceURI, "circle");
    bg.setAttribute("cx", cx);
    bg.setAttribute("cy", cy);
    bg.setAttribute("r", r);
    bg.setAttribute("fill", "none");
    bg.setAttribute("stroke", "var(--border)");
    bg.setAttribute("stroke-width", thickness);
    svg.appendChild(bg);
    return svg;
  }

  data.forEach((d, i) => {
    const frac = d.value / total;
    const dash = frac * circumference;
    const circle = document.createElementNS(svg.namespaceURI, "circle");
    circle.setAttribute("cx", cx);
    circle.setAttribute("cy", cy);
    circle.setAttribute("r", r);
    circle.setAttribute("fill", "none");
    circle.setAttribute("stroke", d.color || PALETTE[i % PALETTE.length]);
    circle.setAttribute("stroke-width", thickness);
    circle.setAttribute("stroke-dasharray", `${dash} ${circumference - dash}`);
    circle.setAttribute("stroke-dashoffset", -offset);
    circle.setAttribute("transform", `rotate(-90 ${cx} ${cy})`);
    circle.setAttribute("stroke-linecap", dash < circumference ? "butt" : "round");
    svg.appendChild(circle);
    offset += dash;
  });

  return svg;
}

export function barChart(data, { width = 320, height = 160, barGap = 10 } = {}) {
  const max = Math.max(1, ...data.map((d) => d.value));
  const barWidth = data.length ? (width - barGap * (data.length - 1)) / data.length : width;
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height + 26}`);
  svg.setAttribute("class", "chart-bar");

  data.forEach((d, i) => {
    const h = (d.value / max) * (height - 6);
    const x = i * (barWidth + barGap);
    const y = height - h;
    const rect = document.createElementNS(svg.namespaceURI, "rect");
    rect.setAttribute("x", x);
    rect.setAttribute("y", y);
    rect.setAttribute("width", barWidth);
    rect.setAttribute("height", Math.max(h, 2));
    rect.setAttribute("rx", 4);
    rect.setAttribute("fill", d.color || PALETTE[i % PALETTE.length]);
    svg.appendChild(rect);

    const val = document.createElementNS(svg.namespaceURI, "text");
    val.setAttribute("x", x + barWidth / 2);
    val.setAttribute("y", Math.max(y - 6, 10));
    val.setAttribute("text-anchor", "middle");
    val.setAttribute("class", "chart-bar-value");
    val.textContent = d.value;
    svg.appendChild(val);

    const label = document.createElementNS(svg.namespaceURI, "text");
    label.setAttribute("x", x + barWidth / 2);
    label.setAttribute("y", height + 18);
    label.setAttribute("text-anchor", "middle");
    label.setAttribute("class", "chart-bar-label");
    label.textContent = d.label;
    svg.appendChild(label);
  });

  return svg;
}

export function sparkline(values, { width = 220, height = 48, color = "#5b8cff" } = {}) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("class", "chart-sparkline");
  if (values.length < 2) {
    return svg;
  }
  const max = Math.max(...values, 1);
  const min = Math.min(...values, 0);
  const range = max - min || 1;
  const step = width / (values.length - 1);
  const points = values.map((v, i) => `${i * step},${height - ((v - min) / range) * (height - 6) - 3}`).join(" ");
  const line = document.createElementNS(svg.namespaceURI, "polyline");
  line.setAttribute("points", points);
  line.setAttribute("fill", "none");
  line.setAttribute("stroke", color);
  line.setAttribute("stroke-width", "2.5");
  line.setAttribute("stroke-linecap", "round");
  line.setAttribute("stroke-linejoin", "round");
  svg.appendChild(line);
  return svg;
}

export function legendFor(data) {
  const wrap = el("div", { class: "chart-legend" });
  data.forEach((d, i) => {
    wrap.appendChild(
      el("div", { class: "chart-legend-item" }, [
        el("span", { class: "legend-dot", style: `background:${d.color || PALETTE[i % PALETTE.length]}` }),
        el("span", {}, `${d.label} (${d.value})`),
      ])
    );
  });
  return wrap;
}
