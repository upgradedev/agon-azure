/*
  S4 Recap: the club's public report, one tab per edition (EN, EL, ES, FR,
  Easy-read). The text is rendered from the sealed edition files, the same
  bytes Verify hashes, never from a copy.

  Every sentence is a button that opens the Why drawer: the events it cites,
  its claims, the gate verdicts, who wrote it and who answers for it. The
  edition label says "approved" only on an approved pipeline run whose
  approval names this bundle hash (isApproved, editionLabel).

  Tabs follow the ARIA tabs pattern by hand (arrow keys, Home, End): a Radix
  tab panel prerenders an inline style, which the CSP would block.
*/
import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { ArrowRight } from "lucide-react";
import { Badge } from "../components/ui/badge";
import { Dialog } from "../components/ui/dialog";
import { Notice } from "../components/ui/notice";
import { LabelLine, ScreenHeader, SeedNotice, StepNav, View } from "../components/screen";
import { editionLabel, isApproved, matchLine, sentenceById, why, type Why } from "../lib/runs";
import { usePreferences } from "../hooks/preferences";
import { Listen } from "../components/listen";
import { hrefFor, type Route } from "../lib/router";
import { cn } from "../lib/cn";
import { VERDICT_TONE } from "./newsroom-wall";
import type { LoadedEdition, LoadedRun } from "../lib/types";

const TAB_LABELS: Record<string, string> = { en: "EN", el: "EL", es: "ES", fr: "FR", easy: "Easy-read" };

interface Props {
  active: boolean;
  route: Route;
  loaded: LoadedRun | null;
  error: string | null;
  blownSeed: number | null;
}

interface Open {
  edition: string;
  index: number;
}

function Row({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div className="grid gap-1 border-b border-line py-2.5 last:border-b-0 sm:grid-cols-[9rem_minmax(0,1fr)] sm:gap-3">
      <dt className="text-xs font-semibold tracking-wide text-muted uppercase">{term}</dt>
      <dd className="min-w-0 text-sm leading-6 text-fg">{children}</dd>
    </div>
  );
}

function WhyBody({ w, bundle }: { w: Why; bundle: string }) {
  return (
    <>
      <dl>
        <Row term="Sentence">
          <code className="font-mono text-xs">{w.sentenceId}</code>
        </Row>
        <Row term="Events it cites">
          {w.eventIds.length ? (
            <ul className="flex flex-wrap gap-1.5">
              {w.eventIds.map((id) => (
                <li key={id}>
                  <code className="rounded-md bg-surface-2 px-1.5 py-0.5 font-mono text-xs">{id}</code>
                </li>
              ))}
            </ul>
          ) : (
            "None: a final-score claim reads the whole log."
          )}
        </Row>
        <Row term="Claim types">{w.claimTypes.length ? w.claimTypes.join(", ") : "None"}</Row>
        <Row term="Gate verdicts">
          {w.gates.length ? (
            <ul className="flex flex-wrap gap-1.5">
              {w.gates.map((g) => (
                <li key={g.gate}>
                  <Badge tone={VERDICT_TONE[g.verdict]}>
                    {g.name}: {g.words}
                  </Badge>
                </li>
              ))}
            </ul>
          ) : (
            "No verdict recorded."
          )}
        </Row>
        <Row term="Author">
          {w.author} Tag: <code className="font-mono text-xs">{w.authorTag}</code>.
        </Row>
        <Row term="Sentence type">{w.type}</Row>
        <Row term="Who answers for it">{w.scope}</Row>
        <Row term="Bundle hash">
          <code className="font-mono text-xs break-all">{bundle}</code>
        </Row>
      </dl>
      <a href={hrefFor("verify")} className="mt-4 inline-flex items-center gap-1.5 text-sm font-medium">
        Recompute the bundle hash on Verify
        <ArrowRight className="size-4" aria-hidden="true" />
      </a>
    </>
  );
}

