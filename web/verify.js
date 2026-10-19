/*
  Verify page (screen S5): recompute every listed file's SHA-256 in the
  visitor's browser.

  The page fetches the same manifest the Match Centre uses (data/ first, then
  the committed fixture), downloads every file it lists, hashes the raw bytes
  with WebCrypto and compares each result with the manifest. The overall line
  is counted from those results on this page; nothing is asserted by a server.

  What a match proves is said on the page: the bytes received equal the bytes
  the manifest lists. Comparing with an approved bundle hash is not built yet.
*/
(function () {
  "use strict";

  var core = window.AgonCore;
  var el = {};
  var running = false;
  var ranOnce = false;

  function byId(id) {
    return document.getElementById(id);
  }

  function setText(node, text) {
    if (node) {
      node.textContent = text;
    }
  }

  function subtle() {
    return window.crypto && window.crypto.subtle ? window.crypto.subtle : null;
  }

  function fetchBytes(url) {
    return window.fetch(url, { cache: "no-store" }).then(function (response) {
      if (!response.ok) {
        throw new Error(url + " answered " + response.status);
      }
      return response.arrayBuffer();
    });
  }

  function decode(buffer) {
    return new TextDecoder("utf-8").decode(new Uint8Array(buffer));
  }

  /* Hash exactly the bytes received, never a decoded and re-encoded copy. */
  function hashBytes(buffer) {
    return subtle()
      .digest("SHA-256", buffer)
      .then(core.toHex);
  }

  function checkFile(base, entry) {
    var result = { entry: entry, status: "error", computed: "", lines: null, reason: "" };
    var url;
    try {
      url = core.fileUrl(base, entry.name);
    } catch (error) {
      result.reason = error.message;
      return Promise.resolve(result);
    }
    return fetchBytes(url)
      .then(function (buffer) {
        var text = decode(buffer);
        if (core.looksLikeHtml(text)) {
          throw new Error(url + " returned a web page instead of the event file");
        }
        result.lines = core.countLines(text);
        return hashBytes(buffer);
      })
      .then(function (hex) {
        result.computed = hex;
        result.status = hex === entry.sha256 ? "match" : "mismatch";
        return result;
      })
      .catch(function (error) {
        result.reason = error.message;
        return result;
      });
  }

  /* ---------------------------------------------------------- rendering */

  function field(label, value, mono) {
    var row = document.createElement("div");
    row.className = "verify-row__field";
    var dt = document.createElement("dt");
    dt.textContent = label;
    var dd = document.createElement("dd");
    if (mono) {
      var code = document.createElement("code");
      code.textContent = value;
      dd.appendChild(code);
    } else {
      dd.textContent = value;
    }
    row.appendChild(dt);
    row.appendChild(dd);
    return row;
  }

  var STATUS_TEXT = { match: "Match", mismatch: "Mismatch", error: "Not checked" };
  var STATUS_CLASS = { match: "chip", mismatch: "chip chip--danger", error: "chip chip--danger" };

  function renderRow(result) {
    var entry = result.entry;
    var item = document.createElement("li");
    item.className = "verify-row verify-row--" + result.status;

    var head = document.createElement("div");
    head.className = "verify-row__head";
    var name = document.createElement("strong");
    name.textContent = String(entry.name);
    var chip = document.createElement("span");
    chip.className = STATUS_CLASS[result.status];
    chip.textContent = STATUS_TEXT[result.status];
    head.appendChild(name);
    head.appendChild(chip);
    item.appendChild(head);

    var list = document.createElement("dl");
    list.className = "verify-row__fields";
    list.appendChild(field("Seed", String(entry.seed), false));
    list.appendChild(field("SHA-256 in the manifest", String(entry.sha256), true));
    list.appendChild(field("SHA-256 computed in your browser", result.computed || "not computed", true));
    if (result.lines !== null) {
      var agree = result.lines === entry.events;
      list.appendChild(field("Events", result.lines + " lines in the file, the manifest lists " + entry.events + (agree ? "" : ". These differ."), false));
    }
    item.appendChild(list);

    if (result.reason) {
      var reason = document.createElement("p");
      reason.className = "hint";
      reason.textContent = "Not checked: " + result.reason + ".";
      item.appendChild(reason);
    }
    return item;
  }

  function clearList() {
    while (el.list.firstChild) {
      el.list.removeChild(el.list.firstChild);
    }
  }

  function finish(resolved, results, manifestHex) {
    clearList();
    var matches = 0;
    var countsAgree = 0;
    for (var i = 0; i < results.length; i += 1) {
      el.list.appendChild(renderRow(results[i]));
      if (results[i].status === "match") {
        matches += 1;
      }
      if (results[i].lines !== null && results[i].lines === results[i].entry.events) {
        countsAgree += 1;
      }
    }
    var total = results.length;
    setText(el.overall, "Recomputed in your browser: " + matches + " of " + total + " files match.");
    el.overall.className = "verify-overall " + (matches === total ? "verify-overall--ok" : "verify-overall--bad");
    setText(el.counts, "Event counts: " + countsAgree + " of " + total + " files have the number of lines the manifest lists.");
    setText(el.manifestHash, manifestHex ? "manifest.json itself hashes to " + manifestHex + " in your browser." : "");
    el.manifestHash.hidden = !manifestHex;
  }

  function run() {
    if (running || !core) {
      return;
    }
    running = true;
    ranOnce = true;
    el.button.setAttribute("aria-busy", "true");
    setText(el.buttonText, "Recomputing");
    setText(el.overall, "Recomputing in your browser.");
    el.overall.className = "verify-overall";

    core
      .resolveManifest(window.fetch.bind(window))
      .then(function (resolved) {
        var fixture = resolved.source === "fixture";
        setText(el.chip, fixture ? "Fixture" : "Deployed data");
        el.chip.className = fixture ? "chip chip--planned" : "chip";
        var files = resolved.manifest.files;
        setText(el.source, "Manifest " + resolved.manifestUrl + ", generator " + resolved.manifest.generator + ", " +
          files.length + (files.length === 1 ? " file." : " files.") +
          (fixture ? " These are the committed development fixture files, not the deployed match files." : ""));
        setText(el.notice, resolved.notice || "");
        el.notice.hidden = !resolved.notice;

        if (!subtle()) {
          var none = files.map(function (entry) {
            return { entry: entry, status: "error", computed: "", lines: null, reason: "this browser cannot compute SHA-256 on this page" };
          });
          finish(resolved, none, "");
          return null;
        }
        var manifestHash = fetchBytes(resolved.manifestUrl).then(hashBytes, function () {
          return "";
        });
        var checks = files.map(function (entry) {
          return checkFile(resolved.base, entry);
        });
        return Promise.all([manifestHash, Promise.all(checks)]).then(function (out) {
          finish(resolved, out[1], out[0]);
        });
      })
      .catch(function (error) {
        clearList();
        setText(el.chip, "Unavailable");
        el.chip.className = "chip chip--danger";
        setText(el.overall, "Nothing was recomputed: " + error.message);
        el.overall.className = "verify-overall verify-overall--bad";
      })
      .then(function () {
        running = false;
        el.button.removeAttribute("aria-busy");
        setText(el.buttonText, "Recompute again");
      });
  }

  function init() {
    var ids = {
      chip: "verify-chip", source: "verify-source", notice: "verify-notice", overall: "verify-overall",
      counts: "verify-counts", manifestHash: "verify-manifest-hash", list: "verify-list",
      button: "recompute", buttonText: "recompute-text"
    };
    for (var key in ids) {
      if (Object.prototype.hasOwnProperty.call(ids, key)) {
        el[key] = byId(ids[key]);
        if (!el[key]) {
          return;
        }
      }
    }
    if (!core || typeof window.fetch !== "function" || typeof TextDecoder === "undefined") {
      setText(el.overall, "This browser cannot fetch and hash files on this page, so nothing was recomputed.");
      return;
    }
    el.button.addEventListener("click", run);
    /* Runs when the visitor opens the page, not on every visit to the site. */
    document.addEventListener("agon:route", function (event) {
      if (event.detail && event.detail.path === "/verify" && !ranOnce) {
        run();
      }
    });
  }

  init();
})();
