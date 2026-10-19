/*
  The match charts, hand-drawn SVG: win probability with the turning points,
  the xG race, momentum with the 5-minute control and pressure bands, the shot
  map and the territory grid. Every value is read from the match analysis
  (agon.webmatch, computed by agon.interpret); the charts only scale and draw.
  Positions are SVG attributes, never inline styles, so the strict CSP holds.
  Time charts follow the replay: what is still to come is shaded.
*/
import type { ReactNode } from "react";
import { cn } from "../../lib/cn";
import {
  INTERPRET_DOC_URL,
  REASON_WORDS,
  SIDE_WORD,
  probability,
  windowWords,
  xg,
  type MatchFile,
  type Shot,
  type Side,
  type WinPoint,
} from "../../lib/match";
import { SIDE_FILL, SIDE_STROKE, SIDE_TEXT, SideTriangle } from "./marks";

export function SourceNote() {
  return (
    <>
      Computed by agon.interpret from the synthetic event log, a self-measured model (
      <a href={INTERPRET_DOC_URL} rel="noopener noreferrer">
        its limits
      </a>
      ).
    </>
  );
}

export function ChartFrame({
  title,
  sentence,
  label,
  children,
  footer,
  className,
}: {
  title: string;
  sentence: string;
  label: string;
  children: ReactNode;
  footer?: ReactNode;
  className?: string;
}) {
  return (
    <figure className={cn("min-w-0 rounded-xl border border-line bg-surface p-4 shadow-card", className)} aria-label={label}>
      <h3 className="text-sm font-semibold text-fg">{title}</h3>
      <p className="mt-1 text-xs leading-5 text-muted">
        {sentence} <SourceNote />
      </p>
      <div className="mt-3">{children}</div>
      {footer ? <div className="mt-3">{footer}</div> : null}
    </figure>
  );
}

export function Swatch({ className }: { className: string }) {
  return (
    <svg viewBox="0 0 10 10" className="size-2.5 shrink-0" aria-hidden="true" focusable="false">
      <rect x="0.5" y="0.5" width="9" height="9" rx="2" className={className} />
    </svg>
  );
}

/* ------------------------------------------------------------- time axis */

interface Frame {
  W: number;
  H: number;
  L: number;
  R: number;
  T: number;
  B: number;
}

function tx(file: MatchFile, f: Frame, s: number): number {
  const end = Math.max(1, file.axis.end_s);
  return f.L + (Math.min(Math.max(s, 0), end) / end) * (f.R - f.L);
}

function TimeAxis({ file, f }: { file: MatchFile; f: Frame }) {
  const ticks = [0, 15, 30, 45, 60, 75, 90].map((m) => ({ m, s: m * 60 + (m > 45 ? file.axis.half_offset_s : 0) }));
  return (
    <g>
      <line x1={f.L} y1={f.B} x2={f.R} y2={f.B} className="stroke-line-strong" />
      {ticks.map((t) => (
        <g key={t.m}>
          <line x1={tx(file, f, t.s)} y1={f.B} x2={tx(file, f, t.s)} y2={f.B + 4} className="stroke-line-strong" />
          <text x={tx(file, f, t.s)} y={f.B + 15} textAnchor="middle" className="fill-muted text-[10px]">
            {t.m}&apos;
          </text>
        </g>
      ))}
      <line x1={tx(file, f, file.axis.half_time_s)} y1={f.T} x2={tx(file, f, file.axis.half_time_s)} y2={f.B} className="stroke-line-strong" strokeDasharray="3 3" />
    </g>
  );
}

function NowShade({ file, f, nowS }: { file: MatchFile; f: Frame; nowS: number | null }) {
  if (nowS === null || nowS >= file.axis.end_s) {
    return null;
  }
  const x = tx(file, f, nowS);
  return (
    <g>
      <rect x={x} y={f.T} width={Math.max(0, f.R - x)} height={f.B - f.T} className="fill-surface opacity-70" />
      <line x1={x} y1={f.T - 4} x2={x} y2={f.B} className="stroke-fg" strokeWidth="1.5" />
    </g>
  );
}

export function winAt(file: MatchFile, nowS: number | null): WinPoint | null {
  if (nowS === null) {
    return null;
  }
  let found: WinPoint | null = null;
  for (const p of file.win_probability) {
    if (p.at_s <= nowS) {
      found = p;
    }
  }
  return found;
}

