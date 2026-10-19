/*
  Recap (screen S4): the club's public match report, one tab per edition
  (EN, EL, ES, FR, Easy-read).

  The text is rendered from the sealed edition files, the same bytes the
  Verify page hashes, never from a copy in the run trace. Every sentence is a
  button: it opens the Why drawer, which lists the sentence's event ids,
  claim types, gate verdicts, author tag and whether the officer attests it.

  The edition label says "AI-drafted ... approved by the club media office
  at <time>" only when the run is an approved pipeline run
  (AgonRuns.editionLabel); the design preview says what it is instead.

  Tabs follow the ARIA tabs pattern: arrow keys, Home and End move between
  tabs, and each panel carries the lang of its edition.
*/
(function () {
  "use strict";

  var runs = window.AgonRuns;
  var data = window.AgonRunData;
  var el = {};
  var state = { loaded: null, edition: "en", token: 0, opener: null };
  var TAB_LABELS = { en: "EN", el: "EL", es: "ES", fr: "FR", easy: "Easy-read" };

  function byId(id) {
    return document.getElementById(id);
  }

  function setText(node, text) {
    if (node) {
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

  /* ---------------------------------------------------------- tabs */

  function renderTabs(run) {
    clear(el.tabs);
    run.editions.forEach(function (e) {
      var tab = make("button", "tab", TAB_LABELS[e.edition] || e.name);
      tab.appendChild(make("span", "visually-hidden", ", " + e.name));
      tab.type = "button";
      tab.id = "recap-tab-" + e.edition;
      tab.setAttribute("role", "tab");
      tab.setAttribute("aria-controls", "recap-panel");
      tab.setAttribute("data-edition", e.edition);
      tab.addEventListener("click", function () {
        select(e.edition, false);
      });
      tab.addEventListener("keydown", onTabKey);
      el.tabs.appendChild(tab);
    });
  }

  function onTabKey(event) {
    var tabs = Array.prototype.slice.call(el.tabs.querySelectorAll("[role='tab']"));
    var i = tabs.indexOf(event.currentTarget);
    var next = -1;
    if (event.key === "ArrowRight") {
      next = (i + 1) % tabs.length;
    } else if (event.key === "ArrowLeft") {
      next = (i - 1 + tabs.length) % tabs.length;
    } else if (event.key === "Home") {
      next = 0;
    } else if (event.key === "End") {
      next = tabs.length - 1;
    }
    if (next !== -1) {
      event.preventDefault();
      select(tabs[next].getAttribute("data-edition"), true);
    }
  }

  function select(edition, focusTab) {
    var loaded = state.loaded;
    if (!loaded || !Object.prototype.hasOwnProperty.call(loaded.editions, edition)) {
      return;
    }
    state.edition = edition;
    var tabs = el.tabs.querySelectorAll("[role='tab']");
    for (var i = 0; i < tabs.length; i += 1) {
      var on = tabs[i].getAttribute("data-edition") === edition;
      tabs[i].setAttribute("aria-selected", on ? "true" : "false");
      tabs[i].tabIndex = on ? 0 : -1;
      if (on && focusTab) {
        tabs[i].focus();
      }
    }
    el.panel.setAttribute("aria-labelledby", "recap-tab-" + edition);
    renderText(loaded.editions[edition]);
  }

  /* ---------------------------------------------------------- text */

  function renderText(edition) {
    clear(el.text);
    el.text.setAttribute("lang", edition.data.language);
    var para = make("p", "recap__para");
    edition.data.sentences.forEach(function (sentence, index) {
      var button = make("button", "sentence", sentence.text);
      button.type = "button";
      button.setAttribute("data-index", String(index));
      button.setAttribute("aria-haspopup", "dialog");
      button.addEventListener("click", function (event) {
        openWhy(edition, index, event.currentTarget);
      });
      para.appendChild(button);
      para.appendChild(document.createTextNode(" "));
    });
    el.text.appendChild(para);
  }

  /* ---------------------------------------------------------- Why drawer */

  function row(list, term, value, mono) {
    list.appendChild(make("dt", "", term));
    var dd = make("dd", "");
    if (Array.isArray(value)) {
      var ul = make("ul", "why__values");
      value.forEach(function (v) {
        var li = make("li", "");
        li.appendChild(mono ? make("code", "", v) : document.createTextNode(v));
        ul.appendChild(li);
      });
      dd.appendChild(ul);
    } else if (mono) {
      dd.appendChild(make("code", "", value));
    } else {
      dd.textContent = value;
    }
    list.appendChild(dd);
  }

  function openWhy(edition, index, opener) {
    var run = state.loaded.run;
    var sentence = edition.data.sentences[index];
    var w = runs.why(run, edition.meta, sentence, index);
    setText(el.whyEdition, edition.meta.name);
    setText(el.whyQuote, w.text);
    el.whyQuote.setAttribute("lang", edition.data.language);
    clear(el.whyList);
    row(el.whyList, "Sentence", w.sentenceId, true);
    row(el.whyList, "Event ids it cites", w.eventIds.length ? w.eventIds : ["none: a final-score claim reads the whole log"], w.eventIds.length > 0);
    row(el.whyList, "Claim types", w.claimTypes.length ? w.claimTypes : ["none"], false);
    row(el.whyList, "Gate verdicts", w.gates.length ? w.gates.map(function (g) { return g.name + ": " + g.words; }) : ["no verdict recorded"], false);
    row(el.whyList, "Author", w.author + " Tag: " + w.authorTag + ".", false);
    row(el.whyList, "Sentence type", w.type, false);
    row(el.whyList, "Who answers for it", w.scope, false);
    row(el.whyList, "Bundle hash", run.bundle.sha256, true);
    state.opener = opener || null;
    if (typeof el.why.showModal === "function") {
      el.why.showModal();
    } else {
      el.why.setAttribute("open", "");
    }
    el.whyClose.focus();
  }

  function closeWhy() {
    if (typeof el.why.close === "function") {
      el.why.close();
    } else {
      el.why.removeAttribute("open");
      restoreFocus();
    }
  }

  function restoreFocus() {
    if (state.opener && typeof state.opener.focus === "function" && document.body.contains(state.opener)) {
      state.opener.focus();
    }
  }

  /* #/recap/<run>?s=en-05 opens that sentence's drawer on arrival. */
  function openFromQuery(query) {
    var m = /(?:^|&)s=([a-z]{2,8}-[0-9]{2})(?:&|$)/.exec(query || "");
    if (!m || !state.loaded) {
      return;
    }
    var found = data.sentenceById(state.loaded, m[1]);
    if (!found) {
      return;
    }
    select(found.edition, false);
    var button = el.text.querySelector("[data-index='" + found.index + "']");
    openWhy(state.loaded.editions[found.edition], found.index, button);
  }

  /* ---------------------------------------------------------- start */

  function show(loaded, query) {
    var run = loaded.run;
    state.loaded = loaded;
    setText(el.match, data.matchLine(run));
    setText(el.label, runs.editionLabel(run));
    el.label.classList.toggle("edition-label--approved", run.approval.status === "approved" && run.provenance === "pipeline");
    renderTabs(run);
    select(Object.prototype.hasOwnProperty.call(loaded.editions, state.edition) ? state.edition : run.editions[0].edition, false);
    openFromQuery(query);
  }

  function open(param, query) {
    state.token += 1;
    var token = state.token;
    el.notice.hidden = true;
    data
      .forRoute(param)
      .then(function (loaded) {
        if (token === state.token) {
          show(loaded, query);
        }
      })
      .catch(function (error) {
        if (token !== state.token) {
          return;
        }
        state.loaded = null;
        clear(el.tabs);
        clear(el.text);
        setText(el.match, "The report could not be shown.");
        setText(el.notice, "The run could not be loaded: " + error.message + ".");
        el.notice.hidden = false;
      });
  }

  function init() {
    var ids = {
      match: "recap-match", label: "recap-label", notice: "recap-notice", tabs: "recap-tabs", panel: "recap-panel",
      text: "recap-text", why: "why-dialog", whyEdition: "why-edition", whyQuote: "why-quote", whyList: "why-list",
      whyClose: "why-close"
    };
    for (var key in ids) {
      if (Object.prototype.hasOwnProperty.call(ids, key)) {
        el[key] = byId(ids[key]);
        if (!el[key]) {
          return;
        }
      }
    }
    if (!runs || !data || typeof window.fetch !== "function") {
      setText(el.match, "This browser cannot load the report on this page.");
      return;
    }
    el.whyClose.addEventListener("click", closeWhy);
    el.why.addEventListener("close", restoreFocus);
    el.why.addEventListener("click", function (event) {
      if (event.target === el.why) {
        closeWhy();
      }
    });
    document.addEventListener("agon:route", function (event) {
      var detail = event.detail || {};
      if (detail.path === "/recap") {
        open(detail.param, detail.query);
      } else if (el.why.open) {
        closeWhy();
      }
    });
  }

  init();
})();