export function Recap({ active, route, loaded, error, blownSeed }: Props) {
  const run = loaded?.run ?? null;
  const [edition, setEdition] = useState(run?.editions[0]?.edition ?? "en");
  const [open, setOpen] = useState<Open | null>(null);
  const tabRefs = useRef(new Map<string, HTMLButtonElement>());
  const opener = useRef<HTMLElement | null>(null);
  const sentenceRefs = useRef(new Map<number, HTMLElement>());
  const panelId = useId();
  const tabIdBase = useId().replace(/[^A-Za-z0-9_-]/g, "");
  const language = usePreferences().language;

  /* The viewer's language (Your view on the Match Centre) opens that edition, when the run has it. */
  useEffect(() => {
    if (run && !route.query.s && run.editions.some((e) => e.edition === language)) {
      setEdition(language);
    }
  }, [language, run, route.query.s]);

  /* #/recap/<run>?s=en-05 opens that sentence's drawer on arrival. */
  useEffect(() => {
    if (!active || !loaded) {
      return;
    }
    const wanted = route.query.s;
    if (!wanted) {
      return;
    }
    const found = sentenceById(loaded, wanted);
    if (!found) {
      return;
    }
    setEdition(found.edition);
    opener.current = null;
    setOpen({ edition: found.edition, index: found.index });
  }, [active, loaded, route.query.s]);

  useEffect(() => {
    if (!active) {
      setOpen(null);
    }
  }, [active]);

  if (!run || !loaded) {
    return (
      <View view="recap" active={active}>
        <ScreenHeader view="recap" eyebrow="Recap" title="Match report" lead="The club's public report, one edition per tab.">
          <LabelLine id="recap-label" tone="plain">
            The run could not be loaded, so there is no edition to show and no label to give it.
          </LabelLine>
        </ScreenHeader>
        <Notice tone="danger">{error ?? "The run could not be loaded."}</Notice>
        <StepNav view="recap" />
      </View>
    );
  }

  const editions = run.editions.filter((e) => loaded.editions[e.edition]);
  const current: LoadedEdition | undefined = loaded.editions[edition] ?? loaded.editions[editions[0]?.edition ?? ""];
  const approved = isApproved(run);
  const tabId = (key: string) => `recap-tab-${tabIdBase}-${key}`;

  function select(key: string, focus: boolean): void {
    setEdition(key);
    if (focus) {
      tabRefs.current.get(key)?.focus();
    }
  }

  function onTabKey(event: KeyboardEvent<HTMLButtonElement>, index: number): void {
    let next = -1;
    if (event.key === "ArrowRight") {
      next = (index + 1) % editions.length;
    } else if (event.key === "ArrowLeft") {
      next = (index - 1 + editions.length) % editions.length;
    } else if (event.key === "Home") {
      next = 0;
    } else if (event.key === "End") {
      next = editions.length - 1;
    }
    if (next !== -1) {
      event.preventDefault();
      select(editions[next].edition, true);
    }
  }

  const openEdition = open ? loaded.editions[open.edition] : undefined;
  const openSentence = open && openEdition ? openEdition.data.sentences[open.index] : undefined;
  const w = open && openEdition && openSentence ? why(run, openEdition.meta, openSentence, open.index) : null;

  return (
    <View view="recap" active={active}>
      <ScreenHeader
        view="recap"
        eyebrow="Recap"
        title="Match report"
        lead="The club's public report in every edition; choose any sentence to see why it says what it says."
      >
        <LabelLine id="recap-label" tone={approved ? "approved" : run.provenance === "design_preview" ? "preview" : "plain"}>
          {editionLabel(run)}
        </LabelLine>
      </ScreenHeader>

      <SeedNotice blownSeed={blownSeed} run={run} />
      <p className="mb-4 text-sm text-muted">{matchLine(run)}</p>

      <div className="max-w-3xl">
        <div role="tablist" aria-label="Editions" className="relative flex gap-1 overflow-x-auto rounded-lg border border-line bg-surface-2 p-1">
          {editions.map((e, i) => {
            const selected = current?.meta.edition === e.edition;
            return (
              <button
                key={e.edition}
                ref={(node) => {
                  if (node) {
                    tabRefs.current.set(e.edition, node);
                  } else {
                    tabRefs.current.delete(e.edition);
                  }
                }}
                type="button"
                role="tab"
                id={tabId(e.edition)}
                aria-selected={selected}
                aria-controls={panelId}
                tabIndex={selected ? 0 : -1}
                onClick={() => select(e.edition, false)}
                onKeyDown={(event) => onTabKey(event, i)}
                className={cn(
                  "shrink-0 rounded-md px-3.5 py-1.5 text-sm font-medium transition-colors",
                  "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus",
                  selected ? "bg-surface text-fg shadow-card" : "text-muted hover:text-fg",
                )}
              >
                {TAB_LABELS[e.edition] ?? e.name}
                <span className="sr-only">, {e.name}</span>
              </button>
            );
          })}
        </div>

        <div
          role="tabpanel"
          id={panelId}
          aria-labelledby={current ? tabId(current.meta.edition) : undefined}
          tabIndex={0}
          className="mt-4 rounded-xl border border-line bg-surface p-5 shadow-card focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus sm:p-7"
        >
          {current ? (
            <>
              <div className="mb-4 flex flex-wrap items-center gap-2">
                <Badge tone="neutral">{current.meta.name}</Badge>
                <Badge tone={loaded.manifest.attested_scope.attested.includes(current.meta.file) ? "success" : "neutral"}>
                  {loaded.manifest.attested_scope.attested.includes(current.meta.file) ? "Attested by the officer" : "Gate-cleared only"}
                </Badge>
              </div>
              <p className="sr-only">Each sentence below is a link that opens why it says what it says.</p>
              <p lang={current.data.language} className="prose-reading">
                {current.data.sentences.map((sentence, index) => (
                  <span key={sentence.sentence_id}>
                    <a
                      ref={(node) => {
                        if (node) {
                          sentenceRefs.current.set(index, node);
                        } else {
                          sentenceRefs.current.delete(index);
                        }
                      }}
                      href={hrefFor("recap", run.run_id, { s: sentence.sentence_id })}
                      aria-haspopup="dialog"
                      onClick={(event) => {
                        event.preventDefault();
                        opener.current = event.currentTarget;
                        setOpen({ edition: current.meta.edition, index });
                      }}
                      className={cn(
                        "rounded-sm text-fg decoration-line-strong decoration-dotted decoration-1 underline-offset-[6px] transition-colors",
                        "hover:bg-accent-bg hover:decoration-primary hover:decoration-solid focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus",
                      )}
                    >
                      {sentence.text}
                    </a>{" "}
                  </span>
                ))}
              </p>
              <p className="mt-5 border-t border-line pt-4 text-xs leading-5 text-muted">
                Choose any sentence to see the events it cites, its claims, the gate verdicts and who answers for it.
              </p>
            </>
          ) : (
            <p className="text-sm text-muted">This run lists no edition that could be loaded, so there is nothing to read.</p>
          )}
        </div>
        {current ? <Listen run={run} manifest={loaded.manifest} edition={current.meta.edition} active={active} /> : null}
      </div>

      <Dialog
        open={Boolean(w)}
        onOpenChange={(value) => {
          if (!value) {
            setOpen(null);
          }
        }}
        placement="sheet"
        returnFocusRef={opener.current ? opener : open ? { current: sentenceRefs.current.get(open.index) ?? null } : undefined}
        eyebrow={openEdition ? <Badge tone="neutral">{openEdition.meta.name}</Badge> : null}
        title="Why this sentence says this"
        description={
          openSentence ? (
            <span lang={openEdition?.data.language} className="block text-fg italic">
              {openSentence.text}
            </span>
          ) : (
            ""
          )
        }
        closeLabel="Close the Why drawer"
      >
        {w ? <WhyBody w={w} bundle={run.bundle.sha256} /> : null}
      </Dialog>

      <StepNav view="recap" param={run.run_id} />
    </View>
  );
}
