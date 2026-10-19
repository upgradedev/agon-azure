# Operations

How Agon is deployed, what the automation is allowed to do, how spend is capped, and the rule that keeps the public demo alive until judging ends.

## Where things run

| Part | Azure service | Region | Name |
|---|---|---|---|
| Logs and traces | Log Analytics workspace, workspace-based Application Insights | Sweden Central | `log-agon-<suffix>`, `appi-agon-<suffix>` |
| Feed, sandbox and work queues | Storage account, StorageV2, Standard LRS | Sweden Central | `stagon<suffix>` |
| Match state, checkpoints, bundles, approvals, receipts | Cosmos DB for NoSQL, serverless | Sweden Central | `cosmos-agon-<suffix>`, database `agon` |
| Keyless app identities | Six user-assigned managed identities | Sweden Central | `id-fleet`, `id-mcp`, `id-api`, `id-logicapp`, `id-publisher`, `id-publisher-sandbox` |
| Models and agents | Microsoft Foundry resource (Cognitive Services, kind AIServices, S0, local auth off), one Foundry project, two DataZoneStandard deployments at capacity 20 each, model version 2026-07-09 pinned with no auto upgrade, project connection to Application Insights | Sweden Central, EU Data Zone | `aif-agon-<suffix>`, project `agon`, deployments `gpt-5.6-luna` and `gpt-5.6-terra` |
| Translation | Azure AI Translator (kind TextTranslation, F0 by default, local auth off) | Sweden Central | `trl-agon-<suffix>` |
| Speech | Azure AI Speech (kind SpeechServices, S0 by default, local auth off) | Sweden Central | `spch-agon-<suffix>` |
| Public web front end | Static Web Apps, Free | West Europe | `swa-agon-<suffix>` |

`<suffix>` is `uniqueString(resourceGroup().id)`, so every redeploy updates the same resources. Static Web Apps is not offered in Sweden Central, so the front end runs in West Europe while the data plane stays in Sweden Central. Never describe the whole system as running in one region.

Everything carries the tag `app=agon`. Everything lives in the resource group `rg-agon-swc` in the subscription "Upgrade - Microsoft Partner Network", and nowhere else.

The templates are `infra/main.bicep` (deployed by CI) and `infra/rbac.bicep` (deployed by the owner-side session). The modules are in `infra/modules/`.

## How to deploy

### The normal path: CI

A push to `main` that touches `infra/`, `web/`, `src/agon/synth/` or the deploy scripts runs `.github/workflows/deploy.yml`. To deploy without a change, open Actions, pick "deploy" and use "Run workflow" on `main`.

1. **what-if** (main only) compiles `infra/main.bicep` and asks Azure what would change, just before the deploy, and prints the diff in the job log. It changes nothing. It does not run on pull requests; see "Pull requests get no Azure token" below.
2. **deploy** (main only, environment `azure`) runs `az deployment group create` in incremental mode under the deployment name `agon-main`, and publishes the outputs in the job summary.
3. **web** generates the recorded synthetic matches for the published seeds 1, 2 and 3 into `web/data/` with `python -m agon.synth.manifest`, stamps the full commit sha into `index.html`, re-hashes every data file against `web/data/manifest.json` (`ci/stamp_web.py`), re-runs `ci/web_check.py` on the exact tree that ships, fetches the Static Web Apps deployment token at run time with `az staticwebapp secrets list`, and uploads `web/`. The token is masked in the log and is never stored as a repository secret.
4. **readiness** waits (at most 20 tries, 15 seconds apart) until the site serves the new commit, then runs `python ci/readiness.py --live` exactly once against the deployed hostname, with the commit sha as a required body marker. It fails on any status other than 200, on a host change, on a missing marker and on any forbidden string. The wait never passes or fails anything; the single gate run decides.
5. **foundry-smoke** (main only, after deploy, in parallel with web) runs `.github/workflows/foundry-smoke.yml`: it checks that `agon-main` reads `Succeeded`, reads the Foundry outputs, gets a Microsoft Entra token for `https://cognitiveservices.azure.com` and makes one chat completion on `gpt-5.6-luna` through the v1 API with `ci/foundry_smoke.py` (Python standard library only). It prints the model, the deployment, the latency in milliseconds and the token usage, and fails on anything but HTTP 200 with a usage block. It is independent of web and readiness, so a red smoke never holds back the public site. It can also be run by hand ("foundry-smoke", "Run workflow" on `main`, either deployment).

