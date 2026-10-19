// Azure RBAC role assignments for the Agon identities (spec 7.1), each scoped to
// one blob container or one queue, never to the account.
//
// NOT deployed by CI. The CI identity holds Contributor on the resource group,
// which cannot write Microsoft.Authorization role assignments. The owner-side
// session deploys this file after main.bicep, feeding it main's outputs; the
// commands are in docs/OPERATIONS.md. CI only compiles and lints it.
//
// Cosmos data-plane grants are not here: they are Microsoft.DocumentDB resources
// in modules/cosmos-access.bicep, deployed by main.bicep under Contributor.
//
// Not here yet, because the resources do not exist in main.bicep yet (add them
// with the resource, in this file):
//   id-fleet: Azure AI User on the Foundry project; Cognitive Services User on
//     Translator and Speech.
//   id-publisher: Key Vault Crypto User on the one signing key.
//   pulls from a registry, if ACR is chosen over GHCR: AcrPull per app identity.
// id-mcp and id-logicapp hold no Azure RBAC role on storage. id-mcp reads Cosmos
// only (cosmos-access.bicep); id-logicapp calls the api over HTTP with an Entra
// token whose audience is the api, which is app configuration, not RBAC.

targetScope = 'resourceGroup'

@description('Storage account name: the storageAccountName output of main.bicep.')
param storageAccountName string

@description('Principal id of id-fleet: the idFleetPrincipalId output of main.bicep.')
param fleetPrincipalId string

@description('Principal id of id-api: the idApiPrincipalId output of main.bicep.')
param apiPrincipalId string

@description('Principal id of id-publisher: the idPublisherPrincipalId output of main.bicep.')
param publisherPrincipalId string

@description('Principal id of id-publisher-sandbox: the idPublisherSandboxPrincipalId output of main.bicep.')
param publisherSandboxPrincipalId string

// Built-in role definition GUIDs. Check each before the first deploy with
// `az role definition list --name <guid> --query [].roleName -o tsv`.
var storageBlobDataContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var storageQueueDataReader = '19e7f393-937e-4f77-808e-94535e297925'
var storageQueueDataMessageProcessor = '8a0f0c08-91a1-4084-bc3d-661d67233fed'
var storageQueueDataMessageSender = 'c6a89b2d-59bc-44d0-9896-0f6e12d7b80a'

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

output queueAssignmentCount int = length(queueGrants)
output containerAssignmentCount int = length(containerGrants)