/* ------------------------------------------------------------- win probability */

export function possessive(name: string): string {
  return name.endsWith("s") ? `${name}'` : `${name}'s`;
}

/* Turning point markers keep at least `gap` apart; a leader line points to the real minute. */
function spread(xs: number[], gap: number, right: number): number[] {
  const out: number[] = [];
  for (const x of xs) {
    const prev = out.length ? out[out.length - 1] : -Infinity;
    out.push(Math.min(right, Math.max(x, prev + gap)));
  }
  for (let i = out.length - 2; i >= 0; i -= 1) {
    out[i] = Math.min(out[i], out[i + 1] - gap);
  }
  return out;
}

export function WinProbabilityChart({ file, nowS, names, compact = false }: { file: MatchFile; nowS: number | null; names: Record<Side, string>; compact?: boolean }) {
  const f: Frame = { W: 480, H: 232, L: 34, R: 470, T: 26, B: 200 };
  const y = (ppm: number) => f.B - (ppm / 1_000_000) * (f.B - f.T);
  const pts = file.win_probability;
  const xs = pts.map((p) => tx(file, f, p.at_s).toFixed(1));
  const line = (vals: number[]) => vals.map((v, i) => `${xs[i]} ${y(v).toFixed(1)}`).join(" L ");
  const home = pts.map((p) => p.home_ppm);
  const homeDraw = pts.map((p) => p.home_ppm + p.draw_ppm);
  const back = (vals: number[]) => vals.map((v, i) => `${xs[i]} ${y(v).toFixed(1)}`).reverse().join(" L ");
  const homeArea = `M ${xs[0]} ${f.B} L ${line(home)} L ${xs[xs.length - 1]} ${f.B} Z`;
  const drawArea = `M ${line(homeDraw)} L ${back(home)} Z`;
  const awayArea = `M ${xs[0]} ${f.T} L ${xs[xs.length - 1]} ${f.T} L ${back(homeDraw)} Z`;
  const now = winAt(file, nowS);
  const tps = file.turning_points;
  const marks = spread(tps.map((t) => tx(file, f, t.at_s)), 18, f.R);
  return (
    <ChartFrame
      title="Win probability"
      sentence="Each club's chance to win, and the chance of a draw, minute by minute; the numbered marks are the turning points."
      label="Win probability chart"
      footer={
        <div className="space-y-2 text-xs">
          <ul className="flex flex-wrap gap-x-4 gap-y-1 text-muted">
            <li className="flex items-center gap-1.5">
              <Swatch className={SIDE_FILL.home} />
              {names.home} win (home)
            </li>
            <li className="flex items-center gap-1.5">
              <Swatch className="fill-draw" />
              Draw
            </li>
            <li className="flex items-center gap-1.5">
              <Swatch className={SIDE_FILL.away} />
              {names.away} win (away)
            </li>
          </ul>
          <p className="font-medium text-fg" aria-live="off">
            {now
              ? `At ${now.minute === "FT" ? "full time" : `${now.minute}'`}: ${names.home} ${probability(now.home_ppm)}, draw ${probability(now.draw_ppm)}, ${names.away} ${probability(now.away_ppm)}.`
              : "Before kick-off the model has not seen an event yet."}
          </p>
          {!compact && tps.length ? (
            <ol className="space-y-1 text-muted">
              {tps.map((t, i) => (
                <li key={t.event_id} className="flex gap-2">
                  <span className="inline-flex size-4 shrink-0 items-center justify-center rounded-full border border-fg text-[0.625rem] font-bold text-fg">{i + 1}</span>
                  <span>
                    {t.minute}&apos; {REASON_WORDS[t.reason] ?? t.reason}, {names[t.side]} ({SIDE_WORD[t.side].toLowerCase()})
                    {t.name ? `, ${t.name}` : ""}:{" "}
                    {t.delta_home_ppm === 0
                      ? `an expected swing of ${Math.round(t.weight_ppm / 10_000)} points of win probability (its xG times what a goal would have changed)`
                      : `${possessive(names.home)} win chance ${t.delta_home_ppm > 0 ? "up" : "down"} ${Math.abs(Math.round(t.delta_home_ppm / 10_000))} points`}
                    .
                  </span>
                </li>
              ))}
            </ol>
          ) : null}
        </div>
      }
    >
      <svg viewBox={`0 0 ${f.W} ${f.H}`} className="block h-auto w-full" role="img" aria-label={`Win probability from kick-off to full time for ${names.home} and ${names.away}.`}>
        {[0.25, 0.5, 0.75].map((v) => (
          <line key={v} x1={f.L} y1={y(v * 1_000_000)} x2={f.R} y2={y(v * 1_000_000)} className="stroke-line" />
        ))}
        <path d={homeArea} className={cn(SIDE_FILL.home, "opacity-85")} />
        <path d={drawArea} className="fill-draw opacity-35" />
        <path d={awayArea} className={cn(SIDE_FILL.away, "opacity-85")} />
        <path d={`M ${line(home)}`} className="fill-none stroke-surface" strokeWidth="1.2" />
        <path d={`M ${line(homeDraw)}`} className="fill-none stroke-surface" strokeWidth="1.2" />
        {[0, 50, 100].map((v) => (
          <text key={v} x={f.L - 6} y={y(v * 10_000) + 3} textAnchor="end" className="fill-muted text-[10px]">
            {v}%
          </text>
        ))}
        <TimeAxis file={file} f={f} />
        {!compact
          ? tps.map((t, i) => {
              const x = tx(file, f, t.at_s);
              const m = marks[i];
              return (
                <g key={t.event_id}>
                  <line x1={x} y1={f.T} x2={x} y2={f.B} className="stroke-fg opacity-50" strokeDasharray="2 2" />
                  <line x1={m} y1={f.T - 6} x2={x} y2={f.T} className="stroke-fg opacity-50" />
                  <circle cx={m} cy={f.T - 14} r="8" className="fill-surface stroke-fg" strokeWidth="1.5" />
                  <text x={m} y={f.T - 10.5} textAnchor="middle" className="fill-fg text-[10px] font-bold">
                    {i + 1}
                  </text>
                </g>
              );
            })
          : null}
        <NowShade file={file} f={f} nowS={nowS} />
      </svg>
    </ChartFrame>
  );
}

