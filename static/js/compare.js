// Hypothetical strategy comparison (homepage and Add strategy): sortable table,
// chart with a hover readout, typed amounts, timeframe, linear/log scale and a draggable timeline.
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
  var mini = box.querySelector("[data-zoom-mini]");
  var track = box.querySelector("[data-zoom-track]");
  var startHandle = box.querySelector('[data-zoom-handle="start"]');
  var endHandle = box.querySelector('[data-zoom-handle="end"]');
  var windowHandle = box.querySelector("[data-zoom-window]");
  var resetZoom = box.querySelector("[data-zoom-reset]");
  var view = null;  // selected index range shared by chart and selected-period table
  var drag = null;
  var miniState = null, miniSignature = ""; // only replot the overview when its data changes
  var resetAfterLoad = false;
  var selectedMetrics = null;
  var metricRequest = 0;
  var metricTimer = null;
  var tablePending = false;
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
    var rows = (state.rows || []).map(function (row) {
      var metrics = selectedMetrics && selectedMetrics.rows && selectedMetrics.rows[row.key];
      if (metrics) return Object.assign({}, row, metrics);
      // Never show inception figures as though they were selected-window returns.
      if (tablePending || (view && !isFullView())) {
        return Object.assign({}, row, { final: null, gain_pct: null, annual: null, drawdown: null });
      }
      return row;
    });
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
      var available = Array.isArray(row.points) && row.points.length === count();
      if (hidden[row.key]) tr.className = "off";
      var td = cell("td");
      var box_ = document.createElement("input");
      box_.type = "checkbox";
      box_.checked = available && !hidden[row.key];
      box_.disabled = !available;
      box_.setAttribute("aria-label", available ? "Show " + row.label :
        "No comparable history for " + row.label);
      td.appendChild(box_);
      tr.appendChild(td);
      var name = cell("th");
      name.scope = "row";
      var sw = cell("span", "sw");
      sw.style.background = row.color;
      name.appendChild(sw);
      name.appendChild(document.createTextNode(row.label + " "));
      name.appendChild(cell("span", "mut", "· " + row.what +
        (available ? "" : " · No comparable history for these dates")));
      tr.appendChild(name);
      var value = cell("td", "r");
      value.appendChild(cell("strong", "", row.final === null ? "—" : "£" + money(row.final)));
      tr.appendChild(value);
      tr.appendChild(cell("td", "r " + (row.gain_pct >= 0 ? "pos" : "neg"), pct(row.gain_pct)));
      tr.appendChild(cell("td", "r", pct(row.annual)));
      tr.appendChild(cell("td", "r neg", pct(row.drawdown)));
      tr.appendChild(cell("td", "mut", row.effort));
      body.appendChild(tr);
    });
  }

  function count() { return (state.put_in_points || []).length; }
  function lastIndex() { return Math.max(0, count() - 1); }
  function gap() { return Math.min(2, lastIndex()); } // a minimum of three visible observations
  function clamp(value, low, high) { return Math.min(high, Math.max(low, value)); }
  function dateAt(i) { return (state.point_dates || [])[i] || ""; }
  function isFullView() { return view && view.start === 0 && view.end === lastIndex(); }

  function renderSummary() {
    var selected = view && !isFullView();
    var status = box.querySelector("[data-table-status]");
    text("[data-period]", selected ? dateAt(view.start) + " – " + dateAt(view.end) : state.period);
    text("[data-putin-title]", selected
      ? (selectedMetrics ? selectedMetrics.reference_label + " opening value + top-ups"
        : "Reference strategy opening value + top-ups")
      : "You'd have put in");
    text("[data-range-words]", selected ? "selected dates" : state.range_words);
    text("[data-putin]", selected
      ? (selectedMetrics ? money(selectedMetrics.put_in) : "—")
      : money(state.put_in));
    status.textContent = selected
      ? (tablePending ? "Calculating selected-period returns…" :
        selectedMetrics ? "Showing results for the selected dates." :
        "Selected-period figures unavailable; try adjusting or resetting the range.")
      : "Showing results for the full selected timeframe.";
    box.querySelector("[data-rows]").setAttribute("aria-busy", tablePending ? "true" : "false");
  }

  function updatePeriodTable() {
    clearTimeout(metricTimer);
    var ticket = ++metricRequest;
    selectedMetrics = null;
    if (!state.ready || !view || isFullView()) {
      tablePending = false;
      if (state.ready) { renderSummary(); renderTable(); }
      return;
    }
    tablePending = true;
    renderSummary();
    renderTable();
    metricTimer = setTimeout(function () {
      var url = (box.dataset.compareUrl || "/compare.json") + "?amount=" +
        encodeURIComponent(state.amount) + "&monthly=" + encodeURIComponent(state.monthly) +
        "&range=" + encodeURIComponent(state.range) +
        "&window_start=" + view.start + "&window_end=" + view.end;
      fetch(url, { headers: { Accept: "application/json" } })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(function (data) {
          if (ticket !== metricRequest || !state.ready) return;
          if (data.start_index !== view.start || data.end_index !== view.end) return;
          selectedMetrics = data;
          tablePending = false;
          renderSummary();
          renderTable();
        }).catch(function () {
          if (ticket !== metricRequest) return;
          tablePending = false;
          renderSummary();
          renderTable();
        });
    }, 140);
  }

  function normaliseView() {
    var last = lastIndex();
    if (!view) view = { start: 0, end: last };
    view.start = clamp(Math.round(view.start), 0, Math.max(0, last - gap()));
    view.end = clamp(Math.round(view.end), view.start + gap(), last);
  }

  // Keep every returned date available to the zoom. Decimate only the SVG paths,
  // so narrow periods use the original close observations, not interpolation.
  function indices(first, last, limit) {
    var result = [], step = Math.max(1, Math.ceil((last - first) / (limit - 1)));
    for (var i = first; i <= last; i += step) result.push(i);
    if (result[result.length - 1] !== last) result.push(last);
    return result;
  }

  function drawLine(target, values, samples, first, last, y, color, cls) {
    var p = document.createElementNS(NS, "polyline");
    var width = Math.max(1, last - first);
    p.setAttribute("points", samples.map(function (i) {
      return ((i - first) / width * 800).toFixed(1) + "," + y(values[i]).toFixed(1);
    }).join(" "));
    if (color) p.style.stroke = color;
    if (cls) p.setAttribute("class", cls);
    target.appendChild(p);
  }

  function renderChart() {
    svg.querySelectorAll("polyline").forEach(function (p) { p.remove(); });
    var legend = box.querySelector("[data-legend]");
    legend.textContent = "";
    var shown = (state.rows || []).filter(function (r) {
      return !hidden[r.key] && Array.isArray(r.points) && r.points.length === count();
    });
    var series = shown.map(function (r) { return r.points; }).concat([state.put_in_points || []]);
    if (!count()) return;
    normaliseView();
    var f = log ? Math.log10 : function (x) { return x; };
    var smallest = Infinity, greatest = -Infinity;
    series.forEach(function (values) {
      for (var i = view.start; i <= view.end; i++) {
        if (values[i] > 0 && Number.isFinite(values[i])) {
          smallest = Math.min(smallest, values[i]);
          greatest = Math.max(greatest, values[i]);
        }
      }
    });
    if (!Number.isFinite(greatest)) return;
    var lo = log ? f(smallest) : 0;
    var hi = f(greatest);
    var span = (hi - lo) || 1;
    var y = function (v) { return 300 - (f(Math.max(v, 1)) - lo) / span * 300; };
    var samples = indices(view.start, view.end, 850);
    drawLine(svg, state.put_in_points || [], samples, view.start, view.end, y, null, "putin");
    shown.forEach(function (r) {
      drawLine(svg, r.points, samples, view.start, view.end, y, r.color);
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

    // The labels and hover index follow the selected window; table data does not.
    var ticks = box.querySelector("[data-ticks]");
    ticks.textContent = "";
    for (var t = 0; t < 5; t++) {
      var at = Math.round(view.start + (view.end - view.start) * t / 4);
      ticks.appendChild(cell("span", "", dateAt(at)));
    }
    renderTimeline(shown, series);
  }

  function renderTimeline(shown, series) {
    var signature = shown.map(function (r) { return r.key; }).join("|") + ":" + log;
    if (miniState !== state || miniSignature !== signature) {
      mini.querySelectorAll("polyline").forEach(function (p) { p.remove(); });
      var bottom = Infinity, top = -Infinity;
      series.forEach(function (values) {
        values.forEach(function (v) {
          if (v > 0 && Number.isFinite(v)) {
            bottom = Math.min(bottom, v);
            top = Math.max(top, v);
          }
        });
      });
      if (!Number.isFinite(top)) return;
      var f = log ? Math.log10 : function (x) { return x; };
      var lo = log ? f(bottom) : 0;
      var span = (f(top) - lo) || 1;
      var y = function (v) { return 58 - (f(Math.max(v, 1)) - lo) / span * 56; };
      var samples = indices(0, lastIndex(), 220);
      drawLine(mini, state.put_in_points || [], samples, 0, lastIndex(), y, null, "putin");
      shown.forEach(function (r) { drawLine(mini, r.points, samples, 0, lastIndex(), y, r.color); });
      miniState = state;
      miniSignature = signature;
    }

    var last = Math.max(1, lastIndex());
    var left = view.start / last * 100;
    var right = view.end / last * 100;
    windowHandle.style.left = left + "%";
    windowHandle.style.width = (right - left) + "%";
    startHandle.style.left = left + "%";
    endHandle.style.left = right + "%";
    startHandle.style.transform = view.start === 0 ? "translateX(0)" : "translateX(-50%)";
    endHandle.style.transform = view.end === lastIndex() ? "translateX(-100%)" : "translateX(-50%)";
    box.querySelector("[data-zoom-before]").style.width = left + "%";
    box.querySelector("[data-zoom-after]").style.left = right + "%";
    box.querySelector("[data-zoom-after]").style.width = (100 - right) + "%";
    text("[data-zoom-dates]", dateAt(view.start) + " – " + dateAt(view.end));
    resetZoom.disabled = view.start === 0 && view.end === lastIndex();
    [[startHandle, view.start, 0, view.end - gap(), dateAt(view.start)],
     [endHandle, view.end, view.start + gap(), lastIndex(), dateAt(view.end)],
     [windowHandle, view.start, 0, lastIndex() - (view.end - view.start),
       dateAt(view.start) + " to " + dateAt(view.end)]].forEach(function (row) {
      row[0].setAttribute("aria-valuemin", row[2]);
      row[0].setAttribute("aria-valuemax", row[3]);
      row[0].setAttribute("aria-valuenow", row[1]);
      row[0].setAttribute("aria-valuetext", row[4]);
    });
  }

  function setView(start, end) {
    var last = lastIndex(), minGap = gap();
    start = clamp(Math.round(start), 0, last - minGap);
    end = clamp(Math.round(end), start + minGap, last);
    if (view.start === start && view.end === end) return;
    view = { start: start, end: end };
    hideTip();
    renderChart();
    updatePeriodTable();
  }

  function render() {
    box.querySelector("[data-empty]").hidden = state.ready;
    box.querySelector("[data-filled]").hidden = !state.ready;
    if (!state.ready) {
      text("[data-range-words]", state.range_words);
      text("[data-putin]", "—");
    }
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
    normaliseView();
    renderChart();
    updatePeriodTable();
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
    var i = Math.round(view.start + frac * (view.end - view.start));
    var x = (i - view.start) / Math.max(1, view.end - view.start) * 800;
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

  // Trim-like drag handles, plus dragging the selection itself. Pointer capture
  // supports mouse, touch and pen without global listeners.
  function atPointer(e) {
    var bounds = track.getBoundingClientRect();
    if (!bounds.width) return 0;
    return clamp(Math.round((e.clientX - bounds.left) / bounds.width * lastIndex()), 0, lastIndex());
  }
  track.addEventListener("pointerdown", function (e) {
    if (e.button !== 0 || !count()) return;
    var handle = e.target.closest("[data-zoom-handle]");
    var inWindow = e.target.closest("[data-zoom-window]");
    var kind = handle ? handle.dataset.zoomHandle : "window";
    if (!handle && !inWindow) {
      var center = atPointer(e), length = view.end - view.start;
      var nextStart = clamp(center - Math.round(length / 2), 0, lastIndex() - length);
      setView(nextStart, nextStart + length);
    }
    drag = { pointer: e.pointerId, kind: kind, origin: atPointer(e),
             start: view.start, end: view.end };
    track.setPointerCapture(e.pointerId);
    e.preventDefault();
  });
  track.addEventListener("pointermove", function (e) {
    if (!drag || drag.pointer !== e.pointerId) return;
    var at = atPointer(e);
    if (drag.kind === "start") setView(Math.min(at, view.end - gap()), view.end);
    else if (drag.kind === "end") setView(view.start, Math.max(at, view.start + gap()));
    else {
      var length = drag.end - drag.start;
      var start = clamp(drag.start + at - drag.origin, 0, lastIndex() - length);
      setView(start, start + length);
    }
  });
  function stopDrag(e) {
    if (drag && drag.pointer === e.pointerId) drag = null;
  }
  track.addEventListener("pointerup", stopDrag);
  track.addEventListener("pointercancel", stopDrag);
  track.addEventListener("lostpointercapture", stopDrag);

  [startHandle, endHandle, windowHandle].forEach(function (handle) {
    handle.addEventListener("keydown", function (e) {
      var kind = handle.dataset.zoomHandle || "window";
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
      var step = e.shiftKey ? 10 : 1;
      var delta = e.key === "ArrowRight" ? step : -step;
      if (kind === "start") {
        var s = e.key === "Home" ? 0 : e.key === "End" ? view.end - gap() : view.start + delta;
        setView(s, view.end);
      } else if (kind === "end") {
        var n = e.key === "Home" ? view.start + gap() : e.key === "End" ? lastIndex() : view.end + delta;
        setView(view.start, n);
      } else {
        var length = view.end - view.start;
        var next = e.key === "Home" ? 0 : e.key === "End" ? lastIndex() - length : view.start + delta;
        next = clamp(next, 0, lastIndex() - length);
        setView(next, next + length);
      }
      e.preventDefault();
    });
  });
  resetZoom.addEventListener("click", function () {
    setView(0, lastIndex());
  });

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
    ++metricRequest;
    clearTimeout(metricTimer);
    box.classList.add("loading");
    var url = (box.dataset.compareUrl || "/compare.json") + "?amount=" + encodeURIComponent(amount.value) +
      "&monthly=" + encodeURIComponent(monthly.value) + "&range=" + range;
    fetch(url, { headers: { Accept: "application/json" } })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (data) {
        if (ticket === latest) {
          state = data;
          selectedMetrics = null;
          tablePending = false;
          if (resetAfterLoad) view = null;
          resetAfterLoad = false;
          hideTip();
          render();
        }
      })
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
    b.addEventListener("click", function () {
      press(frames, b);
      range = b.dataset.range;
      resetAfterLoad = true;  // reset only when the new series arrives
      load();
    });
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
    var row = (state.rows || []).find(function (r) { return r.key === tr.dataset.key; });
    if (!row || !Array.isArray(row.points) || row.points.length !== count()) return;
    hidden[tr.dataset.key] = !hidden[tr.dataset.key];
    hideTip();
    renderTable();
    renderChart();
  });

  render();
})();
