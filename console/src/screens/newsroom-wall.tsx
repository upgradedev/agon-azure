/*
  S2 Newsroom Wall: what each desk did at full time, the handoffs into one
  bundle, the bundle state walking DRAFT, CHECKED, SEALED, AWAITING_APPROVAL,
  and the verdict of every deterministic gate.

  Replay reveals the run one step at a time. Under reduced motion it never
  advances on its own: Next step moves it. State changes are announced in a
  polite live region, only while replaying. The run label sits in the header
  and is a single string, so the prerendered page carries it verbatim.
*/
import { useEffect, useId, useState } from "react";
import { ArrowRight, RotateCcw, StepForward } from "lucide-react";
import { Card, CardBody, CardHeader, CardTitle } from "../components/ui/card";
import { Badge, type BadgeTone } from "../components/ui/badge";
import { Button, ButtonLink } from "../components/ui/button";
import { Notice } from "../components/ui/notice";
import { LabelLine, ScreenHeader, SeedNotice, StepNav, View } from "../components/screen";
import {
  DESIGN_PREVIEW_LABEL,
  VERDICT_WORDS,
  WALL_STATES,
  deskNames,
  lastStep,
  matchLine,
  stateAt,
  stateWords,
  wallItems,
} from "../lib/runs";
import { hrefFor } from "../lib/router";
import { cn } from "../lib/cn";
import { useReducedMotion } from "../hooks/stores";
import type { GateVerdict, LoadedRun, Run } from "../lib/types";

const STEP_MS = 1100;
type Step = number | "all";

export function wallLabel(run: Run): string {
  return run.provenance === "design_preview"
    ? `${DESIGN_PREVIEW_LABEL} The gate verdicts were computed by the real checks over these hand-written sentences.`
    : run.label;
}

export const VERDICT_TONE: Record<GateVerdict, BadgeTone> = {
  pass: "success",
  mismatch: "danger",
  refused: "danger",
  skipped: "neutral",
};

function StepBadge({ step }: { step: number }) {
  return (
    <span className="inline-flex h-5 shrink-0 items-center rounded-md bg-surface-3 px-1.5 text-[0.6875rem] font-semibold text-muted tabular-nums">
      Step {step}
    </span>
  );
}

function Arrow() {
  return <ArrowRight className="size-3.5 shrink-0 text-muted" aria-hidden="true" />;
}

interface Props {
  active: boolean;
  loaded: LoadedRun | null;
  error: string | null;
  blownSeed: number | null;
  replayRequest: number;
}

