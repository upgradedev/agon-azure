/*
  Pieces every screen shares: the view section, the header that answers
  "what am I looking at" in one sentence, the next-step link along the judge
  path, and the sentence that appears when the match a visitor blew full time
  on is not the match of the run on screen.
*/
import type { ReactNode } from "react";
import { ArrowLeft, ArrowRight } from "lucide-react";
import { ButtonLink } from "./ui/button";
import { Notice } from "./ui/notice";
import { PATH_STEPS } from "./shell";
import { hrefFor, type ViewKey } from "../lib/router";
import type { Run } from "../lib/types";

export function View({ view, active, children }: { view: ViewKey; active: boolean; children: ReactNode }) {
  return (
    <section data-view={view} hidden={!active} aria-labelledby={`h-${view}`}>
      <div className="mx-auto max-w-7xl px-4 pt-6 pb-28 sm:px-6 sm:pt-8 lg:pb-16">{children}</div>
    </section>
  );
}

export function ScreenHeader({
  view,
  eyebrow,
  title,
  lead,
  children,
}: {
  view: ViewKey;
  eyebrow: string;
  title: string;
  lead: ReactNode;
  children?: ReactNode;
}) {
  const index = PATH_STEPS.findIndex((step) => step.view === view);
  return (
    <header className="mb-6 max-w-3xl">
      <p className="eyebrow">
        {index === -1 ? eyebrow : `Step ${index + 1} of ${PATH_STEPS.length}: ${eyebrow}`}
      </p>
      <h1 id={`h-${view}`} tabIndex={-1} className="mt-2 text-2xl font-semibold tracking-tight text-balance text-fg focus:outline-none sm:text-3xl">
        {title}
      </h1>
      <p className="mt-2 text-[0.9375rem] leading-7 text-pretty text-muted">{lead}</p>
      {children}
    </header>
  );
}

export function StepNav({ view, param }: { view: ViewKey; param?: string }) {
  const index = PATH_STEPS.findIndex((step) => step.view === view);
  if (index === -1) {
    return null;
  }
  const prev = PATH_STEPS[index - 1];
  const next = PATH_STEPS[index + 1];
  const carry = (target: ViewKey) => (param && (target === "newsroom" || target === "approve" || target === "recap") ? param : "");
  return (
    <nav aria-label="Judge path, next step" className="mt-10 flex flex-col-reverse gap-3 border-t border-line pt-6 sm:flex-row sm:items-center sm:justify-between">
      {prev ? (
        <a href={hrefFor(prev.view, carry(prev.view))} className="inline-flex items-center gap-1.5 text-sm font-medium no-underline">
          <ArrowLeft className="size-4" aria-hidden="true" />
          Back: {prev.long}
        </a>
      ) : (
        <span />
      )}
      {next ? (
        <ButtonLink href={hrefFor(next.view, carry(next.view))} variant="primary">
          Next: {next.long}
          <ArrowRight aria-hidden="true" />
        </ButtonLink>
      ) : (
        <ButtonLink href={hrefFor("how-it-works")} variant="secondary">
          See how Agon works
          <ArrowRight aria-hidden="true" />
        </ButtonLink>
      )}
    </nav>
  );
}

/* Shown on the Wall, the card and the Recap when the visitor blew full time on
   a seed that has no recorded run, so the screen says whose run it shows. */
export function SeedNotice({ blownSeed, run }: { blownSeed: number | null; run: Run | null }) {
  if (blownSeed === null || !run || blownSeed === run.seed) {
    return null;
  }
  return (
    <Notice tone="warning" className="mb-5">
      {run.provenance === "design_preview"
        ? `You blew full time on seed ${blownSeed}, but the only run on this site is the hand-written sample for seed ${run.seed}, so this screen shows that seed ${run.seed} sample instead.`
        : `You blew full time on seed ${blownSeed}, but no recorded run on this site is for that seed, so this screen shows the recorded run for seed ${run.seed} instead.`}
    </Notice>
  );
}

export function LabelLine({ id, tone, children }: { id: string; tone: "preview" | "approved" | "plain"; children: string }) {
  const toneClass =
    tone === "preview"
      ? "border-preview/25 bg-preview-bg text-preview"
      : tone === "approved"
        ? "border-accent/25 bg-accent-bg text-accent"
        : "border-line bg-surface-2 text-fg";
  return (
    <p className={`mt-4 rounded-lg border px-3.5 py-2.5 text-sm leading-6 font-medium ${toneClass}`} id={id}>
      {children}
    </p>
  );
}
