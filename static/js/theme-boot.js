/* Runs before the page paints, so a saved theme never flashes the wrong colours. */
(function () {
  try {
    var t = localStorage.getItem("lp_theme");
    if (t === "light") t = "parchment";
    if (t === "dark") t = "midnight";
    if (t === "parchment" || t === "midnight" || t === "chambers") {
      document.documentElement.setAttribute("data-theme", t);
    }
  } catch (e) { /* storage blocked: follow the device setting */ }
})();
