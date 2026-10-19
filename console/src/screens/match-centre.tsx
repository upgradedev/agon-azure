/*
  S1 Match Centre: pick a seeded fictional match, replay it from its event log
  on the pitch, then blow full time.

  The match files load in the browser after the page is up (data/ first, then
  the committed fixture), so the prerendered page shows the loading state in
  words. Every sentence uses the fictional names from the seed's world file;
  raw ids appear only in the "Ids in the event log" row. Under reduced motion
  the ball jumps from event to event and nothing starts on its own.
*/
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Pause, Play, Whistle } from "lucide-react";
import { Card, CardBody, CardHeader, CardTitle } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Notice } from "../components/ui/notice";
import { Segmented } from "../components/ui/segmented";
import { Pitch } from "../components/pitch";
import { GlossaryTerm } from "../components/glossary-term";
import { GuideCard, type Progress } from "../components/guide";
import { ScreenHeader, View } from "../components/screen";
import {
  ballAt,
  buildTimeline,
  entryForSeed,
  eventIds,
  eventMinute,
  formatMinute,
  homeTeam,
  awayTeam,
  indexAt,
  keyMoments,
  matchClockAt,
  nameIndex,
  percent,
  pressureWindow,
  runningScore,
  scoreAt,
  toFixedFrame,
  unknownIds,
  type Moment,
  type NameIndex,
  type PressureSide,
  type RunningScore,
  type Timeline,
} from "../lib/core";
import { browserFetchText, loadMatch, resolveManifest, type ResolvedManifest } from "../lib/loader";
import { GLOSSARY, GLOSSARY_ORDER } from "../lib/glossary";
import { cn } from "../lib/cn";
import { useReducedMotion } from "../hooks/stores";
import type { ManifestEntry, MatchEvent, RunsData, World } from "../lib/types";

type Speed = "1" | "8" | "60";
type Mode = "analyst" | "casual";

const SPEEDS: Record<Speed, number> = { "1": 1, "8": 8, "60": 60 };
const BAND_WIDTH_M = 17;
const MAX_STEP_SECONDS = 0.25;

const TYPE_WORDS: Record<string, string> = {
  pass: "pass",
  carry: "carry",
  shot: "shot",
  tackle: "tackle",
  pressure: "pressure on the ball",
  foul: "foul",
  set_piece: "set piece",
  sub: "substitution",
  card: "card",
  possession_change: "possession change",
};

const MODE_HINTS: Record<Mode, string> = {
  analyst: "Analyst mode shows the pressure readout and the ids from the event log under the pitch.",
  casual: "Casual mode keeps to the story: the score, the clock and one plain sentence per event.",
};

interface Derived {
  home: string;
  away: string;
  names: NameIndex;
  timeline: Timeline;
  score: RunningScore;
  moments: Moment[];
}

type MatchLoad =
  | { status: "loading"; seed: number | null }
  | { status: "error"; seed: number; message: string }
  | {
      status: "ready";
      seed: number;
      entry: ManifestEntry;
      events: MatchEvent[];
      world: World | null;
      worldProblem: string;
      derived: Derived;
    };

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function words(value: string | null | undefined): string {
  return String(value ?? "").replace(/_/g, " ");
}

function derive(events: MatchEvent[], world: World | null): Derived {
  const home = homeTeam(events);
  const away = awayTeam(events, home);
  const names = nameIndex(world, home);
  const timeline = buildTimeline(events);
  return { home, away, names, timeline, score: runningScore(events, home), moments: keyMoments(events, timeline, names) };
}

function caption(event: MatchEvent, names: NameIndex): string {
  const who = names.club(event.team) + (event.player ? `, ${names.player(event.player)}` : "");
  const details: string[] = [];
  if (event.qualifier) {
    details.push(words(event.qualifier));
  }
  if (event.outcome && event.outcome !== "applied" && event.outcome !== "committed") {
    details.push(words(event.outcome));
  }
  if (event.under_pressure === true) {
    details.push("under pressure");
  }
  const what = TYPE_WORDS[event.type] ?? words(event.type);
  return `${eventMinute(event)} ${who}: ${what}${details.length ? `, ${details.join(", ")}` : ""}.`;
}

