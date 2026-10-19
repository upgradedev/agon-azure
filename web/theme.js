/*
  Applies a saved theme choice before first paint.

  Loaded as a blocking script in <head> because the CSP forbids the usual
  inline snippet, and a deferred script would flash the wrong theme. With no
  saved choice nothing is set and the OS preference decides through CSS.
*/
(function () {
  "use strict";
  try {
    var saved = window.localStorage.getItem("agon-theme");
    if (saved === "light" || saved === "dark") {
      document.documentElement.setAttribute("data-theme", saved);
    }
  } catch (error) {
    /* Storage can be blocked in private windows; the OS preference still applies. */
  }
})();
