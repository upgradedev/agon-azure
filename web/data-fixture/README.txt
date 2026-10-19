Agon web data contract and the committed development fixture
=============================================================

What this folder is
-------------------
A small committed fixture so the Match Centre and the Verify page work in local
development and in tests. It is the first 10 minutes of the first half of seed 1
(events with period 1 and minute below 10) plus the full world file of seed 1
(the fictional league, clubs and players), produced by the synthetic generator:

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
     "files": [{"name": str, "sha256": str, "seed": int, "events": int,
                "world": str, "world_sha256": str}]}

    generator  the generator version, for example synth-v0.1
    name       a bare file name next to manifest.json, exactly "<seed>.ndjson"
               with the seed of the same entry in decimal and no leading
               zeros (seed 1 is 1.ndjson); no folder, no "..", no scheme; the
               browser refuses anything else
    sha256     64 lowercase hex characters over the exact bytes of that file
    seed       the generator seed of the match in that file; each seed is
               listed once, so a manifest with two entries for one seed is
               refused
    events     the number of lines in that file
    world      exactly "<seed>.world.json" for the seed of the same entry,
               a bare file name next to manifest.json
    world_sha256
               64 lowercase hex characters over the exact bytes of the
               world file

No other keys, at either level. agon.webdata.validate_manifest is the checker
and the browser applies the same rules.

    <seed>.world.json
    {"generator": str, "seed": int, "match_id": str, "league": str,
     "home": club id, "away": club id,
     "clubs": [{"club_id": "C1", "name": str, "short": "ABC",
                "kit_primary": str, "kit_primary_hex": "#rrggbb",
                "kit_secondary": str, "kit_secondary_hex": "#rrggbb",
                "players": [{"player_id": "C1P01", "name": str,
                             "shirt": int, "position": "GK|DF|MF|FW"}]}]}
    Canonical JSON (sorted keys, no spaces, ASCII) plus a final LF. A
    projection of the generator's world: names, short codes, kit colours,
    shirt numbers and positions only. agon.webdata.validate_world is the
    checker; the browser applies the same rules (tests/js/world_cases.json).

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
4. Names. Club and player names are not in the NDJSON; they come from the
   world file of the same seed. Every sentence on screen uses the names
   ("Pellmoor Harriers", "Liro Ellow (10)"); the ids such as C1 and C6P10
   appear only in the "Ids in the event log" details row. If the world file
   cannot be used, the page says so in a notice and writes "Home club" and
   "a player" instead of ids. All names are synthetic and fictional, and
   tests/test_web_data.py runs the Rights Guard name screen over each one.
