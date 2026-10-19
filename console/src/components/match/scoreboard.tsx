/*
  The scoreboard above the replay pitch: both crests, names and side badges,
  the score and the match minute, the possession bar and each club's goals so
  far. Every number is picked from the match analysis at the replay's current
  event (agon.webmatch); when no analysis is loaded, the score comes from the
  replay and the possession bar says why it is empty.
*/
import { cn } from "../../lib/cn";
import { percentOfPermille, type Goal, type LivePoint, type MatchClub, type Side } from "../../lib/match";
import { Crest, EventIcon, SideBadge, SIDE_FILL, SIDE_TEXT } from "./marks";

export interface ScoreboardProps {
  clubs: Record<Side, MatchClub | null>;
  names: Record<Side, string>;
  score: { home: number; away: number };
  minute: string;
  half: string;
  live: LivePoint | null;
  goals: Goal[] | null;
  possessionRule: string | null;
}

function ClubLine({ side, club, name }: { side: Side; club: MatchClub | null; name: string }) {
  return (
    <div className={cn("flex min-w-0 items-center gap-2.5", side === "away" && "flex-row-reverse text-right")}>
      <Crest club={club} className="size-9 sm:size-10" />
      <div className={cn("min-w-0", side === "away" && "flex flex-col items-end")}>
        <p className="min-w-0 truncate text-sm font-semibold text-fg sm:text-base">
          {club?.short ? (
            <>
              <span className="sm:hidden" aria-hidden="true">
                {club.short}
              </span>
              <span className="sr-only sm:not-sr-only">{name}</span>
            </>
          ) : (
            name
          )}
        </p>
        <SideBadge side={side} className="mt-0.5" />
      </div>
    </div>
  );
}

function GoalList({ side, goals }: { side: Side; goals: Goal[] }) {
  const mine = goals.filter((g) => g.side === side);
  return (
    <ul className={cn("min-w-0 space-y-0.5 text-xs text-muted", side === "away" && "text-right")} aria-label={`${side === "home" ? "Home" : "Away"} goals so far`}>
      {mine.length ? (
        mine.map((g) => (
          <li key={g.event_id} className={cn("flex items-center gap-1.5", side === "away" && "flex-row-reverse")}>
            <EventIcon kind="goal" className={cn("size-3.5", SIDE_TEXT[side])} />
            <span className="min-w-0 truncate">
              <span className="font-medium text-fg">{g.name}</span> {g.minute}&apos;{g.penalty ? " (pen)" : ""}
            </span>
          </li>
        ))
      ) : (
        <li>No goal yet</li>
      )}
    </ul>
  );
}

export function Scoreboard({ clubs, names, score, minute, half, live, goals, possessionRule }: ScoreboardProps) {
  const homeShare = live ? live.home_possession_permille : 500;
  const width = (homeShare / 10).toFixed(1);
  return (
    <div className="overflow-hidden rounded-xl border border-line bg-surface-2">
      <div className="grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-2 px-3 pt-3 sm:gap-4 sm:px-4">
        <ClubLine side="home" club={clubs.home} name={names.home} />
        <div className="flex flex-col items-center px-1 text-center">
          <p className="flex items-baseline gap-2 text-3xl font-bold tracking-tight tabular-nums text-fg sm:text-4xl">
            <span className={SIDE_TEXT.home}>{score.home}</span>
            <span className="text-xl font-medium text-muted" aria-hidden="true">
              :
            </span>
            <span className="sr-only">to</span>
            <span className={SIDE_TEXT.away}>{score.away}</span>
          </p>
          <p className="mt-0.5 rounded-md bg-surface px-2 text-sm font-semibold tabular-nums text-fg">{minute}</p>
          <p className="text-[0.6875rem] tracking-wide text-muted uppercase">{half}</p>
        </div>
        <ClubLine side="away" club={clubs.away} name={names.away} />
      </div>

      <div className="px-3 pt-3 sm:px-4">
        <div className="flex items-center justify-between text-xs font-medium">
          <span className={SIDE_TEXT.home}>{live ? percentOfPermille(homeShare) : "none yet"}</span>
          <span className="text-muted">Possession</span>
          <span className={SIDE_TEXT.away}>{live ? percentOfPermille(1000 - homeShare) : "none yet"}</span>
        </div>
        <svg viewBox="0 0 100 4" preserveAspectRatio="none" className="mt-1 block h-2 w-full overflow-hidden rounded-full" aria-hidden="true" focusable="false">
          <rect x="0" y="0" width="100" height="4" className="fill-surface-3" />
          {live ? (
            <>
              <rect x="0" y="0" width={width} height="4" className={cn(SIDE_FILL.home, "transition-[width] duration-300")} />
              <rect x={width} y="0" width={(100 - Number(width)).toFixed(1)} height="4" className={SIDE_FILL.away} />
              <rect x={(Number(width) - 0.4).toFixed(1)} y="0" width="0.8" height="4" className="fill-surface" />
            </>
          ) : null}
        </svg>
        <p className="mt-1 text-[0.6875rem] leading-4 text-muted">
          {possessionRule ?? "Possession appears once the match analysis has loaded."}
        </p>
      </div>

      {goals ? (
        <div className="mt-2 grid grid-cols-2 gap-3 border-t border-line px-3 py-2.5 sm:px-4">
          <GoalList side="home" goals={goals} />
          <GoalList side="away" goals={goals} />
        </div>
      ) : (
        <div className="h-3" />
      )}
    </div>
  );
}
