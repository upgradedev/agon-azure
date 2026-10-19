/*
  The match in numbers, below the replay, shaped by the viewer's own view:
  - Fan mode tells the story in plain words: the big moments so far, a few
    simple stats, the star player and, with a favourite club, that club's view;
  - Analyst mode shows every chart, the full team scorecard and every player;
  - one metric tells the whole match through that metric alone;
  - following a player shows that player's scorecard, pass quality, heat zones
    and moments, and dims everyone else on the charts.
  Every value is read from the match analysis (agon.webmatch, computed by
  agon.interpret and agon.governance.recompute); nothing is computed here.
*/
import { cn } from "../../lib/cn";
import {
  KIND_WORDS,
  SIDE_WORD,
  kmh,
  liveAt,
  percentOfPermille,
  probability,
  statValue,
  xg,
  type MatchFile,
  type PlayerLine,
  type Side,
  type TeamStat,
  type TimelineMark,
} from "../../lib/match";
import type { MetricId, Preferences, ViewMode } from "../../hooks/preferences";
import { ChartFrame, MomentumChart, ShotMap, SourceNote, TerritoryGrid, TerritoryMaps, WinProbabilityChart, XgRaceChart, winAt } from "./charts";
import { Crest, EventIcon, ICON_FOR, SIDE_BG, SIDE_FILL, SIDE_TEXT, SideBadge, SideTriangle } from "./marks";

export const METRIC_WORDS: Record<MetricId, string> = {
  xg: "Expected goals (xG)",
  win: "Win probability",
  possession: "Possession",
  ppda: "Pressing (PPDA)",
  pressure: "Pressure events",
  shots: "Shots",
};

const FAN_STATS = ["possession", "shots", "shots_on_target", "corners", "yellow_cards", "red_cards"];

/* ------------------------------------------------------------- team stats */

function StatRow({ stat }: { stat: TeamStat }) {
  const h = stat.home ?? 0;
  const a = stat.away ?? 0;
  const share = h + a > 0 ? h / (h + a) : 0.5;
  let leader: Side | null = null;
  if (stat.home !== null && stat.away !== null && stat.home !== stat.away) {
    const homeHigher = stat.home > stat.away;
    leader = (stat.better === "higher") === homeHigher ? "home" : "away";
  }
  return (
    <li className="py-2">
      <div className="flex items-baseline justify-between gap-3 text-sm">
        <span className={cn("tabular-nums", leader === "home" ? cn("font-bold", SIDE_TEXT.home) : "text-fg")}>{statValue(stat, "home")}</span>
        <span className="text-center text-xs font-medium text-muted">{stat.label}</span>
        <span className={cn("tabular-nums", leader === "away" ? cn("font-bold", SIDE_TEXT.away) : "text-fg")}>{statValue(stat, "away")}</span>
      </div>
      <svg viewBox="0 0 200 6" preserveAspectRatio="none" className="mt-1 block h-1.5 w-full" aria-hidden="true" focusable="false">
        <rect x="0" y="0" width="200" height="6" rx="3" className="fill-surface-3" />
        <rect x={(100 - 100 * share).toFixed(2)} y="0" width={(100 * share).toFixed(2)} height="6" className={SIDE_FILL.home} />
        <rect x="100" y="0" width={(100 * (1 - share)).toFixed(2)} height="6" className={SIDE_FILL.away} />
        <rect x="99.4" y="0" width="1.2" height="6" className="fill-surface" />
      </svg>
    </li>
  );
}