export function NewsroomWall({ active, loaded, error, blownSeed, replayRequest }: Props) {
  const run = loaded?.run ?? null;
  const [step, setStep] = useState<Step>("all");
  const [replaying, setReplaying] = useState(false);
  const [announced, setAnnounced] = useState("");
  const reduceMotion = useReducedMotion();
  const hintId = useId();
  const last = run ? lastStep(run) : 0;
  const shown = step === "all" ? last : step;

  function advance(): void {
    if (!run) {
      return;
    }
    const next = step === "all" || step >= last ? 1 : step + 1;
    const state = stateAt(run, next);
    setStep(next);
    setAnnounced(state ? `Bundle state: ${stateWords(state)}.` : "");
  }

  function startReplay(): void {
    if (!run) {
      return;
    }
    setStep(1);
    setAnnounced("");
    setReplaying(!reduceMotion);
  }

  /* Blow full time on the Match Centre asks for a fresh replay. */
  useEffect(() => {
    if (replayRequest > 0 && run) {
      setStep(1);
      setAnnounced("");
      setReplaying(!reduceMotion);
    }
    /* Only a new request restarts the replay, so the run and the motion
       preference are read as they are at that moment. */
  }, [replayRequest]);

  useEffect(() => {
    if (!replaying || !active || !run) {
      return undefined;
    }
    if (step !== "all" && step >= last) {
      setReplaying(false);
      return undefined;
    }
    const timer = window.setTimeout(advance, STEP_MS);
    return () => window.clearTimeout(timer);
    /* advance is recreated on every render and reads the current step. */
  }, [replaying, active, step, run, last]);

  useEffect(() => {
    if (reduceMotion) {
      setReplaying(false);
    }
  }, [reduceMotion]);

  const current = run ? stateAt(run, shown) : "";
  const reached = current ? WALL_STATES.indexOf(current) : -1;
  const names: Record<string, string> = run ? deskNames(run) : {};
  const activeDesks = new Set(
    run && step !== "all" ? run.desks.filter((d) => d.lines.some((l) => l.step === step)).map((d) => d.desk) : [],
  );

  return (
    <View view="newsroom" active={active}>
      <ScreenHeader
        view="newsroom"
        eyebrow="Newsroom Wall"
        title="What the newsroom did at full time"
        lead="Each desk's work in plain words, the handoffs into one sealed bundle, and the verdict of every deterministic check, step by step."
      >
        {run ? (
          <LabelLine id="wall-label" tone={run.provenance === "design_preview" ? "preview" : "plain"}>
            {wallLabel(run)}
          </LabelLine>
        ) : (
          <LabelLine id="wall-label" tone="plain">
            The run could not be loaded, so this screen cannot say which run it would show.
          </LabelLine>
        )}
      </ScreenHeader>

      <SeedNotice blownSeed={blownSeed} run={run} />
      {error ? (
        <Notice tone="danger" className="mb-5">
          {error}
        </Notice>
      ) : null}

      {run ? (
        <>
          <p className="mb-4 text-sm text-muted">{matchLine(run)}</p>

          <Card aria-labelledby="h-state" className="mb-6">
            <CardBody className="flex flex-col gap-4 pt-5 md:flex-row md:items-center md:justify-between">
              <div className="flex items-center gap-3">
                <h2 id="h-state" className="text-sm font-medium text-muted">
                  Bundle state
                </h2>
                <Badge tone={current === "AWAITING_APPROVAL" ? "warning" : current ? "info" : "neutral"} className="text-sm">
                  {stateWords(current)}
                </Badge>
              </div>
              <ol className="flex flex-wrap items-center gap-1.5" aria-label="Bundle states">
                {WALL_STATES.map((name, i) => {
                  const done = i < reached;
                  const isCurrent = name === current;
                  return (
                    <li key={name} className="flex items-center gap-1.5" aria-current={isCurrent ? "step" : undefined}>
                      {i > 0 ? <span className={cn("h-px w-4 sm:w-6", done || isCurrent ? "bg-primary" : "bg-line-strong")} aria-hidden="true" /> : null}
                      <span
                        className={cn(
                          "rounded-full px-2.5 py-1 text-xs font-medium transition-colors",
                          isCurrent ? "bg-primary text-on-primary" : done ? "bg-accent-bg text-accent" : "border border-line text-muted",
                        )}
                      >
                        {stateWords(name)}
                        {done ? <span className="sr-only">, done</span> : null}
                      </span>
                    </li>
                  );
                })}
              </ol>
              <p className="sr-only" aria-live="polite" aria-atomic="true">
                {replaying || step !== "all" ? announced : ""}
              </p>
            </CardBody>
          </Card>

          <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center">
            <ButtonLink href={hrefFor("approve", run.run_id)} variant="primary">
              Open the approval card
              <ArrowRight aria-hidden="true" />
            </ButtonLink>
            <Button variant="secondary" onClick={startReplay} aria-describedby={hintId}>
              <RotateCcw aria-hidden="true" />
              {replaying ? "Replaying" : "Replay the run"}
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                setReplaying(false);
                advance();
              }}
              aria-describedby={hintId}
            >
              <StepForward aria-hidden="true" />
              Next step
            </Button>
            <p className="text-sm text-muted sm:ml-1">{step === "all" || step >= last ? `Showing every step, ${last} in all.` : `Showing step ${step} of ${last}.`}</p>
          </div>
          <p id={hintId} className="-mt-3 mb-6 text-xs text-muted">
            Replay walks through the run one step at a time. With reduced motion it waits for Next step instead of moving on its own.
          </p>

          <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
            <div className="min-w-0">
              <section aria-labelledby="h-desks" className="hidden md:block">
                <h2 id="h-desks" className="mb-3 text-sm font-semibold text-fg">
                  Desks
                </h2>
                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                  {run.desks.map((desk) => {
                    const lines = desk.lines.filter((l) => l.step <= shown);
                    const working = activeDesks.has(desk.desk);
                    return (
                      <section
                        key={desk.desk}
                        aria-label={desk.name}
                        className={cn(
                          "rounded-xl border bg-surface p-4 shadow-card transition-shadow",
                          working ? "border-primary ring-2 ring-primary/25" : "border-line",
                        )}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <h3 className="text-sm font-semibold text-fg">{desk.name}</h3>
                          {working ? <Badge tone="success">Working</Badge> : lines.length ? null : <Badge tone="neutral">Waiting</Badge>}
                        </div>
                        {lines.length ? (
                          <ol className="mt-2 space-y-2">
                            {lines.map((line, i) => (
                              <li key={`${line.step}-${i}`} className="flex items-start gap-2 text-sm leading-6 text-fg motion-safe:animate-rise">
                                <StepBadge step={line.step} />
                                <span className="min-w-0">{line.text}</span>
                              </li>
                            ))}
                          </ol>
                        ) : (
                          <p className="mt-2 text-sm text-muted">This desk has not started at this step of the replay.</p>
                        )}
                      </section>
                    );
                  })}
                </div>

                <h2 id="h-handoffs" className="mt-6 mb-3 text-sm font-semibold text-fg">
                  Handoffs into the bundle
                </h2>
                <ol className="space-y-2" aria-labelledby="h-handoffs">
                  {run.handoffs
                    .filter((h) => h.step <= shown)
                    .map((h, i) => {
                      const into = stateAt(run, h.step);
                      return (
                        <li key={`${h.step}-${i}`} className="rounded-lg border border-line bg-surface px-3 py-2.5 motion-safe:animate-rise">
                          <p className="flex flex-wrap items-center gap-2 text-sm font-medium text-fg">
                            <StepBadge step={h.step} />
                            <span>{names[h.from] ?? h.from}</span>
                            <Arrow />
                            <span className="sr-only"> to </span>
                            <span>{names[h.to] ?? h.to}</span>
                            {into ? <Badge tone="info">{stateWords(into)}</Badge> : null}
                          </p>
                          <p className="mt-1 text-sm text-muted">{h.text}</p>
                        </li>
                      );
                    })}
                </ol>
              </section>

              <section aria-labelledby="h-timeline" className="md:hidden">
                <h2 id="h-timeline" className="mb-3 text-sm font-semibold text-fg">
                  Timeline
                </h2>
                <ol className="relative space-y-3 border-l border-line pl-4" aria-labelledby="h-timeline">
                  {wallItems(run)
                    .filter((item) => item.step <= shown)
                    .map((item, i) => (
                      <li key={`${item.kind}-${item.step}-${i}`} className="relative motion-safe:animate-rise">
                        <span
                          className={cn(
                            "absolute top-1.5 -left-[1.3rem] size-2.5 rounded-full border-2 border-surface",
                            item.kind === "state" ? "bg-primary" : item.kind === "handoff" ? "bg-info" : "bg-line-strong",
                          )}
                          aria-hidden="true"
                        />
                        <div className="flex flex-wrap items-center gap-2">
                          <StepBadge step={item.step} />
                          {item.kind === "line" ? <strong className="text-sm text-fg">{item.deskName}</strong> : null}
                          {item.kind === "handoff" ? (
                            <span className="flex items-center gap-1.5 text-sm font-medium text-fg">
                              {item.from}
                              <Arrow />
                              <span className="sr-only"> to </span>
                              {item.to}
                            </span>
                          ) : null}
                          {item.kind === "state" ? <Badge tone="info">{item.text}</Badge> : null}
                        </div>
                        {item.kind !== "state" ? <p className="mt-1 text-sm leading-6 text-muted">{item.text}</p> : null}
                      </li>
                    ))}
                </ol>
              </section>
            </div>

            <aside aria-labelledby="h-gates" className="min-w-0">
              <Card aria-labelledby="h-gates">
                <CardHeader>
                  <CardTitle id="h-gates">Gate verdicts</CardTitle>
                </CardHeader>
                <CardBody>
                  <p className="text-xs leading-5 text-muted">
                    Each verdict comes from deterministic code, not from a model. Open a row to read the gate's own output.
                  </p>
                  <ul className="mt-3 space-y-3">
                    {run.gates.map((g) => (
                      <li key={g.gate} className="rounded-lg border border-line p-3">
                        <div className="flex items-start justify-between gap-2">
                          <p className="text-sm font-semibold text-fg">{g.name}</p>
                          <Badge tone={VERDICT_TONE[g.verdict]}>{VERDICT_WORDS[g.verdict]}</Badge>
                        </div>
                        <p className="mt-1 text-sm leading-6 text-muted">{g.detail}</p>
                        <details className="mt-1.5 text-xs">
                          <summary className="cursor-pointer text-link select-none">Gate output</summary>
                          <p className="mt-1 font-mono leading-5 break-words text-muted">{g.evidence}</p>
                        </details>
                      </li>
                    ))}
                  </ul>
                  <a href={hrefFor("recap", run.run_id)} className="mt-4 inline-flex items-center gap-1.5 text-sm font-medium">
                    Read the editions with a Why for every sentence
                    <ArrowRight className="size-4" aria-hidden="true" />
                  </a>
                </CardBody>
              </Card>
            </aside>
          </div>
        </>
      ) : null}

      <StepNav view="newsroom" param={run?.run_id} />
    </View>
  );
}
