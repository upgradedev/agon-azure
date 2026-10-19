Agon web data contract and the committed development fixture
=============================================================

What this folder is
-------------------
A small committed fixture so the Match Centre and the Verify page work in local
development and in tests. It is the first 10 minutes of the first half of seed 1
(events with period 1 and minute below 10), produced by the synthetic generator:

    PYTHONPATH=src python -m agon.webdata --seeds 1 --max-minute 10 --out web/data-fixture

The file is a byte-for-byte prefix of the events.ndjson that
python -m agon.synth.manifest --seeds 1 --out <dir> writes; a test checks that.

tests/test_web_data.py regenerates it and fails if a single byte differs, so a
generator change (a new GENERATOR_VERSION) means re-running the command above
and then the golden the browser tests compare against:

    PYTHONPATH=src python ci/make_frame_golden.py

Where the app looks
-------------------
The app reads data/manifest.json first. If that is missing (404, a network
error, or an HTML fallback page instead of JSON) it reads
data-fixture/manifest.json, and every screen that uses it shows a visible
"Fixture" label. If data/manifest.json is present but is not valid JSON or
breaks the contract, the app also falls back to the fixture, and it says why in
a notice on the page next to that label. It never switches silently.

The contract (written by the deploy step into web/data/, by the command above
into this folder; both use agon.webdata.write_web_data)
---------------------------------------------------------------------------

    manifest.json
    {"generator": str,
     "files": [{"name": str, "sha256": str, "seed": int, "events": int}]}

    generator  the generator version, for example synth-v0.1
    name       a bare file name next to manifest.json, "<seed>.ndjson";
               no folder, no "..", no scheme; the browser refuses anything else
    sha256     64 lowercase hex characters over the exact bytes of that file
    seed       the generator seed of the match in that file
    events     the number of lines in that file

No other keys, at either level. agon.webdata.validate_manifest is the checker
and the browser applies the same rules.

    <seed>.ndjson
    One event per line, canonical JSON (sorted keys, no spaces, ASCII), LF line
    endings, the same encoding as python -m agon.synth.manifest. Fields and
    meaning are in src/agon/synth/schema.py.

Reading the events correctly
----------------------------
1. Coordinates. x_dm and y_dm are integer decimetres on a 1050 by 680 pitch,
   stored in the ACTING team's frame: the team named in "team" always attacks
   towards x_dm = 1050. To draw both teams on one pitch, convert with the rule
   of to_fixed_frame: events by the home club stay as they are; every other
   event is rotated 180 degrees, x to 1050 - x AND y to 680 - y (also the end
   point when present). Defensive events (pressure, tackle, foul, card) by the
   away side are rotated too. Ends are never swapped at half time, so do not
   flip again by period.
2. Home club. The NDJSON carries events only. The home club is the "team" of
   the first event (seq 0, period 1, the kick-off), because the generator gives
   the first-half kick-off to the home club. tests/test_web_data.py checks this
   against the generator's match record for seeds 1 to 3.
3. Clock. minute and second come from a match clock where the second half
   starts at 45:00, while first-half stoppage time also runs past 45:00. Order
   events by seq, never by minute. Show first-half stoppage as 45+n.
4. Names. Club and player names are not in the NDJSON, so the app shows ids
   such as C1 and C1P06. All of them are synthetic and fictional.
