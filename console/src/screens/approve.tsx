/*
  S3 Approval: the stand-in for the Microsoft Teams card, styled like an
  Adaptive Card. Its header sentence says what it is, verbatim.

  It shows the bundle hash (with copy), the editions, what the officer attests
  versus what is gate-cleared only, the flags under the step that raised each
  one (a Fact Gate flag under the Fact Gate, the Desk Editor's own under the
  Desk Editor), the Confirm rows
  that must be ticked, and the destinations that would receive the approved
  bytes. Approve is never disabled: it names any unticked Confirm row, and with
  all ticked it says in a full sentence that this card has no decision channel,
  so nothing was approved and nothing was sent; decisions on the Teams card
  reach the verifier through a queue and the decision intake job.
*/
import { useId, useRef, useState } from "react";
import { ArrowRight, Inbox, ShieldCheck } from "lucide-react";
import { Card, CardBody, CardHeader, CardTitle } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Notice } from "../components/ui/notice";
import { HashText } from "../components/ui/hash-text";
import { LabelLine, ScreenHeader, SeedNotice, StepNav, View } from "../components/screen";
import {
  DECIDED_CARD_SENTENCE,
  NOT_DEPLOYED_SENTENCE,
  STANDIN_HEADER,
  approveAttempt,
  attestation,
  decidedMessage,
  flagGroups,
  matchLine,
  notDeployedMessage,
  sentenceById,
  stateWords,
} from "../lib/runs";
import {
  DESIGN_PREVIEW_DESTINATIONS_SENTENCE,
  RUN_DESTINATIONS_SENTENCE,
  chipsFor,
  isPreviewOnly,
} from "../lib/destinations";
import { hrefFor } from "../lib/router";
import { cn } from "../lib/cn";
import type { DestinationId, LoadedRun } from "../lib/types";

interface Props {
  active: boolean;
  loaded: LoadedRun | null;
  error: string | null;
  blownSeed: number | null;
  onDecided: () => void;
}

type Result = { tone: "warning" | "info"; text: string } | null;

function EvidenceLink({ runId, sentenceId }: { runId: string; sentenceId: string }) {
  return (
    <a href={hrefFor("recap", runId, { s: sentenceId })} className="inline-flex items-center gap-1 text-xs font-medium whitespace-nowrap">
      Open evidence
      <ArrowRight className="size-3.5" aria-hidden="true" />
    </a>
  );
}

