/*
  Newsroom Wall (screen S2).

  Shows one run: desk columns of plain-word lines, the handoffs into the
  bundle, the bundle state chip walking DRAFT, CHECKED, SEALED,
  AWAITING_APPROVAL, and a right rail of gate verdicts. On a phone the
  desks and handoffs become one vertical timeline with the state chip pinned
  at the top (CSS shows one layout or the other).

  Replay reveals the run one step at a time. Under prefers-reduced-motion it
  never advances on its own; Next step moves it. State changes are announced
  in a polite live region, only while replaying.

  The run label is always on screen. The only run today is the hand-written
  design preview, and the static copy in index.html says so before this
  script runs, so the label is present even without JavaScript.
*/
(function () {
  "use strict";

  var runs = window.AgonRuns;
  var data = window.AgonRunData;
  var SVG_NS = "http://www.w3.org/2000/svg";
  var STEP_MS = 1100;
  var ALL = Infinity;

  var el = {};
  var state = { loaded: null, step: ALL, timer: 0, token: 0, reduceMotion: false, lastAnnounced: "" };

  function byId(id) {
    return document.getElementById(id);
  }

  function setText(node, text) {
    if (node && node.textContent !== text) {
      node.textContent = text;
    }
  }

  function clear(node) {
    while (node.firstChild) {
      node.removeChild(node.firstChild);
    }
  }

  function make(tag, className, text) {
    var node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== undefined) {
      node.textContent = text;
    }
    return node;
  }

  function arrow() {
    var svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("class", "handoff__arrow");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    var path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", "M4 12h14M13 6l6 6-6 6");
    svg.appendChild(path);
    return svg;
  }

  function stepBadge(step) {
    return make("span", "step-badge", "Step " + step);
  }

  function visible(step) {
    return step <= state.step;
  }

  /* ---------------------------------------------------------- render */

  function renderStates(run) {
    var current = runs.stateAt(run, state.step);
    setText(el.chip, runs.stateWords(current));
    el.chip.setAttribute("data-state", current || "NONE");
    clear(el.states);
    var reached = runs.WALL_STATES.indexOf(current);
    runs.WALL_STATES.forEach(function (name, i) {
      var li = make("li", "state-step", runs.stateWords(name));
      if (i < reached) {
        li.classList.add("is-done");
      }
      if (name === current) {
        li.classList.add("is-current");
        li.setAttribute("aria-current", "step");
      }
      el.states.appendChild(li);
    });
    return current;
  }

  function renderDesks(run) {
    clear(el.desks);
    run.desks.forEach(function (desk) {
      var column = make("section", "desk");
      column.setAttribute("aria-label", desk.name);
      column.appendChild(make("h3", "desk__name", desk.name));
      var list = make("ol", "desk__lines");
      desk.lines.forEach(function (line) {
        var li = make("li", "desk__line");
        li.appendChild(stepBadge(line.step));
        li.appendChild(make("span", "desk__text", line.text));
        li.hidden = !visible(line.step);
        list.appendChild(li);
      });
      column.appendChild(list);
      el.desks.appendChild(column);
    });
  }

  function renderHandoffs(run) {
    var names = runs.deskNames(run);
    clear(el.handoffs);
    run.handoffs.forEach(function (h) {
      var li = make("li", "handoff");
      var route = make("p", "handoff__route");
      route.appendChild(stepBadge(h.step));
      route.appendChild(make("span", "handoff__desk", names[h.from]));
      route.appendChild(arrow());
      route.appendChild(make("span", "visually-hidden", " to "));
      route.appendChild(make("span", "handoff__desk", names[h.to]));
      li.appendChild(route);
      li.appendChild(make("p", "handoff__text", h.text));
      var into = runs.stateAt(run, h.step);
      if (into) {
        li.appendChild(make("span", "chip chip--state handoff__state", runs.stateWords(into)));
      }
      li.hidden = !visible(h.step);
      el.handoffs.appendChild(li);
    });
  }

  function renderTimeline(run) {
    clear(el.timeline);
    runs.wallItems(run).forEach(function (item) {
      var li = make("li", "timeline__item timeline__item--" + item.kind);
      li.appendChild(stepBadge(item.step));
      if (item.kind === "line") {
        li.appendChild(make("strong", "timeline__desk", item.deskName));
        li.appendChild(make("span", "timeline__text", item.text));
      } else if (item.kind === "handoff") {
        var route = make("span", "timeline__desk");
        route.appendChild(make("span", "", item.from));
        route.appendChild(arrow());
        route.appendChild(make("span", "visually-hidden", " to "));
        route.appendChild(make("span", "", item.to));
        li.appendChild(route);
        li.appendChild(make("span", "timeline__text", item.text));
      } else {
        li.appendChild(make("span", "chip chip--state", runs.stateWords(item.state)));
      }
      li.hidden = !visible(item.step);
      el.timeline.appendChild(li);
    });
  }

  function renderGates(run) {
    clear(el.gates);
    run.gates.forEach(function (g) {
      var li = make("li", "gate gate--" + g.verdict);
      var head = make("p", "gate__head");
      head.appendChild(make("strong", "", g.name));
      var chip = make("span", g.verdict === "pass" ? "chip" : "chip chip--danger", g.verdict === "pass" ? "Pass" : g.verdict === "mismatch" ? "Mismatch" : g.verdict);
      head.appendChild(chip);
      li.appendChild(head);
      li.appendChild(make("p", "gate__detail", g.detail));
      var details = make("details", "ids-row");
      details.appendChild(make("summary", "", "Gate output"));
      details.appendChild(make("p", "small muted", g.evidence));
      li.appendChild(details);
      el.gates.appendChild(li);
    });
  }

  function renderStepText(run) {
    var last = runs.lastStep(run);
    if (state.step === ALL || state.step >= last) {
      setText(el.stepText, "Showing every step, " + last + " in all.");
    } else {
      setText(el.stepText, "Showing step " + state.step + " of " + last + ".");
    }
  }

  function render(announce) {
    var run = state.loaded.run;
    var current = renderStates(run);
    renderDesks(run);
    renderHandoffs(run);
    renderTimeline(run);
    renderStepText(run);
    if (announce && current !== state.lastAnnounced) {
      setText(el.live, "Bundle state: " + runs.stateWords(current) + ".");
    }
    state.lastAnnounced = current;
  }

  function show(loaded) {
    var run = loaded.run;
    state.loaded = loaded;
    state.step = ALL;
    var preview = run.provenance === "design_preview";
    setText(el.label, run.label + (preview ? " The gate verdicts were computed by the real checks over these hand-written sentences." : ""));
    setText(el.match, data.matchLine(run));
    el.approve.setAttribute("href", "#/approve/" + run.run_id);
    el.recap.setAttribute("href", "#/recap/" + run.run_id);
    renderGates(run);
    render(false);
  }

  /* ---------------------------------------------------------- replay */

  function stop() {
    if (state.timer) {
      window.clearTimeout(state.timer);
      state.timer = 0;
    }
    setText(el.replayText, "Replay the run");
  }

  function advance() {
    if (!state.loaded) {
      return;
    }
    var last = runs.lastStep(state.loaded.run);
    if (state.step === ALL || state.step >= last) {
      state.step = 1;
    } else {
      state.step += 1;
    }
    render(true);
    return state.step < last;
  }

  function tick() {
    state.timer = 0;
    if (advance()) {
      state.timer = window.setTimeout(tick, STEP_MS);
    } else {
      stop();
    }
  }

  function replay() {
    if (!state.loaded) {
      return;
    }
    stop();
    state.step = 0;
    if (state.reduceMotion) {
      advance();
      return;
    }
    advance();
    setText(el.replayText, "Replaying");
    state.timer = window.setTimeout(tick, STEP_MS);
  }

  /* ---------------------------------------------------------- start */

  function open(param) {
    state.token += 1;
    var token = state.token;
    stop();
    el.notice.hidden = true;
    data
      .forRoute(param)
      .then(function (loaded) {
        if (token === state.token) {
          show(loaded);
        }
      })
      .catch(function (error) {
        if (token !== state.token) {
          return;
        }
        setText(el.chip, "Unavailable");
        setText(el.match, "The run could not be shown.");
        setText(el.notice, "The run could not be loaded: " + error.message + ".");
        el.notice.hidden = false;
      });
  }

  function init() {
    var ids = {
      label: "wall-label", match: "wall-match", chip: "wall-state-chip", states: "wall-states", live: "wall-live",
      approve: "wall-approve", replay: "wall-replay", next: "wall-next", stepText: "wall-step", notice: "wall-notice",
      desks: "wall-desks", handoffs: "wall-handoffs", timeline: "wall-timeline", gates: "wall-gates", recap: "wall-recap"
    };
    for (var key in ids) {
      if (Object.prototype.hasOwnProperty.call(ids, key)) {
        el[key] = byId(ids[key]);
        if (!el[key]) {
          return;
        }
      }
    }
    el.replayText = el.replay;
    if (!runs || !data || typeof window.fetch !== "function") {
      setText(el.match, "This browser cannot load the run on this page.");
      return;
    }
    if (window.matchMedia) {
      var query = window.matchMedia("(prefers-reduced-motion: reduce)");
      state.reduceMotion = query.matches;
      var onChange = function () {
        state.reduceMotion = query.matches;
        if (state.reduceMotion) {
          stop();
        }
      };
      if (query.addEventListener) {
        query.addEventListener("change", onChange);
      } else if (query.addListener) {
        query.addListener(onChange);
      }
    }
    el.replay.addEventListener("click", replay);
    el.next.addEventListener("click", function () {
      stop();
      advance();
    });
    document.addEventListener("agon:route", function (event) {
      var detail = event.detail || {};
      if (detail.path === "/newsroom") {
        open(detail.param);
      } else {
        stop();
      }
    });
  }

  init();
})();
