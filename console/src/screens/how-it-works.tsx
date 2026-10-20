/*
  "How Agon works": the pipeline as built in the repository, stage by stage,
  and what of it ran in Azure for the recorded run. Every node is a button;
  choosing one shows what it does, what exists today and its evidence (a file
  and line at one commit of main, a green CI run, or the recorded run's
  sealed manifest on the public feed). On a wide screen the details sit beside the diagram; on a phone they open
  in a sheet.
*/
import { useRef, useState } from "react";
import { ChevronRight } from "lucide-react";
import { Card, CardBody, CardHeader, CardTitle } from "../components/ui/card";
import { Badge, type BadgeTone } from "../components/ui/badge";
import { Dialog } from "../components/ui/dialog";
import { EvidenceList } from "../components/evidence-list";
import { ScreenHeader, View } from "../components/screen";
import {
  DRILL_RUN_ID,
  EVIDENCE_SHA,
  FEED_URL,
  PIPELINE,
  RECORDED_BUNDLE,
  RECORDED_EXECUTIONS,
  STATUS_WORDS,
  type NodeStatus,
  type PipelineNode,
} from "../lib/evidence";
import { REPO_URL } from "../lib/commit";
import { cn } from "../lib/cn";
import { useWide } from "../hooks/stores";

const STATUS_TONE: Record<NodeStatus, BadgeTone> = {
  azure: "success",
  live: "success",
  ci: "info",
  repo: "neutral",
  "not-deployed": "warning",
};

function stages(nodes: PipelineNode[]): { stage: string; nodes: PipelineNode[] }[] {
  const out: { stage: string; nodes: PipelineNode[] }[] = [];
  for (const node of nodes) {
    const last = out[out.length - 1];
    if (last && last.stage === node.stage) {
      last.nodes.push(node);
    } else {
      out.push({ stage: node.stage, nodes: [node] });
    }
  }
  return out;
}

function NodeDetail({ node, headingId }: { node: PipelineNode; headingId?: string }) {
  return (
    <div className="space-y-4">
      {headingId ? (
        <div>
          <p className="eyebrow">{node.stage}</p>
          <h2 id={headingId} className="mt-1 text-lg font-semibold tracking-tight text-fg">
            {node.title}
          </h2>
        </div>
      ) : null}
      <Badge tone={STATUS_TONE[node.status]}>{STATUS_WORDS[node.status]}</Badge>
      <p className="text-sm leading-6 text-fg">{node.summary}</p>
      <p className="text-sm leading-6 text-muted">{node.statusText}</p>
      <div>
        <p className="mb-2 text-xs font-semibold tracking-wide text-muted uppercase">Evidence</p>
        <EvidenceList items={node.evidence} />
      </div>
    </div>
  );
}

