# Agon

Agon is an entry in progress for the Microsoft Inside the Game developer hackathon. It uses synthetic, fictional clubs and players only, and it is released under the MIT licence.

Status: work in progress, nothing deployed yet.

## What is in the repo today

Only the CI skeleton. Every pull request and every push to main runs five jobs:

| Job | What fails it |
|---|---|
| secrets | a secret anywhere in the git history (gitleaks), or a planted test token that the scanner misses |
| test | any failing test under `tests/` |
| pins | a line in a requirements file that is not an exact `==` pin |
| dashes | an em or en dash in any text file, selected by path from git |
| readiness-selftest | the readiness gate in `ci/readiness.py` passing a broken fixture or failing a good one |

The readiness gate is not yet run against a live URL, because nothing is deployed.

## Run the checks

```
python ci/check_pins.py
python ci/check_dashes.py
python ci/readiness.py --live
python -m pytest -q
```

`readiness.py --live` reads `ci/readiness.json` and fails until a demo URL exists. The three scripts in `ci/` use the Python standard library only.

## Open-source tooling

pytest and its dependencies (iniconfig, packaging, pluggy, pygments), setuptools as the build backend, the gitleaks container image, and the GitHub Actions `actions/checkout` and `actions/setup-python`. Exact versions are pinned in `requirements/dev.txt`, `pyproject.toml` and `.github/workflows/ci.yml`.

## The console (console/)

The public console is built in React, TypeScript, Vite and Tailwind CSS in `console/`, and it is the live site: the deploy workflow builds it from the committed lock in a job with no Azure identity, stamps the commit, the synthetic data and the season files, runs `ci/web_check.py` and the live readiness gate offline on those exact bytes, and uploads them to Azure Static Web Apps. The hand-written page in `web/` no longer ships; `web/runs` stays the source of the run files the console reads. Every view is prerendered into one static `index.html` at build time, so the page carries its labels without running a script, and it is served under the same strict content security policy (no inline script, no inline style) plus Trusted Types. Nothing installs on a developer machine: `.github/workflows/console.yml` installs from the committed lock, type-checks, runs the unit tests, builds, runs `ci/web_check.py` and an offline rehearsal of `ci/readiness.py` on the built site, then runs Playwright in Chromium with axe-core and uploads screenshots of every screen. It also rehearses the site with a recorded pipeline run at the top of the runs list, exported by `python -m agon.webrecord` from a bundle sealed, approved and published by the real code, so the site and the live gate are tested in that state before a real recorded run is committed.

Open-source libraries in the console, exact versions in `console/package.json` and `console/package-lock.json`: React and React DOM, TypeScript, Vite and @vitejs/plugin-react, Tailwind CSS and @tailwindcss/vite, Radix UI primitives (@radix-ui/react-dialog, @radix-ui/react-popover, @radix-ui/react-tooltip), Lucide icons (lucide-react), the Inter typeface through @fontsource-variable/inter, Vitest, Playwright (@playwright/test), axe-core through @axe-core/playwright, and the type packages @types/react, @types/react-dom and @types/node. The console and deploy workflows also use the GitHub Actions `actions/setup-node`, `actions/upload-artifact` and `actions/download-artifact`.
