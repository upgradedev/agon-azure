/*
  S5 Verify: recompute every published file's SHA-256 in the visitor's browser
  with WebCrypto and compare, on this page.

  Run bundle: for each run, the page downloads the sealed manifest and every
  edition file it lists, hashes the exact bytes received, and compares
  (a) the sha256 of manifest.json with the bundle hash on the approval card,
  (b) each file with the manifest, and (c) the approved hash with the
  published (recomputed) hash. Match files: the same for every event file and
  names file in the data manifest. Every count below is computed here.
*/
import { useCallback, useEffect, useRef, useState } from "react";
import { CircleCheck, CircleX, RotateCcw } from "lucide-react";
import { Card, CardBody, CardHeader, CardTitle } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Notice } from "../components/ui/notice";
import { ScreenHeader, StepNav, View } from "../components/screen";
import { countLines, fileUrl, worldUrl } from "../lib/core";
import { browserFetchText, loadRunsIndex, resolveManifest, type ResolvedManifest } from "../lib/loader";
import { fetchBytes, hasSubtle, selfTest, sha256Hex, type SelfTest } from "../lib/hash";
import {
  BUNDLE_MANIFEST,
  RUNS_BASE,
  bundleCheck,
  bundleFileUrl,
  compareApproved,
  shortHash,
  validateRun,
  type ApprovalComparison,
  type BundleCheck,
  type ComputedBundle,
} from "../lib/runs";
import { cn } from "../lib/cn";
import type { BundleManifest, ManifestEntry, Run } from "../lib/types";

interface FileResult {
  name: string;
  seed: number;
  world: boolean;
  expected: string;
  computed: string;
  status: "match" | "mismatch" | "error";
  lines: number | null;
  events: number | null;
  reason: string;
}

interface BundleResult {
  run: Run;
  computed: ComputedBundle;
  check: BundleCheck;
  approval: ApprovalComparison;
}

type Phase = "idle" | "running" | "done";

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

async function checkFile(base: string, entry: ManifestEntry, world: boolean): Promise<FileResult> {
  const name = world ? entry.world : entry.name;
  const result: FileResult = {
    name,
    seed: entry.seed,
    world,
    expected: world ? entry.world_sha256 : entry.sha256,
    computed: "",
    status: "error",
    lines: null,
    events: world ? null : entry.events,
    reason: "",
  };
  try {
    const url = world ? worldUrl(base, name) : fileUrl(base, name);
    const fetched = await fetchBytes(url);
    if (!world) {
      result.lines = countLines(fetched.text);
    }
    result.computed = await sha256Hex(fetched.buffer);
    result.status = result.computed === result.expected ? "match" : "mismatch";
  } catch (error) {
    result.reason = message(error);
  }
  return result;
}

async function checkBundle(file: string): Promise<BundleResult> {
  const runFile = await fetchBytes(RUNS_BASE + file);
  const run: unknown = JSON.parse(runFile.text);
  const problems = validateRun(run);
  if (problems.length) {
    throw new Error(`${file} breaks the run format: ${problems.join("; ")}`);
  }
  const valid = run as Run;
  const manifestFile = await fetchBytes(bundleFileUrl(valid, BUNDLE_MANIFEST));
  const manifest = JSON.parse(manifestFile.text) as BundleManifest;
  const computed: ComputedBundle = { manifest: await sha256Hex(manifestFile.buffer), files: {} };
  const names = Object.keys(manifest && typeof manifest.artifacts === "object" ? manifest.artifacts : {}).sort();
  await Promise.all(
    names.map(async (name) => {
      try {
        const fetched = await fetchBytes(bundleFileUrl(valid, name));
        computed.files[name] = { sha256: await sha256Hex(fetched.buffer), bytes: fetched.buffer.byteLength };
      } catch {
        /* Left out of computed.files: the check reports it as not fetched. */
      }
    }),
  );
  return { run: valid, computed, check: bundleCheck(valid, manifest, computed), approval: compareApproved(valid, computed.manifest) };
}