export function TeamStats({ file, names, mode }: { file: MatchFile; names: Record<Side, string>; mode: ViewMode }) {
  const rows = mode === "fan" ? file.team_stats.filter((s) => FAN_STATS.includes(s.key)) : file.team_stats;
  return (
    <ChartFrame
      title="Team stats"
      sentence={
        mode === "fan"
          ? "The full-time numbers side by side; the bolder number leads."
          : "Full-time numbers side by side, each bar the club's share; the bolder number leads (for PPDA, fouls and cards, lower leads)."
      }
      label="Team stats scorecard"
      className="lg:col-span-2"
    >
      <div className="mb-1 flex items-center justify-between text-xs font-semibold">
        <span className={cn("flex items-center gap-1", SIDE_TEXT.home)}>
          <SideTriangle side="home" />
          {names.home}
        </span>
        <span className={cn("flex items-center gap-1", SIDE_TEXT.away)}>
          {names.away}
          <SideTriangle side="away" />
        </span>
      </div>
      <ul className="divide-y divide-line">
        {rows.map((s) => (
          <StatRow key={s.key} stat={s} />
        ))}
      </ul>
      <p className="mt-2 text-[0.6875rem] leading-4 text-muted">{file.rules.possession}</p>
    </ChartFrame>
  );
}

/* ------------------------------------------------------------- players */

function passLine(p: PlayerLine): string {
  return `${p.passes_completed} of ${p.passes}${p.pass_accuracy_pct === null ? "" : ` (${p.pass_accuracy_pct}%)`}`;
}

export function PlayerTable({ file, names, mode, favourite }: { file: MatchFile; names: Record<Side, string>; mode: ViewMode; favourite: string | null }) {
  const analyst = mode === "analyst";
  return (
    <ChartFrame
      title="Player scorecards"
      sentence={`Every player who appeared. ${file.rules.speed} ${file.rules.distance}`}
      label="Player scorecards"
      className="lg:col-span-2"
    >
      <div className="grid gap-5 xl:grid-cols-2">
        {(["home", "away"] as Side[]).map((side) => (
          <div key={side} className="min-w-0">
            <p className={cn("mb-1.5 flex items-center gap-1.5 text-xs font-semibold", SIDE_TEXT[side])}>
              <SideTriangle side={side} />
              {names[side]}
            </p>
            <div className="relative overflow-x-auto" tabIndex={0} role="region" aria-label={`${names[side]} players, scrolls sideways on a narrow screen`}>
              <table className="w-full min-w-[22rem] border-collapse text-left text-xs tabular-nums">
                <thead>
                  <tr className="border-b border-line text-muted">
                    <th scope="col" className="py-1.5 pr-2 font-medium">Player</th>
                    <th scope="col" className="px-1.5 py-1.5 text-right font-medium">Min</th>
                    <th scope="col" className="px-1.5 py-1.5 text-right font-medium">Goals</th>
                    <th scope="col" className="px-1.5 py-1.5 text-right font-medium">Assists</th>
                    <th scope="col" className="px-1.5 py-1.5 text-right font-medium">Shots</th>
                    {analyst ? (
                      <>
                        <th scope="col" className="px-1.5 py-1.5 text-right font-medium">xG</th>
                        <th scope="col" className="px-1.5 py-1.5 text-right font-medium">Passes</th>
                        <th scope="col" className="px-1.5 py-1.5 text-right font-medium">Top carry speed (proxy)</th>
                        <th scope="col" className="px-1.5 py-1.5 text-right font-medium">Sprint carries (proxy)</th>
                      </>
                    ) : null}
                  </tr>
                </thead>
                <tbody>
                  {file.players
                    .filter((p) => p.side === side)
                    .map((p) => {
                      const star = file.star?.player_id === p.player_id;
                      const fav = favourite === p.player_id;
                      return (
                        <tr key={p.player_id} className={cn("border-b border-line/60", fav && SIDE_BG[side])}>
                          <th scope="row" className="py-1.5 pr-2 font-normal">
                            <span className="flex min-w-0 items-center gap-1.5">
                              <span className="w-5 shrink-0 text-right text-muted">{p.shirt}</span>
                              <span className={cn("truncate", p.started ? "text-fg" : "text-muted")}>{p.name}</span>
                              <span className="shrink-0 text-[0.625rem] text-muted">{p.position}</span>
                              {star ? (
                                <span className="inline-flex shrink-0 items-center gap-0.5 rounded-full bg-notice-bg px-1.5 text-[0.625rem] font-semibold text-notice">
                                  <EventIcon kind="big_chance" className="size-3" />
                                  Star
                                </span>
                              ) : null}
                              {fav ? <span className="shrink-0 text-[0.625rem] font-semibold text-fg">Your player</span> : null}
                            </span>
                          </th>
                          <td className="px-1.5 py-1.5 text-right">{p.minutes}</td>
                          <td className="px-1.5 py-1.5 text-right">{p.goals}</td>
                          <td className="px-1.5 py-1.5 text-right">{p.assists}</td>
                          <td className="px-1.5 py-1.5 text-right">{p.shots}</td>
                          {analyst ? (
                            <>
                              <td className="px-1.5 py-1.5 text-right">{xg(p.xg_ppm)}</td>
                              <td className="px-1.5 py-1.5 text-right whitespace-nowrap">{passLine(p)}</td>
                              <td className="px-1.5 py-1.5 text-right whitespace-nowrap">{kmh(p.top_carry_proxy_kmh_x10)}</td>
                              <td className="px-1.5 py-1.5 text-right">{p.sprint_carries}</td>
                            </>
                          ) : null}
                        </tr>
                      );
                    })}
                </tbody>
              </table>
            </div>
          </div>
        ))}
      </div>
      <p className="mt-2 text-[0.6875rem] leading-4 text-muted">{file.rules.star}</p>
    </ChartFrame>
  );
}

