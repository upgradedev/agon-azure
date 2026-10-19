// Azure RBAC role assignments for the Agon identities (spec 7.1), each at the
// narrowest scope the service allows: one blob container, one queue, one Foundry
// project or one Cognitive Services account, never the resource group.
//
// NOT deployed by CI. The CI identity holds Contributor on the resource group,
// which cannot write Microsoft.Authorization role assignments. The owner-side
// session deploys this file after both CI deployments, feeding it their outputs:
// agon-main (infra/main.bicep) for storage and the identities, agon-ai
// (infra/ai.bicep) for the Foundry, Translator and Speech names. The commands
// are in docs/OPERATIONS.md. CI only compiles and lints it.
//
// Deploy order: only after agon-main AND agon-ai both read Succeeded. While a CI
// deploy is Running, `az deployment group show` returns null outputs for that
// deployment (agon-main or agon-ai); on 2026-10-19 that fed an empty
// storageAccountName into this file and every assignment failed with
// ParentResourceNotFound. Every parameter below carries @minLength(1), or 2
// where Azure's own name minimum is 2, so an empty output fails validation
// before any write.
//
// Cosmos data-plane grants are not here: they are Microsoft.DocumentDB resources
// in modules/cosmos-access.bicep, deployed by main.bicep under Contributor.
//
// Not here yet, because the resources do not exist in any template yet (add them
// with the resource, in this file):
//   id-publisher: Key Vault Crypto User on the one signing key.
//   pulls from a registry, if ACR is chosen over GHCR: AcrPull per app identity.
// id-mcp and id-logicapp hold no Azure RBAC role on storage. id-mcp reads Cosmos
// only (cosmos-access.bicep); id-logicapp calls the api over HTTP with an Entra
// token whose audience is the api, which is app configuration, not RBAC.

targetScope = 'resourceGroup'

@description('Storage account name: the storageAccountName output of main.bicep.')
@minLength(1)
param storageAccountName string

@description('Principal id of id-fleet: the idFleetPrincipalId output of main.bicep.')
@minLength(1)
param fleetPrincipalId string

@description('Principal id of id-api: the idApiPrincipalId output of main.bicep.')
@minLength(1)
param apiPrincipalId string

@description('Principal id of id-publisher: the idPublisherPrincipalId output of main.bicep.')
@minLength(1)
param publisherPrincipalId string

@description('Principal id of id-publisher-sandbox: the idPublisherSandboxPrincipalId output of main.bicep.')
@minLength(1)
param publisherSandboxPrincipalId string

@description('Object id (not the appId) of the CI service principal agon-azure-ci: the ciPrincipalId output of main.bicep.')
@minLength(1)
param ciPrincipalId string

@description('Foundry resource name: the foundryAccountName output of ai.bicep (deployment agon-ai).')
@minLength(2)
param foundryAccountName string

@description('Foundry project name: the foundryProjectName output of ai.bicep (deployment agon-ai).')
@minLength(2)
param foundryProjectName string

@description('Translator account name: the translatorAccountName output of ai.bicep (deployment agon-ai).')
@minLength(2)
param translatorAccountName string

@description('Speech account name: the speechAccountName output of ai.bicep (deployment agon-ai).')
@minLength(2)
param speechAccountName string

// Built-in role definition GUIDs. Check each before the first deploy with
// `az role definition list --name <guid> --query [].roleName -o tsv`.
var storageBlobDataContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var storageQueueDataReader = '19e7f393-937e-4f77-808e-94535e297925'
var storageQueueDataMessageProcessor = '8a0f0c08-91a1-4084-bc3d-661d67233fed'
var storageQueueDataMessageSender = 'c6a89b2d-59bc-44d0-9896-0f6e12d7b80a'
// Listed on learn.microsoft.com (built-in roles, AI + machine learning, read
// 2026-10-19) as "Foundry User"; spec 7.1 uses its earlier name, Azure AI User.
// Reader on Foundry projects plus Microsoft.CognitiveServices/* data actions.
var foundryUser = '53ca6127-db72-4b80-b1b0-d745d6d5456d'
// Cognitive Services OpenAI User: model inference and reads only, no listkeys
// action and no management write.
var cognitiveServicesOpenAiUser = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
// Cognitive Services User: Microsoft.CognitiveServices/* data actions. It can
// also list keys, which buys nothing here: disableLocalAuth is true on every
// account, so a key cannot authenticate a call.
var cognitiveServicesUser = 'a97b65f3-24c7-4388-baec-2e87135dc908'

