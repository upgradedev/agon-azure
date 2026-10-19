/*
  Server entry for the build-time prerender (scripts/prerender.mjs). It loads
  the run data through the same loader and validators as the browser, from the
  files that ship in dist/, and renders the whole console with route "/".
  Every view is in the output, so the served HTML carries every screen's label.
*/
import { StrictMode } from "react";
import { renderToString } from "react-dom/server";
import { App } from "./App";
import { loadInitialData, type FetchText } from "./lib/loader";

export interface PrerenderResult {
  ok: boolean;
  html: string;
  message: string;
}

export async function prerender(fetchText: FetchText): Promise<PrerenderResult> {
  const initial = await loadInitialData(fetchText);
  if (initial.status !== "ready") {
    return { ok: false, html: "", message: initial.message };
  }
  const html = renderToString(
    <StrictMode>
      <App initial={initial} />
    </StrictMode>,
  );
  return { ok: true, html, message: "" };
}