/* ------------------------------------------------------------- xG race */

export function XgRaceChart({ file, nowS, names, focusPlayer }: { file: MatchFile; nowS: number | null; names: Record<Side, string>; focusPlayer: string | null }) {
  const f: Frame = { W: 480, H: 220, L: 34, R: 470, T: 12, B: 188 };
  const totals = { home: 0, away: 0 };
  for (const s of file.shots) {
    totals[s.side] = Math.max(totals[s.side], s.cum_xg_ppm);
  }
  const top = Math.max(500_000, Math.ceil(Math.max(totals.home, totals.away) / 500_000) * 500_000);
  const y = (ppm: number) => f.B - (ppm / top) * (f.B - f.T);
  const step = (side: Side) => {
    let d = `M ${f.L} ${f.B}`;
    let last = 0;
    for (const s of file.shots.filter((x) => x.side === side)) {
      const x = tx(file, f, s.at_s).toFixed(1);
      d += ` H ${x} V ${y(s.cum_xg_ppm).toFixed(1)}`;
      last = s.cum_xg_ppm;
    }
    return `${d} H ${f.R} V ${y(last).toFixed(1)}`;
  };
  const ticks: number[] = [];
  for (let v = 0; v <= top; v += 500_000) {
    ticks.push(v);
  }
  return (
    <ChartFrame
      title="xG race"
      sentence="Expected goals added up shot by shot for each club; a filled ball is a goal, a ring is a big chance."
      label="xG race chart"
      footer={
        <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
          {(["home", "away"] as Side[]).map((side) => (
            <li key={side} className={cn("flex items-center gap-1.5 font-medium", SIDE_TEXT[side])}>
              <SideTriangle side={side} />
              {names[side]} {xg(totals[side])} xG from {file.shots.filter((s) => s.side === side).length} shots
            </li>
          ))}
        </ul>
      }
    >
      <svg viewBox={`0 0 ${f.W} ${f.H}`} className="block h-auto w-full" role="img" aria-label={`xG race: ${names.home} ${xg(totals.home)}, ${names.away} ${xg(totals.away)}.`}>
        {ticks.map((v) => (
          <g key={v}>
            <line x1={f.L} y1={y(v)} x2={f.R} y2={y(v)} className="stroke-line" />
            <text x={f.L - 6} y={y(v) + 3} textAnchor="end" className="fill-muted text-[10px]">
              {xg(v).replace(/0$/, "")}
            </text>
          </g>
        ))}
        <TimeAxis file={file} f={f} />
        {(["home", "away"] as Side[]).map((side) => (
          <path
            key={side}
            d={step(side)}
            pathLength="1"
            className={cn("fill-none [stroke-dasharray:1] motion-safe:animate-draw", SIDE_STROKE[side])}
            strokeWidth="2.5"
            strokeLinejoin="round"
          />
        ))}
        {file.shots
          .filter((s) => s.outcome === "goal" || s.big_chance)
          .map((s) => {
            const dim = focusPlayer !== null && s.player !== focusPlayer;
            return (
              <g key={s.event_id} className={dim ? "opacity-30" : undefined}>
                {s.outcome === "goal" ? (
                  <circle cx={tx(file, f, s.at_s)} cy={y(s.cum_xg_ppm)} r="6" className={cn(SIDE_FILL[s.side], "stroke-surface")} strokeWidth="2" />
                ) : (
                  <circle cx={tx(file, f, s.at_s)} cy={y(s.cum_xg_ppm)} r="5" className={cn("fill-surface", SIDE_STROKE[s.side])} strokeWidth="2" />
                )}
              </g>
            );
          })}
        <NowShade file={file} f={f} nowS={nowS} />
      </svg>
    </ChartFrame>
  );
}