function side(name: string, part: PressureSide): string {
  if (!part.total) {
    return `${name} none on the ball yet`;
  }
  return `${name} ${percent(part)} (${part.under} of ${part.total})`;
}

const MOMENT_TONE: Record<Moment["kind"], string> = {
  goal: "bg-primary text-on-primary",
  shot: "border border-line-strong text-fg",
  card: "bg-notice-bg text-notice",
  sub: "bg-info-bg text-info",
};

const MOMENT_MARK: Record<Moment["kind"], string> = {
  goal: "fill-primary",
  shot: "fill-line-strong",
  card: "fill-notice",
  sub: "fill-info",
};

/* The full club name, shortened to its three-letter code on a phone; screen
   readers always hear the full name. */
function ClubName({ name, short, className }: { name: string; short: string | null; className?: string }) {
  if (!short) {
    return <span className={cn("truncate text-sm font-medium text-fg", className)}>{name}</span>;
  }
  return (
    <span className={cn("min-w-0 truncate text-sm font-medium text-fg", className)}>
      <span className="sm:hidden" aria-hidden="true">
        {short}
      </span>
      <span className="sr-only sm:not-sr-only">{name}</span>
    </span>
  );
}

interface Props {
  active: boolean;
  runs: RunsData | null;
  progress: Progress;
  onBlowFullTime: (seed: number) => void;
}

