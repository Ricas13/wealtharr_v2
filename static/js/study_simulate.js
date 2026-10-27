/* Study what-if: move a rule's edges, see how often the allocation would have
 * switched. Hypothetical, read-only, nothing is saved. */
(function () {
  "use strict";
  var root = document.querySelector("[data-sim]");
  if (!root) return;
  var form = root.querySelector("[data-sim-form]");
  var out = root.querySelector("[data-sim-result]");
  var url = root.getAttribute("data-sim-url");
  var timer = null;

  function text(tag, value, cls) {
    var el = document.createElement(tag);
    el.textContent = value;
    if (cls) el.className = cls;
    return el;
  }
  function show(data) {
    out.textContent = "";
    var rows = [
      ["Replayed", data.from + " to " + data.to],
      ["Allocation today", data.current],
      ["Switches", data.changes + " in total · about " + data.changes_per_year + " a year"]
    ];
    Object.keys(data.share).forEach(function (name) {
      rows.push(["Time in " + name, data.share[name] + "%"]);
    });
    rows.forEach(function (row) {
      var line = document.createElement("div");
      line.className = "w2-kv";
      line.appendChild(text("span", row[0]));
      line.appendChild(text("span", row[1]));
      out.appendChild(line);
    });
    if (data.recent.length) {
      out.appendChild(text("h3", "Latest switches", "w2-space-top"));
      data.recent.forEach(function (c) {
        var line = document.createElement("div");
        line.className = "w2-kv";
        line.appendChild(text("span", c.date));
        line.appendChild(text("span", c.from + " → " + c.to));
        out.appendChild(line);
      });
    }
    out.appendChild(text("p", data.note, "w2-note"));
  }
  function run() {
    var query = new URLSearchParams(new FormData(form)).toString();
    fetch(url + "?" + query, { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : r.text().then(function (t) { throw new Error(t); }); })
      .then(show)
      .catch(function (err) { out.textContent = ""; out.appendChild(text("p", err.message || "Could not run.", "w2-note w2-warn")); });
  }
  form.addEventListener("input", function (event) {
    var target = event.target;
    var label = root.querySelector('[data-sim-out="' + target.name + '"]');
    if (label) label.textContent = target.value;
    clearTimeout(timer);
    timer = setTimeout(run, 250);
  });
  root.querySelector("[data-sim-reset]").addEventListener("click", function () {
    form.querySelectorAll("input[type=range]").forEach(function (input) {
      input.value = input.getAttribute("data-default");
      var label = root.querySelector('[data-sim-out="' + input.name + '"]');
      if (label) label.textContent = input.value;
    });
    run();
  });
  run();
}());
