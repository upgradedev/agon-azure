/*
  About: who Agon is for, what is real on this site, the glossary in full, the
  data and names rule, and the open-source libraries the console is built on.
*/
import { Card, CardBody, CardHeader, CardTitle } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import { ScreenHeader, View } from "../components/screen";
import { GLOSSARY, GLOSSARY_ORDER } from "../lib/glossary";
import { REPO_URL } from "../lib/commit";
import { DESIGN_PREVIEW_LABEL } from "../lib/runs";
import type { RunsData } from "../lib/types";

const LIBRARIES = [
  "React and React DOM",
  "TypeScript",
  "Vite with its React plugin",
  "Tailwind CSS",
  "Radix UI primitives (dialog, popover, tooltip)",
  "Lucide icons",
  "Inter, through Fontsource",
  "Vitest, Playwright and axe-core in CI",
];

export function About({ active, runs }: { active: boolean; runs: RunsData | null }) {
  const shown = runs ? runs.runs[runs.defaultRunId]?.run ?? null : null;
  const recorded = shown !== null && shown.provenance === "pipeline";
  return (
    <View view="about" active={active}>
      <ScreenHeader
        view="about"
        eyebrow="About"
        title="What Agon is for"
        lead="Agon is being built as the full-time newsroom for a small club media office: agents write and translate the report, code checks it and seals it, and nothing is published until the media officer approves that exact sealed bundle in Microsoft Teams."
      />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card aria-labelledby="h-who">
          <CardHeader>
            <CardTitle id="h-who">Who it is for</CardTitle>
          </CardHeader>
          <CardBody className="text-sm leading-6 text-muted">
            The media and supporter office of a professional football club. In the hour after the whistle, a team of one to three people must
            publish the match report, bulletins for supporters abroad, accessible versions and an item for the club website.
          </CardBody>
        </Card>

        <Card aria-labelledby="h-real">
          <CardHeader>
            <CardTitle id="h-real">What is real on this site</CardTitle>
            <Badge tone="success">Built</Badge>
          </CardHeader>
          <CardBody className="space-y-2 text-sm leading-6 text-muted">
            <p>
              This is a static site. The Match Centre replays synthetic matches from the generator's event files, with fictional names from each
              seed's names file, and Verify recomputes every published hash in your browser.
            </p>
            {recorded && shown ? (
              <p>
                The Newsroom Wall, the stand-in approval card and the recap open on a recorded run, labelled on every screen: {shown.label}.
                It was exported from the bundle the pipeline sealed and the club media office approved, and Verify recomputes its hash and every
                file the Publisher published. The hand-written sample stays listed after it, labelled: {DESIGN_PREVIEW_LABEL} No agent runs on
                this site, this site publishes nothing and no account is needed.
              </p>
            ) : (
              <p>
                The Newsroom Wall, the stand-in approval card and the recap show one run, labelled on every screen: {DESIGN_PREVIEW_LABEL} Its gate
                verdicts and its bundle hash come from the real checks and the real seal over those hand-written sentences. No agent runs here,
                nothing is published and no account is needed.
              </p>
            )}
          </CardBody>
        </Card>

        <Card aria-labelledby="h-glossary" className="lg:col-span-2">
          <CardHeader>
            <CardTitle id="h-glossary">Glossary</CardTitle>
          </CardHeader>
          <CardBody>
            <dl className="grid gap-4 md:grid-cols-2">
              {GLOSSARY_ORDER.map((id) => (
                <div key={id} className="rounded-lg border border-line p-3">
                  <dt className="text-sm font-semibold text-fg">
                    {GLOSSARY[id].term} <span className="font-normal text-muted">({GLOSSARY[id].short})</span>
                  </dt>
                  <dd className="mt-1 text-sm leading-6 text-muted">{GLOSSARY[id].definition}</dd>
                </div>
              ))}
            </dl>
          </CardBody>
        </Card>

        <Card aria-labelledby="h-data">
          <CardHeader>
            <CardTitle id="h-data">Data and names</CardTitle>
          </CardHeader>
          <CardBody className="text-sm leading-6 text-muted">
            Every club, player and match is synthetic and fictional. Agon is not affiliated with or endorsed by any league, club or broadcaster,
            and it shows no real crests or logos; destinations on the approval card are text labels only.
          </CardBody>
        </Card>

        <Card aria-labelledby="h-source">
          <CardHeader>
            <CardTitle id="h-source">Source and libraries</CardTitle>
          </CardHeader>
          <CardBody className="space-y-2 text-sm leading-6 text-muted">
            <p>
              The code is public under the MIT licence at <a href={REPO_URL}>github.com/upgradedev/agon-azure</a>. This console is served as static
              files by Azure Static Web Apps under a strict content security policy: no inline script and no inline style.
            </p>
            <p>Open-source libraries it is built with:</p>
            <ul className="list-disc space-y-0.5 pl-5">
              {LIBRARIES.map((name) => (
                <li key={name}>{name}</li>
              ))}
            </ul>
          </CardBody>
        </Card>
      </div>
    </View>
  );
}
