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
  function show(selector, visible) {
    document.querySelectorAll(selector).forEach(function (el) {
      el.hidden = !visible;
      el.querySelectorAll("input, select").forEach(function (input) {
        // Hidden number inputs still participate in native form validation.
        input.disabled = !visible;
      });
    });
  }
  function switchKind() {
    var trade = kind.value === "BUY" || kind.value === "SELL";
    var split = kind.value === "SPLIT";
    show("[data-v2-trade-field]", trade);
    show("[data-v2-cash-field]", !trade && !split);
    show("[data-v2-split-field]", split);
    show("[data-v2-fund-field]", trade || split);
  }
  kind.addEventListener("change", switchKind);
  switchKind();
}());

/* A strategy picker that points its form at that strategy's ledger. */
(function () {
  "use strict";
  document.querySelectorAll("select[data-w2-form-action]").forEach(function (select) {
    function updateTarget() {
      select.form.action = select.value;
      var current = select.options[select.selectedIndex];
      var label = select.form.querySelector("[data-w2-selected-currency]");
      if (label && current) label.textContent = current.dataset.currencySymbol || "";
    }
    select.addEventListener("change", updateTarget);
    updateTarget();
  });
})();

/* Quick search: press "/" or Ctrl/Cmd+K to jump to any page or strategy. */
(function () {
  "use strict";
  var dialog = document.getElementById("v2-palette");
  var input = document.getElementById("v2-palette-input");
  var list = document.getElementById("v2-palette-list");
  var data = document.getElementById("v2-palette-data");
  var opener = document.getElementById("v2-palette-open");
  if (!dialog || !input || !list || !data || typeof dialog.showModal !== "function") return;
  var items = Array.prototype.map.call(data.querySelectorAll("a[href]"), function (link) {
    return { label: link.textContent.replace(/\s+/g, " ").trim(), href: link.getAttribute("href") };
  });
  var shown = [];
  var active = 0;

  function render() {
    var words = input.value.toLowerCase().split(/\s+/).filter(Boolean);
    shown = items.filter(function (item) {
      var text = item.label.toLowerCase();
      return words.every(function (word) { return text.indexOf(word) !== -1; });
    }).slice(0, 12);
    active = Math.min(active, Math.max(shown.length - 1, 0));
    list.textContent = "";
    shown.forEach(function (item, index) {
      var row = document.createElement("li");
      row.setAttribute("role", "option");
      row.id = "v2-palette-opt-" + index;
      row.className = "w2-palette-row";
      row.setAttribute("aria-selected", String(index === active));
      row.textContent = item.label;
      row.addEventListener("mousedown", function (event) { event.preventDefault(); go(index); });
      list.appendChild(row);
    });
    if (!shown.length) {
      var none = document.createElement("li");
      none.className = "w2-palette-none";
      none.textContent = "Nothing matches.";
      list.appendChild(none);
    }
    input.setAttribute("aria-activedescendant", shown.length ? "v2-palette-opt-" + active : "");
  }
  function go(index) {
    if (shown[index]) window.location.assign(shown[index].href);
  }
  function open() {
    if (dialog.open) return;
    input.value = "";
    active = 0;
    render();
    dialog.showModal();
    input.focus();
  }
  function typing(target) {
    return target && (/^(input|textarea|select)$/i.test(target.tagName) || target.isContentEditable);
  }
  document.addEventListener("keydown", function (event) {
    var isShortcut = (event.key === "k" || event.key === "K") && (event.ctrlKey || event.metaKey);
    if (isShortcut || (event.key === "/" && !typing(event.target) && !event.ctrlKey &&
                       !event.metaKey && !event.altKey)) {
      event.preventDefault();
      open();
    }
  });
  if (opener) opener.addEventListener("click", open);
  input.addEventListener("input", function () { active = 0; render(); });
  input.addEventListener("keydown", function (event) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!shown.length) return;
      active = (active + (event.key === "ArrowDown" ? 1 : shown.length - 1)) % shown.length;
      render();
    } else if (event.key === "Enter") {
      event.preventDefault();
      go(active);
    }
  });
  dialog.addEventListener("click", function (event) {
    if (event.target === dialog) dialog.close();
  });
}());

/* Installable app: the worker caches static files only (see tracker/pwa.py). */
(function () {
  "use strict";
  if (!("serviceWorker" in navigator) || !window.isSecureContext) return;
  window.addEventListener("load", function () {
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(function () {
      /* The app works the same without it. */
    });
  });
}());
