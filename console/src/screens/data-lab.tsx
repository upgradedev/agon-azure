/*
  S8 Data Lab, for now an honest empty state. The season table, the realism
  bands view and the storylines come from the synthetic generator later; until
  they are published to this site, this page shows no number at all.
*/
import { FlaskConical } from "lucide-react";
import { Card, CardBody, CardHeader, CardTitle } from "../components/ui/card";
import { EvidenceList } from "../components/evidence-list";
import { ScreenHeader, View } from "../components/screen";
import { DATA_LAB_EVIDENCE } from "../lib/evidence";

export function DataLab({ active }: { active: boolean }) {
  return (
    <View view="data-lab" active={active}>
      <ScreenHeader
        view="data-lab"
        eyebrow="Data Lab"
        title="The synthetic season, once it is published"
        lead="This page will show the fictional season behind the matches: the league table, the realism bands the generator is held to, and the storylines a visitor can pick."
      />

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_24rem]">
        <section
          aria-labelledby="h-lab-empty"
          className="flex flex-col items-center rounded-xl border border-dashed border-line-strong bg-surface px-6 py-12 text-center"
        >
          <span className="inline-flex size-12 items-center justify-center rounded-full bg-surface-2 text-muted" aria-hidden="true">
            <FlaskConical className="size-6" />
          </span>
          <h2 id="h-lab-empty" className="mt-4 text-lg font-semibold tracking-tight text-fg">
            No season data is published yet
          </h2>
          <p className="mt-2 max-w-xl text-sm leading-6 text-muted">
            The Data Lab is empty on purpose: the season table, the realism bands view and the storylines will appear here when the synthetic
            generator publishes them to this site, and until then this page shows nothing rather than numbers nobody computed.
          </p>
        </section>

        <Card aria-labelledby="h-lab-today">
          <CardHeader>
            <CardTitle id="h-lab-today">What exists today</CardTitle>
          </CardHeader>
          <CardBody>
            <p className="mb-3 text-sm leading-6 text-muted">
              The generator and its checks are already in the public repository; the Match Centre replays its matches.
            </p>
            <EvidenceList items={DATA_LAB_EVIDENCE} />
          </CardBody>
        </Card>
      </div>
    </View>
  );
}