/* ------------------------------------------------------------- momentum and bands */

const BAND_OPACITY: Record<string, number> = { low: 0.2, medium: 0.5, high: 0.9, control: 0.18, contested: 0.45, chaos: 0.85, slow: 0.2, normal: 0.5, fast: 0.9 };

function Strip({ file, label, side, field, nowS }: { file: MatchFile; label: string; side: Side | null; field: "control_band" | "home_pressure_band" | "away_pressure_band"; nowS: number | null }) {
  const f: Frame = { W: 480, H: 14, L: 34, R: 470, T: 0, B: 14 };
  return (
    <div>
      <p className={cn("mb-1 flex items-center gap-1 text-[0.6875rem] font-medium", side ? SIDE_TEXT[side] : "text-muted")}>
        {side ? <SideTriangle side={side} /> : null}
        {label}
      </p>
      <svg viewBox={`0 0 ${f.W} ${f.H}`} className="block h-3.5 w-full" preserveAspectRatio="none" aria-hidden="true" focusable="false">
        <rect x={f.L} y="0" width={f.R - f.L} height={f.H} className="fill-surface-3" />
        {file.bands.map((b) => {
          const band = b[field];
          return band ? (
            <rect
              key={b.window}
              x={tx(file, f, b.from_s)}
              y="1"
              width={Math.max(1, tx(file, f, b.to_s) - tx(file, f, b.from_s) - 1)}
              height={f.H - 2}
              fillOpacity={BAND_OPACITY[band] ?? 0.4}
              className={side ? SIDE_FILL[side] : "fill-fg"}
            />
          ) : null;
        })}
        {nowS !== null && nowS < file.axis.end_s ? <rect x={tx(file, f, nowS)} y="0" width={Math.max(0, f.R - tx(file, f, nowS))} height={f.H} className="fill-surface opacity-70" /> : null}
      </svg>
    </div>
  );
}

