/* Local SVG charts: no remote chart library is required. */
(function () {
  "use strict";
  var source = document.getElementById("dashboard-chart-data");
  if (!source) return;
  var data = JSON.parse(source.textContent);
  var host = document.getElementById("activity-chart");
  var selector = document.getElementById("chart-metric");
  var number = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });
  var compact = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });
  var ns = "http://www.w3.org/2000/svg";
  function node(name, attributes, text) {
    var el = document.createElementNS(ns, name);
    Object.keys(attributes || {}).forEach(function (key) { el.setAttribute(key, attributes[key]); });
    if (text !== undefined) el.textContent = text;
    return el;
  }
  function render() {
    host.replaceChildren();
    var metric = selector.value;
    var label = metric === "rows" ? "Rows processed" : metric === "runs" ? "Number of runs" : data.metric_label;
    document.getElementById("chart-description").textContent = (metric === "runs" ? "All runs " : "Successful processing ") + data.bucket_label;
    var points = data.series;
    var empty = points.every(function (point) { return point.runs === 0; });
    document.getElementById("chart-empty").hidden = !empty;
    if (empty) return;
    var w = Math.max(300, Math.min(720, host.clientWidth));
    var h = host.clientWidth < 400 ? 190 : 216, left = 48, right = 22, top = 18, bottom = 32;
    var values = points.map(function (point) { return point[metric]; });
    var low = Math.min(0, Math.min.apply(null, values));
    var high = Math.max(1, Math.max.apply(null, values)) * 1.15;
    if (metric === "runs") high = Math.max(4, Math.ceil(high / 4) * 4);
    var span = high - low;
    var width = w - left - right, height = h - top - bottom;
    function x(i) { return points.length === 1 ? left + width / 2 : left + i * width / (points.length - 1); }
    function y(value) { return top + (high - value) / span * height; }
    var svg = node("svg", { viewBox: "0 0 " + w + " " + h, class: "activity-svg", role: "group", "aria-label": label + " " + data.bucket_label });
    svg.appendChild(node("title", {}, label + " " + data.bucket_label));
    for (var grid = 0; grid <= 4; grid++) {
      var value = low + span * grid / 4;
      svg.appendChild(node("line", { x1: left, x2: w - right, y1: y(value), y2: y(value), class: "chart-gridline" }));
      svg.appendChild(node("text", { x: left - 10, y: y(value) + 4, "text-anchor": "end", class: "chart-axis" }, compact.format(value)));
    }
    if (metric !== "runs") {
      var path = points.map(function (point, i) { return (i ? "L" : "M") + x(i) + " " + y(point[metric]); }).join(" ");
      if (points.length > 1) {
        svg.appendChild(node("path", { d: path + " L" + x(points.length - 1) + " " + y(0) + " L" + x(0) + " " + y(0) + " Z", class: "chart-area" }));
        svg.appendChild(node("path", { d: path, class: "chart-line" }));
      }
    }
    var tooltip = document.createElement("div");
    tooltip.className = "chart-tooltip";
    tooltip.setAttribute("role", "status");
    tooltip.hidden = true;
    var tickEvery = Math.max(1, Math.ceil(points.length / 5));
    points.forEach(function (point, i) {
      if (i % tickEvery === 0 || i === points.length - 1) {
        svg.appendChild(node("text", { x: x(i), y: h - 12, "text-anchor": "middle", class: "chart-axis" }, point.label));
      }
      var mark;
      if (metric === "runs") {
        var barWidth = Math.min(40, width / Math.max(points.length, 1) * 0.6);
        mark = node("rect", { x: x(i) - barWidth / 2, y: y(point.runs), width: barWidth, height: Math.max(0, y(0) - y(point.runs)), rx: 4, class: "chart-bar" });
      } else {
        mark = node("circle", { cx: x(i), cy: y(point[metric]), r: 5, class: "chart-point" });
      }
      var description = point.label + ": " + number.format(point[metric]) + " " + label.toLowerCase();
      mark.setAttribute("tabindex", "0");
      mark.setAttribute("role", "img");
      mark.setAttribute("aria-label", description);
      mark.appendChild(node("title", {}, description));
      function show() { tooltip.textContent = description; tooltip.hidden = false; }
      function hide() { tooltip.hidden = true; }
      mark.addEventListener("mouseenter", show);
      mark.addEventListener("focus", show);
      mark.addEventListener("mouseleave", hide);
      mark.addEventListener("blur", hide);
      svg.appendChild(mark);
    });
    host.appendChild(svg);
    host.appendChild(tooltip);
  }
  selector.addEventListener("change", render);
  render();
  if (typeof ResizeObserver !== "undefined") new ResizeObserver(render).observe(host);
  var ring = document.getElementById("outcome-ring");
  var total = Object.values(data.outcomes).reduce(function (sum, value) { return sum + value; }, 0);
  if (total) {
    var colors = { success: "#16a34a", failed: "#dc2626", running: "#2563eb", stopped: "#d97706" };
    var start = 0;
    var stops = Object.keys(colors).map(function (key) {
      var end = start + data.outcomes[key] / total * 100;
      var stop = colors[key] + " " + start + "% " + end + "%";
      start = end;
      return stop;
    });
    ring.style.background = "conic-gradient(" + stops.join(",") + ")";
  }
})();
