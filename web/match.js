/*
  Match Centre replay (screen S1).

  Loads the manifest (data/ first, then the committed fixture, see
  agon-core.js), builds the seed picker from it, and replays the chosen
  match's event log on the SVG pitch at 1x, 8x or 60x. Every coordinate goes
  through AgonCore.toFixedFrame, so the home club always attacks to the right.

  Names: each seed ships its world file (<seed>.world.json) with the
  fictional club and player names. Every sentence on screen uses those names;
  the raw ids (C1, C1P09, event ids) appear only in the "Ids in the event
  log" details row. If the world file cannot be used, a notice says so and
  the sentences say "Home club" and "a player" instead of showing ids.

  Accessibility: every control is a native button, radio or range input. The
  score sits in a polite live region that changes only when the score does,
  never on the ticking clock. Under prefers-reduced-motion the ball jumps from
  event to event instead of gliding, and nothing ever starts on its own.

  CSP: no inline script, no HTML strings; DOM is built with createElement and
  textContent, and SVG geometry is set with setAttribute on x, y, cx and cy.
*/
(function () {
  "use strict";

  var core = window.AgonCore;
  var SVG_NS = "http://www.w3.org/2000/svg";
  var SPEEDS = [1, 8, 60];
  var DEFAULT_SPEED = 8;
  var BAND_WIDTH_M = 17;
  var MAX_STEP_SECONDS = 0.25;

  var TYPE_WORDS = {
    pass: "pass",
    carry: "carry",
    shot: "shot",
    tackle: "tackle",
    pressure: "pressure on the ball",
    foul: "foul",
    set_piece: "set piece",
    sub: "substitution",
    card: "card",
    possession_change: "possession change"
  };

  var el = {};
  var state = {
    resolved: null,
    entry: null,
    events: [],
    timeline: null,
    home: "",
    away: "",
    world: null,
    names: null,
    score: null,
    moments: [],
    t: 0,
    index: -2,
    playing: false,
    speed: DEFAULT_SPEED,
    lastFrame: 0,
    scoreKey: "",
    loadToken: 0,
    reduceMotion: false
  };

  function byId(id) {
    return document.getElementById(id);
  }

  function words(value) {
    return String(value || "").replace(/_/g, " ");
  }

  function setText(node, text) {
    if (node && node.textContent !== text) {
      node.textContent = text;
    }
  }

  /* ---------------------------------------------------------- availability */

  function setAvailable(ok, reason) {
    var controls = [el.play, el.scrubber];
    for (var i = 0; i < controls.length; i += 1) {
      if (!controls[i]) {
        continue;
      }
      if (ok) {
        controls[i].removeAttribute("aria-disabled");
        controls[i].removeAttribute("aria-describedby");
        if (controls[i] === el.scrubber) {
          controls[i].disabled = false;
          controls[i].setAttribute("aria-describedby", "scrubber-hint");
        }
      } else {
        controls[i].setAttribute("aria-disabled", "true");
        controls[i].setAttribute("aria-describedby", "play-reason");
        if (controls[i] === el.scrubber) {
          controls[i].disabled = true;
        }
      }
    }
    setText(el.playReason, ok ? "" : reason);
    el.playReason.hidden = ok;
  }

  function showNotice(text) {
    setText(el.notice, text || "");
    el.notice.hidden = !text;
  }

  /* ---------------------------------------------------------- seed picker */

  function buildSeedPicker(manifest, source) {
    var group = el.seedGroup;
    while (group.firstChild) {
      group.removeChild(group.firstChild);
    }
    var files = manifest.files.slice().sort(function (a, b) {
      return a.seed - b.seed;
    });
    for (var i = 0; i < files.length; i += 1) {
      var entry = files[i];
      var input = document.createElement("input");
      input.type = "radio";
      input.name = "seed";
      input.id = "seed-" + entry.seed;
      input.value = String(entry.seed);
      input.checked = i === 0;
      input.addEventListener("change", onSeedChange);
      var label = document.createElement("label");
      label.htmlFor = input.id;
      label.textContent = "Seed " + entry.seed;
      group.appendChild(input);
      group.appendChild(label);
    }
    var seeds = files.map(function (f) {
      return f.seed;
    });
    var hint = "Each seed is a different fictional match from the synthetic generator. Choosing one loads its event file.";
    if (source === "fixture") {
      hint += " The committed fixture holds seed " + seeds.join(", ") + " only; the other seeds are planned to arrive with the deployed match files.";
    }
    setText(el.seedHint, hint);
    return files[0];
  }

  function entryForSeed(seed) {
    var files = state.resolved.manifest.files;
    for (var i = 0; i < files.length; i += 1) {
      if (String(files[i].seed) === String(seed)) {
        return files[i];
      }
    }
    return null;
  }

  function onSeedChange(event) {
    var entry = entryForSeed(event.target.value);
    if (entry) {
      loadEntry(entry);
    }
  }

  /* ---------------------------------------------------------- loading */

  function sourceLabel() {
    var r = state.resolved;
    var league = state.names && state.names.league ? state.names.league + ", " : "";
    return "Synthetic match, " + league + "generator " + r.manifest.generator + ", seed " + state.entry.seed +
      ". Fictional clubs and players. The newsroom is not connected to this replay.";
  }

  /* Nothing from the previous seed may stay on screen while the next one
     loads or after it fails to load. */
  function clearMatch() {
    state.events = [];
    state.timeline = null;
    state.moments = [];
    state.index = -2;
    state.scoreKey = "";
    state.world = null;
    state.names = null;
    setText(el.homeName, "Home");
    setText(el.awayName, "Away");
    setKit(el.homeKit, null);
    setKit(el.awayKit, null);
    setText(el.eventIds, "No event is shown yet.");
    setText(el.homeGoals, "0");
    setText(el.awayGoals, "0");
    setText(el.clockMinute, "1'");
    setText(el.clockPeriod, "First half");
    setText(el.caption, "No match is loaded.");
    setText(el.pressure, "Pressure figures appear once a match is loaded.");
    el.band.setAttribute("visibility", "hidden");
    el.path.setAttribute("visibility", "hidden");
    el.ball.setAttribute("cx", "52.5");
    el.ball.setAttribute("cy", "34");
    el.ring.setAttribute("cx", "52.5");
    el.ring.setAttribute("cy", "34");
    el.scrubber.value = "0";
    el.scrubber.removeAttribute("aria-valuetext");
    renderMoments();
    renderStrip();
  }

  function loadEntry(entry) {
    pause();
    clearMatch();
    state.entry = entry;
    state.loadToken += 1;
    var token = state.loadToken;
    setAvailable(false, "Play and the match time slider are unavailable while seed " + entry.seed + " loads.");
    setText(el.label, "Loading seed " + entry.seed + ".");
    var url = core.fileUrl(state.resolved.base, entry.name);
    var worldLoad = loadWorld(entry);
    window
      .fetch(url, { cache: "no-store" })
      .then(function (response) {
        return response.text().then(function (text) {
          if (!response.ok || core.looksLikeHtml(text)) {
            throw new Error(url + " answered " + response.status + " instead of an event file.");
          }
          return text;
        });
      })
      .then(function (text) {
        return worldLoad.then(function (worldResult) {
          return { text: text, world: worldResult };
        });
      })
      .then(function (loaded) {
        if (token !== state.loadToken) {
          return;
        }
        var events = core.parseNdjson(loaded.text);
        prepare(events, loaded.world.world);
        setText(el.label, sourceLabel());
        var notes = [];
        if (state.resolved.notice) {
          notes.push(state.resolved.notice);
        }
        if (loaded.world.problem) {
          notes.push("The names file for this seed could not be used (" + loaded.world.problem +
            "), so clubs and players are called Home club, Away club and a player. Their ids are in the details row under the pitch.");
        } else {
          var unknown = core.unknownIds(events, loaded.world.world);
          if (unknown.length) {
            notes.push("The names file does not name " + unknown.length + " id" + (unknown.length === 1 ? "" : "s") +
              " used in the event file: " + unknown.slice(0, 5).join(", ") + ".");
          }
        }
        if (state.resolved.source === "fixture") {
          notes.push("Fixture data: this is the small file committed for development, not the deployed match files. It ends at " +
            core.eventMinute(events[events.length - 1]) + ".");
        }
        if (events.length !== entry.events) {
          notes.push("This file holds " + events.length + " events but the manifest lists " + entry.events + ". The Verify page shows the details.");
        }
        showNotice(notes.join(" "));
        setAvailable(true, "");
        seek(0);
      })
      .catch(function (error) {
        if (token !== state.loadToken) {
          return;
        }
        setText(el.label, "Seed " + entry.seed + " could not be loaded.");
        showNotice("The match file could not be read: " + error.message);
        setAvailable(false, "Play and the match time slider are unavailable because the match file could not be read.");
      });
  }

  /* Resolves to {world, problem}; never rejects, so a names problem can
     never stop the replay. */
  function loadWorld(entry) {
    var url;
    try {
      url = core.worldUrl(state.resolved.base, entry.world);
    } catch (error) {
      return Promise.resolve({ world: null, problem: error.message });
    }
    return window
      .fetch(url, { cache: "no-store" })
      .then(function (response) {
        return response.text().then(function (text) {
          if (!response.ok || core.looksLikeHtml(text)) {
            throw new Error(url + " answered " + response.status);
          }
          var world = core.parseWorldText(text);
          if (world.seed !== entry.seed) {
            throw new Error(url + " is for seed " + world.seed + ", not seed " + entry.seed);
          }
          return { world: world, problem: "" };
        });
      })
      .catch(function (error) {
        return { world: null, problem: error.message };
      });
  }

  function setKit(node, club) {
    if (!node) {
      return;
    }
    if (!club) {
      node.setAttribute("visibility", "hidden");
      return;
    }
    node.setAttribute("fill", club.kit_primary_hex);
    node.setAttribute("stroke", club.kit_secondary_hex);
    node.setAttribute("visibility", "visible");
  }

  function clubName(id) {
    return state.names ? state.names.club(id) : id;
  }

  function prepare(events, world) {
    state.events = events;
    state.home = core.homeTeam(events);
    state.away = core.awayTeam(events, state.home);
    state.world = world || null;
    state.names = core.nameIndex(state.world, state.home);
    state.timeline = core.buildTimeline(events);
    state.score = core.runningScore(events, state.home);
    state.moments = core.keyMoments(events, state.timeline, state.names);
    state.index = -2;
    state.scoreKey = "";
    setText(el.homeName, clubName(state.home));
    setText(el.awayName, state.away ? clubName(state.away) : "Away");
    setKit(el.homeKit, state.names.clubRecord(state.home));
    setKit(el.awayKit, state.names.clubRecord(state.away));
    setText(el.direction, clubName(state.home) + " (home) attack to the right in both halves.");
    el.scrubber.max = String(Math.max(1, state.timeline.total - 1));
    renderMoments();
    renderStrip();
  }

  /* ---------------------------------------------------------- moments */

  function renderMoments() {
    var list = el.moments;
    while (list.firstChild) {
      list.removeChild(list.firstChild);
    }
    for (var i = 0; i < state.moments.length; i += 1) {
      var m = state.moments[i];
      var item = document.createElement("li");
      item.className = "moment moment--" + m.kind;
      var button = document.createElement("button");
      button.type = "button";
      button.className = "moment__button";
      button.setAttribute("data-time", String(m.time));
      var minute = document.createElement("span");
      minute.className = "moment__minute";
      minute.textContent = m.minute;
      var text = document.createElement("span");
      text.className = "moment__text";
      text.textContent = m.text;
      button.appendChild(minute);
      button.appendChild(text);
      button.addEventListener("click", onMomentClick);
      item.appendChild(button);
      list.appendChild(item);
    }
    var count = state.moments.length;
    setText(el.momentsCount, String(count));
    el.momentsEmpty.hidden = count > 0;
    list.hidden = count === 0;
  }

  function onMomentClick(event) {
    var time = Number(event.currentTarget.getAttribute("data-time"));
    seek(time);
  }

  function renderStrip() {
    var strip = el.strip;
    while (strip.firstChild) {
      strip.removeChild(strip.firstChild);
    }
    if (!state.timeline) {
      return;
    }
    var total = Math.max(1, state.timeline.total - 1);
    for (var i = 0; i < state.moments.length; i += 1) {
      var m = state.moments[i];
      var mark = document.createElementNS(SVG_NS, "rect");
      var height = m.kind === "goal" ? 12 : m.kind === "card" ? 9 : 6;
      mark.setAttribute("x", String(Math.min(996, (m.time / total) * 1000)));
      mark.setAttribute("y", String(12 - height));
      mark.setAttribute("width", "4");
      mark.setAttribute("height", String(height));
      mark.setAttribute("class", "strip__mark strip__mark--" + m.kind);
      strip.appendChild(mark);
    }
  }

  function markMoments() {
    var items = el.moments.children;
    for (var i = 0; i < items.length; i += 1) {
      var future = state.moments[i] && state.moments[i].index > state.index;
      items[i].classList.toggle("is-future", future);
    }
  }

  /* ---------------------------------------------------------- drawing */

  function caption(event) {
    var who = clubName(event.team) + (event.player ? ", " + state.names.player(event.player) : "");
    var parts = [core.eventMinute(event), who + ":", TYPE_WORDS[event.type] || words(event.type)];
    var details = [];
    if (event.qualifier) {
      details.push(words(event.qualifier));
    }
    if (event.outcome && event.outcome !== "applied" && event.outcome !== "committed") {
      details.push(words(event.outcome));
    }
    if (event.under_pressure === true) {
      details.push("under pressure");
    }
    return parts.join(" ") + (details.length ? ", " + details.join(", ") : "") + ".";
  }

  function side(team, part) {
    var name = clubName(team);
    if (!part.total) {
      return name + " none on the ball yet";
    }
    return name + " " + core.percent(part) + " (" + part.under + " of " + part.total + ")";
  }

  function drawBall() {
    var ball = core.ballAt(state.events, state.timeline, state.t, state.home, !state.reduceMotion);
    var x = (ball.x / 10).toFixed(2);
    var y = (ball.y / 10).toFixed(2);
    el.ball.setAttribute("cx", x);
    el.ball.setAttribute("cy", y);
    el.ring.setAttribute("cx", x);
    el.ring.setAttribute("cy", y);
  }

  function drawEvent(index) {
    if (index < 0) {
      el.band.setAttribute("visibility", "hidden");
      el.path.setAttribute("visibility", "hidden");
      setText(el.caption, "Kick-off has not happened yet.");
      setText(el.eventIds, "No event is shown yet.");
      return;
    }
    var event = state.events[index];
    var p = core.toFixedFrame(event, state.home);
    if (p[2] !== null) {
      el.path.setAttribute("x1", String(p[0] / 10));
      el.path.setAttribute("y1", String(p[1] / 10));
      el.path.setAttribute("x2", String(p[2] / 10));
      el.path.setAttribute("y2", String(p[3] / 10));
      el.path.setAttribute("visibility", "visible");
    } else {
      el.path.setAttribute("visibility", "hidden");
    }
    var pressured = event.under_pressure === true || event.type === "pressure";
    if (pressured) {
      var left = Math.min(105 - BAND_WIDTH_M, Math.max(0, p[0] / 10 - BAND_WIDTH_M / 2));
      el.bandFill.setAttribute("x", left.toFixed(2));
      el.bandHatch.setAttribute("x", left.toFixed(2));
      el.band.setAttribute("visibility", "visible");
    } else {
      el.band.setAttribute("visibility", "hidden");
    }
    setText(el.caption, caption(event));
    setText(el.eventIds, core.eventIds(event) + ".");
    var w = core.pressureWindow(state.events, state.timeline, index, state.home);
    setText(el.pressure, "Passes, carries and shots made under pressure, last 5 minutes of play: " +
      side(state.home, w.home) + "; " + side(state.away, w.away) + ".");
  }

  function drawScore(index) {
    var s = core.scoreAt(state.score, index);
    setText(el.homeGoals, String(s.home));
    setText(el.awayGoals, String(s.away));
    var key = state.entry.seed + ":" + s.home + ":" + s.away;
    if (key === state.scoreKey) {
      return;
    }
    var first = state.scoreKey === "" || state.scoreKey.split(":")[0] !== String(state.entry.seed);
    state.scoreKey = key;
    var line = "Score: " + clubName(state.home) + " " + s.home + ", " + clubName(state.away) + " " + s.away + ".";
    /* The first render of a match is not news; later changes are announced. */
    setText(el.scoreLive, first ? "" : line);
  }

  function render() {
    var t = state.t;
    var index = core.indexAt(state.timeline.times, t);
    var clock = core.matchClockAt(t, state.timeline);
    var minute = core.formatMinute(clock.period, clock.clock);
    setText(el.clockMinute, minute);
    setText(el.clockPeriod, clock.period === 1 ? "First half" : "Second half");
    el.scrubber.value = String(Math.floor(t));
    el.scrubber.setAttribute("aria-valuetext", minute + ", " + (clock.period === 1 ? "first half" : "second half"));
    drawBall();
    if (index !== state.index) {
      state.index = index;
      drawEvent(index);
      drawScore(index);
      markMoments();
    }
  }

  /* ---------------------------------------------------------- playback */

  function seek(t) {
    if (!state.timeline) {
      return;
    }
    state.t = Math.max(0, Math.min(t, state.timeline.total - 1));
    render();
  }

  function frame(now) {
    if (!state.playing) {
      return;
    }
    var step = Math.min(MAX_STEP_SECONDS, Math.max(0, (now - state.lastFrame) / 1000));
    state.lastFrame = now;
    var end = state.timeline.total - 1;
    var next = state.t + step * state.speed;
    if (next >= end) {
      seek(end);
      pause();
      return;
    }
    seek(next);
    window.requestAnimationFrame(frame);
  }

  function play() {
    if (!state.timeline || el.play.getAttribute("aria-disabled") === "true") {
      return;
    }
    if (state.t >= state.timeline.total - 1) {
      seek(0);
    }
    state.playing = true;
    setText(el.playText, "Pause");
    state.lastFrame = window.performance ? window.performance.now() : Date.now();
    window.requestAnimationFrame(frame);
  }

  function pause() {
    state.playing = false;
    if (el.play) {
      setText(el.playText, "Play");
    }
  }

  /* ---------------------------------------------------------- start */

  function bind() {
    el.play.addEventListener("click", function () {
      if (state.playing) {
        pause();
      } else {
        play();
      }
    });
    el.scrubber.addEventListener("input", function () {
      seek(Number(el.scrubber.value));
    });
    var speedInputs = document.querySelectorAll("input[name='speed']");
    for (var i = 0; i < speedInputs.length; i += 1) {
      speedInputs[i].addEventListener("change", function (event) {
        var value = Number(event.target.value);
        if (SPEEDS.indexOf(value) !== -1) {
          state.speed = value;
        }
      });
    }
    document.addEventListener("agon:route", function (event) {
      if (event.detail && event.detail.path !== "/") {
        pause();
      }
    });
    if (window.matchMedia) {
      var query = window.matchMedia("(prefers-reduced-motion: reduce)");
      state.reduceMotion = query.matches;
      var onChange = function () {
        state.reduceMotion = query.matches;
        if (state.timeline) {
          render();
        }
      };
      if (query.addEventListener) {
        query.addEventListener("change", onChange);
      } else if (query.addListener) {
        query.addListener(onChange);
      }
    }
  }

  function init() {
    var ids = {
      label: "match-label", notice: "data-notice", chip: "data-chip", play: "play-toggle", playText: "play-text",
      playReason: "play-reason", scrubber: "scrubber", homeName: "home-name", awayName: "away-name",
      homeGoals: "home-goals", awayGoals: "away-goals", scoreLive: "score-live", clockMinute: "clock-minute",
      clockPeriod: "clock-period", ball: "ball", ring: "ball-ring", band: "pressure-band", bandFill: "pressure-fill",
      bandHatch: "pressure-hatch-rect", path: "event-path", caption: "event-caption", pressure: "pressure-readout",
      direction: "pitch-direction", moments: "moments", momentsCount: "moments-count", momentsEmpty: "moments-empty",
      strip: "moment-strip", seedGroup: "seed-group", seedHint: "seed-hint", eventIds: "event-ids",
      homeKit: "home-kit", awayKit: "away-kit"
    };
    for (var key in ids) {
      if (Object.prototype.hasOwnProperty.call(ids, key)) {
        el[key] = byId(ids[key]);
        if (!el[key]) {
          return;
        }
      }
    }
    if (!core || typeof window.fetch !== "function") {
      setText(el.label, "This browser cannot load the match data on this page.");
      setAvailable(false, "Play and the match time slider are unavailable because this browser cannot fetch the match files.");
      return;
    }
    bind();
    core
      .resolveManifest(window.fetch.bind(window))
      .then(function (resolved) {
        state.resolved = resolved;
        var fixture = resolved.source === "fixture";
        setText(el.chip, fixture ? "Fixture" : "Synthetic data");
        el.chip.className = fixture ? "chip chip--planned" : "chip";
        loadEntry(buildSeedPicker(resolved.manifest, resolved.source));
      })
      .catch(function (error) {
        setText(el.chip, "Unavailable");
        el.chip.className = "chip chip--danger";
        setText(el.label, "No synthetic match could be loaded.");
        showNotice(error.message);
        setAvailable(false, "Play and the match time slider are unavailable because no match data could be loaded.");
      });
  }

  init();
})();