`deploy.yml` passes `ciPrincipalId` to `main.bicep` in both what-if and deploy. It is the **object id** of the `agon-azure-ci` service principal (not its appId), read from the `oid` claim of the job's own ARM token, because reading it from Microsoft Graph needs a directory role CI does not hold. `main.bicep` uses it for one Cosmos grant (the `checkpoints` container, for spike S1 on Cosmos) and echoes it as the `ciPrincipalId` output for `infra/rbac.bicep`.

### Gate C2: the first Foundry model call from CI

The CI inference role is an Azure RBAC grant, so it lives in `infra/rbac.bicep`, which only the owner side can deploy. The first CI run after the Foundry module merges therefore fails its smoke with HTTP 403. That is expected and the job says so. The order is:

1. Merge. The CI deploy creates the Foundry resource, project and deployments; `deploy` goes green, `foundry-smoke` goes red with 403.
2. Owner side: wait for `agon-main` to read `Succeeded`, then run step 3 below (it now also grants CI Cognitive Services OpenAI User on the Foundry account).
3. Wait a few minutes for the grant to propagate, then run "foundry-smoke" by hand on `main`. That run is the gate C2 evidence: record its URL, the printed model, deployment, latency and usage in `STATE.md`. Gate C2 is due 2026-10-21 18:00 UTC.

Two deploys never overlap: every run shares one concurrency group and a running deploy is never cancelled.

`ci.yml` also compiles and lints both templates on every push and pull request (job `bicep`, no Azure login) with Bicep CLI v0.44.1, the same version the deploy uses. Every error and every warning fails the job, after a negative control proves that a known warning is caught. A warning that is genuinely harmless gets a `#disable-next-line` with a reason beside it; the check is never widened.

### Pull requests get no Azure token

Decided 2026-10-19 after review, option (a): the Azure what-if runs only on `main`, and no workflow that a pull request can trigger may request an OIDC token.

Why: a `pull_request` workflow runs the YAML of the pull request's own branch, before anyone reviews it. The only Azure identity this repo has is the app `agon-azure-ci`, which holds Contributor on `rg-agon-swc`. A branch that edits `deploy.yml`, or adds a new workflow, could log in with that app and create, change or delete anything in the group, or read the Static Web Apps deployment token.

What a pull request gets instead: `ci.yml` compiles and lints both templates with no login (job `bicep`), and job `oidc` (`ci/check_oidc.py`) fails if any pull request triggered workflow grants `id-token: write` or `write-all`. That check is an accident guard only, because a branch can edit it in its own diff. The Azure what-if diff appears in the `main` run's log, in the job before the deploy. It informs; it does not stop the deploy unless the `azure` environment has a required reviewer.

**The boundary is on the Entra side, and it is pending owner action.** The bootstrap created a federated credential for the subject `repo:upgradedev/agon-azure:pull_request` on `agon-azure-ci`. Nothing in this repository uses it any more, but while it exists any branch of this repository can still obtain a Contributor token on `rg-agon-swc` through a workflow it adds itself. Step 4 below removes it. Until step 4 is done and checked, treat every open pull request as able to act with Contributor on the group.

To bring pull request what-if back later, use option (b): a separate Entra app that holds only Reader on the group plus `Microsoft.Resources/deployments/whatIf/action` through a custom role, with the `pull_request` subject pointed at that app and its own client id variable. Never point the `pull_request` subject at `agon-azure-ci` again.

