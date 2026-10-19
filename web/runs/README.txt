Agon run files: what the Newsroom Wall, the approval card, the recap and Verify read
====================================================================================

What this folder is
-------------------
One run is one full-time newsroom pass over one match: what each desk did,
how the work was handed on, the state of the bundle, what each gate decided,
which sentences a person must confirm, and the sealed bundle of editions that
came out. The pages read it as static files; nothing here calls a backend.

Today the folder holds ONE run, sample-s1. Design preview: sample run written
by hand, not produced by the pipeline. Its desk lines and sentences were
written by hand; no agent ran. What is computed rather than written:
the names, minutes, counts and event ids come from synthetic seed 1; the gate
verdicts come from the real deterministic gates (agon.governance.factgate and
agon.governance.rights) run over those hand-written sentences, including the
planted "2 shots on target" draft that the Fact Gate refuses; and the bundle
is sealed by agon.governance.seal, so Verify recomputes a real bundle hash.

Regenerate it (byte for byte; tests/test_web_runs.py fails on any drift):

    PYTHONPATH=src python -m agon.webrun --write-sample web/runs

Check the folder the way the deploy step does:

    PYTHONPATH=src python -m agon.webrun --check web/runs

Layout
------
    runs/index.json          the list of runs
    runs/<run_id>.json       the run trace
    runs/<run_id>/           the sealed bundle of that run
        manifest.json        canonical agon.bundle/1 manifest (agon.governance.seal)
        edition-<id>.json    one file per edition, listed in the manifest

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
                         gate's own output, which may hold ids}]
    editions           [{"edition", "language", "name",
                         "file": "edition-<edition>.json"}]
    sentence_types     {sentence id: match_stat | injury_medical |
                        disciplinary_referee | quote_attribution |
                        transfer_contract | neutral_colour}, for the English
                        headline report; edition sentence n has the type of
                        English sentence n
    sentence_gates     {sentence id: {gate: verdict}} for every sentence
    confirm_rows       [{"row_id", "sentence_id", "type", "label"}], one per
                       injury, referee, quote and transfer sentence; label
                       is the routing table's "the injury sentence" and so on
    desk_editor_flags  [{"sentence_id", "text"}]
    approval           {"status": awaiting | approved | changes_requested,
                        "approved_at": "YYYY-MM-DDTHH:MM:SSZ" or null,
                        "approver_role": str or null, "channel": str or null}

Rules the checker (agon.webrun.validate_run) and the browser
(AgonCore.validateRun) both enforce, from one case list
(tests/js/run_cases.json):
1. A design_preview run carries the exact label above and can never be
   approved. ci/web_check.py also refuses a design_preview run without it,
   and the readiness gate fails if the live page or run file lacks it.
2. approval.status "approved" if and only if state is APPROVED or PUBLISHED,
   with approved_at, approver_role and channel set.
3. Every injury, referee, quote and transfer sentence has a Confirm row.

<run_id>/edition-<id>.json
--------------------------
    {"edition": id, "language": "en" | "el" | "es" | "fr", "match_id": str,
     "sentences": [{"sentence_id", "text", "language", "author",
                    "claims": [{"type", "value", "event_ids", "stat"?,
                                "subject"?}]}]}

author is "model", "template" or "officer" for a pipeline run, and "hand"
for the design preview. Claims follow agon.governance.claims. The recap
renders these files, the same bytes Verify hashes, never a copy.
