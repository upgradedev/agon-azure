/*
  "Why it mattered": the Analyst's turning points from a run's sealed card.json,
  where a visitor looks first: on the Recap next to the editions, and on the
  Match Centre in Key moments for the run of the match being replayed. Each pick
  shows the minute code read from its event, the theme and the why, labelled by
  who wrote it, with the club key for ids such as C1 and C6, where the picks are
  sealed, and what code checked one click away (lib/runs analystSummary and
  analystSource, the same words as the Newsroom Wall's Analyst desk).

  A run whose card carries no turning point has no notes, and its screens render
  none of this. Items carry data-why-pick, not data-pick, so the Analyst desk's
  list stays the one data-pick list in the page.
*/
import { Badge } from "./ui/badge";
import { THEME_WORDS, aiWritten, analystByline, analystSource, analystSummary, clubKey, pickAuthorWords } from "../lib/runs";
import { cn } from "../lib/cn";
import type { AnalystNotes, AnalystPick, Run } from "../lib/types";

function Minute({ pick }: { pick: AnalystPick }) {
  return (
    <>
      <span
        aria-hidden="true"
        className="inline-flex min-w-11 justify-center rounded-md border border-line-strong px-1.5 text-xs leading-5 font-semibold text-fg tabular-nums"
      >
        {`${pick.minute}'`}
      </span>
      <span className="sr-only">{`Minute ${pick.minute}, `}</span>
      <span>{THEME_WORDS[pick.theme]}</span>
    </>
  );
}

export function WhyItMattered({
  notes,
  run,
  headingId,
  where,
  lead,
  seekFor,
  className,
}: {
  notes: AnalystNotes;
  run: Run;
  headingId: string;
  where: "recap" | "match-centre";
  lead: string;
  /* On the Match Centre: what moves the replay to a pick's event, or null when the loaded event file does not hold it. */
  seekFor?: (pick: AnalystPick) => (() => void) | null;
  className?: string;
}) {
  const allAi = aiWritten(notes);
  const key = clubKey(notes, run);
  const Heading = where === "recap" ? "h2" : "h3";
  return (
    <section
      aria-labelledby={headingId}
      data-why={where}
      className={cn(
        where === "recap" ? "rounded-xl border border-line bg-surface p-4 shadow-card sm:p-5" : "rounded-lg border border-line bg-surface-2 p-3",
        className,
      )}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Heading id={headingId} className="text-sm font-semibold text-fg">
          Why it mattered: the turning points
        </Heading>
        {allAi ? <Badge tone="info">AI-written</Badge> : null}
      </div>
      <p className="mt-1 text-xs leading-5 text-muted">{lead}</p>
      <ol className="mt-3 space-y-2.5">
        {notes.picks.map((pick, i) => {
          const seek = seekFor ? seekFor(pick) : null;
          return (
            <li key={`${i}-${pick.event_id}`} data-why-pick={pick.event_id}>
              <p className="flex flex-wrap items-center gap-2 text-sm font-medium text-fg">
                {seek ? (
                  <button
                    type="button"
                    onClick={seek}
                    className="inline-flex items-center gap-2 rounded-md text-left transition-colors hover:text-link focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
                  >
                    <Minute pick={pick} />
                    <span className="sr-only">: move the replay to this moment</span>
                  </button>
                ) : (
                  <span className="inline-flex items-center gap-2">
                    <Minute pick={pick} />
                  </span>
                )}
                {allAi ? null : <Badge tone={pick.author === "model" ? "info" : "neutral"}>{pickAuthorWords(pick)}</Badge>}
              </p>
              <p className="mt-0.5 text-sm leading-6 text-fg">{pick.why}</p>
            </li>
          );
        })}
      </ol>
      {key ? <p className="mt-3 text-xs leading-5 text-muted">{key}</p> : null}
      <p className={cn("text-xs leading-5 text-muted", key ? "mt-1" : "mt-3")}>{analystByline(notes, run)}</p>
      <details className="mt-1.5 text-xs">
        <summary className="cursor-pointer text-link select-none">How these were picked and checked</summary>
        <p className="mt-1 leading-5 text-muted">
          {analystSummary(notes)} {analystSource(notes, run)}
        </p>
      </details>
    </section>
  );
}
