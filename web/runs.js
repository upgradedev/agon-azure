/*
  Run data loader shared by the Newsroom Wall, the approval card and the
  recap (screens S2 to S4). Reads static files only:

    runs/index.json            which runs exist
    runs/<run_id>.json         the run trace
    runs/<run_id>/manifest.json and the edition files it lists

  Every response is checked: a non-200 or an HTML page where JSON was
  expected (the host's fallback page for a missing file) is an error, and the
  index and the run must pass AgonRuns.validateIndex and validateRun before
  anything is shown. A design preview run without its exact label is refused
  here, so no screen can show it unlabelled.

  Results are cached per page view; the Verify page does its own fetches,
  because it must hash the exact bytes it receives.
*/
(function () {
  "use strict";

  var core = window.AgonCore;
  var runs = window.AgonRuns;
  var cache = {};
  var indexPromise = null;

  function fetchJson(url) {
    return window.fetch(url, { cache: "no-store" }).then(function (response) {
      return response.text().then(function (text) {
        if (!response.ok || core.looksLikeHtml(text)) {
          throw new Error(url + " answered " + response.status + " instead of a JSON file");
        }
        try {
          return JSON.parse(text);
        } catch (error) {
          throw new Error(url + " is not valid JSON");
        }
      });
    });
  }

  function index() {
    if (!indexPromise) {
      indexPromise = fetchJson(runs.RUNS_BASE + runs.INDEX_NAME).then(function (data) {
        var problems = runs.validateIndex(data);
        if (problems.length) {
          throw new Error("the runs index breaks the run format: " + problems.join("; "));
        }
        return data;
      });
      indexPromise.catch(function () {
        indexPromise = null;
      });
    }
    return indexPromise;
  }

  /* The run to show when a route names none: the first in the index. */
  function defaultRunId() {
    return index().then(function (data) {
      return data.runs[0].run_id;
    });
  }

  function load(runId) {
    if (Object.prototype.hasOwnProperty.call(cache, runId)) {
      return cache[runId];
    }
    var promise = index()
      .then(function (data) {
        var listed = data.runs.some(function (row) {
          return row.run_id === runId;
        });
        if (!listed) {
          throw new Error("there is no run called " + runId + " in the runs index");
        }
        return fetchJson(runs.runUrl(runId));
      })
      .then(function (run) {
        var problems = runs.validateRun(run);
        if (problems.length) {
          throw new Error("the run file breaks the run format: " + problems.join("; "));
        }
        if (run.run_id !== runId) {
          throw new Error("the run file names itself " + run.run_id + ", not " + runId);
        }
        var manifestLoad = fetchJson(runs.bundleFileUrl(run, runs.BUNDLE_MANIFEST));
        var editionLoads = run.editions.map(function (e) {
          return fetchJson(runs.bundleFileUrl(run, e.file)).then(function (data) {
            if (!data || data.edition !== e.edition || !Array.isArray(data.sentences)) {
              throw new Error(e.file + " does not hold the " + e.name + " edition");
            }
            return data;
          });
        });
        return Promise.all([manifestLoad, Promise.all(editionLoads)]).then(function (out) {
          var editions = {};
          run.editions.forEach(function (e, i) {
            editions[e.edition] = { meta: e, data: out[1][i] };
          });
          return { run: run, manifest: out[0], editions: editions };
        });
      });
    cache[runId] = promise;
    promise.catch(function () {
      delete cache[runId];
    });
    return promise;
  }

  /* Resolve a route's run: its parameter, or the default run. */
  function forRoute(param) {
    return (param ? Promise.resolve(param) : defaultRunId()).then(load);
  }

  /* The English sentence with this id, for Confirm rows and flags. */
  function sentenceById(loaded, sentenceId) {
    var keys = Object.keys(loaded.editions);
    for (var i = 0; i < keys.length; i += 1) {
      var list = loaded.editions[keys[i]].data.sentences;
      for (var j = 0; j < list.length; j += 1) {
        if (list[j].sentence_id === sentenceId) {
          return { edition: keys[i], index: j, sentence: list[j] };
        }
      }
    }
    return null;
  }

  function matchLine(run) {
    return run.home.name + " " + run.score.home + ", " + run.away.name + " " + run.score.away +
      ". Synthetic match, seed " + run.seed + ", fictional clubs and players.";
  }

  window.AgonRunData = {
    index: index,
    load: load,
    forRoute: forRoute,
    sentenceById: sentenceById,
    matchLine: matchLine
  };
})();