### One-time steps on the owner side

The CI identity holds Contributor on `rg-agon-swc` and nothing else. Four things need more than that and are done once by the owner-side session, which holds Owner.

**1. Register the resource providers (before the first CI deploy).** Registration is a subscription-level action that Contributor on one group cannot perform. If a provider is not registered, the first deploy fails with `MissingSubscriptionRegistration`. Check each one:

```bash
for ns in Microsoft.DocumentDB Microsoft.Storage Microsoft.ManagedIdentity Microsoft.OperationalInsights Microsoft.Insights Microsoft.Web Microsoft.CognitiveServices; do
  printf '%s ' "$ns"
  az provider show --namespace "$ns" --subscription "Upgrade - Microsoft Partner Network" --query registrationState --output tsv
done
```

Register any that does not say `Registered`:

```bash
az provider register --namespace <namespace> --subscription "Upgrade - Microsoft Partner Network" --wait
```

Registration changes no resource and costs nothing.

**1b. Check the free-tier slots before the first deploy with Translator and Speech.** Microsoft allows one free (F0) account of each kind per subscription, and this subscription holds other projects. `main.bicep` defaults to Translator F0 and Speech S0 (spec 3.2 row 7 names S0 for the DragonHD voices; whether F0 serves them is not verified). List the F0 accounts already in the subscription (read only):

```bash
az cognitiveservices account list --subscription "Upgrade - Microsoft Partner Network" \
  --query "[?sku.name=='F0'].{name:name, kind:kind, group:resourceGroup}" --output table
```

If a `TextTranslation` row appears outside `rg-agon-swc`, the Translator F0 slot is taken: set `translatorSku` to `S1` (add `translatorSku=S1` to the `--parameters` of both what-if and deploy in `deploy.yml`) before merging, or the whole `agon-main` deploy fails and the web job never runs. The same applies to `speechSku=F0` and a `SpeechServices` row. The tier of an existing account can be changed later in place.

**2. Check the built-in role ids used by `infra/rbac.bicep`.** Each line must print the role name in the comment beside it in the template:

```bash
for id in ba92f5b4-2d11-453d-a403-e96b0029c9fe 19e7f393-937e-4f77-808e-94535e297925 8a0f0c08-91a1-4084-bc3d-661d67233fed c6a89b2d-59bc-44d0-9896-0f6e12d7b80a 53ca6127-db72-4b80-b1b0-d745d6d5456d 5e0bd9bd-7b93-4f28-af87-19fc36ad61bd a97b65f3-24c7-4388-baec-2e87135dc908; do
  az role definition list --name "$id" --query "[].roleName" --output tsv
done
```

Expected, in order: Storage Blob Data Contributor, Storage Queue Data Reader, Storage Queue Data Message Processor, Storage Queue Data Message Sender, Foundry User (spec 7.1 calls it by its earlier name, Azure AI User; the tenant may still print that name), Cognitive Services OpenAI User, Cognitive Services User.

**3. Deploy `infra/rbac.bicep` (after the first green CI deploy, and again whenever it changes).** It reads the outputs of the `agon-main` deployment.

**Deploy order: the CI deploy must finish first.** While a CI deploy is `Running`, `az deployment group show -n agon-main` returns null outputs. On 2026-10-19 an rbac run during a CI deploy received an empty `storageAccountName` and every assignment failed with `ParentResourceNotFound`. So both commands below stop unless `agon-main` reads `Succeeded`, and every parameter of `rbac.bicep` carries `@minLength` (1, or 2 for Cognitive Services names), so an empty output fails validation before any write.

What it grants (each at the narrowest scope; full list in the template):

| Principal | Role | Scope | Why |
|---|---|---|---|
| `id-fleet`, `id-api`, `id-publisher`, `id-publisher-sandbox` | Storage queue and blob data roles | one queue or one container each | spec 7.1 |
| `id-fleet` | Foundry User (Azure AI User) | the Foundry project `agon` | spec 7.1 |
| `id-fleet` | Cognitive Services User | the Translator account; the Speech account | spec 7.1 |
| CI (`agon-azure-ci`) | Cognitive Services OpenAI User | the Foundry account only | beyond spec 7.1: gate C2 smoke and the evals job |

