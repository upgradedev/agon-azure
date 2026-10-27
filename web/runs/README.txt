Agon run files: what the Newsroom Wall, the approval card, the recap and Verify read
====================================================================================

What this folder is
-------------------
One run is one full-time newsroom pass over one match: what each desk did,
how the work was handed on, the state of the bundle, what each gate decided,
which sentences a person must confirm, and the sealed bundle of editions that
came out. The pages read it as static files; nothing here calls a backend.

The site shows the run at the top of index.json by default. Until a
recorded run is exported (below), that is the one hand-written run,
sample-s1; once one is, the recorded run takes the top row and sample-s1
stays listed below it as the fallback.

sample-s1: Design preview: sample run written by hand, not produced by the
pipeline. Its desk lines and sentences were
written by hand; no agent ran. What is computed rather than written:
the names, minutes, counts and event ids come from synthetic seed 1; the gate
verdicts come from the real deterministic gates (agon.governance.factgate and
agon.governance.rights) run over those hand-written sentences, including the
planted "2 shots on target" draft that the Fact Gate refuses; and the bundle
is sealed by agon.governance.seal, so Verify recomputes a real bundle hash.

Regenerate it after a change to the hand-written text or to seed 1.
tests/test_web_runs.py does not compare these files byte for byte with a
fresh build: it reruns the real gates over the committed sentences and fails
when they no longer agree with seed 1. The deploy step holds the committed
bytes to the committed bundle hash (verify_run_dir).

    PYTHONPATH=src python -m agon.webrun --write-sample web/runs

Check the folder the way the deploy step does:

    PYTHONPATH=src python -m agon.webrun --check web/runs

Recorded runs (provenance "pipeline")
-------------------------------------
A recorded run is exported from a bundle the pipeline sealed, the club media
office approved and the Publisher published to the public feed container,
by agon.webrecord (see its docstring for every check it makes):

    PYTHONPATH=src python -m agon.webrecord \
        --feed-base-url https://<storage account>.blob.core.windows.net/feed/ \
        --bundle <bundle sha256> --commit <40-character commit sha> \
        --approver-role approver --channel teams \
        --out web/runs --readiness ci/readiness.json

