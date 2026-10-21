/* No SPA router: the v2 navigation uses Django URLs and native <details>. */
(function () {
  "use strict";
  var body = document.body;
  var sidebar = document.getElementById("v2-sidebar");
  var toggle = document.getElementById("v2-mobile-nav");
  var shade = document.getElementById("v2-nav-shade");
  var collapse = document.getElementById("v2-collapse");
  if (!sidebar || !toggle || !shade || !collapse) return;

  var storageKey = "wealtharr-v2-sidebar-collapsed";
  try {
    if (window.localStorage.getItem(storageKey) === "1") {
      body.classList.add("w2-collapsed");
    }
  } catch (_) { /* Private browsing can disable storage. */ }

  function closeDrawer(restoreFocus) {
    var wasOpen = body.classList.contains("w2-drawer-open");
    body.classList.remove("w2-drawer-open");
    toggle.setAttribute("aria-expanded", "false");
    toggle.setAttribute("aria-label", "Open navigation");
    if (wasOpen && restoreFocus) toggle.focus();
  }
  function openDrawer() {
    body.classList.add("w2-drawer-open");
    toggle.setAttribute("aria-expanded", "true");
    toggle.setAttribute("aria-label", "Close navigation");
    var focusable = sidebar.querySelector("a, summary, button");
    if (focusable) focusable.focus();
  }
  toggle.addEventListener("click", function () {
    if (body.classList.contains("w2-drawer-open")) closeDrawer(true);
    else openDrawer();
  });
  shade.addEventListener("click", function () { closeDrawer(true); });
  sidebar.addEventListener("click", function (event) {
    if (event.target.closest("a")) closeDrawer(false);
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closeDrawer(true);
    if (event.key !== "Tab" || !body.classList.contains("w2-drawer-open")) return;
    var els = Array.prototype.slice.call(sidebar.querySelectorAll("a, button, summary"))
      .filter(function (node) { return node.getClientRects().length > 0; });
    if (!els.length) return;
    var first = els[0], last = els[els.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault(); last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault(); first.focus();
    }
  });
  window.addEventListener("resize", function () {
    if (window.innerWidth > 900) closeDrawer(false);
  });

  function updateCollapse() {
    var isCollapsed = body.classList.contains("w2-collapsed");
    collapse.setAttribute("aria-pressed", String(isCollapsed));
    collapse.setAttribute("aria-label", isCollapsed ? "Expand sidebar" : "Collapse sidebar");
    collapse.setAttribute("title", isCollapsed ? "Expand sidebar" : "Collapse sidebar");
  }
  collapse.addEventListener("click", function () {
    body.classList.toggle("w2-collapsed");
    updateCollapse();
    try {
      window.localStorage.setItem(
        storageKey, body.classList.contains("w2-collapsed") ? "1" : "0"
      );
    } catch (_) { /* UI continues without persistence. */ }
  });
  updateCollapse();
  // Setup: changing the strategy or broker reloads the page with its funds.
  var reloaders = document.querySelectorAll("[data-w2-reload-with]");
  Array.prototype.forEach.call(reloaders, function (select) {
    select.addEventListener("change", function () {
      var query = Array.prototype.map.call(reloaders, function (each) {
        return each.name + "=" + encodeURIComponent(each.value);
      }).join("&");
      window.location.assign(window.location.pathname + "?" + query);
    });
  });
  var modeInputs = document.querySelectorAll("input[name=mode]");
  function updateMode() {
    var checked = document.querySelector("input[name=mode]:checked");
    if (!checked) return;
    var fresh = document.getElementById("w2-fresh-fields");
    var hold = document.getElementById("w2-hold-fields");
    if (fresh) fresh.hidden = checked.value !== "fresh";
    if (hold) hold.hidden = checked.value !== "hold";
  }
  Array.prototype.forEach.call(modeInputs, function (input) {
    input.addEventListener("change", updateMode);
  });
  updateMode();
  Array.prototype.forEach.call(
    sidebar.querySelectorAll(".w2-strategy-group > summary"), function (summary) {
      summary.addEventListener("click", function (event) {
        if (window.innerWidth > 900 && body.classList.contains("w2-collapsed")) {
          var today = summary.parentElement.querySelector(".w2-subnav a");
          if (today) { event.preventDefault(); window.location.assign(today.href); }
        }
      });
    }
  );
}());

/* Phase 3 entry form: reveal relevant fields, not a client-side validation gate. */
(function () {
  "use strict";
  var kind = document.getElementById("w2-entry-kind");
  if (!kind) return;
  function switchKind() {
    var trade = kind.value === "BUY" || kind.value === "SELL";
    document.querySelectorAll("[data-v2-trade-field]").forEach(function (el) {
      el.hidden = !trade;
      el.querySelectorAll("input, select").forEach(function (input) {
        // Hidden number inputs still participate in native form validation.
        input.disabled = !trade;
      });
    });
    document.querySelectorAll("[data-v2-cash-field]").forEach(function (el) {
      el.hidden = trade;
      el.querySelectorAll("input, select").forEach(function (input) {
        input.disabled = trade;
      });
    });
  }
  kind.addEventListener("change", switchKind);
  switchKind();
}());

/* A strategy picker that points its form at that strategy's ledger. */
(function () {
  "use strict";
  document.querySelectorAll("select[data-w2-form-action]").forEach(function (select) {
    select.addEventListener("change", function () { select.form.action = select.value; });
  });
})();