From Git Bash:

```bash
rg=rg-agon-swc
state="$(az deployment group show --resource-group "$rg" --name agon-main --query properties.provisioningState --output tsv)"
[ "$state" = "Succeeded" ] || { echo "agon-main is $state; wait for Succeeded"; exit 1; }
out() { az deployment group show --resource-group "$rg" --name agon-main --query "properties.outputs.$1.value" --output tsv; }
az deployment group create --resource-group "$rg" --name agon-rbac --template-file infra/rbac.bicep --mode Incremental \
  --parameters storageAccountName="$(out storageAccountName)" \
               fleetPrincipalId="$(out idFleetPrincipalId)" \
               apiPrincipalId="$(out idApiPrincipalId)" \
               publisherPrincipalId="$(out idPublisherPrincipalId)" \
               publisherSandboxPrincipalId="$(out idPublisherSandboxPrincipalId)" \
               ciPrincipalId="$(out ciPrincipalId)" \
               foundryAccountName="$(out foundryAccountName)" \
               foundryProjectName="$(out foundryProjectName)" \
               translatorAccountName="$(out translatorAccountName)" \
               speechAccountName="$(out speechAccountName)"
```

From PowerShell 5.1:

```powershell
$rg = "rg-agon-swc"
$state = az deployment group show --resource-group $rg --name agon-main --query properties.provisioningState --output tsv
if ($state -ne "Succeeded") { throw "agon-main is $state; wait for Succeeded" }
function Out-Agon($name) { az deployment group show --resource-group $rg --name agon-main --query "properties.outputs.$name.value" --output tsv }
az deployment group create --resource-group $rg --name agon-rbac --template-file infra/rbac.bicep --mode Incremental --parameters storageAccountName=$(Out-Agon storageAccountName) fleetPrincipalId=$(Out-Agon idFleetPrincipalId) apiPrincipalId=$(Out-Agon idApiPrincipalId) publisherPrincipalId=$(Out-Agon idPublisherPrincipalId) publisherSandboxPrincipalId=$(Out-Agon idPublisherSandboxPrincipalId) ciPrincipalId=$(Out-Agon ciPrincipalId) foundryAccountName=$(Out-Agon foundryAccountName) foundryProjectName=$(Out-Agon foundryProjectName) translatorAccountName=$(Out-Agon translatorAccountName) speechAccountName=$(Out-Agon speechAccountName)
```

`ciPrincipalId` is the object id of the `agon-azure-ci` service principal, not its appId. To cross-check it owner-side: `az ad sp show --id <appId> --query id --output tsv` must print the same value as `out ciPrincipalId`.

Run `what-if` with the same arguments first if anything in `rbac.bicep` changed since the last run. Grants that go beyond the spec 7.1 table are marked "BEYOND THE SPEC 7.1 TABLE" (or "beyond spec 7.1") in the templates, each with the reason.

**4. Remove the `pull_request` federated credential from `agon-azure-ci` (pending; do it as soon as possible, it is safe before or after this change merges because nothing uses that subject).** First prove that nothing in the repository still needs it. Every workflow that grants `id-token: write` must have no `pull_request` trigger:

```bash
grep -l "id-token: write" .github/workflows/*.yml | xargs -r grep -lE '^  pull_request|^on:.*pull_request'
python ci/check_oidc.py
```

The `grep` reads trigger lines only (comments that mention pull requests do not count) and must print nothing; the check must end with `problems: 0`. Then list the credentials, note the name of the one whose subject ends in `:pull_request`, and delete that one only:

```bash
app="$(gh variable get AZURE_CLIENT_ID --repo upgradedev/agon-azure)"
az ad app federated-credential list --id "$app" --query "[].{name:name, subject:subject}" --output table
az ad app federated-credential delete --id "$app" --federated-credential-id <name of the pull_request credential>
az ad app federated-credential list --id "$app" --query "[].subject" --output tsv
```

The last command must print exactly two subjects, `repo:upgradedev/agon-azure:ref:refs/heads/main` and `repo:upgradedev/agon-azure:environment:azure`. Deleting this credential does not affect the deploy, the scheduled readiness check or any resource: they use the other two subjects. If a flag name differs in your az version, check `az ad app federated-credential delete --help`. Record the date and the printed subjects in `STATE.md`.

### Never

* Never deploy with `--mode Complete`. The resource group also holds the gate T spike (`agon-teams`, `la-agon-gate-t`, `la-agon-decision-stub`), and complete mode deletes every resource the template does not name.
* Never store the Static Web Apps deployment token as a repository secret or variable. The workflow reads it at run time.
* Never create anything outside `rg-agon-swc`, and never touch the other resource groups in the subscription.
* Never recreate or rename the static site. Its default hostname is random and is the judge URL; a new site gets a new hostname and the submitted link dies.

## What CI can do, and what it cannot

| CI can | CI cannot |
|---|---|
| Compile and lint both templates, with no login | Write Azure RBAC role assignments (`Microsoft.Authorization/roleAssignments/write`), so `infra/rbac.bicep` is owner-side |
| Run what-if on `main`, and create or update any resource inside `rg-agon-swc` | Register resource providers, which is a subscription-level action |
| Compile and lint the templates on a pull request, with no login | Log in to Azure from a pull request: no workflow requests a token on `pull_request` (enforced by `ci/check_oidc.py`), and once step 4 is done Entra refuses that subject |
| Create the Cosmos DB data-plane role assignments, because they are `Microsoft.DocumentDB` resources (`infra/modules/cosmos-access.bicep`) | Read or write blobs or queues: it holds no storage data role and storage shared keys are disabled |
| Read and write Cosmos items in the `checkpoints` container only (built-in Data Contributor at container scope, for spike S1 on Cosmos) | Read or write any other Cosmos container: Cosmos keys are disabled and it holds no other data role |
| Call the Foundry model deployments (Cognitive Services OpenAI User on the Foundry account, once owner step 3 has run) | Call Translator or Speech, use a Foundry project role, or read an account key (local auth is off on every Cognitive Services account) |
| Read the Static Web Apps deployment token at run time and upload the site | Touch the budget, any other resource group, or anything outside the subscription |
| Run the live readiness gate against the deployed hostname | Prove that each identity is denied what spec 7.1 says it must not hold: that needs the deny tests, which run as each identity and are not built yet |

Storage shared-key access is off because nothing in this template needs it: containers, queues, the lifecycle rule and CORS are written through Azure Resource Manager, the apps use their managed identities, and anonymous readers need no key. A tool that defaults to key access (some portal views, `az storage` without `--auth-mode login`) will be refused; use Microsoft Entra sign-in instead.

## Cost guard

* **Budget.** `agon-budget` on `rg-agon-swc` only: $150 a month, with alerts at $40, $60, $100 and $140 (26.67, 40, 66.67 and 93.33 percent). It was created by the bootstrap on 2026-10-19 and is deliberately not in Bicep, so a deploy can never reset its alert recipients. Check it with:

  ```bash
  az consumption budget show --budget-name agon-budget --resource-group rg-agon-swc --subscription "Upgrade - Microsoft Partner Network"
  ```

