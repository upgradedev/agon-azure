/*
  Media office: the recorded run as the head of a club media office reads it.
  Run status, the job executions read from Azure and the spans between them,
  what was delivered in which languages and where, which checks passed, and
  the approval audit trail with the approved hash recomputed against the
  published one in this browser. Built only from the recorded run file, its
  sealed manifest and the executions evidence (ops/); a figure that was not
  measured says so instead of being estimated.
*/
import { useEffect, useState } from "react";
import { CircleCheck, CircleX } from "lucide-react";
import { Card, CardBody, CardHeader, CardTitle } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import { Notice } from "../components/ui/notice";
import { HashText } from "../components/ui/hash-text";
import { ScreenHeader, View } from "../components/screen";
import { Swatch } from "../components/match/charts";
import { fetchBytes, hasSubtle, sha256Hex } from "../lib/hash";
import { CHANNEL_WORDS, bundleFileUrl, formatUtc, shortHash, stateWords } from "../lib/runs";
import { JOB_WORDS, clockUtc, loadOps, span, type Execution, type OpsLoad } from "../lib/ops";
import { cn } from "../lib/cn";
import type { Run, RunsData } from "../lib/types";

const DEST_WORDS: Record<string, string> = {
  delivered: "Delivered",
  preview_not_posted: "Rendered, not posted or sent",
  not_selected: "Offered, not selected",
  not_offered: "Not offered",
};

const EDITION_LANGUAGE: Record<string, string> = { en: "English", el: "Greek", es: "Spanish", fr: "French", easy: "Easy-read English" };

function Tile({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div className="min-w-0 rounded-xl border border-line bg-surface p-4 shadow-card">
      <p className="text-xs font-medium text-muted">{label}</p>
      <p className="mt-1 text-2xl font-semibold tracking-tight tabular-nums text-fg">{value}</p>
      <p className="mt-1 text-xs leading-5 text-muted">{note}</p>
    </div>
  );
}

function ExecutionChart({ rows, approvedAt }: { rows: Execution[]; approvedAt: string | null }) {
  const W = 900;
  const L = 170;
  const R = 886;
  const rowH = 34;
  const top = 26;
  const H = top + rows.length * rowH + 30;
  const times = rows.flatMap((r) => [Date.parse(r.start), Date.parse(r.end)]);
  if (approvedAt) {
    times.push(Date.parse(approvedAt));
  }
  const t0 = Math.min(...times);
  const t1 = Math.max(...times);
  const x = (iso: string) => L + ((Date.parse(iso) - t0) / Math.max(1, t1 - t0)) * (R - L);
  const fleet = rows.filter((r) => r.job === "job-agon-fleet");
  const gap = fleet.length >= 2 ? { from: fleet[0].end, to: fleet[1].start } : null;
  const ticks: number[] = [];
  for (let t = Math.ceil(t0 / 60000) * 60000; t <= t1; t += 60000) {
    ticks.push(t);
  }
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="block h-auto w-full" role="img" aria-label="Job executions of the recorded run, from the worker that posted the card to the publisher.">
      {gap ? (
        <g>
          <rect x={x(gap.from)} y={top - 8} width={Math.max(0, x(gap.to) - x(gap.from))} height={rows.length * rowH + 8} className="fill-preview-bg" />
          <text x={(x(gap.from) + x(gap.to)) / 2} y={top - 12} textAnchor="middle" className="fill-preview text-[10px] font-semibold">
            no fleet worker held the run
          </text>
        </g>
      ) : null}
      {ticks.map((t) => {
        const tx = L + ((t - t0) / Math.max(1, t1 - t0)) * (R - L);
        const d = new Date(t);
        return (
          <g key={t}>
            <line x1={tx} y1={top - 6} x2={tx} y2={top + rows.length * rowH} className="stroke-line" />
            <text x={tx} y={top + rows.length * rowH + 14} textAnchor="middle" className="fill-muted text-[10px]">
              {`${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}`}
            </text>
          </g>
        );
      })}
      {rows.map((r, i) => {
        const y = top + i * rowH;
        return (
          <g key={r.execution}>
            <text x={L - 10} y={y + 15} textAnchor="end" className="fill-fg text-[11px] font-semibold">
              {JOB_WORDS[r.job] ?? r.job}
            </text>
            <text x={L - 10} y={y + 27} textAnchor="end" className="fill-muted text-[9px]">
              {clockUtc(r.start).replace(" UTC", "")} to {clockUtc(r.end).replace(" UTC", "")}
            </text>
            <rect x={x(r.start)} y={y + 6} width={Math.max(3, x(r.end) - x(r.start))} height={rowH - 14} rx="4" className={r.job === "job-agon-fleet" ? "fill-primary" : r.job === "job-agon-publisher" ? "fill-home" : "fill-away"} />
          </g>
        );
      })}
      {approvedAt ? (
        <g>
          <line x1={x(approvedAt)} y1={top - 8} x2={x(approvedAt)} y2={top + rows.length * rowH} className="stroke-fg" strokeWidth="2" strokeDasharray="4 3" />
          <text x={x(approvedAt) + 4} y={top + rows.length * rowH + 26} className="fill-fg text-[10px] font-semibold">
            decision verified {clockUtc(approvedAt).replace(" UTC", "")}
          </text>
        </g>
      ) : null}
    </svg>
  );
}