export function Approve({ active, loaded, error, blownSeed, onDecided }: Props) {
  const run = loaded?.run ?? null;
  const choice = run ? chipsFor(run) : null;
  /* The verifier refuses an approval with an unticked Confirm row, so an approved run shows them ticked. */
  const [ticked, setTicked] = useState<string[]>(() =>
    run && run.approval.status === "approved" ? run.confirm_rows.map((row) => row.row_id) : [],
  );
  const [missing, setMissing] = useState<string[]>([]);
  const [chosen, setChosen] = useState<DestinationId[]>(() => (choice ? choice.chips.filter((c) => c.ticked).map((c) => c.id) : []));
  const [result, setResult] = useState<Result>(null);
  const boxes = useRef(new Map<string, HTMLInputElement>());
  const confirmHint = useId();
  const destHint = useId();
  const resultId = useId();

  if (!run || !loaded || !choice) {
    return (
      <View view="approve" active={active}>
        <ScreenHeader view="approve" eyebrow="Approval" title="Approve the full-time report" lead="The officer approves one sealed hash on this card.">
          <LabelLine id="approve-label" tone="plain">
            The run could not be loaded, so this screen cannot say which run it would show.
          </LabelLine>
        </ScreenHeader>
        <Notice tone="danger">{error ?? "The run could not be loaded."}</Notice>
        <StepNav view="approve" />
      </View>
    );
  }

  const att = attestation(run, loaded.manifest);
  const waiting = run.state === "AWAITING_APPROVAL";
  const chosenLabels = choice.chips
    .filter((d) => d.offered && chosen.includes(d.id))
    .map((d) => (isPreviewOnly(d.id) ? `${d.label} (preview only, never posted)` : d.label));

  function toggleRow(rowId: string, on: boolean): void {
    setTicked((current) => (on ? [...new Set([...current, rowId])] : current.filter((id) => id !== rowId)));
    setMissing((current) => current.filter((id) => id !== rowId));
  }

  function toggleDestination(id: DestinationId, on: boolean): void {
    setChosen((current) => (on ? [...new Set([...current, id])] : current.filter((d) => d !== id)));
  }

  function onApprove(): void {
    if (!run) {
      return;
    }
    const decided = decidedMessage(run);
    if (decided) {
      setResult({ tone: "info", text: decided });
      onDecided();
      return;
    }
    const attempt = approveAttempt(run, ticked, chosenLabels);
    setMissing(attempt.missing);
    setResult({ tone: attempt.ok ? "info" : "warning", text: attempt.message });
    if (!attempt.ok && attempt.missing.length) {
      boxes.current.get(attempt.missing[0])?.focus();
      return;
    }
    onDecided();
  }

  function onChanges(): void {
    if (!run) {
      return;
    }
    setResult({ tone: "info", text: decidedMessage(run) ?? notDeployedMessage("changes", run) });
    onDecided();
  }

  return (
    <View view="approve" active={active}>
      <ScreenHeader
        view="approve"
        eyebrow="Approval"
        title="Approve the full-time report"
        lead="The media officer approves one sealed hash; this card shows what that hash covers, what a person must confirm, and where the approved bytes would go."
      >
        <LabelLine id="approve-label" tone={run.provenance === "design_preview" ? "preview" : "plain"}>
          {run.label}
        </LabelLine>
      </ScreenHeader>

      <SeedNotice blownSeed={blownSeed} run={run} />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,42rem)_minmax(0,1fr)]">
        <article aria-labelledby="acard-title" className="min-w-0 overflow-hidden rounded-xl border border-line bg-surface shadow-raised">
          <p className="border-b border-line bg-info-bg px-5 py-2.5 text-sm leading-6 font-medium text-info">{STANDIN_HEADER}</p>

          <header className="flex items-center gap-3 px-5 pt-5">
            <span className="inline-flex size-9 items-center justify-center rounded-lg bg-primary text-on-primary" aria-hidden="true">
              <Inbox className="size-5" />
            </span>
            <div className="min-w-0">
              <p className="text-xs font-medium text-muted">Agon newsroom</p>
              <h2 id="acard-title" className="text-base font-semibold tracking-tight text-fg">
                Full-time report ready for approval
              </h2>
            </div>
          </header>

          <div className="space-y-5 px-5 pt-4 pb-5">
            <p className="text-sm text-fg">
              {matchLine(run)} Bundle state: {stateWords(run.state)}.
            </p>
            {!waiting ? (
              <Notice tone="warning">This run is not waiting for approval: its bundle state is {stateWords(run.state)}.</Notice>
            ) : null}

            <div>
              <p className="mb-1.5 text-xs font-semibold tracking-wide text-muted uppercase">Bundle hash</p>
              <HashText hash={run.bundle.sha256} label="Bundle hash" id="acard-hash" />
            </div>

            <div>
              <p className="mb-1.5 text-xs font-semibold tracking-wide text-muted uppercase">Editions in this bundle</p>
              <ul className="flex flex-wrap gap-1.5">
                {run.editions.map((e) => {
                  const attested = loaded.manifest.attested_scope.attested.includes(e.file);
                  return (
                    <li key={e.edition}>
                      <Badge tone={attested ? "success" : "neutral"}>
                        {e.name}, {attested ? "attested" : "gate-cleared"}
                      </Badge>
                    </li>
                  );
                })}
              </ul>
            </div>

            <div className="grid gap-2 sm:grid-cols-2">
              <p className="flex items-start gap-2 rounded-lg border border-accent/30 bg-accent-bg px-3 py-2 text-sm font-medium text-accent">
                <ShieldCheck className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                {att.attesting}
              </p>
              <p className="rounded-lg border border-line bg-surface-2 px-3 py-2 text-sm text-fg">{att.cleared}</p>
            </div>

            {flagGroups(run).map((group) => (
              <div key={group.name}>
                <p className="mb-1.5 text-xs font-semibold tracking-wide text-muted uppercase">{`${group.name} flags`}</p>
                <ul className="space-y-2">
                  {group.flags.map((flag, i) => {
                    const found = sentenceById(loaded, flag.sentence_id);
                    return (
                      <li key={`${flag.sentence_id}-${i}`} className="rounded-lg border border-notice/30 bg-notice-bg px-3 py-2.5 text-sm">
                        <p className="text-notice">{flag.text}</p>
                        {found ? <q className="mt-1 block text-fg italic">{found.sentence.text}</q> : null}
                        <div className="mt-1.5">
                          <EvidenceLink runId={run.run_id} sentenceId={flag.sentence_id} />
                        </div>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))}

            <fieldset aria-describedby={confirmHint}>
              <legend className="mb-1 text-xs font-semibold tracking-wide text-muted uppercase">Confirm rows</legend>
              <p id={confirmHint} className="mb-2 text-xs leading-5 text-muted">
                Code cannot check these sentences against the event log. Tick each one you have confirmed before approving.
              </p>
              <ul className="space-y-2">
                {run.confirm_rows.map((row) => {
                  const found = sentenceById(loaded, row.sentence_id);
                  const isMissing = missing.includes(row.row_id);
                  const inputId = `confirm-${row.row_id}`;
                  return (
                    <li
                      key={row.row_id}
                      className={cn(
                        "flex items-start gap-3 rounded-lg border px-3 py-2.5 transition-colors",
                        isMissing ? "border-danger bg-danger-bg" : ticked.includes(row.row_id) ? "border-accent/40 bg-accent-bg" : "border-line",
                      )}
                    >
                      <input
                        ref={(node) => {
                          if (node) {
                            boxes.current.set(row.row_id, node);
                          } else {
                            boxes.current.delete(row.row_id);
                          }
                        }}
                        id={inputId}
                        type="checkbox"
                        checked={ticked.includes(row.row_id)}
                        onChange={(event) => toggleRow(row.row_id, event.target.checked)}
                        className="mt-1 size-4 shrink-0 accent-primary"
                      />
                      <div className="min-w-0 flex-1">
                        <label htmlFor={inputId} className="text-sm font-semibold text-fg">
                          Confirm {row.label}
                          {isMissing ? <span className="ml-2 font-medium text-danger">Not ticked yet</span> : null}
                        </label>
                        {found ? <q className="mt-0.5 block text-sm text-muted italic">{found.sentence.text}</q> : null}
                        <div className="mt-1">
                          <EvidenceLink runId={run.run_id} sentenceId={row.sentence_id} />
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ul>
            </fieldset>

            <fieldset aria-describedby={destHint}>
              <legend className="mb-1 text-xs font-semibold tracking-wide text-muted uppercase">Destinations for the approved bytes</legend>
              <p id={destHint} className="mb-2 text-xs leading-5 text-muted">
                {choice.fromRun ? RUN_DESTINATIONS_SENTENCE : DESIGN_PREVIEW_DESTINATIONS_SENTENCE}
              </p>
              <ul className="grid gap-2 sm:grid-cols-2">
                {choice.chips.map((d) => {
                  const inputId = `destination-${d.id}`;
                  const noteId = `destination-${d.id}-note`;
                  const preview = isPreviewOnly(d.id);
                  const on = d.offered && chosen.includes(d.id);
                  const described = d.note || d.status ? noteId : undefined;
                  return (
                    <li key={d.id}>
                      <label
                        htmlFor={inputId}
                        className={cn(
                          "flex h-full items-start gap-2.5 rounded-lg border px-3 py-2.5 transition-colors",
                          d.offered ? "cursor-pointer" : "cursor-not-allowed bg-surface-2",
                          on ? "border-primary bg-accent-bg" : d.offered ? "border-line hover:bg-surface-2" : "border-line",
                          "has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-focus",
                        )}
                      >
                        <input
                          id={inputId}
                          type="checkbox"
                          checked={on}
                          disabled={!d.offered}
                          onChange={(event) => toggleDestination(d.id, event.target.checked)}
                          aria-describedby={described}
                          className="mt-0.5 size-4 shrink-0 accent-primary focus-visible:outline-none"
                        />
                        <span className="min-w-0">
                          <span className="flex flex-wrap items-center gap-1.5 text-sm font-medium text-fg">
                            {d.label}
                            {preview ? <Badge tone="preview">Preview</Badge> : null}
                          </span>
                          {described ? (
                            <span id={noteId} className="mt-0.5 block text-xs leading-5 wrap-anywhere text-muted">
                              {[d.note, d.status && d.status !== d.note ? d.status : null].filter(Boolean).join(" ")}
                            </span>
                          ) : null}
                        </span>
                      </label>
                    </li>
                  );
                })}
              </ul>
            </fieldset>

            <Notice tone="info">
              {decidedMessage(run) ? DECIDED_CARD_SENTENCE : NOT_DEPLOYED_SENTENCE}
            </Notice>
          </div>

          <footer className="flex flex-col gap-3 border-t border-line bg-surface-2 px-5 py-4">
            <p id={resultId} aria-live="polite" className={cn("text-sm leading-6", result ? "" : "sr-only")}>
              {result ? (
                <span className={cn("block rounded-lg px-3 py-2", result.tone === "warning" ? "bg-notice-bg text-notice" : "bg-info-bg text-info")}>
                  {result.text}
                </span>
              ) : (
                ""
              )}
            </p>
            <div className="flex flex-wrap gap-2">
              <Button onClick={onApprove}>Approve</Button>
              <Button variant="secondary" onClick={onChanges}>
                Request changes
              </Button>
            </div>
          </footer>
        </article>

        <aside className="min-w-0 space-y-6">
          <Card aria-labelledby="h-after">
            <CardHeader>
              <CardTitle id="h-after">What Approve would send if this card had a decision channel</CardTitle>
            </CardHeader>
            <CardBody>
              <ol className="space-y-3 text-sm leading-6 text-muted">
                <li>
                  <strong className="text-fg">The exact hash.</strong> The decision carries the bundle hash above, a single-use nonce and the ticked
                  Confirm rows, never the text.
                </li>
                <li>
                  <strong className="text-fg">One verifier.</strong> Today the Teams card&apos;s decision runs through it in the decision intake job:
                  hash, nonce unused, not expired, approver role, consumed once. The Outlook approval email (built, waiting for the owner&apos;s
                  consent), a signed link and this card are built to call the same verifier and are not deployed.
                </li>
                <li>
                  <strong className="text-fg">Resume and publish.</strong> The waiting workflow resumes from its checkpoint, and the Publisher writes
                  the sealed bundle, unchanged, to the feed archive and renders only the approved payloads for the club website and the feeds. Instagram and YouTube get previews that are not posted; the
                  newsroom and journalist messages are written, not sent.
                </li>
              </ol>
              <a href={hrefFor("how-it-works")} className="mt-4 inline-flex items-center gap-1.5 text-sm font-medium">
                See the evidence for each step
                <ArrowRight className="size-4" aria-hidden="true" />
              </a>
            </CardBody>
          </Card>
        </aside>
      </div>

      <StepNav view="approve" param={run.run_id} />
    </View>
  );
}
