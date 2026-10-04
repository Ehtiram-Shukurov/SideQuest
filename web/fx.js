"use strict";
/* Small interface enhancements. No decorative loops or network requests. */
(function () {
  const req = document.getElementById("req");
  function fitRequest() {
    req.style.height = "auto";
    req.style.height = Math.max(116, req.scrollHeight + 2) + "px";
  }
  req.addEventListener("input", fitRequest);
  document.getElementById("examples").addEventListener("click", (e) => {
    const button = e.target.closest("button.eg");
    if (!button) return;
    req.value = button.dataset.text || "";
    req.dispatchEvent(new Event("input", { bubbles: true }));
    req.focus({ preventScroll: true });
  });
  new ResizeObserver(() => {
    if (req.offsetParent) fitRequest();
  }).observe(req.parentElement);
  fitRequest();
  let timer;
  window.fx = {
    working(on) {
      clearInterval(timer);
      document.body.classList.toggle("busy", !!on);
      const out = document.getElementById("workTimer");
      if (!on || !out) return;
      const started = Date.now();
      out.textContent = "0 s";
      timer = setInterval(() => {
        if (document.getElementById("working").classList.contains("hide")) {
          clearInterval(timer);
          document.body.classList.remove("busy");
          return;
        }
        out.textContent = Math.round((Date.now() - started) / 1000) + " s";
      }, 1000);
    },
  };
})();
