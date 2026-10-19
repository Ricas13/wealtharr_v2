"use strict";
document.querySelectorAll("[data-loading-form]").forEach(function(form) {
  form.addEventListener("submit", function() {
    var button = form.querySelector("button[type=submit]");
    if (!button) return;
    button.textContent = button.getAttribute("data-loading-text") || "Checking…";
    button.setAttribute("aria-busy", "true");
    button.disabled = true;
  });
});
// Existing site.js changes the selected visual class. Keep ARIA state aligned.
document.querySelectorAll('[data-range]').forEach(function(button) {
  button.addEventListener('click', function() {
    document.querySelectorAll('[data-range]').forEach(function(peer) {
      peer.setAttribute('aria-pressed', String(peer === button));
    });
  });
});
