/*
  The deploy step replaces the placeholder in <body data-commit> with the full
  commit sha (ci/stamp_web.py). Anything that is not a hex sha is reported as
  not stamped rather than shown, so a local build can never pose as a deployed
  commit. The placeholder text itself never appears in the bundle, because the
  stamp step fails if it finds it anywhere but index.html.
*/
export const REPO_URL = "https://github.com/upgradedev/agon-azure";
const COMMIT = /^[0-9a-f]{7,40}$/i;

export function stampedCommit(value: string | null | undefined): string | null {
  const sha = (value ?? "").trim();
  return COMMIT.test(sha) ? sha.toLowerCase() : null;
}