export function MediaOffice({ active, runs }: { active: boolean; runs: RunsData | null }) {
  const run: Run | null = runs ? runs.runs[runs.defaultRunId]?.run ?? null : null;
  const recorded = run && run.provenance === "pipeline" ? run : null;
  const [ops, setOps] = useState<OpsLoad>({ status: "loading" });
  const [published, setPublished] = useState<string | null>(null);

  useEffect(() => {
    if (!active || !recorded) {
      return undefined;
    }
    let cancelled = false;
    loadOps(recorded.run_id, recorded.bundle.sha256).then((value) => {
      if (!cancelled) {
        setOps(value);
      }
    });
    if (hasSubtle()) {
      fetchBytes(bundleFileUrl(recorded, "manifest.json"))
        .then((f) => sha256Hex(f.buffer))
        .then((sha) => {
          if (!cancelled) {
            setPublished(sha);
          }
        })
        .catch(() => {
          if (!cancelled) {
            setPublished("");
          }
        });
    }
    return () => {
      cancelled = true;
    };
  }, [active, recorded]);

  const approval = recorded?.approval ?? null;
  const evidence = ops.status === "ready" ? ops.evidence : null;
  const rows = evidence ? [...evidence.executions].sort((a, b) => Date.parse(a.start) - Date.parse(b.start)) : [];
  const fleet = rows.filter((r) => r.job === "job-agon-fleet");
  const publisher = rows.find((r) => r.job === "job-agon-publisher") ?? null;
  const intake = rows.find((r) => r.job === "job-agon-intake") ?? null;
  const gates = recorded ? recorded.gates.filter((g) => g.gate !== "factgate_draft") : [];
  const passed = gates.filter((g) => g.verdict === "pass").length;
  const equal = published !== null && approval?.approved_bundle_sha256 === published && published !== "";

  return (
    <View view="media-office" active={active}>
      <ScreenHeader
        view="media-office"
        eyebrow="Media office"
        title="What the newsroom did for this match"
        lead="For the head of the club media office: the recorded run's status, how long each step took, what went out in which languages, which checks passed and who approved which hash. Built only from the recorded run file, its sealed manifest and the job executions read from Azure; a figure that was not measured says so."
      />
      {!recorded ? (
        <Notice tone="info" data-ops="none">
          No recorded pipeline run is on this site yet, so there is nothing to report: the hand-written sample is a design preview and never ran.
        </Notice>
      ) : (
        <div className="space-y-6" data-ops={ops.status !== "loading" && published !== null ? "done" : undefined}>
          <Card aria-labelledby="h-run-status">
            <CardHeader>
              <CardTitle id="h-run-status">Run status</CardTitle>
              <Badge tone={recorded.state === "PUBLISHED" ? "success" : "neutral"}>{stateWords(recorded.state)}</Badge>
            </CardHeader>
            <CardBody className="space-y-3">
              <p className="text-sm font-medium text-fg">{recorded.label}</p>
              <p className="text-sm text-muted">
                {recorded.home.name} {recorded.score.home}, {recorded.away.name} {recorded.score.away}. Synthetic match, seed {recorded.seed}, fictional clubs and players.
              </p>
              <div>
                <p className="mb-1.5 text-xs font-semibold tracking-wide text-muted uppercase">Bundle hash</p>
                <HashText hash={recorded.bundle.sha256} label="Bundle hash" id="ops-hash" />
              </div>
            </CardBody>
          </Card>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <Tile
              label="Decision verified to published"
              value={approval?.approved_at && publisher ? span(approval.approved_at, publisher.end) : "not in the files"}
              note={
                publisher
                  ? "From the time the verifier recorded the officer's decision (the run file) to the publisher job ending (executions evidence); the tap on the card came earlier."
                  : "Needs the executions evidence of the publisher job."
              }
            />
            <Tile
              label="Worker run to the approval pause"
              value={fleet[0] ? span(fleet[0].start, fleet[0].end) : "not in the files"}
              note="The fleet worker ran the full-time workflow, posted the approval card and ended its process inside this span."
            />
            <Tile
              label="No fleet worker held the run"
              value={fleet.length >= 2 ? span(fleet[0].end, fleet[1].start) : "not in the files"}
              note="Between the fleet worker that paused and the new fleet worker that resumed from the durable checkpoint; the decision intake job ran inside this gap."
            />
            <Tile
              label="Editions sealed"
              value={String(recorded.editions.length)}
              note={`${recorded.editions.map((e) => EDITION_LANGUAGE[e.edition] ?? e.name).join(", ")}, all in the bundle hash; the destinations carry the English edition (what went out, and where, is below).`}
            />
            <Tile label="Checks passed" value={`${passed} of ${gates.length}`} note={gates.map((g) => g.name).join(", ")} />
            <Tile label="Cost per match" value="not measured yet" note="No cost figure has been measured for a run, so none is shown." />
          </div>

          <Card aria-labelledby="h-survived">
            <CardHeader>
              <CardTitle id="h-survived">The approval survived the process ending</CardTitle>
              {evidence ? <Badge tone="success">From Azure</Badge> : <Badge tone="neutral">No executions file</Badge>}
            </CardHeader>
            <CardBody className="space-y-3">
              {ops.status === "loading" ? <Notice tone="loading">Loading the job executions of this run.</Notice> : null}
              {ops.status === "missing" || ops.status === "error" ? <Notice tone={ops.status === "error" ? "danger" : "info"}>{ops.message}</Notice> : null}
              {evidence && fleet.length ? (
                <>
                  <p className="text-sm leading-6 text-fg">
                    The fleet worker that posted the approval card ended at {clockUtc(fleet[0].end)}.
                    {fleet[1] ? ` No fleet worker held the run for ${span(fleet[0].end, fleet[1].start)}.` : ""}
                    {approval?.approved_at
                      ? ` The decision the ${approval.approver_role ?? "approver"} took on ${CHANNEL_WORDS[approval.channel ?? ""] ?? "an approval channel"} was verified and recorded at ${clockUtc(approval.approved_at)}${
                          intake ? ` (the intake job, which verified it, ran from ${clockUtc(intake.start)} to ${clockUtc(intake.end)})` : ""
                        }.`
                      : ""}
                    {fleet[1] ? ` A new fleet worker process started at ${clockUtc(fleet[1].start)} and resumed from the durable checkpoint.` : ""}
                    {publisher ? ` The publisher finished at ${clockUtc(publisher.end)}.` : ""}
                  </p>
                  <div className="relative overflow-x-auto" tabIndex={0} role="region" aria-label="Job executions chart, scrolls sideways on a narrow screen">
                    <div className="min-w-[40rem]">
                      <ExecutionChart rows={rows} approvedAt={approval?.approved_at ?? null} />
                    </div>
                  </div>
                  <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
                    <li className="flex items-center gap-1.5">
                      <Swatch className="fill-primary" />
                      Fleet worker
                    </li>
                    <li className="flex items-center gap-1.5">
                      <Swatch className="fill-away" />
                      Decision intake
                    </li>
                    <li className="flex items-center gap-1.5">
                      <Swatch className="fill-home" />
                      Publisher
                    </li>
                    <li className="flex items-center gap-1.5">
                      <Swatch className="fill-preview-bg stroke-preview" />
                      No fleet worker held the run
                    </li>
                  </ul>
                  <ol className="space-y-1.5 text-sm">
                    {rows.map((r) => (
                      <li key={r.execution} className="rounded-lg bg-surface-2 px-3 py-2">
                        <span className="font-medium text-fg">{JOB_WORDS[r.job] ?? r.job}</span>{" "}
                        <span className="text-muted">
                          {clockUtc(r.start)} to {clockUtc(r.end)}, {r.status}: {r.role}.
                        </span>
                      </li>
                    ))}
                  </ol>
                  <p className="text-xs leading-5 text-muted">
                    Read from Azure on {evidence.captured_at} with: <code className="font-mono break-all">{evidence.source_command}</code>
                  </p>
                </>
              ) : null}
            </CardBody>
          </Card>

          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <Card aria-labelledby="h-delivered">
              <CardHeader>
                <CardTitle id="h-delivered">What went out, and where</CardTitle>
              </CardHeader>
              <CardBody className="space-y-3">
                <p className="text-sm text-muted">
                  Editions: {recorded.editions.map((e) => EDITION_LANGUAGE[e.edition] ?? e.name).join(", ")}, every one checked by the Fact Gate and the Rights Guard and sealed in the
                  same hash.
                </p>
                <ul className="divide-y divide-line">
                  {(recorded.destinations ?? []).map((d) => (
                    <li key={d.id} className="flex items-start justify-between gap-3 py-2 text-sm">
                      <span className="font-medium text-fg">{d.label}</span>
                      <span className={cn("text-right", d.status === "delivered" ? "text-accent" : "text-muted")}>{DEST_WORDS[d.status] ?? d.status}</span>
                    </li>
                  ))}
                </ul>
                {!recorded.destinations ? <p className="text-sm text-muted">This run file carries no destinations list.</p> : null}
              </CardBody>
            </Card>

            <Card aria-labelledby="h-checks">
              <CardHeader>
                <CardTitle id="h-checks">Checks</CardTitle>
                <Badge tone={passed === gates.length ? "success" : "danger"}>
                  {passed} of {gates.length} passed
                </Badge>
              </CardHeader>
              <CardBody>
                <ul className="space-y-2">
                  {gates.map((g) => (
                    <li key={g.gate} className="flex items-start gap-2.5 text-sm">
                      {g.verdict === "pass" ? (
                        <CircleCheck className="mt-0.5 size-4 shrink-0 text-accent" aria-hidden="true" />
                      ) : (
                        <CircleX className="mt-0.5 size-4 shrink-0 text-danger" aria-hidden="true" />
                      )}
                      <span className="min-w-0">
                        <span className="font-medium text-fg">{g.name}</span>{" "}
                        <span className="text-muted">{g.detail.startsWith(g.name) ? g.detail.slice(g.name.length).replace(/^:\s*/, "") : g.detail}</span>
                      </span>
                    </li>
                  ))}
                </ul>
              </CardBody>
            </Card>
          </div>

          <Card aria-labelledby="h-audit">
            <CardHeader>
              <CardTitle id="h-audit">Approval audit trail</CardTitle>
              {published === null ? (
                <Badge tone="neutral">Recomputing</Badge>
              ) : equal ? (
                <Badge tone="success">Approved hash equals published hash</Badge>
              ) : (
                <Badge tone="danger">Hashes differ</Badge>
              )}
            </CardHeader>
            <CardBody>
              <dl className="grid gap-3 sm:grid-cols-2">
                {[
                  ["Who", `The ${approval?.approver_role ?? "approver"} role (one verifier for every channel)`],
                  ["Channel", CHANNEL_WORDS[approval?.channel ?? ""] ?? "not recorded"],
                  ["Decision verified and recorded", approval?.approved_at ? formatUtc(approval.approved_at) : "not recorded"],
                  ["Approved hash", approval?.approved_bundle_sha256 ? shortHash(approval.approved_bundle_sha256) : "none"],
                  ["Published hash, computed on this page", published === null ? "computing" : published ? shortHash(published) : "could not be computed"],
                  ["Result", equal ? "The officer approved exactly the bytes that are published." : published === null ? "Recomputing." : "The published bytes are not the approved ones."],
                ].map(([k, v]) => (
                  <div key={k} className="rounded-lg bg-surface-2 px-3 py-2">
                    <dt className="text-xs text-muted">{k}</dt>
                    <dd className="mt-0.5 text-sm font-medium break-words text-fg">{v}</dd>
                  </div>
                ))}
              </dl>
            </CardBody>
          </Card>
        </div>
      )}
    </View>
  );
}
