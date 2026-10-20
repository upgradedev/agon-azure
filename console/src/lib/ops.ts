/*
  The recorded run's job execution evidence (ops/<run_id>.executions.json,
  copied and validated at deploy by agon.webops from evidence/): when each
  Container Apps job execution started and ended, read from Azure with the
  command the file names. The Media office view lists it and derives the
  spans between those timestamps; nothing here is estimated.
*/
import { isObject } from "./core";
import { fetchBytes, sha256Hex } from "./hash";

export const OPS_MANIFEST = "ops/manifest.json";

export interface Execution {
  job: string;
  execution: string;
  status: string;
  start: string;
  end: string;
  role: string;
}

export interface ExecutionEvidence {
  schema: string;
  run_id: string;
  recorded_run: string;
  bundle_sha256: string;
  source_command: string;
  captured_at: string;
  executions: Execution[];
}

export type OpsLoad =
  | { status: "loading" }
  | { status: "missing"; message: string }
  | { status: "error"; message: string }
  | { status: "ready"; evidence: ExecutionEvidence };

const KEYS = ["bundle_sha256", "captured_at", "executions", "recorded_run", "run_id", "schema", "source_command"];
const EXEC_KEYS = ["end", "execution", "job", "role", "start", "status"];
const ZONED = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$/;

export function validateEvidence(body: unknown, runId: string, bundleSha256: string): string[] {
  if (!isObject(body) || Object.keys(body).sort().join(",") !== KEYS.join(",")) {
    return ["the evidence keys differ from agon.run-executions/1"];
  }
  const problems: string[] = [];
  if (body.schema !== "agon.run-executions/1") {
    problems.push("schema must be agon.run-executions/1");
  }
  if (body.recorded_run !== runId || body.bundle_sha256 !== bundleSha256) {
    problems.push("the evidence describes another run or bundle");
  }
  if (!Array.isArray(body.executions) || !body.executions.length) {
    problems.push("executions must be a non-empty list");
    return problems;
  }
  for (const [i, row] of (body.executions as unknown[]).entries()) {
    if (!isObject(row) || Object.keys(row).sort().join(",") !== EXEC_KEYS.join(",")) {
      problems.push(`executions[${i}] keys differ`);
      continue;
    }
    if (typeof row.start !== "string" || typeof row.end !== "string" || !ZONED.test(row.start) || !ZONED.test(row.end)) {
      problems.push(`executions[${i}] needs zoned ISO times`);
    } else if (Date.parse(row.end) < Date.parse(row.start)) {
      problems.push(`executions[${i}] ends before it starts`);
    }
  }
  return problems;
}

export async function loadOps(runId: string, bundleSha256: string): Promise<OpsLoad> {
  const none: OpsLoad = {
    status: "missing",
    message: `This recorded run has no executions file, so its job timings are not shown; the run file carries only the time the decision was verified and recorded.`,
  };
  try {
    const list: unknown = JSON.parse((await fetchBytes(OPS_MANIFEST)).text);
    if (!isObject(list) || list.schema !== "agon.ops-manifest/1" || !Array.isArray(list.files)) {
      return { status: "error", message: "ops/manifest.json is not an agon.ops-manifest/1 list." };
    }
    const entry = (list.files as unknown[]).find((f) => isObject(f) && f.run_id === runId);
    if (!isObject(entry) || typeof entry.name !== "string" || typeof entry.sha256 !== "string" || !/^[a-z0-9-]+\.executions\.json$/.test(entry.name)) {
      return none;
    }
    const fetched = await fetchBytes(`ops/${entry.name}`);
    if ((await sha256Hex(fetched.buffer)) !== entry.sha256) {
      return { status: "error", message: `ops/${entry.name} does not hash to its list entry, so it is not shown.` };
    }
    const body: unknown = JSON.parse(fetched.text);
    const problems = validateEvidence(body, runId, bundleSha256);
    if (problems.length) {
      return { status: "error", message: `ops/${entry.name} is not shown: ${problems.join("; ")}.` };
    }
    return { status: "ready", evidence: body as ExecutionEvidence };
  } catch (error) {
    const text = error instanceof Error ? error.message : String(error);
    if (/answered 404|returned a web page/.test(text)) {
      return none;
    }
    return { status: "error", message: `The job timings could not be read: ${text}.` };
  }
}

/* "21:21:36 UTC" from a zoned ISO time, in UTC whatever the zone. */
export function clockUtc(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())} UTC`;
}

/* "3 min 30 s" between two zoned ISO times. */
export function span(fromIso: string, toIso: string): string {
  const seconds = Math.round((Date.parse(toIso) - Date.parse(fromIso)) / 1000);
  if (seconds < 0) {
    return "none";
  }
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return m ? `${m} min ${s} s` : `${s} s`;
}

export const JOB_WORDS: Record<string, string> = {
  "job-agon-fleet": "Fleet worker",
  "job-agon-intake": "Decision intake",
  "job-agon-publisher": "Publisher",
};
