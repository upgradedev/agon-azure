/*
  A small static server for the end-to-end tests, standing in for Azure Static
  Web Apps. It reads dist/staticwebapp.config.json and applies its
  globalHeaders (so the strict Content-Security-Policy is enforced in the
  browser during every test) and its navigationFallback: a request that
  matches no file is answered with /index.html, unless it matches an exclude
  pattern, which gets a 404 like the real host.

  Dependency-free Node. Usage: node scripts/serve.mjs <dir> <port>
*/
import { createServer } from "node:http";
import { existsSync, readFileSync, statSync } from "node:fs";
import { extname, join, normalize, resolve, sep } from "node:path";

const root = resolve(process.argv[2] ?? "dist");
const port = Number(process.argv[3] ?? 4173);
const config = JSON.parse(readFileSync(join(root, "staticwebapp.config.json"), "utf8"));
const headers = config.globalHeaders ?? {};
const fallback = config.navigationFallback ?? {};

const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".ndjson": "application/x-ndjson; charset=utf-8",
  ".svg": "image/svg+xml",
  ".txt": "text/plain; charset=utf-8",
  ".woff2": "font/woff2",
  ".woff": "font/woff",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".webmanifest": "application/manifest+json",
};

/* A Static Web Apps pattern: "*" matches any run of characters, and
   "{a,b}" is a choice. "/runs/*" therefore covers the whole folder. */
function patternToRegex(pattern) {
  let out = "";
  for (let i = 0; i < pattern.length; i += 1) {
    const c = pattern[i];
    if (c === "*") {
      out += ".*";
    } else if (c === "{") {
      const end = pattern.indexOf("}", i);
      out += `(?:${pattern.slice(i + 1, end).split(",").map((p) => p.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`;
      i = end;
    } else {
      out += c.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    }
  }
  return new RegExp(`^${out}$`);
}

const excludes = (fallback.exclude ?? []).map(patternToRegex);

function send(res, status, body, type) {
  res.writeHead(status, { ...headers, "Content-Type": type, "Cache-Control": "no-store" });
  res.end(body);
}

createServer((req, res) => {
  const url = new URL(req.url ?? "/", "http://localhost");
  let path = decodeURIComponent(url.pathname);
  if (path.endsWith("/")) {
    path += "index.html";
  }
  const file = normalize(join(root, path));
  if (file !== root && !file.startsWith(root + sep)) {
    send(res, 400, "bad path", "text/plain; charset=utf-8");
    return;
  }
  if (existsSync(file) && statSync(file).isFile()) {
    send(res, 200, readFileSync(file), TYPES[extname(file).toLowerCase()] ?? "application/octet-stream");
    return;
  }
  if (excludes.some((re) => re.test(url.pathname)) || !fallback.rewrite) {
    send(res, 404, "not found", "text/plain; charset=utf-8");
    return;
  }
  send(res, 200, readFileSync(join(root, fallback.rewrite)), TYPES[".html"]);
}).listen(port, "127.0.0.1", () => {
  console.log(`serve: ${root} on http://127.0.0.1:${port}/ with the headers from staticwebapp.config.json`);
});
