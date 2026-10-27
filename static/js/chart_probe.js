/* Hover/touch crosshair, keyboard reading and drag-to-zoom for the server-built
 * charts. The SVG is already drawn; this only reads its data and moves a view box. */
(function () {
  "use strict";
  document.querySelectorAll("[data-chart-probe]").forEach(function (box) {
    if (box.dataset.probeBound) return;
    box.dataset.probeBound = "1";
    var svg = box.querySelector("svg");
    var probe = box.querySelector(".w2-probe");
    var tip = box.querySelector(".w2-probe-tip");
    var line = box.querySelector(".w2-probe-line");
    var zoomBox = box.querySelector(".w2-zoom-box");
    var reset = box.querySelector(".w2-zoom-reset");
    var live = box.querySelector("[data-probe-live]");
    var samples;
    try { samples = JSON.parse(box.getAttribute("data-readout") || "[]"); } catch (_) { samples = []; }
    if (!svg || !probe || samples.length < 2) return;
    var view = { x: 0, w: 800 };
    var current = -1;
    var drag = null;

    function pixelOf(x) { return (x - view.x) / view.w * svg.getBoundingClientRect().width; }
    function unitOf(clientX) {
      var rect = svg.getBoundingClientRect();
      return view.x + (clientX - rect.left) / rect.width * view.w;
    }
    function nearest(x) {
      var best = 0, gap = Infinity;
      samples.forEach(function (s, i) {
        var d = Math.abs(s.x - x);
        if (d < gap) { gap = d; best = i; }
      });
      return best;
    }
    function show(index) {
      current = Math.max(0, Math.min(samples.length - 1, index));
      var s = samples[current];
      if (s.x < view.x || s.x > view.x + view.w) { probe.hidden = true; return; }
      var px = pixelOf(s.x);
      var width = svg.getBoundingClientRect().width;
      probe.hidden = false;
      line.style.left = px + "px";
      tip.textContent = "";
      var head = document.createElement("strong");
      head.textContent = s.d;
      tip.appendChild(head);
      s.t.forEach(function (row) { tip.appendChild(document.createElement("br")); tip.appendChild(document.createTextNode(row)); });
      tip.style.left = (px > width / 2 ? px - tip.offsetWidth - 10 : px + 10) + "px";
      if (live) live.textContent = s.d + ": " + s.t.join(", ");
    }
    function hide() { probe.hidden = true; current = -1; }
    function setView(x, w) {
      view = { x: x, w: w };
      svg.setAttribute("viewBox", x + " 0 " + w + " 200");
      reset.hidden = w >= 800;
      hide();
    }

    box.addEventListener("pointermove", function (e) {
      if (drag) {
        var a = Math.min(drag.px, e.clientX), b = Math.max(drag.px, e.clientX);
        var rect = box.getBoundingClientRect();
        zoomBox.hidden = b - a < 6;
        zoomBox.style.left = (a - rect.left) + "px";
        zoomBox.style.width = (b - a) + "px";
        return;
      }
      show(nearest(unitOf(e.clientX)));
    });
    box.addEventListener("pointerleave", function () { if (!drag) hide(); });
    box.addEventListener("pointerdown", function (e) {
      if (e.pointerType === "touch" || e.button !== 0 || e.target === reset) return;
      drag = { px: e.clientX };
      box.setPointerCapture(e.pointerId);
    });
    box.addEventListener("pointerup", function (e) {
      if (!drag) return;
      var a = Math.min(drag.px, e.clientX), b = Math.max(drag.px, e.clientX);
      drag = null;
      zoomBox.hidden = true;
      if (b - a < 12) return;
      var left = unitOf(a), right = unitOf(b);
      var w = Math.max(right - left, 40);
      setView(Math.max(0, Math.min(left, 800 - w)), w);
    });
    box.addEventListener("dblclick", function () { setView(0, 800); });
    reset.addEventListener("click", function () { setView(0, 800); });
    box.addEventListener("keydown", function (e) {
      if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        e.preventDefault();
        show((current < 0 ? samples.length - 1 : current) + (e.key === "ArrowRight" ? 1 : -1));
      } else if (e.key === "Escape") { hide(); setView(0, 800); }
    });
    box.addEventListener("blur", hide);
  });
}());
