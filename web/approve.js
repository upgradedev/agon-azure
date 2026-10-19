/*
  Stand-in approval card (screen S3), styled like an Adaptive Card.

  The header says what it is: a stand-in for the Microsoft Teams card. It
  shows the bundle hash (with a copy button), the editions, what the officer
  attests versus what is gate-cleared only, the Desk Editor's flags and the
  Confirm rows that must be ticked.

  Approve is never disabled. Clicking it names every unticked Confirm row;
  with all rows ticked it says, in a full sentence, that the decision
  endpoint is not deployed in this static build, so nothing was approved.
  Request changes says the same. Results go to a polite live region.
*/
(function () {
  "use strict";

  var runs = window.AgonRuns;
  var data = window.AgonRunData;
  var el = {};
  var state = { loaded: null, token: 0 };

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

  function evidenceLink(run, sentenceId) {
    var link = make("a", "small", "Open evidence");
    link.href = "#/recap/" + run.run_id + "?s=" + sentenceId;
    return link;
  }

  function renderEditions(loaded) {
    clear(el.editions);
    var scope = loaded.manifest.attested_scope;
    loaded.run.editions.forEach(function (e) {
      var attested = scope.attested.indexOf(e.file) !== -1;
      var li = make("li", attested ? "chip" : "chip chip--neutral", e.name + (attested ? ", attested" : ", gate-cleared"));
      el.editions.appendChild(li);
    });
  }

  function renderFlags(loaded) {
    var run = loaded.run;
    clear(el.flags);
    run.desk_editor_flags.forEach(function (flag) {
      var li = make("li", "flag");
      var found = data.sentenceById(loaded, flag.sentence_id);
      li.appendChild(make("p", "flag__text", flag.text));
      if (found) {
        li.appendChild(make("q", "flag__sentence", found.sentence.text));
      }
      li.appendChild(evidenceLink(run, flag.sentence_id));
      el.flags.appendChild(li);
    });
    el.flagsBlock.hidden = run.desk_editor_flags.length === 0;
  }

  function renderConfirm(loaded) {
    var run = loaded.run;
    clear(el.confirm);
    run.confirm_rows.forEach(function (row) {
      var li = make("li", "confirm-row");
      var input = document.createElement("input");
      input.type = "checkbox";
      input.id = "confirm-" + row.row_id;
      input.value = row.row_id;
      input.className = "confirm-row__box";
      var label = make("label", "confirm-row__label");
      label.htmlFor = input.id;
      label.appendChild(make("strong", "", "Confirm " + row.label));
      var found = data.sentenceById(loaded, row.sentence_id);
      if (found) {
        label.appendChild(make("q", "confirm-row__sentence", found.sentence.text));
      }
      li.appendChild(input);
      li.appendChild(label);
      li.appendChild(evidenceLink(run, row.sentence_id));
      el.confirm.appendChild(li);
    });
  }

  function ticked() {
    var boxes = el.confirm.querySelectorAll("input[type='checkbox']");
    var out = [];
    for (var i = 0; i < boxes.length; i += 1) {
      boxes[i].closest("li").classList.remove("is-missing");
      if (boxes[i].checked) {
        out.push(boxes[i].value);
      }
    }
    return out;
  }

  function show(loaded) {
    var run = loaded.run;
    state.loaded = loaded;
    setText(el.label, run.label);
    setText(el.match, data.matchLine(run) + " Bundle state: " + runs.stateWords(run.state) + ".");
    setText(el.hash, run.bundle.sha256);
    setText(el.copyStatus, "");
    setText(el.result, "");
    el.result.className = "acard__result";
    var att = runs.attestation(run, loaded.manifest);
    setText(el.attesting, att.attesting);
    setText(el.cleared, att.cleared);
    renderEditions(loaded);
    renderFlags(loaded);
    renderConfirm(loaded);
    var waiting = run.state === "AWAITING_APPROVAL";
    el.notice.hidden = waiting;
    if (!waiting) {
      setText(el.notice, "This run is not waiting for approval: its bundle state is " + runs.stateWords(run.state) + ".");
    }
  }

  function onApprove() {
    if (!state.loaded) {
      setText(el.result, "The run has not loaded, so there is nothing to approve yet.");
      return;
    }
    var attempt = runs.approveAttempt(state.loaded.run, ticked());
    attempt.missing.forEach(function (rowId) {
      var box = byId("confirm-" + rowId);
      if (box) {
        box.closest("li").classList.add("is-missing");
      }
    });
    el.result.className = "acard__result " + (attempt.ok ? "acard__result--info" : "acard__result--warn");
    setText(el.result, attempt.message);
    if (!attempt.ok && attempt.missing.length) {
      var first = byId("confirm-" + attempt.missing[0]);
      if (first) {
        first.focus();
      }
    }
  }

  function onChanges() {
    if (!state.loaded) {
      setText(el.result, "The run has not loaded, so there is nothing to send yet.");
      return;
    }
    el.result.className = "acard__result acard__result--info";
    setText(el.result, runs.notDeployedMessage("changes", state.loaded.run));
  }

  function onCopy() {
    var hash = el.hash.textContent;
    var done = function () {
      setText(el.copyStatus, "Copied the full bundle hash.");
    };
    var fallback = function () {
      var range = document.createRange();
      range.selectNodeContents(el.hash);
      var selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      setText(el.copyStatus, "This browser did not allow copying, so the full hash is selected for you to copy.");
    };
    if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      navigator.clipboard.writeText(hash).then(done, fallback);
    } else {
      fallback();
    }
  }

  function open(param) {
    state.token += 1;
    var token = state.token;
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
        state.loaded = null;
        setText(el.match, "The run could not be shown.");
        setText(el.notice, "The run could not be loaded: " + error.message + ".");
        el.notice.hidden = false;
      });
  }

  function init() {
    var ids = {
      label: "approve-label", match: "acard-match", notice: "acard-notice", hash: "acard-hash", copy: "acard-copy",
      copyStatus: "acard-copy-status", editions: "acard-editions", attesting: "acard-attesting", cleared: "acard-cleared",
      flags: "acard-flags", flagsBlock: "acard-flags-block", confirm: "acard-confirm", approve: "acard-approve",
      changes: "acard-changes", result: "acard-result"
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
      setText(el.match, "This browser cannot load the run on this page.");
      return;
    }
    el.approve.addEventListener("click", onApprove);
    el.changes.addEventListener("click", onChanges);
    el.copy.addEventListener("click", onCopy);
    el.confirm.addEventListener("change", function () {
      ticked();
    });
    document.addEventListener("agon:route", function (event) {
      var detail = event.detail || {};
      if (detail.path === "/approve") {
        open(detail.param);
      }
    });
  }

  init();
})();