* **An alert does not stop spend.** It sends a message. When one fires, look at Cost analysis for the group, find the meter, and switch off the live path first. The recorded run is static and keeps working.
* **What this template costs at rest.** Static Web Apps Free has no charge. Cosmos DB serverless bills per request and per GB stored, with no idle charge. Storage is Standard LRS with a one-day delete rule on sandbox blobs (the rule runs about once a day, so a blob can live up to a day longer). Log Analytics keeps 30 days and caps ingestion at 1 GB a day. The two Foundry model deployments are DataZoneStandard, billed per token with no hourly charge; the Foundry resource itself has no idle charge. Translator F0 is free within its monthly character allowance; Speech S0 bills per character synthesized. No compute is deployed yet. These are the service pricing models, not a measured bill.
* **Later lanes add** the app-level circuit breaker in the Cosmos `usage` container and the live-run caps of spec 9.3.

## Demo survival until 2026-11-11 07:59 UTC

Judges may open the public URL at any time until 2026-11-11 07:59 UTC. Until then:

* The default view stays the recorded run, served as static files from Static Web Apps Free. It costs nothing and cannot be stopped by a budget alert.
* No teardown, no key or identity rotation, no identity redeploy and no recreation of the static site, Cosmos account or storage account.
* Never make the repository private. A private flip can take a judge URL down, and making it public again does not always bring it back.
* After the submission deadline, 2026-10-28 06:59 UTC, nothing changes in the repository or on the site. A defect is recorded, not fixed, unless the owner decides otherwise.
* The readiness gate keeps checking the live URL: on every deploy (with the commit sha as a marker) and twice a day from `.github/workflows/readiness.yml` (05:17 and 17:17 UTC, read only, no commit marker because the deployed commit is not always the head of `main`). That workflow stops checking by itself after 2026-11-11 07:59 UTC; disable it then.

## Known gaps

* Until owner step 4 is done, the `pull_request` federated subject still maps to the Contributor app, so a branch could add a workflow that logs in with it. Status: pending owner action, opened 2026-10-19.
* Pull requests show no Azure what-if diff. The diff appears only in the `main` run, just before the deploy job.

* App Insights availability tests on the root and `feed.json` (spec 9.4) are not built yet; the scheduled readiness workflow covers the root and the data manifest meanwhile.
* Application Insights still accepts telemetry with its connection string (local authentication is on). Keyless telemetry needs a Monitoring Metrics Publisher grant per app identity in `infra/rbac.bicep`; decide when the apps exist.
* The Static Web Apps deploy action is pinned by commit, but it pulls a container image that the pin does not fix.
* Anonymous blob read needs `allowBlobPublicAccess` on the account. If an Azure Policy in the subscription denies it, the storage deployment fails and the feed design needs another route.
* The first deploy creates the identities and their Cosmos role assignments in one run. If Microsoft Entra ID has not yet propagated a new identity, an assignment can fail once; rerunning the workflow fixes it.
* Grants for resources that do not exist yet (Key Vault, a registry) are listed at the top of `infra/rbac.bicep` and are added with those resources.
* The Cognitive Services templates use API version 2026-03-01, not the latest GA 2026-07-01, because the pinned Bicep CLI v0.44.1 has no type data for 2026-05-01 or later and the lint gate fails on BCP081. Moving to a newer API version means moving the CLI pin in `ci.yml` and `deploy.yml` together.
* `id-fleet` holds Foundry User on the project, as spec 7.1 says. Whether a project-scope grant also authorizes direct chat completions on the account's OpenAI endpoint is not verified; the live allow and deny tests settle it, and the fallback is Cognitive Services OpenAI User on the account.
* The Foundry project's Application Insights connection stores the component's connection string as an ApiKey credential, which is the documented shape for that connection category. It works because Application Insights still accepts telemetry with its connection string (the gap above). The App Insights connection itself is unverified until a traced run shows spans in the workspace.
* The CI identity's Cosmos grant is container-scoped only. If the Cosmos SDK needs the account-scope `readMetadata` action at client start, add CI to `metadataPrincipals` in `infra/modules/cosmos-access.bicep` with a reason, not a wider data role.
* The Writer guardrail of spec 7.4 (prompt shields, groundedness, protected material) is not in this template yet: both deployments use the default content filter `Microsoft.DefaultV2`.
