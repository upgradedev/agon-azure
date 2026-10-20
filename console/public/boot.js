/*
  Runs before first paint, as a blocking script in <head>, because the CSP
  forbids the usual inline snippet and a deferred script would flash.

  1. Applies a saved theme choice. With none, the OS preference decides
     through CSS.
  2. Marks a skipped guide, so the prerendered guide card never flashes in
     for a visitor who already skipped it.
  3. Copies the route from the address (#/verify) into data-boot-route, so
     the prerendered page shows that view until the app takes over.

  Storage can be blocked (private windows, strict settings); every read is
  wrapped, and the page works without it.
*/
(function () {
  "use strict";
  var root = document.documentElement;
  try {
    var theme = window.localStorage.getItem("agon-theme");
    if (theme === "light" || theme === "dark") {
      root.setAttribute("data-theme", theme);
    }
    if (window.localStorage.getItem("agon-guide") === "skipped") {
      root.setAttribute("data-guide", "skipped");
    }
  } catch (error) {
    /* No storage: the defaults apply. */
  }
  var known = ["newsroom", "approve", "recap", "verify", "data-lab", "media-office", "how-it-works", "about"];
  var match = /^#\/([a-z-]+)/.exec(window.location.hash || "");
  var key = match && known.indexOf(match[1]) !== -1 ? match[1] : "home";
  root.setAttribute("data-boot-route", key);
})();
