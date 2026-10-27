/* A sent form shows it was sent and cannot be sent twice by a second tap. No data is read or changed. */
(function () {
  "use strict";
  function clear() {
    document.querySelectorAll(".is-busy").forEach(function (button) {
      button.classList.remove("is-busy");
      button.removeAttribute("aria-busy");
    });
  }
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form || form.method !== "post" || event.defaultPrevented) return;
    if (form.dataset.w2Sent === "1") { event.preventDefault(); return; }
    form.dataset.w2Sent = "1";
    var button = event.submitter || form.querySelector("button[type=submit], .w2-button-primary");
    // A class, not `disabled`: a disabled submitter would drop its name/value from the request.
    if (button) { button.classList.add("is-busy"); button.setAttribute("aria-busy", "true"); }
  });
  // Back/forward restores the page from cache: let the form be used again.
  window.addEventListener("pageshow", function () {
    document.querySelectorAll("form[data-w2-sent]").forEach(function (form) { delete form.dataset.w2Sent; });
    clear();
  });
})();