It re-hashes the published manifest and every published artifact, writes
<run_id>.json (label "Recorded run, <UTC date of the approval>, commit <the
short 7-hex commit>") and <run_id>/ with the manifest and the published artifacts byte for
byte, puts the run at the top of index.json, and with --readiness rewrites the
three anchored label markers of the live gate's demo target to that run's
labels (ci/web_check.py page_label_markers) and adds the demo-recorded
targets. Commit web/runs and ci/readiness.json together. A recorded run is
never overwritten; the export refuses a run id that is already listed.

Layout
------
    runs/index.json          the list of runs
    runs/<run_id>.json       the run trace
    runs/<run_id>/           the sealed bundle of that run
        manifest.json        canonical agon.bundle/1 manifest (agon.governance.seal)
        edition-<id>.json    one file per edition, listed in the manifest
                             (a pipeline run names them report-<id>.json, as the
                             fleet's seal step does)
        card.json, destination-<id>.json, ...
                             a pipeline run's other published artifacts, each
                             listed in the manifest

A pipeline run's folder holds every artifact the manifest lists except the
payload of a destination the officer did not tick: the Publisher never
publishes it, so the run lists that destination as "not_selected" and the
checker and Verify report its payload as withheld by design, still named by
its sha256 in the manifest. Any other missing file fails.

Every file is UTF-8 JSON in canonical form (sorted keys, no spaces) with LF
line endings. A CR byte changes the hash, so the checker refuses one.

index.json
----------
    {"format": "agon.web-runs/1",
     "runs": [{"run_id": str, "file": "<run_id>.json",
               "provenance": "design_preview" | "pipeline"}]}

<run_id>.json
-------------
    format             "agon.web-run/1"
    run_id             lowercase letters, digits and hyphens
    provenance         "design_preview" (written by hand) or "pipeline"
    label              for design_preview EXACTLY:
                       "Design preview: sample run written by hand, not produced by the pipeline."
                       for pipeline: the run's own label, for example
                       "Recorded run, <date>, commit <sha>"
    seed, match_id, generator
                       the synthetic match the run reports on
    home, away         {"club_id", "name"}
    score              {"home": int, "away": int}
    bundle             {"sha256": the bundle hash, "dir": run_id,
                        "manifest": "manifest.json"}; sha256 is sha256 over
                        the exact bytes of <run_id>/manifest.json
    state              DRAFT, CHECKED, SEALED, AWAITING_APPROVAL, APPROVED,
                       PUBLISHED or CHANGES_REQUESTED
    states             [{"state", "step"}] in step order, ending at state
    desks              [{"desk": id, "name": str,
                         "lines": [{"step": int, "text": plain words}]}]
    handoffs           [{"step", "from": desk id, "to": desk id or "approval",
                         "text"}]
    gates              [{"gate", "name", "verdict": pass | mismatch | refused
                         | skipped, "detail": plain words, "evidence": the
                         gate's own output, which may hold ids}]; when the
                         Fact Gate passed, a reason about a sentence it
                         replaced says so after that sentence's id
    editions           [{"edition", "language", "name",
                         "file": "edition-<edition>.json"}]; a pipeline run
                       may name the file "report-<edition>.json" instead
    sentence_types     {sentence id: match_stat | injury_medical |
                        disciplinary_referee | quote_attribution |
                        transfer_contract | neutral_colour}, for the English
                        headline report; edition sentence n has the type of
                        English sentence n (a pipeline edition names its
                        sentences after the English ids, s1-el for s1, and
                        takes the type of that id)
    sentence_gates     {sentence id: {gate: verdict}} for every sentence
    confirm_rows       [{"row_id", "sentence_id", "type", "label"}], one per
                       injury, referee, quote and transfer sentence; label
                       is the routing table's "the injury sentence" and so on
    desk_editor_flags  [{"sentence_id", "text"}], only the flags the Desk
                       Editor raised itself (card.json "by": "desk_editor")
    approval           {"status": awaiting | approved | changes_requested,
                        "approved_at": "YYYY-MM-DDTHH:MM:SSZ" or null,
                        "approver_role": str or null, "channel": str or null,
                        "approved_bundle_sha256": the bundle.sha256 that was
                        approved, or null}

Rules the checker (agon.webrun.validate_run) and the browser
(AgonCore.validateRun) both enforce, from one case list
(tests/js/run_cases.json):
1. A design_preview run carries the exact label above and can never be
   approved. ci/web_check.py also refuses a design_preview run without it,
   and the readiness gate fails if the live page or run file lacks it.
2. approval.status "approved" if and only if state is APPROVED or PUBLISHED,
   with approved_at, approver_role and channel set, and with
   approved_bundle_sha256 equal to bundle.sha256. A run that is not approved
   has approved_bundle_sha256 null. The browser shows the approved edition
   label only when the two hashes match (AgonRuns.isApproved), so an
   approval can never float free of the hash Verify recomputes.
3. Every injury, referee, quote and transfer sentence has a Confirm row.

destinations (optional key)
---------------------------
Where the approved bytes went, one row per publish destination, all six in
this order (agon.governance.destinations), built by
agon.publish.webview.destinations_for_run from the sealed manifest, the
approval record and the Publisher's receipt:

    destinations  [{"id", "label", "status", "preview"}]

    id       label (plain text, never a logo)
    club_site         Club website (CMS feed)
    feeds             RSS and JSON Feed
    instagram         Instagram
    youtube           YouTube
    newsroom_teams    Internal newsroom (Teams)
    journalist_email  Journalist email digest

    status   "delivered"           approved, and the receipt shows the bytes
                                   written (club_site, feeds); only on a
                                   PUBLISHED run
             "preview_not_posted"  approved and rendered, not posted or sent:
                                   Instagram and YouTube always, with the note
                                   "Preview: not posted; posting needs Meta and
                                   Google app approval."; the newsroom and
                                   journalist outbox until their send flow is
                                   deployed, with the outbox note; the club
                                   website and feeds between approval and the
                                   publish, with the waiting note
             "not_selected"        offered by the sealed bundle and not
                                   approved; on a run that is not approved
                                   (yet), every offered destination reads this
             "not_offered"         the sealed bundle does not offer it
    preview  null for not_offered, otherwise
             {"kind": "written" | "outbox" | "preview",
              "note": the note above, or null,
              "title": str, "text": str (the exact Instagram caption, the exact
              YouTube title and description, a plain excerpt otherwise),
              "payload_sha256": the sealed payload hash in the manifest,
              "path": the published path, or null}

The officer ticks destinations AFTER the seal, so the ticked set is not in
the bundle hash: the manifest lists the OFFERED destinations with each
payload's sha256, and the approval record (keyed by approval id, naming the
bundle hash) stores the approved subset. agon.webrun.validate_run checks the
key when it is present (agon.publish.webview.destination_problems). The
sample run does not carry it. The live site is the React console
(console/), which reads this key; the retired hand-written page
(web/agon-runs.js) refuses it and no longer ships.

check_flags (optional key)
--------------------------
The sealed card.json flags on a sentence raised by any step but the Desk
Editor, each shown under the step that raised it, never as a Desk Editor
flag (agon.webrecord.flags_by_source):

    check_flags  [{"sentence_id", "by", "name", "text"}]

    by    card.json "by", the step that raised the flag: "factgate",
          "factgate_lang", "easy_read", "rights" and so on; never
          "desk_editor"
    name  that step as the approval card names it: "Fact Gate",
          "Fact Gate, other editions", "Easy-read rules", ...
    text  the flag in plain words. A template-line flag says which sentence
          failed (the Writer's, for the Fact Gate; the translated one, for
          the Fact Gate on other editions; the English one, for the
          easy-read rules), gives the reason, and says code replaced it
          with the template line the bundle carries: the reason is about
          the replaced sentence, not the line shown beside it.

The Fact Gate's retry (code "factgate_retry", no sentence id) is told on the
Newsroom Wall as the Fact Gate's step 3 line, which names the sentences it
sent back to the Writer. A recorded run carries check_flags; the sample
does not.

<run_id>/edition-<id>.json
--------------------------
    {"edition": id, "language": "en" | "el" | "es" | "fr", "match_id": str,
     "sentences": [{"sentence_id", "text", "language", "author",
                    "claims": [{"type", "value", "event_ids", "stat"?,
                                "subject"?}]}]}

author is "model", "template" or "officer" for a pipeline run, and "hand"
for the design preview. Claims follow agon.governance.claims. The recap
renders these files, the same bytes Verify hashes, never a copy.
