/*
  Client entry. Loads the run data the prerendered page was built from, then
  hydrates the prerendered markup in place, so nothing flashes and focus is
  kept. If the run data cannot be loaded, the first render would differ from
  the prerendered page, so the app renders fresh and says why on each screen.
*/
import "@fontsource-variable/inter";
import "./styles/index.css";
import { StrictMode } from "react";
import { createRoot, hydrateRoot } from "react-dom/client";
import { App } from "./App";
import { browserFetchText, loadInitialData } from "./lib/loader";

async function start(): Promise<void> {
  const container = document.getElementById("root");
  if (!container) {
    return;
  }
  const initial = await loadInitialData(browserFetchText);
  const app = (
    <StrictMode>
      <App initial={initial} />
    </StrictMode>
  );
  if (initial.status === "ready" && container.firstElementChild) {
    hydrateRoot(container, app);
  } else {
    createRoot(container).render(app);
  }
}

void start();
