/*
  Verify page (screen S5): recompute every listed file's SHA-256 in the
  visitor's browser.

  The page fetches the same manifest the Match Centre uses (data/ first, then
  the committed fixture), downloads every file it lists, hashes the raw bytes
  with WebCrypto and compares each result with the manifest. The overall line
  is counted from those results on this page; nothing is asserted by a server.

  What a match proves is said on the page: the bytes received equal the bytes
  the manifest lists. Comparing with an approved bundle hash is not built yet.

  Run bundle check: for each run in runs/index.json, the page downloads the
  sealed manifest and every edition file it lists, hashes the exact bytes
  received, and compares (a) sha256 of manifest.json with the bundle hash the
  run and the approval card show, and (b) each file's sha256 and length with
  the manifest. AgonRuns.bundleCheck makes the comparison; nothing is taken
  from a server's say-so.
*/
(function () {
  "use strict";

  var core = window.AgonCore;
  var runsApi = window.AgonRuns;
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

  /* A world file is checked like an event file, under its own name and hash,
     without a line count. */
  function worldEntry(entry) {
    return { name: entry.world, sha256: entry.world_sha256, seed: entry.seed, events: null, world: true };
  }

  function checkFile(base, entry) {
    var result = { entry: entry, status: "error", computed: "", lines: null, reason: "" };
    var url;
    try {
      url = entry.world ? core.worldUrl(base, entry.name) : core.fileUrl(base, entry.name);
    } catch (error) {
      result.reason = error.message;
      return Promise.resolve(result);
    }
    return fetchBytes(url)
      .then(function (buffer) {
        var text = decode(buffer);
        if (core.looksLikeHtml(text)) {
          throw new Error(url + " returned a web page instead of the " + (entry.world ? "names file" : "event file"));
        }
        if (!entry.world) {
          result.lines = core.countLines(text);
        }
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
    list.appendChild(field(entry.world ? "Names for seed" : "Seed", String(entry.seed), false));
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
    var eventFiles = results.filter(function (r) { return !r.entry.world; }).length;
    setText(el.overall, "Recomputed in your browser: " + matches + " of " + total + " files match.");
    el.overall.className = "verify-overall " + (matches === total ? "verify-overall--ok" : "verify-overall--bad");
    setText(el.counts, "Event counts: " + countsAgree + " of " + eventFiles + " event files have the number of lines the manifest lists.");
    setText(el.manifestHash, manifestHex ? "manifest.json itself hashes to " + manifestHex + " in your browser." : "");
    el.manifestHash.hidden = !manifestHex;
  }

  /* ---------------------------------------------------------- run bundles */

  function fetchJsonBytes(url) {
    return fetchBytes(url).then(function (buffer) {
      var text = decode(buffer);
      if (core.looksLikeHtml(text)) {
        throw new Error(url + " returned a web page instead of a JSON file");
      }
      return { buffer: buffer, json: JSON.parse(text) };
    });
  }

  function hashFile(url) {
    return fetchBytes(url).then(function (buffer) {
      if (core.looksLikeHtml(decode(buffer))) {
        throw new Error(url + " returned a web page instead of a JSON file");
      }
      return hashBytes(buffer).then(function (hex) {
        return { sha256: hex, bytes: buffer.byteLength };
      });
    });
  }

  function checkRunBundle(row) {
    return fetchJsonBytes(runsApi.RUNS_BASE + row.file).then(function (runFile) {
      var runData = runFile.json;
      var problems = runsApi.validateRun(runData);
      if (problems.length) {
        throw new Error(row.file + " breaks the run format: " + problems.join("; "));
      }
      var manifestUrl = runsApi.bundleFileUrl(runData, runsApi.BUNDLE_MANIFEST);
      return fetchJsonBytes(manifestUrl).then(function (manifestFile) {
        var manifest = manifestFile.json;
        var names = Object.keys(manifest && manifest.artifacts ? manifest.artifacts : {}).sort();
        var computed = { manifest: "", files: {} };
        var jobs = names.map(function (name) {
          var url;
          try {
            url = runsApi.bundleFileUrl(runData, name);
          } catch (error) {
            return Promise.resolve();
          }
          return hashFile(url).then(function (got) {
            computed.files[name] = got;
          }, function () {
            /* Left out of computed.files: bundleCheck reports it as not fetched. */
          });
        });
        jobs.push(hashBytes(manifestFile.buffer).then(function (hex) {
          computed.manifest = hex;
        }));
        return Promise.all(jobs).then(function () {
          return { run: runData, result: runsApi.bundleCheck(runData, manifest, computed), computed: computed };
        });
      });
    });
  }

  var BUNDLE_STATUS = { match: "Match", mismatch: "Mismatch", error: "Not checked" };

  function renderBundle(checked) {
    var runData = checked.run;
    var result = checked.result;
    var item = document.createElement("li");
    item.className = "verify-row verify-row--" + (result.bundleMatches ? "match" : "mismatch");
    var head = document.createElement("div");
    head.className = "verify-row__head";
    var name = document.createElement("strong");
    name.textContent = "Bundle of " + runData.run_id;
    var chip = document.createElement("span");
    chip.className = result.bundleMatches ? "chip" : "chip chip--danger";
    chip.textContent = result.bundleMatches ? "Match" : "Mismatch";
    head.appendChild(name);
    head.appendChild(chip);
    item.appendChild(head);
    var list = document.createElement("dl");
    list.className = "verify-row__fields";
    list.appendChild(field("Bundle hash on the approval card", runData.bundle.sha256, true));
    list.appendChild(field("SHA-256 of manifest.json computed in your browser", checked.computed.manifest || "not computed", true));
    item.appendChild(list);
    var rows = [item];
    result.rows.forEach(function (r) {
      var fileItem = document.createElement("li");
      fileItem.className = "verify-row verify-row--" + r.status;
      var fileHead = document.createElement("div");
      fileHead.className = "verify-row__head";
      var fileName = document.createElement("strong");
      fileName.textContent = r.name;
      var fileChip = document.createElement("span");
      fileChip.className = r.status === "match" ? "chip" : "chip chip--danger";
      fileChip.textContent = BUNDLE_STATUS[r.status];
      fileHead.appendChild(fileName);
      fileHead.appendChild(fileChip);
      fileItem.appendChild(fileHead);
      var fields = document.createElement("dl");
      fields.className = "verify-row__fields";
      fields.appendChild(field("SHA-256 in the sealed manifest", r.expected, true));
      fields.appendChild(field("SHA-256 computed in your browser", r.computed || "not computed", true));
      fileItem.appendChild(fields);
      if (r.reason) {
        var reason = document.createElement("p");
        reason.className = "hint";
        reason.textContent = "Differs: " + r.reason + ".";
        fileItem.appendChild(reason);
      }
      rows.push(fileItem);
    });
    return rows;
  }

  function runBundles() {
    if (!runsApi || !window.AgonRunData || !subtle()) {
      setText(el.bundleOverall, "This browser cannot fetch and hash the run bundle on this page, so nothing was recomputed.");
      return Promise.resolve();
    }
    while (el.bundleList.firstChild) {
      el.bundleList.removeChild(el.bundleList.firstChild);
    }
    setText(el.bundleOverall, "Recomputing the run bundle in your browser.");
    el.bundleOverall.className = "verify-overall";
    return window.AgonRunData.index()
      .then(function (index) {
        return Promise.all(index.runs.map(checkRunBundle));
      })
      .then(function (results) {
        var good = 0;
        var labels = [];
        results.forEach(function (checked) {
          renderBundle(checked).forEach(function (node) {
            el.bundleList.appendChild(node);
          });
          if (checked.result.ok) {
            good += 1;
          }
          if (labels.indexOf(checked.run.label) === -1) {
            labels.push(checked.run.label);
          }
        });
        setText(el.bundleLabel, labels.join(" "));
        var first = results[0];
        var files = first.result.rows.length;
        var matched = first.result.rows.filter(function (r) { return r.status === "match"; }).length;
        var line = first.result.bundleMatches
          ? "Recomputed in your browser: the bundle hash equals the hash on the approval card, " + runsApi.shortHash(first.run.bundle.sha256) + ", and " + matched + " of " + files + " edition files match the sealed manifest."
          : "Recomputed in your browser: the bundle hash does not equal the hash on the approval card, and " + matched + " of " + files + " edition files match the sealed manifest.";
        if (results.length > 1) {
          line += " " + good + " of " + results.length + " run bundles check out in full.";
        }
        setText(el.bundleOverall, line);
        el.bundleOverall.className = "verify-overall " + (good === results.length ? "verify-overall--ok" : "verify-overall--bad");
        setText(el.bundleChip, good === results.length ? "Match" : "Mismatch");
        el.bundleChip.className = good === results.length ? "chip" : "chip chip--danger";
      })
      .catch(function (error) {
        setText(el.bundleChip, "Unavailable");
        el.bundleChip.className = "chip chip--danger";
        setText(el.bundleOverall, "The run bundle was not recomputed: " + error.message + ".");
        el.bundleOverall.className = "verify-overall verify-overall--bad";
      });
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
          files.length + (files.length === 1 ? " match" : " matches") + ", each with an event file and a names file." +
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
        var checks = [];
        files.forEach(function (entry) {
          checks.push(checkFile(resolved.base, entry));
          checks.push(checkFile(resolved.base, worldEntry(entry)));
        });
        return Promise.all([manifestHash, Promise.all(checks), runBundles()]).then(function (out) {
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
      button: "recompute", buttonText: "recompute-text", bundleChip: "bundle-chip", bundleLabel: "bundle-label",
      bundleOverall: "bundle-overall", bundleList: "bundle-list"
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