export function StarCard({ file, names }: { file: MatchFile; names: Record<Side, string> }) {
  const star = file.star;
  const line = star ? file.players.find((p) => p.player_id === star.player_id) ?? null : null;
  return (
    <section aria-labelledby="h-star" className="min-w-0 rounded-xl border border-line bg-surface p-4 shadow-card">
      <h3 id="h-star" className="flex items-center gap-1.5 text-sm font-semibold text-fg">
        <EventIcon kind="big_chance" className="size-4 text-notice" />
        Star player
      </h3>
      {star && line ? (
        <div className="mt-3 flex items-start gap-3">
          <Crest club={file.clubs[star.side]} className="size-12" />
          <div className="min-w-0">
            <p className="text-lg font-semibold tracking-tight text-fg">{star.name}</p>
            <p className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-muted">
              <SideBadge side={star.side} />
              {names[star.side]}, shirt {line.shirt}
            </p>
            <dl className="mt-3 grid grid-cols-3 gap-2 text-center">
              {[
                ["Goals", String(line.goals)],
                ["Assists", String(line.assists)],
                ["xG", xg(line.xg_ppm)],
              ].map(([k, v]) => (
                <div key={k} className="rounded-lg bg-surface-2 px-2 py-1.5">
                  <dt className="text-[0.6875rem] text-muted">{k}</dt>
                  <dd className="text-base font-semibold tabular-nums text-fg">{v}</dd>
                </div>
              ))}
            </dl>
          </div>
        </div>
      ) : (
        <p className="mt-3 text-sm text-muted">Nobody scored or assisted, so the stated rule picks no star player.</p>
      )}
      <p className="mt-3 text-[0.6875rem] leading-4 text-muted">{file.rules.star}</p>
    </section>
  );
}

/* ------------------------------------------------------------- the story (fan mode) */

function storySentence(mark: TimelineMark, file: MatchFile, names: Record<Side, string>): string {
  const club = `${names[mark.side]} (${SIDE_WORD[mark.side].toLowerCase()})`;
  switch (mark.kind) {
    case "goal": {
      const g = file.goals.find((x) => x.event_id === mark.event_id);
      const score = g ? ` ${names.home} ${g.score_after.home}, ${names.away} ${g.score_after.away}.` : "";
      return `Goal for ${club}: ${mark.name ?? "a player"} scores${mark.penalty ? " from the penalty spot" : ""}.${score}`;
    }
    case "red":
      return `${mark.second_yellow ? "Second yellow, so a red card" : "Red card"} for ${mark.name ?? "a player"} of ${club}.`;
    case "yellow":
      return `Yellow card for ${mark.name ?? "a player"} of ${club}.`;
    case "sub":
      return `Substitution for ${club}: ${mark.related_name ?? "a player"} comes on for ${mark.name ?? "a player"}.`;
    default:
      return `Big chance for ${club}: ${mark.name ?? "a player"}, ${KIND_WORDS[mark.kind].toLowerCase()}.`;
  }
}