export function MatchCentre({ active, runs, progress, onBlowFullTime }: Props) {
  const [resolved, setResolved] = useState<ResolvedManifest | null>(null);
  const [manifestError, setManifestError] = useState<string | null>(null);
  const [seed, setSeed] = useState<number | null>(null);
  const [match, setMatch] = useState<MatchLoad>({ status: "loading", seed: null });
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<Speed>("8");
  const [mode, setMode] = useState<Mode>("analyst");
  const [scoreNews, setScoreNews] = useState("");
  const reduceMotion = useReducedMotion();
  const tRef = useRef(0);
  const scoreKey = useRef("");
  const ids = {
    playReason: useId(),
    scrubber: useId(),
    scrubberHint: useId(),
    seedHint: useId(),
    modeHint: useId(),
    liveReason: useId(),
    liveLabel: useId(),
    blowHint: useId(),
    momentsHint: useId(),
  };

  /* The manifest, once. */
  useEffect(() => {
    let cancelled = false;
    resolveManifest(browserFetchText)
      .then((value) => {
        if (cancelled) {
          return;
        }
        setResolved(value);
        const ordered = [...value.manifest.files].sort((a, b) => a.seed - b.seed);
        setSeed(ordered[0]?.seed ?? null);
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setManifestError(message(error));
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  /* The chosen seed's events and names. Nothing from the previous seed stays. */
  useEffect(() => {
    if (!resolved || seed === null) {
      return undefined;
    }
    const entry = entryForSeed(resolved.manifest, seed);
    if (!entry) {
      return undefined;
    }
    let cancelled = false;
    setPlaying(false);
    tRef.current = 0;
    setT(0);
    scoreKey.current = "";
    setScoreNews("");
    setMatch({ status: "loading", seed });
    loadMatch(browserFetchText, resolved, entry)
      .then((loaded) => {
        if (!cancelled) {
          setMatch({ status: "ready", seed, entry, ...loaded, derived: derive(loaded.events, loaded.world) });
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setMatch({ status: "error", seed, message: message(error) });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [resolved, seed]);

  /* Playback on animation frames; at most a quarter second per frame. */
  useEffect(() => {
    if (!playing || match.status !== "ready") {
      return undefined;
    }
    const end = match.derived.timeline.total - 1;
    let last = performance.now();
    let frame = 0;
    const step = (now: number) => {
      const dt = Math.min(MAX_STEP_SECONDS, Math.max(0, (now - last) / 1000));
      last = now;
      const next = tRef.current + dt * SPEEDS[speed];
      if (next >= end) {
        tRef.current = end;
        setT(end);
        setPlaying(false);
        return;
      }
      tRef.current = next;
      setT(next);
      frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [playing, speed, match]);

  useEffect(() => {
    if (!active) {
      setPlaying(false);
    }
  }, [active]);

  const ready = match.status === "ready" ? match : null;
  const view = useMemo(() => {
    if (!ready) {
      return null;
    }
    const { events, derived } = ready;
    const index = indexAt(derived.timeline.times, t);
    const clock = matchClockAt(t, derived.timeline);
    const minute = formatMinute(clock.period, clock.clock);
    const ball = ballAt(events, derived.timeline, t, derived.home, !reduceMotion);
    const event = index >= 0 ? events[index] : null;
    let path: [number, number, number, number] | null = null;
    let pressureLeft: number | null = null;
    if (event) {
      const p = toFixedFrame(event, derived.home);
      if (p[2] !== null && p[3] !== null) {
        path = [p[0] / 10, p[1] / 10, p[2] / 10, p[3] / 10];
      }
      if (event.under_pressure === true || event.type === "pressure") {
        pressureLeft = Math.min(105 - BAND_WIDTH_M, Math.max(0, p[0] / 10 - BAND_WIDTH_M / 2));
      }
    }
    const score = scoreAt(derived.score, index);
    const press = pressureWindow(events, derived.timeline, index, derived.home);
    return { index, clock, minute, ball: { x: ball.x / 10, y: ball.y / 10 }, event, path, pressureLeft, score, press };
  }, [ready, t, reduceMotion]);

  /* The score is announced only when it changes, never on the ticking clock,
     and not on the first render of a match. */
  useEffect(() => {
    if (!ready || !view) {
      return;
    }
    const key = `${ready.seed}:${view.score.home}:${view.score.away}`;
    if (key === scoreKey.current) {
      return;
    }
    const firstForSeed = scoreKey.current === "" || scoreKey.current.split(":")[0] !== String(ready.seed);
    scoreKey.current = key;
    const n = ready.derived.names;
    setScoreNews(
      firstForSeed
        ? ""
        : `Score: ${n.club(ready.derived.home)} ${view.score.home}, ${n.club(ready.derived.away)} ${view.score.away}.`,
    );
  }, [ready, view]);

  function seek(value: number): void {
    if (!ready) {
      return;
    }
    const clamped = Math.max(0, Math.min(value, ready.derived.timeline.total - 1));
    tRef.current = clamped;
    setT(clamped);
  }

  function togglePlay(): void {
    if (!ready) {
      return;
    }
    if (playing) {
      setPlaying(false);
      return;
    }
    if (tRef.current >= ready.derived.timeline.total - 1) {
      seek(0);
    }
    setPlaying(true);
  }

  const seeds = resolved ? [...resolved.manifest.files].sort((a, b) => a.seed - b.seed).map((f) => f.seed) : [];
  const runForSeed = runs && seed !== null ? Object.values(runs.runs).find((r) => r.run.seed === seed) ?? null : null;
  const defaultRun = runs ? runs.runs[runs.defaultRunId] ?? null : null;

  let blowHint = "Starts the newsroom run you follow on the next screens. Live agents are not connected, so it opens the recorded run, labelled on every screen.";
  if (seed !== null && runForSeed) {
    blowHint = `Opens the newsroom run for seed ${seed}. Live agents are not connected, so this is the recorded run, labelled on every screen.`;
  } else if (seed !== null && defaultRun) {
    blowHint = `There is no run for seed ${seed} yet, so the newsroom shows the hand-written sample for seed ${defaultRun.run.seed} and says so on every screen.`;
  }

  let playReason = "";
  if (manifestError) {
    playReason = "Play and the match time slider are unavailable because no match data could be loaded.";
  } else if (match.status === "error") {
    playReason = "Play and the match time slider are unavailable because the match file could not be read.";
  } else if (!ready) {
    playReason =
      match.seed === null
        ? "Play and the match time slider are unavailable until the match file has loaded."
        : `Play and the match time slider are unavailable while seed ${match.seed} loads.`;
  }

  const notices: string[] = [];
  if (resolved?.notice) {
    notices.push(resolved.notice);
  }
  if (ready) {
    if (ready.worldProblem) {
      notices.push(
        `The names file for this seed could not be used (${ready.worldProblem}), so clubs and players are called Home club, Away club and a player. Their ids are in the details row under the pitch.`,
      );
    } else {
      const unknown = unknownIds(ready.events, ready.world);
      if (unknown.length) {
        notices.push(
          `The names file does not name ${unknown.length} id${unknown.length === 1 ? "" : "s"} used in the event file: ${unknown.slice(0, 5).join(", ")}.`,
        );
      }
    }
    if (resolved?.source === "fixture") {
      notices.push(
        `Fixture data: this is the small file committed for development, not the deployed match files. It ends at ${eventMinute(ready.events[ready.events.length - 1])}.`,
      );
    }
    if (ready.events.length !== ready.entry.events) {
      notices.push(`This file holds ${ready.events.length} events but the manifest lists ${ready.entry.events}. The Verify page shows the details.`);
    }
  }

  const names = ready?.derived.names ?? null;
  const homeName = ready && names ? names.club(ready.derived.home) : "Home";
  const awayName = ready && names ? (ready.derived.away ? names.club(ready.derived.away) : "Away") : "Away";
  const homeKit = ready && names ? names.clubRecord(ready.derived.home) : null;
  const awayKit = ready && names ? names.clubRecord(ready.derived.away) : null;
  const total = ready ? Math.max(1, ready.derived.timeline.total - 1) : 1;

  let dataChip = <Badge tone="neutral">Loading</Badge>;
  if (manifestError) {
    dataChip = <Badge tone="danger">Unavailable</Badge>;
  } else if (resolved) {
    dataChip = resolved.source === "fixture" ? <Badge tone="warning">Fixture</Badge> : <Badge tone="success">Synthetic data</Badge>;
  }

  let matchLabel = "Loading the synthetic match data.";
  if (manifestError) {
    matchLabel = "No synthetic match could be loaded.";
  } else if (match.status === "error") {
    matchLabel = `Seed ${match.seed} could not be loaded.`;
  } else if (match.status === "loading" && match.seed !== null) {
    matchLabel = `Loading seed ${match.seed}.`;
  } else if (ready && resolved) {
    const league = names?.league ? `${names.league}, ` : "";
    matchLabel = `Synthetic match, ${league}generator ${resolved.manifest.generator}, seed ${ready.seed}. Fictional clubs and players.`;
  }

  const pitchSummary = view?.event
    ? `${caption(view.event, ready!.derived.names)} The home club attacks to the right in both halves.`
    : "A pitch drawn to scale. The home club attacks to the right in both halves. No event is shown yet.";

  const blowButton = (className: string) => (
    <Button
      size="lg"
      className={className}
      aria-describedby={ids.blowHint}
      onClick={() => {
        if (seed !== null) {
          setPlaying(false);
          onBlowFullTime(seed);
        } else {
          onBlowFullTime(defaultRun?.run.seed ?? 1);
        }
      }}
    >
      <Whistle aria-hidden="true" />
      Blow full time
    </Button>
  );

  return (
    <View view="home" active={active}>
      <ScreenHeader
        view="home"
        eyebrow="Match Centre"
        title="Replay a fictional match, then blow the whistle"
        lead="Pick one of the seeded fictional matches and watch it replay from its event log; Blow full time opens the newsroom run you follow on the next four screens."
      />
      <GuideCard progress={progress} />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <Card aria-labelledby="h-replay" data-state={ready ? "ready" : match.status} className="min-w-0">
          <CardHeader>
            <CardTitle id="h-replay">Replay</CardTitle>
            {dataChip}
          </CardHeader>
          <CardBody className="space-y-4">
            <p className="text-sm text-muted">{matchLabel}</p>
            {manifestError ? <Notice tone="danger">{manifestError}</Notice> : null}
            {match.status === "error" ? <Notice tone="danger">The match file could not be read: {match.message}</Notice> : null}
            {notices.length ? <Notice tone="warning">{notices.join(" ")}</Notice> : null}

            <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-3 rounded-lg bg-surface-2 px-3 py-2.5">
              <p className="flex min-w-0 items-center gap-2">
                <svg viewBox="0 0 12 12" className="size-3 shrink-0" aria-hidden="true" focusable="false">
                  {homeKit ? <circle cx="6" cy="6" r="4.5" fill={homeKit.kit_primary_hex} stroke={homeKit.kit_secondary_hex} strokeWidth="1.5" /> : null}
                </svg>
                <ClubName name={homeName} short={homeKit?.short ?? null} />
                <span className="ml-auto text-2xl font-semibold tabular-nums text-fg">{view ? view.score.home : 0}</span>
              </p>
              <p className="flex flex-col items-center px-2 text-center">
                <span className="text-base font-semibold tabular-nums text-fg">{view ? view.minute : "1'"}</span>
                <span className="text-[0.6875rem] tracking-wide text-muted uppercase">
                  {view ? (view.clock.period === 1 ? "1st half" : "2nd half") : "1st half"}
                </span>
              </p>
              <p className="flex min-w-0 items-center gap-2">
                <span className="text-2xl font-semibold tabular-nums text-fg">{view ? view.score.away : 0}</span>
                <ClubName name={awayName} short={awayKit?.short ?? null} className="ml-auto text-right" />
                <svg viewBox="0 0 12 12" className="size-3 shrink-0" aria-hidden="true" focusable="false">
                  {awayKit ? <circle cx="6" cy="6" r="4.5" fill={awayKit.kit_primary_hex} stroke={awayKit.kit_secondary_hex} strokeWidth="1.5" /> : null}
                </svg>
              </p>
            </div>
            <p className="sr-only" aria-live="polite" aria-atomic="true">
              {scoreNews}
            </p>

            <Pitch ball={view ? view.ball : null} path={view ? view.path : null} pressureLeft={view ? view.pressureLeft : null} summary={pitchSummary} />

            <p className="min-h-6 text-sm leading-6 text-fg">
              {view?.event && ready ? caption(view.event, ready.derived.names) : ready ? "Kick-off has not happened yet." : "No match is loaded yet."}
            </p>
            {mode === "analyst" ? (
              <details className="group rounded-lg border border-line px-3 py-2 text-sm">
                <summary className="cursor-pointer text-muted select-none group-open:text-fg">Ids in the event log</summary>
                <p className="mt-1.5 font-mono text-xs text-muted">{view?.event ? `${eventIds(view.event)}.` : "No event is shown yet."}</p>
              </details>
            ) : null}

            <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted" aria-label="Pitch legend">
              <li className="flex items-center gap-1.5">
                <span className="inline-block size-2.5 rounded-sm bg-pressure" aria-hidden="true" />
                Under pressure
              </li>
              <li className="flex items-center gap-1.5">
                <span className="inline-block size-2.5 rounded-full border border-ball-edge bg-ball" aria-hidden="true" />
                Ball
              </li>
              <li>{ready ? `${homeName} (home) attack to the right in both halves.` : "The home club attacks to the right in both halves."}</li>
            </ul>

            <div className="space-y-3 rounded-lg border border-line p-3">
              <div className="flex flex-wrap items-center gap-3">
                <Button
                  onClick={togglePlay}
                  aria-disabled={ready ? undefined : true}
                  aria-describedby={ready ? undefined : ids.playReason}
                  className="min-w-28"
                >
                  {playing ? <Pause aria-hidden="true" /> : <Play aria-hidden="true" />}
                  {playing ? "Pause" : "Play"}
                </Button>
                <Segmented<Speed>
                  name="replay-speed"
                  legend="Replay speed"
                  legendHidden
                  value={speed}
                  onChange={setSpeed}
                  options={[
                    { value: "1", label: "1x" },
                    { value: "8", label: "8x" },
                    { value: "60", label: "60x" },
                  ]}
                />
              </div>
              {playReason ? (
                <p id={ids.playReason} className="text-sm text-muted">
                  {playReason}
                </p>
              ) : null}
              <div>
                <label htmlFor={ids.scrubber} className="text-sm font-medium text-fg">
                  Match time
                </label>
                <svg viewBox="0 0 1000 12" preserveAspectRatio="none" className="mt-1 block h-3 w-full" aria-hidden="true" focusable="false">
                  {ready?.derived.moments.map((m) => {
                    const height = m.kind === "goal" ? 12 : m.kind === "card" ? 9 : 6;
                    return (
                      <rect
                        key={`${m.kind}-${m.index}`}
                        x={Math.min(996, (m.time / total) * 1000).toFixed(1)}
                        y={12 - height}
                        width="4"
                        height={height}
                        className={MOMENT_MARK[m.kind]}
                      />
                    );
                  })}
                </svg>
                <input
                  id={ids.scrubber}
                  type="range"
                  min={0}
                  max={total}
                  step={1}
                  value={Math.floor(t)}
                  disabled={!ready}
                  onChange={(event) => seek(Number(event.target.value))}
                  aria-valuetext={view ? `${view.minute}, ${view.clock.period === 1 ? "1st half" : "2nd half"}` : undefined}
                  aria-describedby={ready ? ids.scrubberHint : ids.playReason}
                  className="mt-1 w-full accent-primary"
                />
                <p id={ids.scrubberHint} className="mt-1 text-xs text-muted">
                  Arrow keys move the replay one second at a time; Page Up and Page Down jump further. The marks above the slider are the key
                  moments.
                </p>
              </div>
            </div>

            {mode === "analyst" ? (
              <p className="text-sm text-muted">
                {view && ready && names
                  ? `Passes, carries and shots made under pressure, last 5 minutes of play: ${side(names.club(ready.derived.home), view.press.home)}; ${side(
                      names.club(ready.derived.away),
                      view.press.away,
                    )}.`
                  : "Pressure figures appear once a match is loaded."}
              </p>
            ) : null}
          </CardBody>
        </Card>

        <div className="flex min-w-0 flex-col gap-6">
          <Card aria-labelledby="h-setup">
            <CardHeader>
              <CardTitle id="h-setup">Match setup</CardTitle>
            </CardHeader>
            <CardBody className="space-y-5">
              {seeds.length ? (
                <div>
                  <Segmented<string>
                    name="seed"
                    legend="Seed"
                    value={seed === null ? null : String(seed)}
                    onChange={(value) => setSeed(Number(value))}
                    describedBy={ids.seedHint}
                    options={seeds.map((s) => ({ value: String(s), label: `Seed ${s}` }))}
                  />
                  <p id={ids.seedHint} className="mt-1.5 text-xs leading-5 text-muted">
                    Each seed is a different fictional match from the synthetic generator.
                    {resolved?.source === "fixture"
                      ? ` The committed fixture holds seed ${seeds.join(", ")} only; the other seeds arrive with the deployed match files.`
                      : ""}
                  </p>
                </div>
              ) : (
                <Notice tone={manifestError ? "danger" : "loading"}>
                  {manifestError
                    ? "The list of seeded matches could not be loaded, so there is no match to pick."
                    : "Loading the list of seeded matches from the match data."}
                </Notice>
              )}

              <div className="hidden lg:block">
                {blowButton("w-full")}
                <p id={ids.blowHint} className="mt-2 text-xs leading-5 text-muted">
                  {blowHint}
                </p>
              </div>

              <div>
                <Segmented<Mode>
                  name="mode"
                  legend="Mode"
                  value={mode}
                  onChange={setMode}
                  describedBy={ids.modeHint}
                  options={[
                    { value: "analyst", label: "Analyst" },
                    { value: "casual", label: "Casual" },
                  ]}
                />
                <p id={ids.modeHint} className="mt-1.5 text-xs leading-5 text-muted" aria-live="polite">
                  {MODE_HINTS[mode]}
                </p>
              </div>

              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <p id={ids.liveLabel} className="text-sm font-medium text-fg">
                    Run it live
                  </p>
                  <p id={ids.liveReason} className="mt-0.5 text-xs leading-5 text-muted">
                    Live runs are off: the full-time fleet is not connected to this public page, so Blow full time opens the recorded run instead.
                  </p>
                </div>
                <button
                  type="button"
                  role="switch"
                  aria-checked="false"
                  aria-disabled="true"
                  aria-labelledby={ids.liveLabel}
                  aria-describedby={ids.liveReason}
                  className="relative mt-0.5 inline-flex h-6 w-11 shrink-0 cursor-not-allowed items-center rounded-full border border-line-strong bg-surface-3 p-0.5 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
                >
                  <span className="size-4 rounded-full bg-line-strong" aria-hidden="true" />
                </button>
              </div>
            </CardBody>
          </Card>

          <Card aria-labelledby="h-moments">
            <CardHeader>
              <CardTitle id="h-moments">Key moments</CardTitle>
              <Badge tone="neutral">{ready ? ready.derived.moments.length : 0}</Badge>
            </CardHeader>
            <CardBody>
              <p id={ids.momentsHint} className="text-xs leading-5 text-muted">
                Goals, shots, cards and substitutions from the event log. Choose one to move the replay there; moments still to come are dimmed.
              </p>
              {ready && ready.derived.moments.length ? (
                <ol className="mt-3 max-h-80 space-y-1 overflow-y-auto pr-1" aria-labelledby="h-moments" aria-describedby={ids.momentsHint}>
                  {ready.derived.moments.map((m) => {
                    const future = view ? m.index > view.index : true;
                    return (
                      <li key={`${m.kind}-${m.index}`}>
                        <button
                          type="button"
                          onClick={() => seek(m.time)}
                          className={cn(
                            "flex w-full items-start gap-2.5 rounded-lg px-2 py-1.5 text-left text-sm transition-colors hover:bg-surface-2",
                            "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus",
                            future ? "text-muted" : "text-fg",
                          )}
                        >
                          <span className={cn("mt-0.5 inline-flex min-w-11 justify-center rounded-md px-1.5 text-xs leading-5 font-semibold tabular-nums", MOMENT_TONE[m.kind])}>
                            {m.minute}
                          </span>
                          <span className="min-w-0">{m.text}</span>
                        </button>
                      </li>
                    );
                  })}
                </ol>
              ) : (
                <p className="mt-3 text-sm text-muted">
                  {ready ? "No goals, shots, cards or substitutions are in the loaded event file yet." : "Key moments appear once a match is loaded."}
                </p>
              )}
            </CardBody>
          </Card>
        </div>
      </div>

      <Card aria-labelledby="h-reads" className="mt-6">
        <CardHeader>
          <CardTitle id="h-reads">What the newsroom reads</CardTitle>
        </CardHeader>
        <CardBody>
          <p className="text-sm leading-6 text-muted">
            Before anyone writes, plain code turns the event log into a few numbers the writers must cite instead of guess. Choose a term to see
            what it means.
          </p>
          <ul className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
            {GLOSSARY_ORDER.map((id) => (
              <li key={id} className="rounded-lg border border-line bg-surface-2 px-3 py-2.5">
                <GlossaryTerm id={id} className="text-sm" />
                <p className="mt-0.5 text-xs text-muted">{GLOSSARY[id].short}</p>
              </li>
            ))}
          </ul>
        </CardBody>
      </Card>

      <div className="fixed inset-x-0 bottom-0 z-30 border-t border-line bg-surface/95 px-4 py-3 shadow-raised backdrop-blur lg:hidden">
        <div className="mx-auto flex max-w-7xl items-center gap-3">
          {blowButton("flex-1")}
        </div>
        <p className="mx-auto mt-1.5 max-w-7xl text-[0.6875rem] leading-4 text-muted">{blowHint}</p>
      </div>
    </View>
  );
}
