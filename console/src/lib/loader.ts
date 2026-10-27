/*
  Loading the static files, with every response checked.

  A non-200, or an HTML page where JSON was expected (the host's fallback page
  for a missing file), is an error; the runs index and each run must pass
  validateIndex and validateRun before any screen sees them. The fetch is
  injected, so the prerender step reads the same files from disk through the
  same code and the first client render matches the prerendered page.
*/
import {
  DATA_BASE,
  FIXTURE_BASE,
  MANIFEST_NAME,
  fileUrl,
  isObject,
  looksLikeHtml,
  parseManifestText,
  parseNdjson,
  parseWorldText,
  worldUrl,
} from "./core";
import { BUNDLE_MANIFEST, CARD_FILE, INDEX_NAME, RUNS_BASE, analystNotes, bundleFileUrl, runUrl, validateIndex, validateRun } from "./runs";
import type {
  AnalystNotes,
  BundleManifest,
  DataManifest,
  EditionFile,
  InitialData,
  LoadedEdition,
  LoadedRun,
  ManifestEntry,
  MatchEvent,
  Run,
  RunsData,
  RunsIndex,
  World,
} from "./types";

export interface TextResponse {
  ok: boolean;
  status: number;
  text: string;
}

export type FetchText = (url: string) => Promise<TextResponse>;

export const browserFetchText: FetchText = async (url) => {
  const response = await fetch(url, { cache: "no-store" });
  return { ok: response.ok, status: response.status, text: await response.text() };
};

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

async function fetchJson(fetchText: FetchText, url: string): Promise<unknown> {
  const res = await fetchText(url);
  if (!res.ok || looksLikeHtml(res.text)) {
    throw new Error(`${url} answered ${res.status} instead of a JSON file`);
  }
  try {
    return JSON.parse(res.text);
  } catch {
    throw new Error(`${url} is not valid JSON`);
  }
}

function checkBundleManifest(value: unknown, url: string): BundleManifest {
  const scope = isObject(value) ? value.attested_scope : null;
  if (
    !isObject(value) ||
    !isObject(value.artifacts) ||
    !isObject(scope) ||
    !Array.isArray(scope.attested) ||
    !Array.isArray(scope.gate_cleared)
  ) {
    throw new Error(`${url} is not a sealed bundle manifest`);
  }
  return value as unknown as BundleManifest;
}

/* The Analyst's turning points, read from the sealed card.json of a pipeline run whose manifest
   lists it. They never stop the run from loading: a card that is missing, unreadable or carries
   no turning point gives no Analyst desk, and Verify still reports the file by its hash. */
async function loadAnalyst(fetchText: FetchText, run: Run, manifest: BundleManifest): Promise<AnalystNotes | null> {
  if (run.provenance !== "pipeline" || !Object.prototype.hasOwnProperty.call(manifest.artifacts, CARD_FILE)) {
    return null;
  }
  try {
    return analystNotes(await fetchJson(fetchText, bundleFileUrl(run, CARD_FILE)), manifest);
  } catch {
    return null;
  }
}

export async function loadRun(fetchText: FetchText, runId: string): Promise<LoadedRun> {
  const raw = await fetchJson(fetchText, runUrl(runId));
  const problems = validateRun(raw);
  if (problems.length) {
    throw new Error(`the run file breaks the run format: ${problems.join("; ")}`);
  }
  const run = raw as Run;
  if (run.run_id !== runId) {
    throw new Error(`the run file names itself ${run.run_id}, not ${runId}`);
  }
  const manifestUrl = bundleFileUrl(run, BUNDLE_MANIFEST);
  const manifest = checkBundleManifest(await fetchJson(fetchText, manifestUrl), manifestUrl);
  const editions: Record<string, LoadedEdition> = {};
  for (const meta of run.editions) {
    const data = await fetchJson(fetchText, bundleFileUrl(run, meta.file));
    if (!isObject(data) || data.edition !== meta.edition || !Array.isArray(data.sentences)) {
      throw new Error(`${meta.file} does not hold the ${meta.name} edition`);
    }
    editions[meta.edition] = { meta, data: data as unknown as EditionFile };
  }
  return { run, manifest, editions, analyst: await loadAnalyst(fetchText, run, manifest) };
}