export function Story({ file, names, nowSeq, club, player }: { file: MatchFile; names: Record<Side, string>; nowSeq: number | null; club: Side | null; player: string | null }) {
  const big = file.timeline.filter((m) => m.kind === "goal" || m.kind === "red" || m.kind === "yellow" || m.kind === "sub" || m.big_chance);
  const past = nowSeq === null ? [] : big.filter((m) => m.seq <= nowSeq);
  const left = big.length - past.length;
  return (
    <section aria-labelledby="h-story" className="min-w-0 rounded-xl border border-line bg-surface p-4 shadow-card lg:col-span-2">
      <h3 id="h-story" className="text-sm font-semibold text-fg">
        The story so far
      </h3>
      <p className="mt-1 text-xs leading-5 text-muted">Goals, cards, substitutions and big chances in plain words, as the replay reaches them.</p>
      {past.length ? (
        <ol className="mt-3 space-y-2">
          {[...past].reverse().map((m) => {
            const mine = club === m.side;
            const theirPlayer = player !== null && m.player === player;
            return (
              <li key={m.event_id} className={cn("flex items-start gap-3 rounded-lg px-2.5 py-2", mine || theirPlayer ? SIDE_BG[m.side] : "bg-surface-2")}>
                <span className={cn("mt-0.5 inline-flex size-7 shrink-0 items-center justify-center rounded-full bg-surface", SIDE_TEXT[m.side])}>
                  <EventIcon kind={ICON_FOR[m.kind] ?? "big_chance"} className="size-4" />
                </span>
                <span className="min-w-0 text-sm leading-6 text-fg">
                  <span className="mr-1.5 font-semibold tabular-nums">{m.minute}&apos;</span>
                  {storySentence(m, file, names)}
                  {mine ? <span className={cn("ml-1.5 text-xs font-semibold", SIDE_TEXT[m.side])}>Your club</span> : null}
                  {theirPlayer ? <span className={cn("ml-1.5 text-xs font-semibold", SIDE_TEXT[m.side])}>Your player</span> : null}
                </span>
              </li>
            );
          })}
        </ol>
      ) : (
        <p className="mt-3 text-sm text-muted">Nothing big has happened yet. Press Play, or move the match time.</p>
      )}
      {left > 0 ? <p className="mt-2 text-xs text-muted">{left} more to come in the replay.</p> : null}
    </section>
  );
}

export function YourClub({ file, names, side, nowS, nowSeq }: { file: MatchFile; names: Record<Side, string>; side: Side; nowS: number | null; nowSeq: number | null }) {
  const other: Side = side === "home" ? "away" : "home";
  const live = liveAt(file, nowSeq);
  const win = winAt(file, nowS);
  const score = file.goals.filter((g) => nowSeq !== null && g.seq <= nowSeq);
  const mine = score.filter((g) => g.side === side).length;
  const theirs = score.length - mine;
  const verdict = mine > theirs ? "ahead" : mine < theirs ? "behind" : "level";
  return (
    <section aria-labelledby="h-your-club" className={cn("min-w-0 rounded-xl border border-line p-4 shadow-card", SIDE_BG[side])}>
      <h3 id="h-your-club" className={cn("flex items-center gap-2 text-sm font-semibold", SIDE_TEXT[side])}>
        <Crest club={file.clubs[side]} className="size-6" />
        Your club: {names[side]}
      </h3>
      <p className="mt-2 text-sm leading-6 text-fg">
        {nowSeq === null
          ? `${names[side]} play ${side === "home" ? "at home" : "away"} against ${names[other]}. Press Play to follow them.`
          : `${names[side]} are ${verdict}, ${mine} to ${theirs}.${win ? ` The model gives them a ${probability(side === "home" ? win.home_ppm : win.away_ppm)} chance to win from here.` : ""}${
              live ? ` Their xG so far is ${xg(side === "home" ? live.home_xg_ppm : live.away_xg_ppm)} against ${xg(side === "home" ? live.away_xg_ppm : live.home_xg_ppm)}, with ${percentOfPermille(side === "home" ? live.home_possession_permille : 1000 - live.home_possession_permille)} of the passes.` : ""
            }`}
      </p>
    </section>
  );
}

