/*
  Hash routes. Static Web Apps serves one index.html; the part after "#"
  never reaches the server, so every view lives in the one prerendered page.
  "#/approve/sample-s1" names a run; a query after "?" carries options such
  as "?s=en-05" (open that sentence's Why drawer) or "?replay=1".
*/
export type ViewKey =
  | "home"
  | "newsroom"
  | "approve"
  | "recap"
  | "verify"
  | "data-lab"
  | "how-it-works"
  | "about"
  | "not-found";

export interface Route {
  view: ViewKey;
  param: string;
  query: Record<string, string>;
}

const PATHS: Record<string, ViewKey> = {
  "": "home",
  newsroom: "newsroom",
  approve: "approve",
  recap: "recap",
  verify: "verify",
  "data-lab": "data-lab",
  "how-it-works": "how-it-works",
  about: "about",
};
const WITH_PARAM = new Set<ViewKey>(["newsroom", "approve", "recap"]);
const PARAM = /^[a-z0-9][a-z0-9-]{0,62}$/;

export const HOME: Route = { view: "home", param: "", query: {} };

export const VIEW_TITLES: Record<ViewKey, string> = {
  home: "Match Centre",
  newsroom: "Newsroom Wall",
  approve: "Approval card",
  recap: "Recap",
  verify: "Verify",
  "data-lab": "Data Lab",
  "how-it-works": "How Agon works",
  about: "About",
  "not-found": "Page not found",
};

function parseQuery(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const part of text.split("&")) {
    if (!part) {
      continue;
    }
    const at = part.indexOf("=");
    const key = at === -1 ? part : part.slice(0, at);
    const value = at === -1 ? "" : part.slice(at + 1);
    if (/^[a-z]{1,16}$/.test(key) && /^[A-Za-z0-9-]{0,64}$/.test(value)) {
      out[key] = value;
    }
  }
  return out;
}

/* null means an in-page anchor such as "#main": the current view stays. */
export function parseHash(hash: string): Route | null {
  if (!hash || hash === "#" || hash === "#/") {
    return HOME;
  }
  if (!hash.startsWith("#/")) {
    return null;
  }
  const body = hash.slice(2);
  const at = body.indexOf("?");
  const path = (at === -1 ? body : body.slice(0, at)).replace(/\/+$/, "");
  const query = parseQuery(at === -1 ? "" : body.slice(at + 1));
  const [head = "", ...rest] = path.split("/");
  const view = PATHS[head];
  if (!view) {
    return { view: "not-found", param: "", query: {} };
  }
  const param = rest.join("/");
  if (param && (!WITH_PARAM.has(view) || !PARAM.test(param))) {
    return { view: "not-found", param: "", query: {} };
  }
  return { view, param, query };
}

export function hrefFor(view: ViewKey, param = "", query: Record<string, string> = {}): string {
  const path = view === "home" ? "" : view;
  const tail = param ? `/${param}` : "";
  const q = Object.entries(query)
    .map(([k, v]) => `${k}=${v}`)
    .join("&");
  return `#/${path}${tail}${q ? `?${q}` : ""}`;
}
