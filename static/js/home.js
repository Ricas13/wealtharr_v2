// Signed-out homepage: example review, strategy comparison and how-it-works steps.
(function () {
  "use strict";

  function press(buttons, active) {
    buttons.forEach(function (b) {
      var on = b === active;
      b.classList.toggle("on", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  // Example quarterly review.
  var demo = document.querySelector("[data-demo]");
  if (demo) {
    var stage = 0;
    var steppers = Array.prototype.slice.call(demo.querySelectorAll(".stepper [data-demo-go]"));
    var show = function (n) {
      stage = n;
      demo.querySelectorAll("[data-stage]").forEach(function (el) { el.hidden = Number(el.dataset.stage) !== n; });
      demo.querySelector("[data-mix]").hidden = n === 0;
      var growth = n === 2 ? 60 : 68;
      demo.querySelector("[data-mix-fill]").style.width = growth + "%";
      demo.querySelector("[data-mix-growth]").textContent = growth;
      demo.querySelector("[data-mix-safe]").textContent = 100 - growth;
      press(steppers, steppers[n]);
    };
    demo.addEventListener("click", function (e) {
      var next = e.target.closest("[data-demo-next]");
      var go = e.target.closest("[data-demo-go]");
      if (next) show(Math.min(2, stage + 1));
      else if (go) show(Number(go.dataset.demoGo));
    });
  }

  // How it works.
  var steps = Array.prototype.slice.call(document.querySelectorAll("[data-step]"));
  steps.forEach(function (b) {
    b.addEventListener("click", function () {
      press(steps, b);
      document.querySelectorAll("[data-step-text]").forEach(function (p) { p.hidden = p.dataset.stepText !== b.dataset.step; });
    });
  });
})();
