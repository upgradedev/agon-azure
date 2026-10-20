/*
  Listen: the recorded run's spoken report for the edition on screen, the
  audio file the fleet sealed in the bundle (audio-<edition>.mp3), its AI
  disclosure and its captions (captions-<edition>.json, agon.captions/1) as a
  transcript. Playback state is announced politely. The captions carry no word
  timings in this run, so no line is highlighted in time with the voice and no
  single sentence is cut out of the audio; the page says so instead of
  guessing a timing.
*/
import { useEffect, useId, useRef, useState } from "react";
import { Headphones } from "lucide-react";
import { bundleFileUrl } from "../lib/runs";
import { fetchBytes } from "../lib/hash";
import { isObject } from "../lib/core";
import type { BundleManifest, Run } from "../lib/types";

export interface Captions {
  edition: string;
  language: string;
  disclosure: string;
  voice: string | null;
  sentences: { sentence_id: string; text: string }[];
  word_timings: unknown;
}

const LANGUAGE_WORDS: Record<string, string> = { en: "English", el: "Greek", es: "Spanish", fr: "French", easy: "easy-read English" };

function readCaptions(body: unknown): Captions | null {
  if (!isObject(body) || body.schema !== "agon.captions/1" || typeof body.disclosure !== "string" || !Array.isArray(body.sentences)) {
    return null;
  }
  const sentences = (body.sentences as unknown[]).filter(
    (s): s is { sentence_id: string; text: string } => isObject(s) && typeof s.sentence_id === "string" && typeof s.text === "string",
  );
  return {
    edition: String(body.edition ?? ""),
    language: String(body.language ?? "en"),
    disclosure: body.disclosure,
    voice: typeof body.voice === "string" ? body.voice : null,
    sentences,
    word_timings: body.word_timings ?? null,
  };
}

/* What the page says about the disclosure line, whatever state the captions are in. */
export function disclosureLine(captions: Captions | null, problem: string, hasCaptions: boolean): string {
  if (captions) {
    return captions.disclosure;
  }
  if (problem) {
    return "an AI disclosure, whose words are not shown here because the captions could not be read";
  }
  if (!hasCaptions) {
    return "an AI disclosure; this run carries no captions file with its words";
  }
  return "an AI disclosure, whose words are still loading";
}

/* The note under the transcript: timings, and for Greek the goal line, said once and consistently. */
export function captionsNote(captions: Captions, edition: string): string {
  if (captions.word_timings) {
    return "This run's captions carry word timings, but this page does not read them yet, so the lines are not highlighted in time with the voice.";
  }
  const note = "The captions carry no word timings in this run, so the lines are not highlighted in time with the voice.";
  return edition === "el"
    ? `${note} The Greek goal line: the Greek audio speaks every sentence, the goals included, but without word timings the goal sentence cannot be cut out and played on its own; play the report to hear it.`
    : note;
}

export function Listen({ run, manifest, edition, active = true }: { run: Run; manifest: BundleManifest; edition: string; active?: boolean }) {
  const names = Object.keys(manifest.artifacts ?? {});
  const audio = names.find((n) => n.startsWith(`audio-${edition}.`) && n.endsWith(".mp3")) ?? null;
  const captionsName = names.includes(`captions-${edition}.json`) ? `captions-${edition}.json` : null;
  const [captions, setCaptions] = useState<Captions | null>(null);
  const [problem, setProblem] = useState("");
  const [status, setStatus] = useState("");
  const statusId = useId();
  const player = useRef<HTMLAudioElement>(null);

  /* Screens stay mounted when hidden: leaving the recap, or hiding the tab, pauses the voice. */
  useEffect(() => {
    if (!active) {
      player.current?.pause();
    }
  }, [active]);
  useEffect(() => {
    const onVisibility = () => {
      if (document.visibilityState === "hidden") {
        player.current?.pause();
      }
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  useEffect(() => {
    setCaptions(null);
    setProblem("");
    setStatus("");
    if (!captionsName) {
      return undefined;
    }
    let cancelled = false;
    fetchBytes(bundleFileUrl(run, captionsName))
      .then((f) => {
        const read = readCaptions(JSON.parse(f.text));
        if (!cancelled) {
          if (read) {
            setCaptions(read);
          } else {
            setProblem(`${captionsName} is not an agon.captions/1 file, so no transcript is shown.`);
          }
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setProblem(`The captions could not be read: ${error instanceof Error ? error.message : String(error)}.`);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [run, captionsName]);

  const language = LANGUAGE_WORDS[edition] ?? edition;
  return (
    <section aria-labelledby="h-listen" className="mt-6 rounded-xl border border-line bg-surface p-5 shadow-card sm:p-7">
      <h2 id="h-listen" className="flex items-center gap-2 text-base font-semibold text-fg">
        <Headphones className="size-5 text-accent" aria-hidden="true" />
        Listen to the {language} report
      </h2>
      {!audio ? (
        <p className="mt-2 text-sm leading-6 text-muted">
          {run.provenance === "design_preview"
            ? "The hand-written sample has no audio. A recorded run carries one spoken report per edition, sealed in the same bundle hash."
            : "This run carries no audio for this edition."}
        </p>
      ) : (
        <div className="mt-3 space-y-3">
          <p className="text-sm leading-6 text-muted">
            The spoken report the fleet sealed in this bundle ({audio}), made with a synthetic voice{captions?.voice ? ` (${captions.voice})` : ""}. It opens
            with {captions ? "this disclosure: " : ""}
            <span lang={captions?.language ?? undefined} className="text-fg">
              {disclosureLine(captions, problem, Boolean(captionsName))}
            </span>
            {captions ? "" : "."}
          </p>
          <audio
            ref={player}
            controls
            preload="none"
            src={bundleFileUrl(run, audio)}
            className="w-full"
            aria-describedby={statusId}
            onPlay={() => setStatus(`Playing the ${language} report.`)}
            onPause={(event) => {
              if (!event.currentTarget.ended) {
                setStatus("Paused.");
              }
            }}
            onEnded={() => setStatus("The report has finished.")}
          />
          <p id={statusId} aria-live="polite" className="text-xs text-muted">
            {status || "Not playing."}
          </p>
          {captions ? (
            <>
              <h3 className="text-sm font-semibold text-fg">Captions</h3>
              <ol lang={captions.language === "easy" ? "en" : captions.language} className="space-y-1.5 text-sm leading-6 text-fg">
                {captions.sentences.map((s) => (
                  <li key={s.sentence_id} className="rounded-lg bg-surface-2 px-3 py-1.5">
                    {s.text}
                  </li>
                ))}
              </ol>
              <p className="text-xs leading-5 text-muted">{captionsNote(captions, edition)}</p>
            </>
          ) : problem ? (
            <p className="text-sm text-muted">{problem}</p>
          ) : captionsName ? (
            <p className="text-sm text-muted">Loading the captions.</p>
          ) : (
            <p className="text-sm text-muted">This run carries no captions for this edition.</p>
          )}
        </div>
      )}
    </section>
  );
}
