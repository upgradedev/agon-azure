# Operations

How Agon is deployed, what the automation is allowed to do, how spend is capped, and the rule that keeps the public demo alive until judging ends.

## Where things run

| Part | Azure service | Region | Name |
|---|---|---|---|
| Logs and traces | Log Analytics workspace, workspace-based Application Insights | Sweden Central | `log-agon-<suffix>`, `appi-agon-<suffix>` |
| Feed, sandbox and work queues | Storage account, StorageV2, Standard LRS | Sweden Central | `stagon<suffix>` |
| Match state, checkpoints, bundles, approvals, receipts | Cosmos DB for NoSQL, serverless | Sweden Central | `cosmos-agon-<suffix>`, database `agon` |
| Keyless app identities | Six user-assigned managed identities | Sweden Central | `id-fleet`, `id-mcp`, `id-api`, `id-logicapp`, `id-publisher`, `id-publisher-sandbox` |
| Public web front end | Static Web Apps, Free | West Europe | `swa-agon-<suffix>` |

`<suffix>` is `uniqueString(resourceGroup().id)`, so every redeploy updates the same resources. Static Web Apps is not offered in Sweden Central, so the front end runs in West Europe while the data plane stays in Sweden Central. Never describe the whole system as running in one region.

Everything carries the tag `app=agon`. Everything lives in the resource group `rg-agon-swc` in the subscription "Upgrade - Microsoft Partner Network", and nowhere else.

The templates are `infra/main.bicep` (deployed by CI) and `infra/rbac.bicep` (deployed by the owner-side session). The modules are in `infra/modules/`.

## How to deploy

### The normal path: CI

A push to `main` that touches `infra/`, `web/`, `src/agon/synth/` or the deploy scripts runs `.github/workflows/deploy.yml`. To deploy without a change, open Actions, pick "deploy" and use "Run workflow" on `main`.

1. **what-if** compiles `infra/main.bicep` and asks Azure what would change. It also runs on every pull request that touches those paths, so a reviewer sees the change before merge. It changes nothing.
2. **deploy** (main only, environment `azure`) runs `az deployment group create` in incremental mode under the deployment name `agon-main`, and publishes the outputs in the job summary.
3. **web** generates the recorded synthetic matches for the published seeds 1, 2 and 3 into `web/data/` with `python -m agon.synth.manifest`, stamps the full commit sha into `index.html`, re-hashes every data file against `web/data/manifest.json` (`ci/stamp_web.py`), re-runs `ci/web_check.py` on the exact tree that ships, fetches the Static Web Apps deployment token at run time with `az staticwebapp secrets list`, and uploads `web/`. The token is masked in the log and is never stored as a repository secret.
4. **readiness** waits (at most 20 tries, 15 seconds apart) until the site serves the new commit, then runs `python ci/readiness.py --live` exactly once against the deployed hostname, with the commit sha as a required body marker. It fails on any status other than 200, on a host change, on a missing marker and on any forbidden string. The wait never passes or fails anything; the single gate run decides.

Two deploys never overlap: every run on `main` shares one concurrency group and a running deploy is never cancelled. A pull request's what-if has its own group, so it can never cancel a queued deploy.

`ci.yml` also compiles and lints both templates on every push and pull request (job `bicep`) with Bicep CLI v0.44.1, the same version the deploy uses. Every error and every warning fails the job, after a negative control proves that a known warning is caught. A warning that is genuinely harmless gets a `#disable-next-line` with a reason beside it; the check is never widened.

### One-time steps on the owner side

The CI identity holds Contributor on `rg-agon-swc` and nothing else. Three things need more than that and are done once by the owner-side session, which holds Owner.

**1. Register the resource providers (before the first CI deploy).** Registration is a subscription-level action that Contributor on one group cannot perform. If a provider is not registered, the first deploy fails with `MissingSubscriptionRegistration`. Check each one:

```bash
for ns in Microsoft.DocumentDB Microsoft.Storage Microsoft.ManagedIdentity Microsoft.OperationalInsights Microsoft.Insights Microsoft.Web; do
  printf '%s ' "$ns"
  az provider show --namespace "$ns" --subscription "Upgrade - Microsoft Partner Network" --query registrationState --output tsv
done
```

Register any that does not say `Registered`:

```bash
az provider register --namespace <namespace> --subscription "Upgrade - Microsoft Partner Network" --wait
```

Registration changes no resource and costs nothing.

**2. Check the built-in role ids used by `infra/rbac.bicep`.** Each line must print the role name in the comment beside it in the template:

```bash
for id in ba92f5b4-2d11-453d-a403-e96b0029c9fe 19e7f393-937e-4f77-808e-94535e297925 8a0f0c08-91a1-4084-bc3d-661d67233fed c6a89b2d-59bc-44d0-9896-0f6e12d7b80a; do
  az role definition list --name "$id" --query "[].roleName" --output tsv
done
```

Expected, in order: Storage Blob Data Contributor, Storage Queue Data Reader, Storage Queue Data Message Processor, Storage Queue Data Message Sender.

**3. Deploy `infra/rbac.bicep` (after the first green CI deploy, and again whenever it changes).** It reads the outputs of the `agon-main` deployment. From Git Bash:

```bash
rg=rg-agon-swc
out() { az deployment group show --resource-group "$rg" --name agon-main --query "properties.outputs.$1.value" --output tsv; }
az deployment group create --resource-group "$rg" --name agon-rbac --template-file infra/rbac.bicep --mode Incremental \
  --parameters storageAccountName="$(out storageAccountName)" \
               fleetPrincipalId="$(out idFleetPrincipalId)" \
               apiPrincipalId="$(out idApiPrincipalId)" \
               publisherPrincipalId="$(out idPublisherPrincipalId)" \
               publisherSandboxPrincipalId="$(out idPublisherSandboxPrincipalId)"
```

From PowerShell 5.1:

```powershell
$rg = "rg-agon-swc"
function Out-Agon($name) { az deployment group show --resource-group $rg --name agon-main --query "properties.outputs.$name.value" --output tsv }
az deployment group create --resource-group $rg --name agon-rbac --template-file infra/rbac.bicep --mode Incremental --parameters storageAccountName=$(Out-Agon storageAccountName) fleetPrincipalId=$(Out-Agon idFleetPrincipalId) apiPrincipalId=$(Out-Agon idApiPrincipalId) publisherPrincipalId=$(Out-Agon idPublisherPrincipalId) publisherSandboxPrincipalId=$(Out-Agon idPublisherSandboxPrincipalId)
```

Run `what-if` with the same arguments first if anything in `rbac.bicep` changed since the last run. Grants that go beyond the spec 7.1 table are marked "BEYOND THE SPEC 7.1 TABLE" in the templates, each with the reason.

### Never

* Never deploy with `--mode Complete`. The resource group also holds the gate T spike (`agon-teams`, `la-agon-gate-t`, `la-agon-decision-stub`), and complete mode deletes every resource the template does not name.
* Never store the Static Web Apps deployment token as a repository secret or variable. The workflow reads it at run time.
* Never create anything outside `rg-agon-swc`, and never touch the other resource groups in the subscription.
* Never recreate or rename the static site. Its default hostname is random and is the judge URL; a new site gets a new hostname and the submitted link dies.

## What CI can do, and what it cannot