export function MomentumChart({ file, nowS, names }: { file: MatchFile; nowS: number | null; names: Record<Side, string> }) {
  const f: Frame = { W: 480, H: 190, L: 34, R: 470, T: 10, B: 160 };
  const mid = (f.T + f.B) / 2;
  const peak = Math.max(1, ...file.momentum.map((m) => Math.abs(m.momentum_ppm)));
  const barW = Math.max(1, ((f.R - f.L) * 60) / Math.max(1, file.axis.end_s) - 0.6);
  const chaos = file.bands.filter((b) => b.control_band === "chaos").map((b) => windowWords(b.window));
  return (
    <ChartFrame
      title="Momentum, pressure and control"
      sentence="Bars above the line lean to the home club, below to the away club (threat from xG and xT, decaying minute by minute); the strips show each club's pressing and how settled play was, per 5 minutes."
      label="Momentum, pressure and control chart"
      footer={
        <div className="space-y-2">
          <Strip file={file} label="Control vs chaos (darker is more chaotic)" side={null} field="control_band" nowS={nowS} />
          <Strip file={file} label={`${names.home} pressing (darker is higher)`} side="home" field="home_pressure_band" nowS={nowS} />
          <Strip file={file} label={`${names.away} pressing (darker is higher)`} side="away" field="away_pressure_band" nowS={nowS} />
          <p className="text-xs text-muted">{chaos.length ? `Most chaotic spells: ${chaos.slice(0, 3).join("; ")}.` : "No 5-minute window reached the chaos band."}</p>
        </div>
      }
    >
      <div className="mb-1 flex items-center justify-between text-[0.6875rem] font-medium">
        <span className={cn("flex items-center gap-1", SIDE_TEXT.home)}>
          <SideTriangle side="home" />
          Above the line: {names.home}
        </span>
        <span className={cn("flex items-center gap-1", SIDE_TEXT.away)}>
          Below: {names.away}
          <SideTriangle side="away" />
        </span>
      </div>
      <svg viewBox={`0 0 ${f.W} ${f.H}`} className="block h-auto w-full" role="img" aria-label={`Momentum per minute between ${names.home} and ${names.away}.`}>
        {file.momentum.map((m) => {
          const h = (Math.abs(m.momentum_ppm) / peak) * (mid - f.T - 2);
          const x = tx(file, f, m.at_s);
          const side: Side = m.momentum_ppm >= 0 ? "home" : "away";
          return <rect key={`${m.period}-${m.minute}`} x={x} y={side === "home" ? mid - h : mid} width={barW} height={Math.max(0.5, h)} className={SIDE_FILL[side]} />;
        })}
        <line x1={f.L} y1={mid} x2={f.R} y2={mid} className="stroke-fg" />
        <TimeAxis file={file} f={f} />
        <NowShade file={file} f={f} nowS={nowS} />
      </svg>
    </ChartFrame>
  );
}

/* ------------------------------------------------------------- pitch based */

export function PitchLines() {
  return (
    <g className="fill-none stroke-line-strong" strokeWidth="0.35">
      <rect x="0" y="0" width="105" height="68" />
      <line x1="52.5" y1="0" x2="52.5" y2="68" />
      <circle cx="52.5" cy="34" r="9.15" />
      <rect x="0" y="13.84" width="16.5" height="40.32" />
      <rect x="88.5" y="13.84" width="16.5" height="40.32" />
      <rect x="0" y="24.84" width="5.5" height="18.32" />
      <rect x="99.5" y="24.84" width="5.5" height="18.32" />
      <rect x="-1.8" y="30.34" width="1.8" height="7.32" />
      <rect x="105" y="30.34" width="1.8" height="7.32" />
    </g>
  );
}

function shotRadius(s: Shot): number {
  return 0.9 + Math.sqrt(s.xg_ppm / 1_000_000) * 4.4;
}

const OUTCOME_WORDS: Record<Shot["outcome"], string> = { goal: "goal", saved: "saved", off_target: "off target", blocked: "blocked" };

