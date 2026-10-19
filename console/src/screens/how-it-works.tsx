/*
  "How Agon works": the pipeline as built in the repository, stage by stage.
  Every node is a button; choosing one shows what it does, what exists today
  and its evidence (a file and line at one commit of main, or a green CI run).
  On a wide screen the details sit beside the diagram; on a phone they open
  in a sheet.
*/
import { useRef, useState } from "react";
import { ArrowDown, ChevronRight } from "lucide-react";
import { Card, CardBody, CardHeader, CardTitle } from "../components/ui/card";
import { Badge, type BadgeTone } from "../components/ui/badge";
import { Dialog } from "../components/ui/dialog";
import { EvidenceList } from "../components/evidence-list";
import { ScreenHeader, View } from "../components/screen";
import { EVIDENCE_SHA, PIPELINE, STATUS_WORDS, type NodeStatus, type PipelineNode } from "../lib/evidence";
import { REPO_URL } from "../lib/commit";
import { cn } from "../lib/cn";
import { useWide } from "../hooks/stores";

const STATUS_TONE: Record<NodeStatus, BadgeTone> = {
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
        title="From the final whistle to a verified report"
        lead="The pipeline as it is built in the public repository; choose any step to see what it does, what exists today and the file or CI run that proves it."
      />

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_26rem]">
        <ol className="space-y-2" aria-label="Pipeline stages">
          {groups.map((group, gi) => (
            <li key={group.stage}>
              <div className="rounded-xl border border-line bg-surface p-3 shadow-card">
                <p className="mb-2 px-1 text-xs font-semibold tracking-wide text-muted uppercase">
                  {gi + 1}. {group.stage}
                </p>
                <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
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
              {gi < groups.length - 1 ? (
                <div className="flex justify-center py-1" aria-hidden="true">
                  <ArrowDown className="size-4 text-line-strong" />
                </div>
              ) : null}
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
