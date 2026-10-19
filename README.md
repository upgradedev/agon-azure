# Agon

Agon is an entry in progress for the Microsoft Inside the Game developer hackathon. It uses synthetic, fictional clubs and players only, and it is released under the MIT licence.

Status: work in progress, nothing deployed yet.

## What is in the repo today

Only the CI skeleton. Every push and pull request runs five jobs:

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
python ci/readiness.py --offline <fixture dir>
python -m pytest -q
```

The three scripts in `ci/` use the Python standard library only.

## Open-source tooling

pytest and its dependencies (iniconfig, packaging, pluggy, pygments), setuptools as the build backend, the gitleaks container image, and the GitHub Actions `actions/checkout` and `actions/setup-python`. Exact versions are pinned in `requirements/dev.txt`, `pyproject.toml` and `.github/workflows/ci.yml`.
