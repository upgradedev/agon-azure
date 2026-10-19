/*
  The match timeline: home events above the line, away events below, one
  drawn icon per event type, the half-time line and a playhead that follows the
  replay. Every position comes from the match analysis (at_s on the drawn time
  axis). It is a picture: screen readers get the summary sentence, and the key
  moments list beside the replay is the keyboard way to the same events.
*/
import { cn } from "../../lib/cn";
import { KIND_WORDS, type MatchFile, type Side, type TimelineMark } from "../../lib/match";
import { EventIcon, ICON_FOR, SIDE_FILL, SIDE_TEXT, SideTriangle } from "./marks";

interface Layout {
  width: number;
  height: number;
  pad: number;
  icon: number;
}

const WIDE: Layout = { width: 1000, height: 132, pad: 28, icon: 24 };
const COMPACT: Layout = { width: 420, height: 132, pad: 14, icon: 18 };
const LANES = 3;

function place(marks: TimelineMark[], side: Side, layout: Layout, x: (s: number) => number) {
  const lastX: number[] = Array(LANES).fill(-1e9);
  const out: { mark: TimelineMark; x: number; lane: number }[] = [];
  for (const mark of marks.filter((m) => m.side === side)) {
    const px = x(mark.at_s);
    let lane = lastX.findIndex((v) => px - v >= layout.icon + 2);
    if (lane === -1) {
      lane = lastX.indexOf(Math.min(...lastX));
    }
    lastX[lane] = px;
    out.push({ mark, x: px, lane });
  }
  return out;
}

function TimelineSvg({ file, marks, nowS, layout, className }: { file: MatchFile; marks: TimelineMark[]; nowS: number | null; layout: Layout; className?: string }) {
  const { width, height, pad, icon } = layout;
  const end = Math.max(1, file.axis.end_s);
  const x = (s: number) => pad + (Math.min(Math.max(s, 0), end) / end) * (width - 2 * pad);
  const mid = height / 2;
  const step = icon + 4;
  const ticks = [0, 15, 30, 45, 60, 75, 90].map((m) => ({ m, s: m * 60 + (m > 45 ? file.axis.half_offset_s : 0) }));
  const home = place(marks, "home", layout, x);
  const away = place(marks, "away", layout, x);
  const now = nowS === null ? null : x(nowS);
  return (
    <svg viewBox={`0 0 ${width} ${height}`} className={cn("block h-auto w-full", className)} aria-hidden="true" focusable="false">
      <rect x={pad} y={mid - 1} width={width - 2 * pad} height="2" className="fill-line-strong" />
      <line x1={x(file.axis.half_time_s)} y1="6" x2={x(file.axis.half_time_s)} y2={height - 6} className="stroke-line-strong" strokeDasharray="3 3" />
      {ticks.map((t) => (
        <g key={t.m}>
          <line x1={x(t.s)} y1={mid - 4} x2={x(t.s)} y2={mid + 4} className="stroke-line-strong" />
        </g>
      ))}
      {[...home.map((p) => ({ ...p, side: "home" as Side })), ...away.map((p) => ({ ...p, side: "away" as Side }))].map(({ mark, x: px, lane, side }) => {
        const cy = side === "home" ? mid - 8 - icon / 2 - lane * step : mid + 8 + icon / 2 + lane * step;
        const future = now !== null && px > now + 0.5;
        return (
          <g key={mark.event_id} className={cn("transition-opacity duration-300", future ? "opacity-35" : "opacity-100")}>
            <line x1={px} y1={mid} x2={px} y2={side === "home" ? cy + icon / 2 : cy - icon / 2} className="stroke-line-strong" strokeWidth="1" />
            <circle cx={px} cy={cy} r={icon / 2 + 2} className={cn(mark.kind === "goal" ? SIDE_FILL[side] : "fill-surface", "stroke-line-strong")} strokeWidth="1" />
            <svg x={px - icon / 2 + 2} y={cy - icon / 2 + 2} width={icon - 4} height={icon - 4} viewBox="0 0 24 24" overflow="visible">
              <g className={mark.kind === "goal" ? "text-surface" : SIDE_TEXT[side]}>
                <EventIcon kind={ICON_FOR[mark.kind] ?? "foul"} className="size-full" />
              </g>
            </svg>
            {mark.big_chance ? <circle cx={px + icon / 2} cy={cy - icon / 2} r="3.2" className="fill-pressure stroke-fg" strokeWidth="0.8" /> : null}
          </g>
        );
      })}
      {now !== null ? (
        <g>
          <line x1={now} y1="4" x2={now} y2={height - 4} className="stroke-fg" strokeWidth="1.5" />
          <circle cx={now} cy={mid} r="4" className="fill-fg" />
        </g>
      ) : null}
    </svg>
  );
}

