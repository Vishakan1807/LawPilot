/* Runs before the page paints, so a saved theme never flashes the wrong colours. */
(function () {
  try {
    var t = localStorage.getItem("lp_theme");
    var legacy = { light: "brief", parchment: "brief", dark: "blackcoat", midnight: "blackcoat", chambers: "courtroom" };
    if (legacy[t]) t = legacy[t];
    if (t === "brief" || t === "blackcoat" || t === "courtroom") {
      document.documentElement.setAttribute("data-theme", t);
    }
  } catch (e) { /* storage blocked: follow the device setting */ }
})();