| CI can | CI cannot |
|---|---|
| Compile and lint both templates, with no login | Write Azure RBAC role assignments (`Microsoft.Authorization/roleAssignments/write`), so `infra/rbac.bicep` is owner-side |
| Run what-if, and create or update any resource inside `rg-agon-swc` | Register resource providers, which is a subscription-level action |
| Create the Cosmos DB data-plane role assignments, because they are `Microsoft.DocumentDB` resources (`infra/modules/cosmos-access.bicep`) | Read or write blobs, queues or Cosmos items: it holds no data role, storage shared keys are disabled and Cosmos keys are disabled |
| Read the Static Web Apps deployment token at run time and upload the site | Touch the budget, any other resource group, or anything outside the subscription |
| Run the live readiness gate against the deployed hostname | Prove that each identity is denied what spec 7.1 says it must not hold: that needs the deny tests, which run as each identity and are not built yet |

Storage shared-key access is off because nothing in this template needs it: containers, queues, the lifecycle rule and CORS are written through Azure Resource Manager, the apps use their managed identities, and anonymous readers need no key. A tool that defaults to key access (some portal views, `az storage` without `--auth-mode login`) will be refused; use Microsoft Entra sign-in instead.

## Cost guard

* **Budget.** `agon-budget` on `rg-agon-swc` only: $150 a month, with alerts at $40, $60, $100 and $140 (26.67, 40, 66.67 and 93.33 percent). It was created by the bootstrap on 2026-10-19 and is deliberately not in Bicep, so a deploy can never reset its alert recipients. Check it with:

  ```bash
  az consumption budget show --budget-name agon-budget --resource-group rg-agon-swc --subscription "Upgrade - Microsoft Partner Network"
  ```

* **An alert does not stop spend.** It sends a message. When one fires, look at Cost analysis for the group, find the meter, and switch off the live path first. The recorded run is static and keeps working.
* **What this template costs at rest.** Static Web Apps Free has no charge. Cosmos DB serverless bills per request and per GB stored, with no idle charge. Storage is Standard LRS with a one-day delete rule on sandbox blobs (the rule runs about once a day, so a blob can live up to a day longer). Log Analytics keeps 30 days and caps ingestion at 1 GB a day. No compute is deployed yet. These are the service pricing models, not a measured bill.
* **Later lanes add** the app-level circuit breaker in the Cosmos `usage` container and the live-run caps of spec 9.3.

## Demo survival until 2026-11-11 07:59 UTC

Judges may open the public URL at any time until 2026-11-11 07:59 UTC. Until then:

* The default view stays the recorded run, served as static files from Static Web Apps Free. It costs nothing and cannot be stopped by a budget alert.
* No teardown, no key or identity rotation, no identity redeploy and no recreation of the static site, Cosmos account or storage account.
* Never make the repository private. A private flip can take a judge URL down, and making it public again does not always bring it back.
* After the submission deadline, 2026-10-28 06:59 UTC, nothing changes in the repository or on the site. A defect is recorded, not fixed, unless the owner decides otherwise.
* The readiness gate keeps checking the live URL: on every deploy (with the commit sha as a marker) and twice a day from `.github/workflows/readiness.yml` (05:17 and 17:17 UTC, read only, no commit marker because the deployed commit is not always the head of `main`). That workflow stops checking by itself after 2026-11-11 07:59 UTC; disable it then.

## Known gaps

* App Insights availability tests on the root and `feed.json` (spec 9.4) are not built yet; the scheduled readiness workflow covers the root and the data manifest meanwhile.
* Application Insights still accepts telemetry with its connection string (local authentication is on). Keyless telemetry needs a Monitoring Metrics Publisher grant per app identity in `infra/rbac.bicep`; decide when the apps exist.
* The Static Web Apps deploy action is pinned by commit, but it pulls a container image that the pin does not fix.
* Anonymous blob read needs `allowBlobPublicAccess` on the account. If an Azure Policy in the subscription denies it, the storage deployment fails and the feed design needs another route.
* The first deploy creates the identities and their Cosmos role assignments in one run. If Microsoft Entra ID has not yet propagated a new identity, an assignment can fail once; rerunning the workflow fixes it.
* Grants for resources that do not exist yet (Foundry, Translator, Speech, Key Vault, a registry) are listed at the top of `infra/rbac.bicep` and are added with those resources.
