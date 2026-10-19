/*
  Agon web shell behaviour.

  No framework and no build step: the page is served as static files by Azure
  Static Web Apps under a CSP with script-src 'self'. So there is no inline
  script, every DOM write uses textContent or created nodes, and nothing is
  evaluated from strings. ci/web_check.py enforces those rules.

  Nothing here calls a backend. The preview is honest about that: the primary
  button opens a panel that says the newsroom is not connected.
*/
(function () {
  "use strict";

  var COMMIT_PATTERN = /^[0-9a-f]{7,40}$/i;
  var REPO_URL = "https://github.com/upgradedev/agon-azure";
  var THEME_KEY = "agon-theme";
  var ROUTES = ["/", "/verify", "/about"];

  /* SHA-256 of "abc", from FIPS 180-2 appendix B.1. Used as a known answer test. */
  var SHA256_ABC = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";

  var MODE_HINTS = {
    analyst: "Analyst mode is planned to lead with tactics and the turning points of the match.",
    casual: "Casual mode is planned to tell the story of the match in plain words."
  };
  var MODE_NAMES = { analyst: "Analyst mode", casual: "Casual mode" };

  function byId(id) {
    return document.getElementById(id);
  }

  /* ---------------------------------------------------------- run label */

  /* The deploy step replaces the placeholder in data-commit with the commit
     sha. Anything that is not a hex sha is reported as not stamped rather
     than shown, so a local build can never pose as a deployed commit. */
  function initRunLabel() {
    var label = byId("run-label");
    var sha = (document.body.getAttribute("data-commit") || "").trim();
    if (!label || !COMMIT_PATTERN.test(sha)) {
      return;
    }
    var link = document.createElement("a");
    link.href = REPO_URL + "/commit/" + sha;
    var code = document.createElement("code");
    code.textContent = sha.slice(0, 7);
    link.appendChild(code);
    link.setAttribute("aria-label", "commit " + sha);
    label.textContent = "Preview build, commit ";
    label.appendChild(link);
  }

  /* ---------------------------------------------------------- theme */

  function systemPrefersDark() {
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  }

  function currentTheme() {
    var explicit = document.documentElement.getAttribute("data-theme");
    if (explicit === "light" || explicit === "dark") {
      return explicit;
    }
    return systemPrefersDark() ? "dark" : "light";
  }

  function syncThemeToggle() {
    var toggle = byId("theme-toggle");
    if (!toggle) {
      return;
    }
    var next = currentTheme() === "dark" ? "light" : "dark";
    toggle.setAttribute("data-next", next);
    toggle.setAttribute("aria-label", "Switch to " + next + " theme");
  }

  function initTheme() {
    var toggle = byId("theme-toggle");
    if (!toggle) {
      return;
    }
    syncThemeToggle();
    toggle.addEventListener("click", function () {
      var next = currentTheme() === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      try {
        window.localStorage.setItem(THEME_KEY, next);
      } catch (error) {
        /* The choice still applies for this visit. */
      }
      syncThemeToggle();
    });
    if (window.matchMedia) {
      var query = window.matchMedia("(prefers-color-scheme: dark)");
      var onChange = function () {
        syncThemeToggle();
      };
      if (query.addEventListener) {
        query.addEventListener("change", onChange);
      } else if (query.addListener) {
        query.addListener(onChange);
      }
    }
  }

  /* ---------------------------------------------------------- router */

  /* Hash routes only: Static Web Apps serves one index.html and the hash
     never reaches the server. Hashes that do not start with "#/" are in-page
     anchors (the skip link) and leave the current view alone. */
  function pathFromHash(hash) {
    if (!hash || hash === "#" || hash === "#/") {
      return "/";
    }
    if (hash.indexOf("#/") !== 0) {
      return null;
    }
    var path = hash.slice(1).split("?")[0];
    if (path.length > 1 && path.charAt(path.length - 1) === "/") {
      path = path.slice(0, -1);
    }
    return path;
  }

  function render(path, moveFocus) {
    var key = ROUTES.indexOf(path) === -1 ? "not-found" : path;
    var views = document.querySelectorAll("[data-view]");
    var active = null;
    for (var i = 0; i < views.length; i += 1) {
      var matches = views[i].getAttribute("data-view") === key;
      views[i].hidden = !matches;
      if (matches) {
        active = views[i];
      }
    }

    var links = document.querySelectorAll(".nav a[data-route]");
    for (var j = 0; j < links.length; j += 1) {
      if (links[j].getAttribute("data-route") === key) {
        links[j].setAttribute("aria-current", "page");
      } else {
        links[j].removeAttribute("aria-current");
      }
    }

    document.body.classList.toggle("has-action-bar", key === "/");

    if (active) {
      document.title = "Agon | " + (active.getAttribute("data-title") || "Agon");
      if (moveFocus) {
        var heading = active.querySelector("h1");
        window.scrollTo(0, 0);
        if (heading) {
          heading.focus();
        }
      }
    }
  }

  function initRouter() {
    var initial = pathFromHash(window.location.hash);
    render(initial === null ? "/" : initial, false);
    window.addEventListener("hashchange", function () {
      var path = pathFromHash(window.location.hash);
      if (path !== null) {
        render(path, true);
      }
    });
  }

  /* ---------------------------------------------------------- controls */

  function checkedValue(name) {
    var input = document.querySelector("input[name='" + name + "']:checked");
    return input ? input.value : "";
  }

  function choiceSummary() {
    return "Seed " + checkedValue("seed") + ", " + (MODE_NAMES[checkedValue("mode")] || "Analyst mode");
  }

  function initControls() {
    var modeHint = byId("mode-hint");
    var modeInputs = document.querySelectorAll("input[name='mode']");
    for (var i = 0; i < modeInputs.length; i += 1) {
      modeInputs[i].addEventListener("change", function (event) {
        if (modeHint && MODE_HINTS[event.target.value]) {
          modeHint.textContent = MODE_HINTS[event.target.value];
        }
      });
    }

    /* aria-disabled keeps the control focusable so its reason can be found;
       this listener makes sure activating it does nothing. */
    var disabled = document.querySelectorAll("[aria-disabled='true']");
    for (var j = 0; j < disabled.length; j += 1) {
      disabled[j].addEventListener("click", function (event) {
        event.preventDefault();
        event.stopImmediatePropagation();
      });
    }
  }

  /* ---------------------------------------------------------- full-time panel */

  function initFullTimeDialog() {
    var button = byId("blow-full-time");
    var dialog = byId("full-time-dialog");
    var close = byId("ft-close");
    var choice = byId("ft-choice");
    if (!button || !dialog) {
      return;
    }
    var opener = null;

    function closeDialog() {
      if (typeof dialog.close === "function") {
        dialog.close();
      } else {
        dialog.removeAttribute("open");
        restoreFocus();
      }
    }

    function restoreFocus() {
      if (opener && typeof opener.focus === "function") {
        opener.focus();
      }
    }

    button.addEventListener("click", function () {
      opener = button;
      if (choice) {
        choice.textContent = choiceSummary();
      }
      if (typeof dialog.showModal === "function") {
        dialog.showModal();
      } else {
        dialog.setAttribute("open", "");
      }
      if (close) {
        close.focus();
      }
    });

    if (close) {
      close.addEventListener("click", closeDialog);
    }
    dialog.addEventListener("close", restoreFocus);
    /* A click on the backdrop lands on the dialog element itself. */
    dialog.addEventListener("click", function (event) {
      if (event.target === dialog) {
        closeDialog();
      }
    });
  }

  /* ---------------------------------------------------------- WebCrypto self-test */

  function toHex(buffer) {
    var bytes = new Uint8Array(buffer);
    var out = "";
    for (var i = 0; i < bytes.length; i += 1) {
      out += (bytes[i] < 16 ? "0" : "") + bytes[i].toString(16);
    }
    return out;
  }

  function setCryptoResult(ok, message) {
    var status = byId("crypto-status");
    var chip = byId("crypto-chip");
    if (status) {
      status.textContent = message;
    }
    if (chip) {
      chip.textContent = ok ? "Ready" : "Unavailable";
      chip.className = ok ? "chip" : "chip chip--planned";
    }
  }

  /* Proves on the visitor's own device that the hashing the Verify page will
     rely on works, by checking a published test vector. */
  function initCryptoSelfTest() {
    if (!byId("crypto-status")) {
      return;
    }
    var subtle = window.crypto && window.crypto.subtle;
    if (!subtle || typeof TextEncoder === "undefined") {
      setCryptoResult(false, "This browser cannot compute SHA-256 on this page. WebCrypto needs a secure https page, so verification would not run here.");
      return;
    }
    subtle
      .digest("SHA-256", new TextEncoder().encode("abc"))
      .then(function (digest) {
        if (toHex(digest) === SHA256_ABC) {
          setCryptoResult(true, "This browser computed the standard SHA-256 test vector correctly, so it can verify published files on this device.");
        } else {
          setCryptoResult(false, "This browser returned a wrong SHA-256 result for the standard test vector, so its verification could not be trusted.");
        }
      })
      .catch(function () {
        setCryptoResult(false, "This browser refused to compute SHA-256 on this page, so verification would not run here.");
      });
  }

  /* ---------------------------------------------------------- start */

  initRunLabel();
  initTheme();
  initRouter();
  initControls();
  initFullTimeDialog();
  initCryptoSelfTest();
})();