function StatusBadge({ status }: { status: "match" | "mismatch" | "error" }) {
  if (status === "match") {
    return (
      <Badge tone="success">
        <CircleCheck aria-hidden="true" />
        Match
      </Badge>
    );
  }
  return (
    <Badge tone="danger">
      <CircleX aria-hidden="true" />
      {status === "mismatch" ? "Mismatch" : "Not checked"}
    </Badge>
  );
}

function HashRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid gap-0.5 sm:grid-cols-[15rem_minmax(0,1fr)] sm:gap-3">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="min-w-0 font-mono text-xs break-all text-fg">{value || "not computed"}</dd>
    </div>
  );
}

export function Verify({ active }: { active: boolean }) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [browserCheck, setBrowserCheck] = useState<SelfTest | null>(null);
  const [resolved, setResolved] = useState<ResolvedManifest | null>(null);
  const [files, setFiles] = useState<FileResult[]>([]);
  const [manifestHash, setManifestHash] = useState("");
  const [dataError, setDataError] = useState<string | null>(null);
  const [bundles, setBundles] = useState<BundleResult[]>([]);
  const [bundleError, setBundleError] = useState<string | null>(null);
  const ranOnce = useRef(false);

  const run = useCallback(async () => {
    setPhase("running");
    setDataError(null);
    setBundleError(null);
    const test = await selfTest();
    setBrowserCheck(test);
    if (!test.ok || !hasSubtle()) {
      setDataError("Nothing was recomputed, because this browser cannot compute SHA-256 on this page.");
      setPhase("done");
      return;
    }
    const dataJob = (async () => {
      try {
        const value = await resolveManifest(browserFetchText);
        setResolved(value);
        const manifestBytes = await fetchBytes(value.manifestUrl).catch(() => null);
        setManifestHash(manifestBytes ? await sha256Hex(manifestBytes.buffer) : "");
        const checks = value.manifest.files.flatMap((entry) => [checkFile(value.base, entry, false), checkFile(value.base, entry, true)]);
        setFiles(await Promise.all(checks));
      } catch (error) {
        setFiles([]);
        setDataError(`Nothing was recomputed: ${message(error)}`);
      }
    })();
    const bundleJob = (async () => {
      try {
        const index = await loadRunsIndex(browserFetchText);
        setBundles(await Promise.all(index.runs.map((row) => checkBundle(row.file))));
      } catch (error) {
        setBundles([]);
        setBundleError(`The run bundle was not recomputed: ${message(error)}.`);
      }
    })();
    await Promise.all([dataJob, bundleJob]);
    setPhase("done");
  }, []);

  /* Runs when the visitor opens the page, not on every visit to the site. */
  useEffect(() => {
    if (active && !ranOnce.current) {
      ranOnce.current = true;
      void run();
    }
  }, [active, run]);

  const matches = files.filter((f) => f.status === "match").length;
  const eventFiles = files.filter((f) => !f.world);
  const countsAgree = eventFiles.filter((f) => f.lines !== null && f.lines === f.events).length;
  const bundlesOk = bundles.filter((b) => b.check.ok).length;
  const first = bundles[0];
  const running = phase === "running";

  let bundleOverall = "The bundle check starts when you open this page.";
  if (running && !bundles.length) {
    bundleOverall = "Recomputing the run bundle in your browser.";
  } else if (bundleError) {
    bundleOverall = bundleError;
  } else if (first) {
    const total = first.check.rows.length;
    const matched = first.check.rows.filter((r) => r.status === "match").length;
    bundleOverall = first.check.bundleMatches
      ? `Recomputed in your browser: the bundle hash equals the hash on the approval card, ${shortHash(first.run.bundle.sha256)}, and ${matched} of ${total} edition files match the sealed manifest.`
      : `Recomputed in your browser: the bundle hash does not equal the hash on the approval card, and ${matched} of ${total} edition files match the sealed manifest.`;
    if (bundles.length > 1) {
      bundleOverall += ` ${bundlesOk} of ${bundles.length} run bundles check out in full.`;
    }
  }

  let dataOverall = "The check starts when you open this page.";
  if (running && !files.length) {
    dataOverall = "Recomputing in your browser.";
  } else if (dataError) {
    dataOverall = dataError;
  } else if (files.length) {
    dataOverall = `Recomputed in your browser: ${matches} of ${files.length} files match.`;
  }

  return (
    <View view="verify" active={active}>
      <ScreenHeader
        view="verify"
        eyebrow="Verify"
        title="Recompute every hash in your browser"
        lead="This page downloads each published file and hashes the exact bytes with SHA-256 on your device; every match or mismatch below is counted here, not asserted by a server."
      />

      <div className="mb-6 flex flex-wrap items-center gap-3">
        <Button variant="secondary" onClick={() => void run()} aria-busy={running || undefined}>
          <RotateCcw aria-hidden="true" />
          {running ? "Recomputing" : phase === "done" ? "Recompute again" : "Recompute now"}
        </Button>
        <p className="text-sm text-muted" aria-live="polite">
          {running ? "Recomputing in your browser." : phase === "done" ? "Done. Every figure below was computed on this page." : "Nothing has been recomputed yet."}
        </p>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card aria-labelledby="h-bundle" className="lg:col-span-2" data-state={phase}>
          <CardHeader>
            <CardTitle id="h-bundle">Run bundle</CardTitle>
            {first ? <StatusBadge status={first.check.ok ? "match" : "mismatch"} /> : <Badge tone="neutral">{running ? "Running" : "Not run"}</Badge>}
          </CardHeader>
          <CardBody className="space-y-4">
            {first ? (
              <p className={cn("rounded-lg border px-3 py-2 text-sm font-medium", first.run.provenance === "design_preview" ? "border-preview/25 bg-preview-bg text-preview" : "border-line bg-surface-2 text-fg")}>
                {first.run.label}
              </p>
            ) : null}
            <p className={cn("text-sm font-medium", first ? (first.check.ok ? "text-accent" : "text-danger") : bundleError ? "text-danger" : "text-fg")} aria-live="polite">
              {bundleOverall}
            </p>
            {bundles.map((b) => (
              <div key={b.run.run_id} className="space-y-3">
                <div className="space-y-2 rounded-lg border border-line p-3">
                  <dl className="space-y-1.5">
                    <HashRow label="Bundle hash on the approval card" value={b.run.bundle.sha256} />
                    <HashRow label="sha256 of manifest.json, computed here" value={b.computed.manifest} />
                  </dl>
                  <StatusBadge status={b.check.bundleMatches ? "match" : "mismatch"} />
                </div>
                <Notice tone={b.approval.kind === "equal" ? "success" : b.approval.kind === "differs" ? "danger" : "info"}>
                  <strong className="font-semibold">Approved hash and published hash. </strong>
                  {b.approval.sentence}
                </Notice>
                <ul className="space-y-2">
                  {b.check.rows.map((r) => (
                    <li key={r.name} className="rounded-lg border border-line p-3">
                      <div className="mb-2 flex items-center justify-between gap-2">
                        <strong className="font-mono text-sm text-fg">{r.name}</strong>
                        <StatusBadge status={r.status} />
                      </div>
                      <dl className="space-y-1">
                        <HashRow label="sha256 in the sealed manifest" value={r.expected} />
                        <HashRow label="sha256 computed in your browser" value={r.computed} />
                      </dl>
                      {r.reason ? <p className="mt-1.5 text-xs text-danger">Differs: {r.reason}.</p> : null}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </CardBody>
        </Card>

        <Card aria-labelledby="h-files" data-state={phase}>
          <CardHeader>
            <CardTitle id="h-files">Match files</CardTitle>
            {resolved ? (
              <Badge tone={resolved.source === "fixture" ? "warning" : "success"}>{resolved.source === "fixture" ? "Fixture" : "Deployed data"}</Badge>
            ) : (
              <Badge tone="neutral">{running ? "Running" : "Not run"}</Badge>
            )}
          </CardHeader>
          <CardBody className="space-y-3">
            {resolved ? (
              <p className="text-sm text-muted">
                Manifest {resolved.manifestUrl}, generator {resolved.manifest.generator}, {resolved.manifest.files.length}{" "}
                {resolved.manifest.files.length === 1 ? "match" : "matches"}, each with an event file and a names file.
                {resolved.source === "fixture" ? " These are the committed development fixture files, not the deployed match files." : ""}
              </p>
            ) : null}
            {resolved?.notice ? <Notice tone="warning">{resolved.notice}</Notice> : null}
            <p className={cn("text-sm font-medium", files.length && matches === files.length ? "text-accent" : dataError ? "text-danger" : "text-fg")} aria-live="polite">
              {dataOverall}
            </p>
            {eventFiles.length ? (
              <p className="text-xs text-muted">
                Event counts: {countsAgree} of {eventFiles.length} event files have the number of lines the manifest lists.
              </p>
            ) : null}
            <ul className="space-y-2">
              {files.map((f) => (
                <li key={f.name} className="rounded-lg border border-line p-3">
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <strong className="font-mono text-sm text-fg">{f.name}</strong>
                    <StatusBadge status={f.status} />
                  </div>
                  <dl className="space-y-1">
                    <HashRow label={f.world ? `Names for seed ${f.seed}, in the manifest` : `Seed ${f.seed}, in the manifest`} value={f.expected} />
                    <HashRow label="Computed in your browser" value={f.computed} />
                  </dl>
                  {f.lines !== null ? (
                    <p className="mt-1.5 text-xs text-muted">
                      {f.lines} lines in the file, the manifest lists {f.events}
                      {f.lines === f.events ? "." : ". These differ."}
                    </p>
                  ) : null}
                  {f.reason ? <p className="mt-1.5 text-xs text-danger">Not checked: {f.reason}.</p> : null}
                </li>
              ))}
            </ul>
            {manifestHash ? <p className="font-mono text-xs break-all text-muted">manifest.json itself hashes to {manifestHash} in your browser.</p> : null}
          </CardBody>
        </Card>

        <div className="flex flex-col gap-6">
          <Card aria-labelledby="h-browser">
            <CardHeader>
              <CardTitle id="h-browser">Your browser</CardTitle>
              {browserCheck ? <Badge tone={browserCheck.ok ? "success" : "danger"}>{browserCheck.ok ? "Ready" : "Unavailable"}</Badge> : <Badge tone="neutral">Not checked</Badge>}
            </CardHeader>
            <CardBody>
              <p className="text-sm leading-6 text-fg" aria-live="polite">
                {browserCheck ? browserCheck.message : "The browser check runs when you open this page: a known SHA-256 test vector goes through WebCrypto on your device."}
              </p>
              <p className="mt-2 text-xs text-muted">Nothing leaves your device for this check.</p>
            </CardBody>
          </Card>

          <Card aria-labelledby="h-proves">
            <CardHeader>
              <CardTitle id="h-proves">What a match proves, and what it does not</CardTitle>
            </CardHeader>
            <CardBody className="space-y-2 text-sm leading-6 text-muted">
              <p>
                A match means the bytes your browser received are exactly the bytes the manifest lists, and that the bundle on the approval card
                is the bundle served here.
              </p>
              <p>
                It does not show that anyone approved them. The approved-hash line above is computed from the run file: today no report has been
                approved or published from this site.
              </p>
            </CardBody>
          </Card>
        </div>
      </div>

      <StepNav view="verify" />
    </View>
  );
}
