/*
  "Your view": the viewer's own way through the match, remembered per browser
  (hooks/preferences). Fan or Analyst mode, a favourite club and player,
  following that player, one metric, and a language for the report editions
  and any timed cues on the replay. Every control is a native form control with a visible
  label, so it works by keyboard and with a screen reader.
*/
import { useId } from "react";
import { Segmented } from "../ui/segmented";
import { LANGUAGES, METRICS, setPreferences, type Language, type MetricId, type Preferences, type ViewMode } from "../../hooks/preferences";
import type { MatchFile } from "../../lib/match";
import { METRIC_WORDS } from "./analysis";

export const LANGUAGE_WORDS: Record<Language, string> = { en: "EN", el: "EL", es: "ES", fr: "FR", easy: "Easy" };

const MODE_HINTS: Record<ViewMode, string> = {
  fan: "Fan mode tells the match in plain words: the big moments as they happen, a few simple stats and the star player.",
  analyst: "Analyst mode shows every chart, the full team scorecard, every player, the pressure readout and the ids from the event log.",
};

const selectClass =
  "mt-1 block w-full rounded-lg border border-line-strong bg-surface px-2.5 py-1.5 text-sm text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus";

export function YourView({ prefs, file, clubs }: { prefs: Preferences; file: MatchFile | null; clubs: { club_id: string; name: string }[] }) {
  const ids = { mode: useId(), club: useId(), player: useId(), follow: useId(), followHint: useId(), metric: useId(), metricHint: useId(), lang: useId() };
  const players = file ? file.players : [];
  const known = prefs.player !== null && players.some((p) => p.player_id === prefs.player);
  const clubHere = prefs.club === null || clubs.some((c) => c.club_id === prefs.club);
  return (
    <div className="space-y-5">
      <div>
        <Segmented<ViewMode>
          name="view-mode"
          legend="Mode"
          value={prefs.mode}
          onChange={(mode) => setPreferences({ mode })}
          describedBy={ids.mode}
          options={[
            { value: "fan", label: "Fan" },
            { value: "analyst", label: "Analyst" },
          ]}
        />
        <p id={ids.mode} className="mt-1.5 text-xs leading-5 text-muted" aria-live="polite">
          {MODE_HINTS[prefs.mode]}
        </p>
      </div>

      <div>
        <label htmlFor={ids.club} className="text-sm font-medium text-fg">
          Favourite club
        </label>
        <select id={ids.club} className={selectClass} value={clubHere ? prefs.club ?? "" : ""} onChange={(event) => setPreferences({ club: event.target.value || null })}>
          <option value="">No favourite</option>
          {clubs.map((c) => (
            <option key={c.club_id} value={c.club_id}>
              {c.name}
            </option>
          ))}
        </select>
        {!clubHere ? <p className="mt-1 text-xs text-muted">Your favourite club does not play in this match.</p> : null}
      </div>

      <div>
        <label htmlFor={ids.player} className="text-sm font-medium text-fg">
          Favourite player
        </label>
        <select
          id={ids.player}
          className={selectClass}
          value={known ? prefs.player ?? "" : ""}
          disabled={!file}
          aria-describedby={file ? undefined : ids.followHint}
          onChange={(event) => setPreferences({ player: event.target.value || null })}
        >
          <option value="">No favourite</option>
          {file
            ? (["home", "away"] as const).map((side) => (
                <optgroup key={side} label={`${file.clubs[side].name} (${side})`}>
                  {players
                    .filter((p) => p.side === side)
                    .map((p) => (
                      <option key={p.player_id} value={p.player_id}>
                        {p.shirt} {p.name}
                      </option>
                    ))}
                </optgroup>
              ))
            : null}
        </select>
      </div>

      <div className="flex items-start gap-2.5">
        <input
          id={ids.follow}
          type="checkbox"
          className="mt-1 size-4 shrink-0 accent-primary"
          checked={prefs.focus && known}
          disabled={!known}
          aria-describedby={ids.followHint}
          onChange={(event) => setPreferences({ focus: event.target.checked })}
        />
        <div className="min-w-0">
          <label htmlFor={ids.follow} className="text-sm font-medium text-fg">
            Follow this player through the match
          </label>
          <p id={ids.followHint} className="text-xs leading-5 text-muted">
            {file
              ? known
                ? "Shows their scorecard, pass quality, heat zones and moments, and dims everyone else on the charts."
                : "Choose a favourite player in this match to follow them."
              : "Players appear once the match analysis has loaded."}
          </p>
        </div>
      </div>

      <div>
        <label htmlFor={ids.metric} className="text-sm font-medium text-fg">
          One metric I care about
        </label>
        <select
          id={ids.metric}
          className={selectClass}
          value={prefs.metric ?? ""}
          aria-describedby={ids.metricHint}
          onChange={(event) => setPreferences({ metric: (event.target.value || null) as MetricId | null })}
        >
          <option value="">All of them</option>
          {METRICS.map((m) => (
            <option key={m} value={m}>
              {METRIC_WORDS[m]}
            </option>
          ))}
        </select>
        <p id={ids.metricHint} className="mt-1 text-xs leading-5 text-muted">
          Tells the whole match through that one number instead of every chart.
        </p>
      </div>

      <div>
        <Segmented<Language>
          name="view-language"
          legend="Language"
          value={prefs.language}
          onChange={(language) => setPreferences({ language })}
          describedBy={ids.lang}
          options={LANGUAGES.map((l) => ({ value: l, label: LANGUAGE_WORDS[l] }))}
        />
        <p id={ids.lang} className="mt-1.5 text-xs leading-5 text-muted">
          Opens the report in that edition (English, Greek, Spanish, French or easy-read English) and picks the language of timed cues on the replay, when a cue file exists; the console&apos;s own
          words stay in English.
        </p>
      </div>
    </div>
  );
}
