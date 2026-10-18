(function () {
  "use strict";
  var toggle = document.getElementById("nav-toggle");
  var sidebar = document.getElementById("sidebar");
  var shade = document.getElementById("nav-shade");
  function closeNav() {
    if (sidebar) sidebar.classList.remove("open");
    if (shade) shade.classList.remove("open");
    if (toggle) toggle.setAttribute("aria-expanded", "false");
  }
  if (toggle && sidebar) {
    toggle.addEventListener("click", function () {
      var open = !sidebar.classList.contains("open");
      sidebar.classList.toggle("open", open);
      shade.classList.toggle("open", open);
      toggle.setAttribute("aria-expanded", String(open));
    });
    shade.addEventListener("click", closeNav);
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeNav(); });
  }

  var kind = document.getElementById("id_kind");
  if (kind) {
    var tradeFields = ["symbol", "quantity", "unit_price", "fee"];
    var amount = document.getElementById("id_amount");
    function updateFields() {
      var trade = kind.value === "BUY" || kind.value === "SELL";
      tradeFields.forEach(function (key) {
        var field = document.getElementById("id_" + key);
        if (!field) return;
        var parent = field.closest(".form-field");
        if (parent) parent.style.display = trade ? "" : "none";
        field.disabled = !trade;
      });
      if (amount) {
        amount.disabled = trade;
        if (amount.closest(".form-field")) amount.closest(".form-field").style.display = trade ? "none" : "";
      }
    }
    kind.addEventListener("change", updateFields);
    updateFields();
  }

  var host = document.getElementById("wealth-chart") || document.getElementById("strategy-chart");
  var pointsSource = document.getElementById("wealth-chart-points") || document.getElementById("strategy-chart-points");
  if (!host || !pointsSource) return;
  var all = [];
  try { all = JSON.parse(pointsSource.textContent || "[]"); } catch (_) { all = []; }
  all.sort(function (a, b) { return a.date.localeCompare(b.date); });
  var fmt = new Intl.NumberFormat("en-GB", {style: "currency", currency: "GBP", maximumFractionDigits: 0});
  var fmtPence = new Intl.NumberFormat("en-GB", {style: "currency", currency: "GBP", minimumFractionDigits: 2});
  var active = "all";
  var buttons = document.querySelectorAll("[data-range]");
  var tooltip = document.createElement("div");
  tooltip.className = "chart-tooltip";
  tooltip.style.display = "none";
  host.appendChild(tooltip);
  function empty(title, description) {
    host.innerHTML = '<div class="chart-empty"><b>' + title + '</b><span>' + description + '</span></div>';
    host.appendChild(tooltip);
  }
  function render() {
    var cutoff = new Date();
    if (active === "1m") cutoff.setMonth(cutoff.getMonth() - 1);
    else if (active === "3m") cutoff.setMonth(cutoff.getMonth() - 3);
    else if (active === "1y") cutoff.setFullYear(cutoff.getFullYear() - 1);
    var values = active === "all" ? all : all.filter(function (p) { return new Date(p.date + "T12:00:00") >= cutoff; });
    if (!values.length) {
      empty(all.length ? "No valuations in this period" : "Your wealth chart starts here",
        all.length ? "Choose All to see earlier valuations." : "Your first verified quote-day valuation will appear after the next market sync.");
      return;
    }
    var w = 1000, h = 285, l = 77, r = 25, t = 18, b = 30;
    var range = values.map(function(p){return Number(p.value);}).concat(values.map(function(p){return Number(p.contributed);}));
    var lo = Math.min(0, Math.min.apply(null, range));
    var hi = Math.max(1, Math.max.apply(null, range));
    var padding = (hi - lo) * .1;
    var min = lo - padding, max = hi + padding;
    function x(i){ return values.length === 1 ? (l + w - r) / 2 : l + i * (w - r - l)/(values.length-1); }
    function y(n){ return t + (max - n) * (h - b - t) / (max - min); }
    function path(key){ return values.map(function(p,i){return (i ? "L" : "M") + x(i).toFixed(1) + " " + y(Number(p[key])).toFixed(1);}).join(" "); }
    var line = path("value"), contributed = path("contributed");
    var area = line + "L " + x(values.length-1).toFixed(1) + " " + (h-b) + " L " + x(0).toFixed(1) + " " + (h-b) + " Z";
    var svg = '<svg viewBox="0 0 '+w+' '+h+'" aria-label="Actual saved portfolio valuations" role="img" preserveAspectRatio="none"><defs><linearGradient id="wealth-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#21d2be" stop-opacity=".24"/><stop offset="1" stop-color="#21d2be" stop-opacity=".015"/></linearGradient></defs>';
    for (var n=0;n<5;n++){
      var val = min+(max-min)*n/4, py=y(val);
      svg += '<line class="gridline" x1="'+l+'" x2="'+(w-r)+'" y1="'+py+'" y2="'+py+'"></line>';
      svg += '<text class="axis-text" x="'+(l-10)+'" y="'+(py+4)+'" text-anchor="end">'+fmt.format(val)+'</text>';
    }
    svg += '<path d="'+area+'" fill="url(#wealth-fill)"></path><path class="cash-line" d="'+contributed+'"></path><path class="value-line" d="'+line+'"></path>';
    values.forEach(function(p,i){
      var date = new Date(p.date + "T12:00:00").toLocaleDateString("en-GB", {day:"numeric", month:"short", year:"numeric"});
      svg += '<circle class="hover-hit" data-point="'+i+'" cx="'+x(i)+'" cy="'+y(Number(p.value))+'" r="9"></circle>';
      if (values.length < 9 || i===0 || i===values.length-1) svg += '<text class="axis-text" x="'+x(i)+'" y="'+(h-5)+'" text-anchor="middle">'+(values.length>5 ? date.slice(3) : date)+'</text>';
    });
    svg += '</svg>';
    host.innerHTML = svg;
    host.appendChild(tooltip);
    var nodes = host.querySelectorAll("[data-point]");
    nodes.forEach(function(node){
      node.addEventListener("mouseenter", function(){
        var p = values[Number(node.getAttribute("data-point"))];
        tooltip.innerHTML = '<span>'+new Date(p.date+"T12:00:00").toLocaleDateString("en-GB",{day:"numeric",month:"long",year:"numeric"})+'</span><strong>'+fmtPence.format(Number(p.value))+'</strong><span>Net added: '+fmtPence.format(Number(p.contributed))+'</span>';
        tooltip.style.display = "block";
      });
      node.addEventListener("mousemove",function(e){
        var rect = host.getBoundingClientRect();
        var left = Math.min(rect.width-195, Math.max(5, e.clientX-rect.left+13));
        tooltip.style.left = left+"px";
        tooltip.style.top = Math.max(0, e.clientY-rect.top-68)+"px";
      });
      node.addEventListener("mouseleave",function(){tooltip.style.display="none";});
    });
  }
  buttons.forEach(function (button) {
    button.addEventListener("click", function () {
      active = button.getAttribute("data-range");
      buttons.forEach(function(b){b.classList.toggle("selected", b===button);});
      render();
    });
  });
  render();
})();