var queueGrants = [
  // Spec 7.1 table.
  {
    principalId: fleetPrincipalId
    queue: 'runs'
    role: storageQueueDataMessageProcessor
    reason: 'spec 7.1: id-fleet receives on runs'
  }
  {
    principalId: fleetPrincipalId
    queue: 'decisions'
    role: storageQueueDataMessageProcessor
    reason: 'spec 7.1: id-fleet receives on decisions'
  }
  {
    principalId: fleetPrincipalId
    queue: 'publish-requests'
    role: storageQueueDataMessageSender
    reason: 'spec 7.1: id-fleet sends on publish-requests'
  }
  {
    principalId: fleetPrincipalId
    queue: 'sandbox-publish-requests'
    role: storageQueueDataMessageSender
    reason: 'spec 7.1: id-fleet sends on sandbox-publish-requests'
  }
  {
    principalId: apiPrincipalId
    queue: 'runs'
    role: storageQueueDataMessageSender
    reason: 'spec 7.1: id-api sends on runs'
  }
  {
    principalId: apiPrincipalId
    queue: 'decisions'
    role: storageQueueDataMessageSender
    reason: 'spec 7.1: id-api sends on decisions'
  }
  // BEYOND THE SPEC 7.1 TABLE. Spec 3.2 rows 2, 11 and 11b scale the fleet and
  // trigger both publisher jobs from these queues with KEDA, which reads queue
  // length as the app identity, and each job must receive and delete its own
  // messages. Confirm the reader grant is needed at spike S5; remove it if not.
  {
    principalId: fleetPrincipalId
    queue: 'runs'
    role: storageQueueDataReader
    reason: 'beyond spec 7.1: KEDA queue length for the fleet scaler'
  }
  {
    principalId: fleetPrincipalId
    queue: 'decisions'
    role: storageQueueDataReader
    reason: 'beyond spec 7.1: KEDA queue length for the fleet scaler'
  }
  {
    principalId: publisherPrincipalId
    queue: 'publish-requests'
    role: storageQueueDataMessageProcessor
    reason: 'beyond spec 7.1: the Publisher job receives its own trigger queue'
  }
  {
    principalId: publisherPrincipalId
    queue: 'publish-requests'
    role: storageQueueDataReader
    reason: 'beyond spec 7.1: KEDA queue length for the Publisher job'
  }
  {
    principalId: publisherSandboxPrincipalId
    queue: 'sandbox-publish-requests'
    role: storageQueueDataMessageProcessor
    reason: 'beyond spec 7.1: the sandbox publisher job receives its own trigger queue'
  }
  {
    principalId: publisherSandboxPrincipalId
    queue: 'sandbox-publish-requests'
    role: storageQueueDataReader
    reason: 'beyond spec 7.1: KEDA queue length for the sandbox publisher job'
  }
]

// Spec 7.1: each publisher writes exactly one container. Nothing else holds a
// blob write role; the public read of feed and sandbox is anonymous.
var containerGrants = [
  {
    principalId: publisherPrincipalId
    container: 'feed'
    role: storageBlobDataContributor
    reason: 'spec 7.1: id-publisher is the only writer of feed'
  }
  {
    principalId: publisherSandboxPrincipalId
    container: 'sandbox'
    role: storageBlobDataContributor
    reason: 'spec 7.1: id-publisher-sandbox writes sandbox only'
  }
]