export function ShotMap({ file, names, focusPlayer, nowS }: { file: MatchFile; names: Record<Side, string>; focusPlayer: string | null; nowS: number | null }) {
  const shown = file.shots.filter((s) => nowS === null || s.at_s <= nowS);
  const best = [...file.shots].sort((a, b) => b.xg_ppm - a.xg_ppm)[0];
  return (
    <ChartFrame
      title="Shot map"
      sentence={`Every shot so far, sized by its xG and coloured by club; ${names.home} attack to the right, ${names.away} to the left.`}
      label="Shot map"
      footer={
        <div className="space-y-1.5 text-xs text-muted">
          <ul className="flex flex-wrap gap-x-4 gap-y-1">
            <li className="flex items-center gap-1.5">
              <svg viewBox="0 0 12 12" className="size-3" aria-hidden="true" focusable="false">
                <circle cx="6" cy="6" r="4" className="fill-fg" />
                <circle cx="6" cy="6" r="5.4" className="fill-none stroke-fg" strokeWidth="0.9" />
              </svg>
              Goal
            </li>
            <li className="flex items-center gap-1.5">
              <svg viewBox="0 0 12 12" className="size-3" aria-hidden="true" focusable="false">
                <circle cx="6" cy="6" r="4.5" className="fill-fg opacity-70" />
              </svg>
              Saved
            </li>
            <li className="flex items-center gap-1.5">
              <svg viewBox="0 0 12 12" className="size-3" aria-hidden="true" focusable="false">
                <circle cx="6" cy="6" r="4.2" className="fill-none stroke-fg" strokeWidth="1.4" />
              </svg>
              Off target
            </li>
            <li className="flex items-center gap-1.5">
              <svg viewBox="0 0 12 12" className="size-3" aria-hidden="true" focusable="false">
                <circle cx="6" cy="6" r="4.2" className="fill-none stroke-fg" strokeWidth="1.4" strokeDasharray="2 1.5" />
              </svg>
              Blocked
            </li>
          </ul>
          {best ? (
            <p>
              Biggest chance: {best.name} ({names[best.side]}), {best.minute}&apos;, xG {xg(best.xg_ppm)}, {OUTCOME_WORDS[best.outcome]}.
            </p>
          ) : null}
        </div>
      }
    >
      <svg viewBox="-3 -3 111 74" className="block h-auto w-full rounded-lg bg-surface-2" role="img" aria-label={`Shot map: ${shown.length} of ${file.shots.length} shots shown so far.`}>
        <PitchLines />
        {shown.map((s) => {
          const x = s.x_dm / 10;
          const y = s.y_dm / 10;
          const r = shotRadius(s);
          const dim = focusPlayer !== null && s.player !== focusPlayer;
          return (
            <g key={s.event_id} className={cn("motion-safe:animate-pop-in", dim && "opacity-25")}>
              {s.outcome === "goal" ? (
                <>
                  <circle cx={x} cy={y} r={r + 1} className={cn("fill-none", SIDE_STROKE[s.side])} strokeWidth="0.5" />
                  <circle cx={x} cy={y} r={r} className={cn(SIDE_FILL[s.side], "stroke-surface")} strokeWidth="0.4" />
                </>
              ) : s.outcome === "saved" ? (
                <circle cx={x} cy={y} r={r} className={cn(SIDE_FILL[s.side], "opacity-70 stroke-surface")} strokeWidth="0.3" />
              ) : (
                <circle cx={x} cy={y} r={r} className={cn("fill-none", SIDE_STROKE[s.side])} strokeWidth="0.6" strokeDasharray={s.outcome === "blocked" ? "1 0.7" : undefined} />
              )}
            </g>
          );
        })}
      </svg>
    </ChartFrame>
  );
}

export function TerritoryGrid({ cells, cols, rows, side, label }: { cells: number[]; cols: number; rows: number; side: Side; label: string }) {
  const max = Math.max(1, ...cells);
  const w = 105 / cols;
  const h = 68 / rows;
  return (
    <svg viewBox="-3 -3 111 74" className="block h-auto w-full rounded-lg bg-surface-2" role="img" aria-label={label}>
      {cells.map((n, i) =>
        n > 0 ? (
          <rect
            key={i}
            x={(i % cols) * w + 0.15}
            y={Math.floor(i / cols) * h + 0.15}
            width={w - 0.3}
            height={h - 0.3}
            fillOpacity={(0.1 + 0.85 * (n / max)).toFixed(2)}
            className={SIDE_FILL[side]}
          />
        ) : null,
      )}
      <PitchLines />
      <path d={side === "home" ? "M 44 71.5 H 61 M 58 69.5 L 61 71.5 L 58 73.5" : "M 61 71.5 H 44 M 47 69.5 L 44 71.5 L 47 73.5"} className={cn("fill-none", SIDE_STROKE[side])} strokeWidth="0.7" />
    </svg>
  );
}

export function TerritoryMaps({ file, names }: { file: MatchFile; names: Record<Side, string> }) {
  const t = file.territory;
  const total = (side: Side) => t[side].reduce((a, b) => a + b, 0);
  return (
    <ChartFrame
      title="Territory"
      sentence="Where each club's passes, carries and shots started, on the 16 by 12 zone grid; darker zones saw more of its play, and the arrow is its attacking direction."
      label="Territory maps"
      className="lg:col-span-2"
    >
      <div className="grid gap-4 sm:grid-cols-2">
        {(["home", "away"] as Side[]).map((side) => (
          <div key={side}>
            <p className={cn("mb-1.5 flex items-center gap-1 text-xs font-medium", SIDE_TEXT[side])}>
              <SideTriangle side={side} />
              {names[side]}: {total(side)} actions
            </p>
            <TerritoryGrid cells={t[side]} cols={t.cols} rows={t.rows} side={side} label={`Territory of ${names[side]}, ${total(side)} on-ball actions.`} />
          </div>
        ))}
      </div>
    </ChartFrame>
  );
}
