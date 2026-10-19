/*
  S8 Data Lab: the fictional season behind the matches, read from season/
  (agon.webseason) when the deploy has written it. Every season file is hashed
  in the browser against season/manifest.json before it is shown. Without the
  season folder the page says so in a full sentence and shows no number.
*/
import { useEffect, useRef, useState } from "react";
import { FlaskConical } from "lucide-react";
import { Card, CardBody, CardHeader, CardTitle } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import { Notice } from "../components/ui/notice";
import { EvidenceList } from "../components/evidence-list";
import { ScreenHeader, View } from "../components/screen";
import { DATA_LAB_EVIDENCE } from "../lib/evidence";
import { loadSeason, trimDecimal, type SeasonLoad, type SeasonView } from "../lib/season";

type State = { status: "idle" } | { status: "loading" } | SeasonLoad;

const COLUMNS: [string, string][] = [
  ["P", "Played"],
  ["W", "Won"],
  ["D", "Drawn"],
  ["L", "Lost"],
  ["GF", "Goals for"],
  ["GA", "Goals against"],
  ["GD", "Goal difference"],
  ["Pts", "Points"],
];

function EmptyState() {
  return (
    <section
      aria-labelledby="h-lab-empty"
      className="flex flex-col items-center rounded-xl border border-dashed border-line-strong bg-surface px-6 py-12 text-center"
    >
      <span className="inline-flex size-12 items-center justify-center rounded-full bg-surface-2 text-muted" aria-hidden="true">
        <FlaskConical className="size-6" />
      </span>
      <h2 id="h-lab-empty" className="mt-4 text-lg font-semibold tracking-tight text-fg">
        No season data is published on this site yet
      </h2>
      <p className="mt-2 max-w-xl text-sm leading-6 text-muted">
        The season table, the realism bands and the storylines appear here once the deploy writes the season files, and until then this page
        shows nothing rather than numbers nobody computed.
      </p>
    </section>
  );
}