/* Fan mode keeps the big moments: goals, cards, substitutions and big chances. */
function fanMark(m: TimelineMark): boolean {
  return m.kind === "goal" || m.kind === "yellow" || m.kind === "red" || m.kind === "sub" || m.big_chance;
}

export function MatchTimeline({ file, nowS, names, fan = false }: { file: MatchFile; nowS: number | null; names: Record<Side, string>; fan?: boolean }) {
  const marks = fan ? file.timeline.filter(fanMark) : file.timeline;
  const counts = (side: Side) => {
    const mine = file.timeline.filter((m) => m.side === side);
    const goals = mine.filter((m) => m.kind === "goal").length;
    const shots = mine.filter((m) => ["goal", "save", "block", "shot_off"].includes(m.kind)).length;
    const cards = mine.filter((m) => m.kind === "yellow" || m.kind === "red").length;
    return `${names[side]}: ${goals} goal${goals === 1 ? "" : "s"}, ${shots} shot${shots === 1 ? "" : "s"}, ${cards} card${cards === 1 ? "" : "s"}`;
  };
  const kinds = Array.from(new Set(marks.map((m) => m.kind)));
  return (
    <figure className="space-y-2">
      <div className="flex items-center justify-between text-xs font-medium">
        <span className={cn("flex items-center gap-1", SIDE_TEXT.home)}>
          <SideTriangle side="home" />
          {names.home} above the line
        </span>
        <span className={cn("flex items-center gap-1", SIDE_TEXT.away)}>
          {names.away} below
          <SideTriangle side="away" />
        </span>
      </div>
      <div className="rounded-lg border border-line bg-surface px-1">
        <TimelineSvg file={file} marks={marks} nowS={nowS} layout={COMPACT} className="sm:hidden" />
        <TimelineSvg file={file} marks={marks} nowS={nowS} layout={WIDE} className="hidden sm:block" />
      </div>
      <div className="flex justify-between px-1 text-[0.6875rem] text-muted tabular-nums" aria-hidden="true">
        <span>0&apos;</span>
        <span>45&apos;</span>
        <span>90&apos;</span>
      </div>
      <figcaption className="sr-only">
        Match timeline. {counts("home")}. {counts("away")}.
      </figcaption>
      <ul className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted" aria-label="Timeline legend">
        {kinds.map((kind) => (
          <li key={kind} className="flex items-center gap-1">
            <EventIcon kind={ICON_FOR[kind] ?? "foul"} className="size-3.5 text-fg" />
            {KIND_WORDS[kind]}
          </li>
        ))}
        <li className="flex items-center gap-1">
          <svg viewBox="0 0 10 10" className="size-2.5" aria-hidden="true" focusable="false">
            <circle cx="5" cy="5" r="3.6" className="fill-pressure stroke-fg" strokeWidth="1" />
          </svg>
          Big chance (xG of 0.2 or more)
        </li>
        <li className="flex items-center gap-1">
          <EventIcon kind="offside" className="size-3.5 text-muted" />
          Offside: not in the synthetic event log
        </li>
      </ul>
    </figure>
  );
}
