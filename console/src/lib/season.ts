/*
  The season files the Data Lab reads (agon.webseason, schema agon.webseason/1),
  written into season/ by ci/stamp_web.py at deploy time:

    manifest.json  {schema, generator, season, seed, season_sha256, files: [{name, sha256}]}
    fixtures.json  the league, its clubs and every match of the fictional season
    tables.json    the table after every matchday, with the tie-break rule
    datalab.json   the 22 realism bands on held-out seeds (M2, self-measured)

  Each file is fetched as bytes, hashed with WebCrypto and compared with the
  manifest before anything is shown; a missing season folder is not an error,
  it is the honest empty state.
*/
import { isInt, isObject } from "./core";
import { fetchBytes, sha256Hex } from "./hash";

export const SEASON_SCHEMA = "agon.webseason/1";
const SHA256_HEX = /^[0-9a-f]{64}$/;
const NEEDED = ["fixtures.json", "tables.json", "datalab.json"] as const;

export interface SeasonClub {
  club_id: string;
  name: string;
  short: string;
}

export interface TableRow {
  position: number;
  club_id: string;
  played: number;
  won: number;
  drawn: number;
  lost: number;
  goals_for: number;
  goals_against: number;
  goal_difference: number;
  points: number;
}

export interface BandRow {
  id: string;
  statistic: string;
  unit: string;
  lo: string;
  hi: string;
  observed: string | null;
  status: "PASS" | "FAIL" | "NOT MEASURABLE";
}

export interface SeasonView {
  season: number;
  generator: string;
  seasonSha256: string;
  league: string;
  matchday: number;
  matchdaysTotal: number;
  clubs: Record<string, SeasonClub>;
  table: TableRow[];
  tiebreak: string;
  bands: { label: string; seeds: string; passed: number; of: number; threshold: number; command: string; rows: BandRow[] };
  datasetCardUrl: string;
  filesChecked: number;
}

export type SeasonLoad = { status: "absent"; reason: string } | { status: "error"; message: string } | { status: "ready"; view: SeasonView };

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

const ROW_INTS = ["position", "played", "won", "drawn", "lost", "goals_for", "goals_against", "points"] as const;

function checkRow(row: unknown, where: string): TableRow {
  if (!isObject(row) || typeof row.club_id !== "string" || !ROW_INTS.every((k) => isInt(row[k])) || !isInt(row.goal_difference)) {
    throw new Error(`${where} is not a table row`);
  }
  return row as unknown as TableRow;
}

function checkBand(row: unknown, where: string): BandRow {
  if (
    !isObject(row) ||
    typeof row.id !== "string" ||
    typeof row.statistic !== "string" ||
    typeof row.lo !== "string" ||
    typeof row.hi !== "string" ||
    !(row.observed === null || typeof row.observed === "string") ||
    !(row.status === "PASS" || row.status === "FAIL" || row.status === "NOT MEASURABLE")
  ) {
    throw new Error(`${where} is not a realism band row`);
  }
  return row as unknown as BandRow;
}

/* "2.3000" to "2.3", "4.0000" to "4". */
export function trimDecimal(value: string): string {
  return value.includes(".") ? value.replace(/0+$/, "").replace(/\.$/, "") : value;
}

export async function loadSeason(): Promise<SeasonLoad> {
  let manifestText: string;
  try {
    manifestText = (await fetchBytes("season/manifest.json")).text;
  } catch (error) {
    return { status: "absent", reason: message(error) };
  }
  try {
    const manifest: unknown = JSON.parse(manifestText);
    if (
      !isObject(manifest) ||
      manifest.schema !== SEASON_SCHEMA ||
      !isInt(manifest.season) ||
      typeof manifest.generator !== "string" ||
      typeof manifest.season_sha256 !== "string" ||
      !Array.isArray(manifest.files)
    ) {
      throw new Error(`season/manifest.json is not an ${SEASON_SCHEMA} manifest`);
    }
    const listed = new Map<string, string>();
    for (const entry of manifest.files as unknown[]) {
      if (isObject(entry) && typeof entry.name === "string" && typeof entry.sha256 === "string" && SHA256_HEX.test(entry.sha256)) {
        listed.set(entry.name, entry.sha256);
      }
    }
    const bodies: Record<string, unknown> = {};
    for (const name of NEEDED) {
      const expected = listed.get(name);
      if (!expected) {
        throw new Error(`season/manifest.json does not list ${name}`);
      }
      const fetched = await fetchBytes(`season/${name}`);
      const got = await sha256Hex(fetched.buffer);
      if (got !== expected) {
        throw new Error(`season/${name} hashes to ${got.slice(0, 12)} in your browser, the manifest lists ${expected.slice(0, 12)}`);
      }
      bodies[name] = JSON.parse(fetched.text);
    }
    const fixtures = bodies["fixtures.json"];
    const tables = bodies["tables.json"];
    const datalab = bodies["datalab.json"];
    if (!isObject(fixtures) || !Array.isArray(fixtures.clubs) || typeof fixtures.league !== "string" || !isInt(fixtures.matchdays_total)) {
      throw new Error("season/fixtures.json does not hold the league and its clubs");
    }
    if (!isObject(tables) || !Array.isArray(tables.matchdays) || tables.matchdays.length === 0) {
      throw new Error("season/tables.json holds no matchday table");
    }
    if (!isObject(datalab) || !isObject(datalab.bands) || !Array.isArray(datalab.bands.rows) || !isObject(datalab.dataset_card)) {
      throw new Error("season/datalab.json holds no realism bands");
    }
    const clubs: Record<string, SeasonClub> = {};
    (fixtures.clubs as unknown[]).forEach((club, i) => {
      if (!isObject(club) || typeof club.club_id !== "string" || typeof club.name !== "string" || typeof club.short !== "string") {
        throw new Error(`season/fixtures.json clubs[${i}] is not a club`);
      }
      clubs[club.club_id] = { club_id: club.club_id, name: club.name, short: club.short };
    });
    const last: unknown = tables.matchdays[tables.matchdays.length - 1];
    if (!isObject(last) || !isInt(last.matchday) || !Array.isArray(last.rows)) {
      throw new Error("season/tables.json ends with a matchday that is not a table");
    }
    const table = (last.rows as unknown[]).map((row, i) => checkRow(row, `the table row ${i + 1}`));
    const bands = datalab.bands;
    if (!isInt(bands.passed) || !isInt(bands.of) || !isInt(bands.threshold) || typeof bands.label !== "string") {
      throw new Error("season/datalab.json bands lack their counts");
    }
    const rows = (bands.rows as unknown[]).map((row, i) => checkBand(row, `band ${i + 1}`));
    return {
      status: "ready",
      view: {
        season: manifest.season,
        generator: manifest.generator,
        seasonSha256: manifest.season_sha256,
        league: fixtures.league,
        matchday: last.matchday,
        matchdaysTotal: fixtures.matchdays_total,
        clubs,
        table,
        tiebreak: typeof tables.tiebreak_rule === "string" ? tables.tiebreak_rule : "",
        bands: {
          label: bands.label,
          seeds: typeof bands.seeds === "string" ? bands.seeds : "",
          passed: bands.passed,
          of: bands.of,
          threshold: bands.threshold,
          command: typeof bands.command === "string" ? bands.command : "",
          rows,
        },
        datasetCardUrl: typeof datalab.dataset_card.url === "string" ? datalab.dataset_card.url : "",
        filesChecked: NEEDED.length,
      },
    };
  } catch (error) {
    return { status: "error", message: message(error) };
  }
}
