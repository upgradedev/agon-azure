/*
  The deploy step replaces the placeholder in <body data-commit> with the full
  commit sha (ci/stamp_web.py). Anything that is not a hex sha is reported as
  not stamped rather than shown, so a local build can never pose as a deployed
  commit. The placeholder text itself never appears in the bundle, because the
  stamp step fails if it finds it anywhere but index.html.

  The same step rewrites the prerendered build label (id="run-label") from the
  unstamped sentence to the stamped one, so the served HTML names the commit
  before any script runs; the browser then hydrates against that markup
  (hooks/stores useCommit reads data-commit for the hydration snapshot too).
  ci/stamp_web.py writes exactly what BuildLabelView renders for a commit;
  tests/ssr.test.tsx and tests/test_stamp_web.py hold both to one string.
*/
export const REPO_URL = "https://github.com/upgradedev/agon-azure";
export const UNSTAMPED_LABEL = "Preview build, commit not stamped (local build)";
export const STAMPED_LABEL_PREFIX = "Preview build, commit ";
const COMMIT = /^[0-9a-f]{7,40}$/i;

export function stampedCommit(value: string | null | undefined): string | null {
  const sha = (value ?? "").trim();
  return COMMIT.test(sha) ? sha.toLowerCase() : null;
}