export function HowItWorks({ active }: { active: boolean }) {
  const [selected, setSelected] = useState<string>(PIPELINE[0].id);
  const [sheetOpen, setSheetOpen] = useState(false);
  const wide = useWide();
  const opener = useRef<HTMLButtonElement | null>(null);
  const node = PIPELINE.find((n) => n.id === selected) ?? PIPELINE[0];
  const groups = stages(PIPELINE);

  return (
    <View view="how-it-works" active={active}>
      <ScreenHeader
        view="how-it-works"
        eyebrow="How Agon works"
        title="From the event log to a verified report"
        lead="The pipeline as it is built in the public repository and as it ran in Azure for the recorded run; choose any step to see what it does, what exists today and the file, CI run or published file that proves it."
      />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_26rem]">
        <ol aria-label="Pipeline stages" className="rounded-xl border border-line bg-surface p-4 shadow-card sm:p-5">
          {groups.map((group, gi) => (
            <li key={group.stage} className="grid grid-cols-[2rem_minmax(0,1fr)] gap-x-3">
              <div className="flex flex-col items-center" aria-hidden="true">
                <span className="inline-flex size-8 shrink-0 items-center justify-center rounded-full border border-line-strong bg-surface text-xs font-semibold text-fg tabular-nums">
                  {gi + 1}
                </span>
                {gi < groups.length - 1 ? <span className="my-1 w-px flex-1 bg-line" /> : null}
              </div>
              <div className={cn("min-w-0", gi < groups.length - 1 ? "pb-5" : "")}>
                <p className="mt-1.5 text-xs font-semibold tracking-wide text-muted uppercase">
                  <span className="sr-only">Stage {gi + 1}: </span>
                  {group.stage}
                </p>
                <ul className="mt-2 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                  {group.nodes.map((n) => {
                    const isSelected = n.id === node.id;
                    return (
                      <li key={n.id}>
                        <button
                          type="button"
                          aria-pressed={wide ? isSelected : undefined}
                          aria-haspopup={wide ? undefined : "dialog"}
                          onClick={(event) => {
                            setSelected(n.id);
                            if (!wide) {
                              opener.current = event.currentTarget;
                              setSheetOpen(true);
                            }
                          }}
                          className={cn(
                            "flex h-full w-full items-start justify-between gap-2 rounded-lg border px-3 py-2.5 text-left transition-colors",
                            "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus",
                            isSelected && wide ? "border-primary bg-accent-bg" : "border-line hover:border-line-strong hover:bg-surface-2",
                          )}
                        >
                          <span className="min-w-0">
                            <span className="block text-sm font-semibold text-fg">{n.title}</span>
                            <span className="mt-1 block">
                              <Badge tone={STATUS_TONE[n.status]}>{STATUS_WORDS[n.status]}</Badge>
                            </span>
                          </span>
                          <ChevronRight className="mt-0.5 size-4 shrink-0 text-muted" aria-hidden="true" />
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            </li>
          ))}
        </ol>

        <div className="hidden lg:block">
          <Card aria-labelledby="h-node" className="sticky top-20">
            <CardBody className="pt-5">
              <NodeDetail node={node} headingId="h-node" />
            </CardBody>
          </Card>
        </div>
      </div>

      <Card aria-labelledby="h-honest" className="mt-6">
        <CardHeader>
          <CardTitle id="h-honest">How to read the status labels</CardTitle>
        </CardHeader>
        <CardBody className="space-y-2 text-sm leading-6 text-muted">
          <p>
            "{STATUS_WORDS.azure}" means the step ran in the deployed Azure resources for the recorded run of 2026-10-19: an operator started it
            with <a href={`${REPO_URL}/actions/runs/${DRILL_RUN_ID}`}>drill run {DRILL_RUN_ID}</a>, the Container Apps job executions, read from
            Azure, are in{" "}
            <a href={`${REPO_URL}/blob/${EVIDENCE_SHA}/${RECORDED_EXECUTIONS}`} className="font-mono break-all">
              {RECORDED_EXECUTIONS}
            </a>
            , and the <a href={`${FEED_URL}${RECORDED_BUNDLE}/manifest.json`}>sealed manifest on the public feed</a> names the agents and the gate
            verdicts.
          </p>
          <p>
            "Live on this site" means a visitor can see it working here. "In the repo, tested in CI" means the code is public and a green GitHub
            Actions run exercised it; it is not a running service. "Not deployed yet" means exactly that, and this page says it in the same
            sentence wherever it applies.
          </p>
          <p>
            File links point at commit{" "}
            <a href={`${REPO_URL}/commit/${EVIDENCE_SHA}`} className="font-mono">
              {EVIDENCE_SHA.slice(0, 7)}
            </a>{" "}
            of main, so a line never moves under its link.
          </p>
        </CardBody>
      </Card>

      <Dialog
        open={sheetOpen && !wide}
        onOpenChange={setSheetOpen}
        placement="sheet"
        returnFocusRef={opener}
        eyebrow={<span className="eyebrow">{node.stage}</span>}
        title={node.title}
        description={STATUS_WORDS[node.status]}
        closeLabel="Close the details"
      >
        <NodeDetail node={node} />
      </Dialog>
    </View>
  );
}