function SeasonTables({ view }: { view: SeasonView }) {
  const bands = view.bands;
  const met = bands.passed >= bands.threshold;
  return (
    <div className="space-y-6">
      <Notice tone="success">
        Every season file below matched its sha256 in season/manifest.json, recomputed in your browser ({view.filesChecked} files). Season{" "}
        {view.season}, generator {view.generator}.
      </Notice>

      <Card aria-labelledby="h-lab-table">
        <CardHeader>
          <CardTitle id="h-lab-table">
            Final table, {view.league}
          </CardTitle>
          <Badge tone="neutral">
            Matchday {view.matchday} of {view.matchdaysTotal}
          </Badge>
        </CardHeader>
        <CardBody>
          <div className="-mx-2 overflow-x-auto px-2" tabIndex={0} role="region" aria-label="Final table, scrolls sideways on a narrow screen">
            <table className="w-full min-w-[34rem] border-collapse text-sm tabular-nums">
              <caption className="sr-only">
                Final table of season {view.season} after matchday {view.matchday}, fictional clubs.
              </caption>
              <thead>
                <tr className="border-b border-line text-xs text-muted">
                  <th scope="col" className="py-2 pr-2 text-left font-medium">
                    Pos
                  </th>
                  <th scope="col" className="py-2 pr-2 text-left font-medium">
                    Club
                  </th>
                  {COLUMNS.map(([short, long]) => (
                    <th key={short} scope="col" className="px-1.5 py-2 text-right font-medium">
                      <span aria-hidden="true">{short}</span>
                      <span className="sr-only">{long}</span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {view.table.map((row) => (
                  <tr key={row.club_id} className="border-b border-line last:border-b-0">
                    <td className="py-2 pr-2 text-muted">{row.position}</td>
                    <th scope="row" className="py-2 pr-2 text-left font-medium text-fg">
                      {view.clubs[row.club_id]?.name ?? row.club_id}
                    </th>
                    <td className="px-1.5 py-2 text-right">{row.played}</td>
                    <td className="px-1.5 py-2 text-right">{row.won}</td>
                    <td className="px-1.5 py-2 text-right">{row.drawn}</td>
                    <td className="px-1.5 py-2 text-right">{row.lost}</td>
                    <td className="px-1.5 py-2 text-right">{row.goals_for}</td>
                    <td className="px-1.5 py-2 text-right">{row.goals_against}</td>
                    <td className="px-1.5 py-2 text-right">{row.goal_difference > 0 ? `+${row.goal_difference}` : row.goal_difference}</td>
                    <td className="px-1.5 py-2 text-right font-semibold text-fg">{row.points}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {view.tiebreak ? <p className="mt-3 text-xs leading-5 text-muted">Order: {view.tiebreak}.</p> : null}
        </CardBody>
      </Card>

      <Card aria-labelledby="h-lab-bands">
        <CardHeader>
          <CardTitle id="h-lab-bands">Realism bands on held-out seeds</CardTitle>
          <Badge tone={met ? "success" : "warning"}>{bands.label}</Badge>
        </CardHeader>
        <CardBody>
          <p className="text-sm leading-6 text-muted">
            {bands.passed} of {bands.of} bands pass on seeds {bands.seeds}; the target is {bands.threshold}, so this measurement{" "}
            {met ? "meets it" : "falls short of it"}. The bands are ranges chosen as sanity checks for a synthetic generator, not claims about
            any real league.
          </p>
          <ul className="mt-3 divide-y divide-line rounded-lg border border-line">
            {bands.rows.map((row) => (
              <li key={row.id} className="flex flex-col gap-1 px-3 py-2.5 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-fg">{row.statistic}</p>
                  <p className="text-xs text-muted">
                    Band {trimDecimal(row.lo)} to {trimDecimal(row.hi)} {row.unit}; observed {row.observed === null ? "not measurable" : trimDecimal(row.observed)}
                  </p>
                </div>
                <Badge tone={row.status === "PASS" ? "success" : row.status === "FAIL" ? "danger" : "neutral"} className="self-start sm:self-auto">
                  {row.status === "PASS" ? "Pass" : row.status === "FAIL" ? "Fail" : "Not measurable"}
                </Badge>
              </li>
            ))}
          </ul>
          {bands.command ? (
            <p className="mt-3 text-xs text-muted">
              Re-measure with <code className="font-mono">{bands.command}</code>.
            </p>
          ) : null}
        </CardBody>
      </Card>
    </div>
  );
}

export function DataLab({ active }: { active: boolean }) {
  const [state, setState] = useState<State>({ status: "idle" });
  const started = useRef(false);

  useEffect(() => {
    if (!active || started.current) {
      return;
    }
    started.current = true;
    setState({ status: "loading" });
    void loadSeason().then(setState);
  }, [active]);

  return (
    <View view="data-lab" active={active}>
      <ScreenHeader
        view="data-lab"
        eyebrow="Data Lab"
        title="The fictional season behind the matches"
        lead="The league table and the realism bands of the synthetic season, read from the season files this site publishes and checked against their hashes in your browser."
      />

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_24rem]">
        <div className="min-w-0" data-state={state.status}>
          {state.status === "idle" || state.status === "loading" ? (
            <Notice tone="loading">Looking for the season files on this site and hashing them in your browser.</Notice>
          ) : null}
          {state.status === "absent" ? <EmptyState /> : null}
          {state.status === "error" ? <Notice tone="danger">The season files could not be used: {state.message}.</Notice> : null}
          {state.status === "ready" ? <SeasonTables view={state.view} /> : null}
        </div>

        <Card aria-labelledby="h-lab-today">
          <CardHeader>
            <CardTitle id="h-lab-today">Where these numbers come from</CardTitle>
          </CardHeader>
          <CardBody>
            <p className="mb-3 text-sm leading-6 text-muted">
              A seeded generator in the public repository plays every match; the same seed always gives the same bytes. The Match Centre replays
              three of its matches.
            </p>
            <EvidenceList items={DATA_LAB_EVIDENCE} />
          </CardBody>
        </Card>
      </div>
    </View>
  );
}