/* ------------------------------------------------------------- player focus */

export function PlayerFocus({ file, player, names, nowSeq }: { file: MatchFile; player: PlayerLine; names: Record<Side, string>; nowSeq: number | null }) {
  const cells = Array<number>(file.territory.cols * file.territory.rows).fill(0);
  for (const [cell, n] of player.zones) {
    cells[cell] = n;
  }
  const moments = file.timeline.filter((m) => m.player === player.player_id || (m.kind === "sub" && m.related_name === player.name));
  const stats: [string, string][] = [
    ["Minutes", String(player.minutes)],
    ["Goals", String(player.goals)],
    ["Assists", String(player.assists)],
    ["Shots", String(player.shots)],
    ["xG", xg(player.xg_ppm)],
    ["Passes completed", passLine(player)],
    ["Mean pass length", player.mean_pass_distance_dm === null ? "none" : `${(player.mean_pass_distance_dm / 10).toFixed(1)} m`],
    ["Mean pass difficulty", player.mean_pass_difficulty_centi === null ? "none" : `${(player.mean_pass_difficulty_centi / 100).toFixed(0)} of 100`],
    ["Top carry speed (proxy)", kmh(player.top_carry_proxy_kmh_x10)],
    ["Sprint carries (proxy)", String(player.sprint_carries)],
  ];
  return (
    <section aria-labelledby="h-focus" className="min-w-0 rounded-xl border border-line bg-surface p-4 shadow-card lg:col-span-2">
      <h3 id="h-focus" className="flex flex-wrap items-center gap-2 text-sm font-semibold text-fg">
        <Crest club={file.clubs[player.side]} className="size-6" />
        Following {player.name}
        <SideBadge side={player.side} />
        <span className="text-xs font-normal text-muted">
          {names[player.side]}, shirt {player.shirt}, {player.position}, {player.started ? "started" : "came on"}
        </span>
      </h3>
      <p className="mt-1 text-xs leading-5 text-muted">
        Their scorecard, pass quality and where they had the ball; on the charts above everyone else is dimmed. Pass difficulty is agon.interpret&apos;s modelled
        chance that a pass like it fails, from 0 (easy) to 100. <SourceNote />
      </p>
      <div className="mt-3 grid gap-4 md:grid-cols-2">
        <dl className="grid grid-cols-2 gap-2">
          {stats.map(([k, v]) => (
            <div key={k} className="rounded-lg bg-surface-2 px-2.5 py-1.5">
              <dt className="text-[0.6875rem] text-muted">{k}</dt>
              <dd className="text-sm font-semibold tabular-nums text-fg">{v}</dd>
            </div>
          ))}
        </dl>
        <div>
          <p className={cn("mb-1.5 text-xs font-medium", SIDE_TEXT[player.side])}>Heat zones: where {player.name} started passes, carries and shots</p>
          <TerritoryGrid cells={cells} cols={file.territory.cols} rows={file.territory.rows} side={player.side} label={`Heat zones of ${player.name}.`} />
        </div>
      </div>
      <h4 className="mt-4 text-xs font-semibold text-fg">Their moments</h4>
      {moments.length ? (
        <ul className="mt-1.5 flex flex-wrap gap-1.5">
          {moments.map((m) => (
            <li
              key={m.event_id}
              className={cn(
                "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs",
                nowSeq !== null && m.seq <= nowSeq ? cn(SIDE_BG[m.side], SIDE_TEXT[m.side], "font-medium") : "bg-surface-2 text-muted",
              )}
            >
              <EventIcon kind={ICON_FOR[m.kind] ?? "foul"} className="size-3.5" />
              {m.minute}&apos; {m.kind === "sub" && m.related_name === player.name ? "Came on" : KIND_WORDS[m.kind]}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-1.5 text-xs text-muted">No goal, shot, card, corner, foul or substitution by this player.</p>
      )}
    </section>
  );
}

/* ------------------------------------------------------------- one metric */

export function MetricLens({ file, metric, names, nowS, focusPlayer }: { file: MatchFile; metric: MetricId; names: Record<Side, string>; nowS: number | null; focusPlayer: string | null }) {
  const head = (
    <p className="text-sm leading-6 text-fg">
      The whole match told through one number: <span className="font-semibold">{METRIC_WORDS[metric]}</span>.
    </p>
  );
  if (metric === "xg") {
    const best = [...file.shots].sort((a, b) => b.xg_ppm - a.xg_ppm)[0];
    return (
      <div className="space-y-3 lg:col-span-2">
        {head}
        <XgRaceChart file={file} nowS={nowS} names={names} focusPlayer={focusPlayer} />
        {best ? (
          <p className="text-sm text-muted">
            The best chance was {best.name}&apos;s for {names[best.side]} at {best.minute}&apos;, worth {xg(best.xg_ppm)} xG; it was {best.outcome.replace("_", " ")}.
          </p>
        ) : null}
      </div>
    );
  }
  if (metric === "win") {
    return (
      <div className="space-y-3 lg:col-span-2">
        {head}
        <WinProbabilityChart file={file} nowS={nowS} names={names} />
      </div>
    );
  }
  if (metric === "shots") {
    return (
      <div className="space-y-3 lg:col-span-2">
        {head}
        <ShotMap file={file} names={names} focusPlayer={focusPlayer} nowS={nowS} />
      </div>
    );
  }
  if (metric === "possession") {
    const f = { W: 480, H: 160, L: 34, R: 470, T: 10, B: 140 };
    const end = Math.max(1, file.axis.end_s);
    const x = (s: number) => f.L + (Math.min(s, end) / end) * (f.R - f.L);
    const y = (permille: number) => f.B - (permille / 1000) * (f.B - f.T);
    const d = file.live.map((p, i) => `${i ? "L" : "M"} ${x(p.at_s).toFixed(1)} ${y(p.home_possession_permille).toFixed(1)}`).join(" ");
    const last = file.live[file.live.length - 1];
    return (
      <div className="space-y-3 lg:col-span-2">
        {head}
        <ChartFrame title="Possession over the match" sentence={`${names.home}'s running share of the passes attempted; above the middle line ${names.home} had more of the ball.`} label="Possession over the match">
          <svg viewBox={`0 0 ${f.W} ${f.H}`} className="block h-auto w-full" role="img" aria-label={`Possession: ${names.home} ${last ? percentOfPermille(last.home_possession_permille) : "none"} at full time.`}>
            <rect x={f.L} y={f.T} width={f.R - f.L} height={(f.B - f.T) / 2} className={cn(SIDE_FILL.home, "opacity-10")} />
            <rect x={f.L} y={(f.T + f.B) / 2} width={f.R - f.L} height={(f.B - f.T) / 2} className={cn(SIDE_FILL.away, "opacity-10")} />
            <line x1={f.L} y1={y(500)} x2={f.R} y2={y(500)} className="stroke-line-strong" strokeDasharray="3 3" />
            <path d={d} pathLength="1" className="fill-none stroke-fg [stroke-dasharray:1] motion-safe:animate-draw" strokeWidth="2" />
            {nowS !== null && nowS < end ? <rect x={x(nowS)} y={f.T} width={Math.max(0, f.R - x(nowS))} height={f.B - f.T} className="fill-surface opacity-70" /> : null}
          </svg>
        </ChartFrame>
      </div>
    );
  }
  if (metric === "ppda") {
    const rows = file.ppda_by_15;
    const top = Math.max(100, ...rows.flatMap((r) => [r.home_ppda_centi ?? 0, r.away_ppda_centi ?? 0]));
    return (
      <div className="space-y-3 lg:col-span-2">
        {head}
        <ChartFrame title="Pressing per 15 minutes (PPDA)" sentence="Opponent passes allowed per defensive action in the opponent's build-up zone; a shorter bar means a harder press." label="Pressing per 15 minutes">
          <ul className="space-y-2">
            {rows.map((r) => (
              <li key={r.bin} className="grid grid-cols-[4.5rem_minmax(0,1fr)] items-center gap-2 text-xs">
                <span className="text-muted tabular-nums">{r.bin.replace("-", " to ").replace("+", " plus")}</span>
                <div className="space-y-1">
                  {(["home", "away"] as Side[]).map((side) => {
                    const v = side === "home" ? r.home_ppda_centi : r.away_ppda_centi;
                    return (
                      <div key={side} className="flex items-center gap-2">
                        <svg viewBox="0 0 100 6" preserveAspectRatio="none" className="block h-2 flex-1" aria-hidden="true" focusable="false">
                          <rect x="0" y="0" width="100" height="6" className="fill-surface-3" />
                          {v !== null ? <rect x="0" y="0" width={((v / top) * 100).toFixed(1)} height="6" className={SIDE_FILL[side]} /> : null}
                        </svg>
                        <span className={cn("w-24 shrink-0 tabular-nums", SIDE_TEXT[side])}>
                          {names[side].split(" ")[0]} {v === null ? "no press" : (v / 100).toFixed(2)}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </li>
            ))}
          </ul>
        </ChartFrame>
      </div>
    );
  }
  return (
    <div className="space-y-3 lg:col-span-2">
      {head}
      <MomentumChart file={file} nowS={nowS} names={names} />
    </div>
  );
}

/* ------------------------------------------------------------- the section */

export interface AnalysisProps {
  file: MatchFile;
  names: Record<Side, string>;
  prefs: Preferences;
  nowS: number | null;
  nowSeq: number | null;
}

export function Analysis({ file, names, prefs, nowS, nowSeq }: AnalysisProps) {
  const club: Side | null = prefs.club === file.clubs.home.club_id ? "home" : prefs.club === file.clubs.away.club_id ? "away" : null;
  const favourite = prefs.player ? file.players.find((p) => p.player_id === prefs.player) ?? null : null;
  const focus = prefs.focus && favourite ? favourite : null;
  const focusId = focus ? focus.player_id : null;
  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      {club ? <YourClub file={file} names={names} side={club} nowS={nowS} nowSeq={nowSeq} /> : null}
      {focus ? <PlayerFocus file={file} player={focus} names={names} nowSeq={nowSeq} /> : null}
      {prefs.metric ? (
        <MetricLens file={file} metric={prefs.metric} names={names} nowS={nowS} focusPlayer={focusId} />
      ) : prefs.mode === "fan" ? (
        <>
          <Story file={file} names={names} nowSeq={nowSeq} club={club} player={prefs.player} />
          <StarCard file={file} names={names} />
          <WinProbabilityChart file={file} nowS={nowS} names={names} compact />
          <TeamStats file={file} names={names} mode="fan" />
          <ShotMap file={file} names={names} focusPlayer={focusId} nowS={nowS} />
        </>
      ) : (
        <>
          <WinProbabilityChart file={file} nowS={nowS} names={names} />
          <XgRaceChart file={file} nowS={nowS} names={names} focusPlayer={focusId} />
          <MomentumChart file={file} nowS={nowS} names={names} />
          <ShotMap file={file} names={names} focusPlayer={focusId} nowS={nowS} />
          <TerritoryMaps file={file} names={names} />
          <StarCard file={file} names={names} />
          <TeamStats file={file} names={names} mode="analyst" />
          <PlayerTable file={file} names={names} mode="analyst" favourite={prefs.player} />
        </>
      )}
      {prefs.mode === "fan" && !prefs.metric ? <PlayerTable file={file} names={names} mode="fan" favourite={prefs.player} /> : null}
    </div>
  );
}
