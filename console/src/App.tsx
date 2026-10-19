/*
  The console. Every view is rendered into the one page and the inactive ones
  carry the hidden attribute, exactly as in the prerendered HTML: the readiness
  gate reads that HTML without running scripts, so every screen's label must be
  in it, and the live page keeps the same structure after hydration.

  The first client render repeats the server render (route "/", the same run
  data), then useSyncExternalStore switches to the route in the address.
*/
import { useEffect, useRef, useState } from "react";
import { TooltipProvider } from "./components/ui/tooltip";
import { Shell } from "./components/shell";
import type { Progress } from "./components/guide";
import { MatchCentre } from "./screens/match-centre";
import { NewsroomWall } from "./screens/newsroom-wall";
import { Approve } from "./screens/approve";
import { Recap } from "./screens/recap";
import { Verify } from "./screens/verify";
import { DataLab } from "./screens/data-lab";
import { HowItWorks } from "./screens/how-it-works";
import { About } from "./screens/about";
import { NotFound } from "./screens/not-found";
import { navigate, useRoute } from "./hooks/stores";
import { VIEW_TITLES, hrefFor, parseHash } from "./lib/router";
import type { InitialData, LoadedRun } from "./lib/types";

function pickRun(initial: InitialData, param: string): { loaded: LoadedRun | null; error: string | null } {
  if (initial.status !== "ready") {
    return { loaded: null, error: initial.message };
  }
  const id = param || initial.runs.defaultRunId;
  const loaded = initial.runs.runs[id];
  if (!loaded) {
    return { loaded: null, error: `There is no run called ${id} in the runs index, so this screen has nothing to show.` };
  }
  return { loaded, error: null };
}

export function App({ initial }: { initial: InitialData }) {
  const route = useRoute();
  const [blownSeed, setBlownSeed] = useState<number | null>(null);
  const [replayRequest, setReplayRequest] = useState(0);
  const [progress, setProgress] = useState<Progress>({ blown: false, decided: false, verified: false });
  const lastView = useRef(route.view);

  const runParam = route.view === "newsroom" || route.view === "approve" || route.view === "recap" ? route.param : "";
  const { loaded, error } = pickRun(initial, runParam);
  const runs = initial.status === "ready" ? initial.runs : null;
  const fallback = pickRun(initial, "").loaded;
  /* A screen keeps its own state (ticks, replay step) until it shows another run. */
  const runKey = (view: string) => (route.view === view ? loaded?.run.run_id ?? "missing" : fallback?.run.run_id ?? "missing");

  /* Marks the page as taken over by the app (the end-to-end tests wait for it). */
  useEffect(() => {
    document.documentElement.setAttribute("data-app", "ready");
  }, []);

  /* Title, focus and scroll follow the view after a navigation (not on load). */
  useEffect(() => {
    document.title = `Agon | ${VIEW_TITLES[route.view]}`;
    if (route.view === "verify") {
      setProgress((p) => (p.verified ? p : { ...p, verified: true }));
    }
    if (lastView.current !== route.view) {
      lastView.current = route.view;
      window.scrollTo(0, 0);
      document.getElementById(`h-${route.view}`)?.focus();
    }
    const wanted = parseHash(window.location.hash);
    if (!wanted || wanted.view === route.view) {
      document.documentElement.removeAttribute("data-boot-route");
    }
  }, [route.view]);

  function onBlowFullTime(seed: number): void {
    setBlownSeed(seed);
    setProgress((p) => ({ ...p, blown: true }));
    setReplayRequest((n) => n + 1);
    const target = runs ? Object.values(runs.runs).find((r) => r.run.seed === seed)?.run.run_id ?? runs.defaultRunId : "";
    navigate(hrefFor("newsroom", target));
  }

  return (
    <TooltipProvider delayDuration={300}>
      <Shell view={route.view}>
        <MatchCentre active={route.view === "home"} runs={runs} progress={progress} onBlowFullTime={onBlowFullTime} />
        <NewsroomWall
          key={`wall-${runKey("newsroom")}`}
          active={route.view === "newsroom"}
          loaded={route.view === "newsroom" ? loaded : fallback}
          error={route.view === "newsroom" ? error : null}
          blownSeed={blownSeed}
          replayRequest={replayRequest}
        />
        <Approve
          key={`approve-${runKey("approve")}`}
          active={route.view === "approve"}
          loaded={route.view === "approve" ? loaded : fallback}
          error={route.view === "approve" ? error : null}
          blownSeed={blownSeed}
          onDecided={() => setProgress((p) => ({ ...p, decided: true }))}
        />
        <Recap
          key={`recap-${runKey("recap")}`}
          active={route.view === "recap"}
          route={route}
          loaded={route.view === "recap" ? loaded : fallback}
          error={route.view === "recap" ? error : null}
          blownSeed={blownSeed}
        />
        <Verify active={route.view === "verify"} />
        <DataLab active={route.view === "data-lab"} />
        <HowItWorks active={route.view === "how-it-works"} />
        <About active={route.view === "about"} />
        <NotFound active={route.view === "not-found"} />
      </Shell>
    </TooltipProvider>
  );
}