module queueAssignments 'modules/rbac-queue.bicep' = [for (grant, i) in queueGrants: {
  name: 'agon-rbac-queue-${i}'
  params: {
    storageAccountName: storageAccountName
    queueName: grant.queue
    principalId: grant.principalId
    roleDefinitionGuid: grant.role
    reason: grant.reason
  }
}]

module containerAssignments 'modules/rbac-container.bicep' = [for (grant, i) in containerGrants: {
  name: 'agon-rbac-container-${i}'
  params: {
    storageAccountName: storageAccountName
    containerName: grant.container
    principalId: grant.principalId
    roleDefinitionGuid: grant.role
    reason: grant.reason
  }
}]

resource foundryAccount 'Microsoft.CognitiveServices/accounts@2026-03-01' existing = {
  name: foundryAccountName
}

resource foundryProject 'Microsoft.CognitiveServices/accounts/projects@2026-03-01' existing = {
  parent: foundryAccount
  name: foundryProjectName
}

resource translator 'Microsoft.CognitiveServices/accounts@2026-03-01' existing = {
  name: translatorAccountName
}

resource speech 'Microsoft.CognitiveServices/accounts@2026-03-01' existing = {
  name: speechAccountName
}

// Role: Foundry User (Azure AI User). Spec 7.1, id-fleet row: "Azure AI User on
// the Foundry project". Scoped to the project, which is an ARM child resource
// and therefore assignable. Not verified yet: whether a project-scope grant also
// authorizes direct chat completions on the account's OpenAI endpoint. The live
// allow and deny tests settle it; the fallback is Cognitive Services OpenAI User
// on the account.
resource fleetFoundryUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: foundryProject
  name: guid(foundryProject.id, fleetPrincipalId, foundryUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', foundryUser)
    principalId: fleetPrincipalId
    principalType: 'ServicePrincipal'
    description: 'spec 7.1: id-fleet holds Azure AI User (Foundry User) on the Foundry project'
  }
}

// Role: Cognitive Services User. Spec 7.1, id-fleet row: "Translator and Speech
// user". One assignment per account; there is no narrower Translator role.
resource fleetTranslatorUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: translator
  name: guid(translator.id, fleetPrincipalId, cognitiveServicesUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUser)
    principalId: fleetPrincipalId
    principalType: 'ServicePrincipal'
    description: 'spec 7.1: id-fleet holds Cognitive Services User on Translator'
  }
}

// Role: Cognitive Services User. Spec 7.1, id-fleet row: "Translator and Speech user".
resource fleetSpeechUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: speech
  name: guid(speech.id, fleetPrincipalId, cognitiveServicesUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUser)
    principalId: fleetPrincipalId
    principalType: 'ServicePrincipal'
    description: 'spec 7.1: id-fleet holds Cognitive Services User on Speech'
  }
}

// Role: Cognitive Services OpenAI User. BEYOND THE SPEC 7.1 TABLE, which gives
// the CI principal Contributor plus User Access Administrator instead; the
// bootstrap gave it Contributor only (spec 14.2). Needed for gate C2 (spec 11.2
// S3) and the evals job of spec 9.2: CI calls the model deployments from
// .github/workflows/foundry-smoke.yml. Inference on this one account and nothing
// else: no Translator, no Speech, no project role.
resource ciFoundryOpenAiUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: foundryAccount
  name: guid(foundryAccount.id, ciPrincipalId, cognitiveServicesOpenAiUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesOpenAiUser)
    principalId: ciPrincipalId
    principalType: 'ServicePrincipal'
    description: 'beyond spec 7.1: the CI identity calls the Foundry model deployments for the gate C2 smoke and the evals'
  }
}

output cognitiveAssignmentIds array = [
  fleetFoundryUser.id
  fleetTranslatorUser.id
  fleetSpeechUser.id
  ciFoundryOpenAiUser.id
]
output queueAssignmentCount int = length(queueGrants)
output containerAssignmentCount int = length(containerGrants)
