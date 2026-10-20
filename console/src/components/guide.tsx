/*
  The first-run guide: three steps of the judge path, each one sentence, with
  a tick as the visitor completes it. Skipping is remembered per browser
  (wrapped localStorage); the footer link brings it back. public/boot.js hides
  it before paint for a visitor who skipped, so it never flashes in.
*/
import { Check } from "lucide-react";
import { Button } from "./ui/button";
import { cn } from "../lib/cn";
import { setGuideSkipped, useGuideSkipped } from "../hooks/stores";

export interface Progress {
  blown: boolean;
  decided: boolean;
  verified: boolean;
}

const STEPS: { key: keyof Progress; title: string; text: string }[] = [
  {
    key: "blown",
    title: "Pick a match, blow full time",
    text: "Choose a seeded fictional match, replay it if you like, then press Blow full time to replay its newsroom run.",
  },
  {
    key: "decided",
    title: "Watch the newsroom, then approve",
    text: "The Newsroom Wall shows each desk and every check; the stand-in card asks you to approve one sealed hash.",
  },
  {
    key: "verified",
    title: "Read the editions, verify the hash",
    text: "Open any sentence to see its evidence, then let Verify recompute the bundle hash and every sealed file in your own browser.",
  },
];

export function GuideCard({ progress }: { progress: Progress }) {
  const skipped = useGuideSkipped();
  if (skipped) {
    return null;
  }
  return (
    <section
      data-guide-card=""
      aria-labelledby="guide-title"
      className="mb-6 rounded-xl border border-line bg-surface p-4 shadow-card motion-safe:animate-rise sm:p-5"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 id="guide-title" className="text-[0.9375rem] font-semibold tracking-tight text-fg">
            New here? Three steps
          </h2>
          <p className="mt-0.5 text-sm text-muted">Everything runs in this page; nothing you do here is sent anywhere.</p>
        </div>
        <Button variant="ghost" size="sm" onClick={() => setGuideSkipped(true)}>
          Skip the guide
        </Button>
      </div>
      <ol className="mt-4 grid gap-3 sm:grid-cols-3">
        {STEPS.map((step, i) => {
          const done = progress[step.key];
          return (
            <li key={step.key} className={cn("flex gap-3 rounded-lg border p-3", done ? "border-accent/30 bg-accent-bg" : "border-line bg-surface-2")}>
              <span
                aria-hidden="true"
                className={cn(
                  "mt-0.5 inline-flex size-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold",
                  done ? "bg-primary text-on-primary" : "border border-line-strong bg-surface text-fg",
                )}
              >
                {done ? <Check className="size-3.5" /> : i + 1}
              </span>
              <div className="min-w-0">
                <p className="text-sm font-semibold text-fg">
                  {step.title}
                  {done ? <span className="sr-only">, done</span> : null}
                </p>
                <p className="mt-0.5 text-[0.8125rem] leading-5 text-muted">{step.text}</p>
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