export async function loadRunsIndex(fetchText: FetchText): Promise<RunsIndex> {
  const index = await fetchJson(fetchText, RUNS_BASE + INDEX_NAME);
  const problems = validateIndex(index);
  if (problems.length) {
    throw new Error(`the runs index breaks the run format: ${problems.join("; ")}`);
  }
  return index as RunsIndex;
}

export async function loadRunsData(fetchText: FetchText): Promise<RunsData> {
  const index = await loadRunsIndex(fetchText);
  const runs: Record<string, LoadedRun> = {};
  for (const row of index.runs) {
    runs[row.run_id] = await loadRun(fetchText, row.run_id);
  }
  return { index, runs, defaultRunId: index.runs[0].run_id };
}

export async function loadInitialData(fetchText: FetchText): Promise<InitialData> {
  try {
    return { status: "ready", runs: await loadRunsData(fetchText) };
  } catch (error) {
    return { status: "error", message: `The run could not be loaded: ${message(error)}.` };
  }
}

/* ------------------------------------------------------------- match data */

export interface ResolvedManifest {
  source: "data" | "fixture";
  base: string;
  manifest: DataManifest;
  manifestUrl: string;
  notice: string | null;
}

/* data/ first, then the committed fixture. The notice says why when
   data/manifest.json exists but could not be used, so the page never
   switches silently. */
export async function resolveManifest(fetchText: FetchText): Promise<ResolvedManifest> {
  const dataUrl = DATA_BASE + MANIFEST_NAME;
  const fixtureUrl = FIXTURE_BASE + MANIFEST_NAME;

  async function fromFixture(notice: string | null): Promise<ResolvedManifest> {
    const res = await fetchText(fixtureUrl);
    if (!res.ok || looksLikeHtml(res.text)) {
      throw new Error(`No match data could be loaded: ${fixtureUrl} answered ${res.status}.`);
    }
    let manifest: DataManifest;
    try {
      manifest = parseManifestText(res.text);
    } catch (error) {
      throw new Error(`The fixture manifest is not valid: ${message(error)}`);
    }
    return { source: "fixture", base: FIXTURE_BASE, manifest, manifestUrl: fixtureUrl, notice };
  }

  let res: TextResponse;
  try {
    res = await fetchText(dataUrl);
  } catch {
    return fromFixture(null);
  }
  if (!res.ok || looksLikeHtml(res.text)) {
    return fromFixture(null);
  }
  try {
    const manifest = parseManifestText(res.text);
    return { source: "data", base: DATA_BASE, manifest, manifestUrl: dataUrl, notice: null };
  } catch (error) {
    return fromFixture(`${dataUrl} was found but could not be used (${message(error)}), so the committed fixture is shown instead.`);
  }
}

export interface LoadedMatch {
  events: MatchEvent[];
  world: World | null;
  worldProblem: string;
}

/* The world never stops the replay: a names problem comes back as text. */
async function loadWorld(fetchText: FetchText, base: string, entry: ManifestEntry): Promise<{ world: World | null; problem: string }> {
  try {
    const url = worldUrl(base, entry.world);
    const res = await fetchText(url);
    if (!res.ok || looksLikeHtml(res.text)) {
      throw new Error(`${url} answered ${res.status}`);
    }
    const world = parseWorldText(res.text);
    if (world.seed !== entry.seed) {
      throw new Error(`${url} is for seed ${world.seed}, not seed ${entry.seed}`);
    }
    return { world, problem: "" };
  } catch (error) {
    return { world: null, problem: message(error) };
  }
}

export async function loadMatch(fetchText: FetchText, resolved: ResolvedManifest, entry: ManifestEntry): Promise<LoadedMatch> {
  const url = fileUrl(resolved.base, entry.name);
  const [res, world] = await Promise.all([fetchText(url), loadWorld(fetchText, resolved.base, entry)]);
  if (!res.ok || looksLikeHtml(res.text)) {
    throw new Error(`${url} answered ${res.status} instead of an event file.`);
  }
  return { events: parseNdjson(res.text), world: world.world, worldProblem: world.problem };
}
