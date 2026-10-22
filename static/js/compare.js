// Hypothetical strategy comparison (homepage and Add strategy): sortable table,
// chart with a hover readout, typed amounts, timeframe and linear/log scale.
(function () {
  "use strict";

  function press(buttons, active) {
    buttons.forEach(function (b) {
      var on = b === active;
      b.classList.toggle("on", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  var box = document.querySelector("[data-compare]");
  var source = document.getElementById("compare-data");
  if (!box || !source) return;
  var state = JSON.parse(source.textContent);
  var hidden = {};
  var log = false;
  var sortKey = null;  // null keeps the server's order
  var sortDir = 1;     // 1 ascending, -1 descending
  var scale = null;    // the last drawn y-scale, for the hover readout
  var svg = box.querySelector("[data-chart]");
  var NS = "http://www.w3.org/2000/svg";
  var money = function (v) { return Math.round(v).toLocaleString("en-GB"); };
  var pct = function (v) { return v === null ? "—" : (v >= 0 ? "+" : "−") + Math.abs(v).toFixed(1) + "%"; };

  function text(sel, value) { box.querySelector(sel).textContent = value; }

  function cell(tag, cls, content) {
    var el = document.createElement(tag);
    if (cls) el.className = cls;
    if (content !== undefined) el.textContent = content;
    return el;
  }

  function sortedRows() {
    var rows = (state.rows || []).slice();
    if (!sortKey) return rows;
    return rows.sort(function (a, b) {
      var x = a[sortKey], y = b[sortKey];
      if (x === null || x === undefined) return 1;  // no figure: always last
      if (y === null || y === undefined) return -1;
      if (typeof x === "string") return x.localeCompare(y) * sortDir;
      return (x - y) * sortDir;
    });
  }

  function renderSortHeads() {
    box.querySelectorAll("[data-sort]").forEach(function (b) {
      var th = b.closest("th");
      var on = b.dataset.sort === sortKey;
      th.setAttribute("aria-sort", on ? (sortDir > 0 ? "ascending" : "descending") : "none");
    });
  }

  function renderTable() {
    var body = box.querySelector("[data-rows]");
    body.textContent = "";
    renderSortHeads();
    sortedRows().forEach(function (row) {
      var tr = document.createElement("tr");
      tr.dataset.key = row.key;
      if (hidden[row.key]) tr.className = "off";
      var td = cell("td");
      var box_ = document.createElement("input");
      box_.type = "checkbox";
      box_.checked = !hidden[row.key];
      box_.setAttribute("aria-label", "Show " + row.label);
      td.appendChild(box_);
      tr.appendChild(td);
      var name = cell("th");
      name.scope = "row";
      var sw = cell("span", "sw");
      sw.style.background = row.color;
      name.appendChild(sw);
      name.appendChild(document.createTextNode(row.label + " "));
      name.appendChild(cell("span", "mut", "· " + row.what));
      tr.appendChild(name);
      var value = cell("td", "r");
      value.appendChild(cell("strong", "", "£" + money(row.final)));
      tr.appendChild(value);
      tr.appendChild(cell("td", "r " + (row.gain_pct >= 0 ? "pos" : "neg"), pct(row.gain_pct)));
      tr.appendChild(cell("td", "r", pct(row.annual)));
      tr.appendChild(cell("td", "r neg", pct(row.drawdown)));
      tr.appendChild(cell("td", "mut", row.effort));
      body.appendChild(tr);
    });
  }

  function renderChart() {
    svg.querySelectorAll("polyline").forEach(function (p) { p.remove(); });
    var legend = box.querySelector("[data-legend]");
    legend.textContent = "";
    var shown = (state.rows || []).filter(function (r) { return !hidden[r.key]; });
    var series = shown.map(function (r) { return r.points; }).concat([state.put_in_points || []]);
    var all = [];
    series.forEach(function (s) { s.forEach(function (v) { if (v > 0) all.push(v); }); });
    if (!all.length) return;
    var f = log ? Math.log10 : function (x) { return x; };
    var lo = log ? f(Math.min.apply(null, all)) : 0;
    var hi = f(Math.max.apply(null, all));
    var span = (hi - lo) || 1;
    var line = function (values, color, cls) {
      var p = document.createElementNS(NS, "polyline");
      p.setAttribute("points", values.map(function (v, i) {
        return (i / Math.max(1, values.length - 1) * 800).toFixed(1) + "," +
          (300 - (f(Math.max(v, 1)) - lo) / span * 300).toFixed(1);
      }).join(" "));
      if (color) p.style.stroke = color;
      if (cls) p.setAttribute("class", cls);
      svg.appendChild(p);
    };
    line(state.put_in_points || [], null, "putin");
    shown.forEach(function (r) {
      line(r.points, r.color);
      var item = cell("span");
      var sw = cell("span", "sw");
      sw.style.background = r.color;
      item.appendChild(sw);
      item.appendChild(document.createTextNode(r.label));
      legend.appendChild(item);
    });
    var put = cell("span");
    var psw = cell("span", "sw");
    psw.style.background = "#6b7480";
    put.appendChild(psw);
    put.appendChild(document.createTextNode("Money put in"));
    legend.appendChild(put);
    scale = { f: f, lo: lo, span: span, shown: shown };
    var label = function (x) { return "£" + money(log ? Math.pow(10, x) : x); };
    text('[data-y="0"]', label(hi));
    text('[data-y="1"]', label(lo + span / 2));
    text('[data-y="2"]', label(lo));
  }

  function render() {
    box.querySelector("[data-empty]").hidden = state.ready;
    box.querySelector("[data-filled]").hidden = !state.ready;
    text("[data-range-words]", state.range_words);
    text("[data-putin]", state.ready ? money(state.put_in) : "—");
    box.querySelector("[data-simulated]").hidden = !state.simulated;
    var other = box.querySelector("[data-simulated-other]");
    if (other) other.hidden = !state.simulated_other;
    var leftOut = box.querySelector("[data-left-out]");
    if (leftOut) {
      leftOut.hidden = !(state.left_out && state.left_out.length);
      text("[data-left-out-names]", (state.left_out || []).join(", "));
    }
    var period = box.querySelector("[data-period-wrap]");
    if (period) period.hidden = !state.ready;
    if (!state.ready) return;
    text("[data-period]", state.period);
    var ticks = box.querySelector("[data-ticks]");
    ticks.textContent = "";
    state.ticks.forEach(function (t) { ticks.appendChild(cell("span", "", t)); });
    renderTable();
    renderChart();
  }

  // Hover (or tap) readout: each shown strategy's value that day and its
  // gain against the money put in by then.
  var chartBox = box.querySelector("[data-chart-box]");
  var tip = box.querySelector("[data-tip]");
  var cursor = document.createElementNS(NS, "line");
  cursor.setAttribute("class", "cursor");
  cursor.setAttribute("y1", "0");
  cursor.setAttribute("y2", "300");

  function hideTip() {
    tip.hidden = true;
    if (cursor.parentNode) cursor.parentNode.removeChild(cursor);
  }

  function showTip(clientX) {
    var putIn = state.put_in_points || [];
    if (!scale || !putIn.length) return;
    var rect = svg.getBoundingClientRect();
    var frac = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
    var i = Math.round(frac * (putIn.length - 1));
    var x = i / Math.max(1, putIn.length - 1) * 800;
    cursor.setAttribute("x1", x.toFixed(1));
    cursor.setAttribute("x2", x.toFixed(1));
    svg.appendChild(cursor);
    tip.textContent = "";
    tip.appendChild(cell("strong", "tip-date", (state.point_dates || [])[i] || ""));
    var rows = scale.shown.slice().sort(function (a, b) { return b.points[i] - a.points[i]; });
    rows.forEach(function (r) {
      var line = cell("div", "tip-row");
      var name = cell("span", "tip-name");
      var sw = cell("span", "sw");
      sw.style.background = r.color;
      name.appendChild(sw);
      name.appendChild(document.createTextNode(r.label));
      line.appendChild(name);
      var gain = putIn[i] > 0 ? (r.points[i] / putIn[i] - 1) * 100 : null;
      line.appendChild(cell("span", "tip-value", "£" + money(r.points[i])));
      line.appendChild(cell("span", "tip-gain " + (gain >= 0 ? "pos" : "neg"), pct(gain)));
      tip.appendChild(line);
    });
    tip.appendChild(cell("div", "tip-foot mut", "Money put in by then: £" + money(putIn[i])));
    tip.hidden = false;
    var left = frac * chartBox.clientWidth;
    var wide = tip.offsetWidth;
    tip.style.left = Math.max(0, Math.min(chartBox.clientWidth - wide, left > chartBox.clientWidth / 2 ? left - wide - 12 : left + 12)) + "px";
  }

  svg.addEventListener("pointermove", function (e) { showTip(e.clientX); });
  svg.addEventListener("pointerdown", function (e) { showTip(e.clientX); });
  svg.addEventListener("pointerleave", function (e) { if (e.pointerType === "mouse") hideTip(); });

  box.querySelectorAll("[data-sort]").forEach(function (b) {
    b.addEventListener("click", function () {
      var key = b.dataset.sort;
      if (sortKey === key) {
        sortDir = -sortDir;
      } else {
        sortKey = key;
        // Names A-Z first; figures best first (worst fall: smallest first).
        sortDir = key === "label" ? 1 : -1;
      }
      renderTable();
    });
  });

  var amount = box.querySelector("[data-amount]");
  var monthly = box.querySelector("[data-monthly]");
  var range = state.range;
  var timer = null;
  var latest = 0;

  function validAmounts() {
    return amount.value !== "" && monthly.value !== "" && amount.checkValidity() && monthly.checkValidity();
  }

  function load() {
    var ticket = ++latest;
    box.classList.add("loading");
    var url = (box.dataset.compareUrl || "/compare.json") + "?amount=" + encodeURIComponent(amount.value) +
      "&monthly=" + encodeURIComponent(monthly.value) + "&range=" + range;
    fetch(url, { headers: { Accept: "application/json" } })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (data) { if (ticket === latest) { state = data; hideTip(); render(); } })
      .catch(function () {})
      .then(function () { if (ticket === latest) box.classList.remove("loading"); });
  }

  function later() {
    clearTimeout(timer);
    if (validAmounts()) timer = setTimeout(load, 450);
  }
  amount.addEventListener("input", later);
  monthly.addEventListener("input", later);

  var frames = Array.prototype.slice.call(document.querySelectorAll("[data-range]"));
  frames.forEach(function (b) {
    b.addEventListener("click", function () { press(frames, b); range = b.dataset.range; load(); });
  });

  var scales = Array.prototype.slice.call(box.querySelectorAll("[data-scale]"));
  scales.forEach(function (b) {
    b.addEventListener("click", function () {
      press(scales, b);
      log = b.dataset.scale === "log";
      text("[data-scale-hint]", log
        ? "Log: equal heights are equal % growth, so early years and smaller strategies are easier to compare."
        : "Linear: equal heights are equal pounds.");
      hideTip();
      renderChart();
    });
  });

  box.querySelector("[data-rows]").addEventListener("click", function (e) {
    var tr = e.target.closest("tr");
    if (!tr) return;
    hidden[tr.dataset.key] = !hidden[tr.dataset.key];
    hideTip();
    renderTable();
    renderChart();
  });

  render();
})();
