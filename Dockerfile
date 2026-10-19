# One image for every Agon job (infra/apps.bicep); each job runs one module.
# Built and pushed in CI only (deploy workflow, job apps); pull requests build it
# without pushing (ci workflow, job image). Never built on a workstation.
#
# Base image pinned by digest: python 3.12.14-slim-bookworm, published 2026-09-19,
# digest read from the Docker Hub API on 2026-10-19.
FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Exact pins only (ci/check_pins.py covers these files). dev.txt comes in through
# the -r lines and brings the pinned setuptools the package build below needs.
# worker.txt adds the Storage SDKs of the fleet worker (job-agon-fleet).
COPY requirements/ requirements/
RUN python -m pip install -r requirements/fleet.txt -r requirements/intake.txt -r requirements/worker.txt

COPY pyproject.toml README.md LICENSE ./
COPY src/ src/
RUN python -m pip install --no-deps --no-build-isolation . \
 && rm -rf /app/src

# Fail the build if package data did not ship: the frozen interpret model, the
# realism bands and the pinned prompts are read at run time.
RUN cd / && python -c "import agon.interpret.model as m; m.load_model(); from agon.agents.prompts import load_prompt; load_prompt('writer'); import agon.synth.bands, agon.intake, agon.governance.store_cosmos, agon.fleet.worker, agon.agents.publish; import azure.storage.blob, azure.storage.queue, agent_framework; print('package data ok')"

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin agon
USER 10001

CMD ["python", "-m", "agon.intake", "--once"]
