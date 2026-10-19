// Agon core infrastructure (spec 3.1, 3.2 rows 8, 12, 13, 14, 9.1). The AI
// services (rows 4, 6, 7) are a separate deployment, agon-ai, in infra/ai.bicep,
// so a failure there never fails this one or the public site it serves.
//
// Deployed by .github/workflows/deploy.yml with the CI identity, which holds
// Contributor on the resource group only. Contributor cannot write
// Microsoft.Authorization role assignments, so every Azure RBAC grant is in
// infra/rbac.bicep, deployed by the owner-side session (docs/OPERATIONS.md).
// Cosmos data-plane grants are Microsoft.DocumentDB resources and are deployed
// here.
//
// Always deploy in incremental mode (the default). The resource group also holds
// the gate T spike (agon-teams, la-agon-gate-t, la-agon-decision-stub), which a
// complete-mode deployment of this template would delete.

targetScope = 'resourceGroup'

@description('Data-plane region. Sweden Central per spec 3.1.')
param location string = resourceGroup().location

@description('Static Web Apps region. The service is not offered in swedencentral.')
param staticSiteLocation string = 'westeurope'

@description('Object id of the CI service principal (agon-azure-ci), NOT its appId. deploy.yml reads it from the oid claim of its own access token. Gets a Cosmos data role on the checkpoints container only (modules/cosmos-access.bicep).')
@minLength(1)
param ciPrincipalId string

@description('Extra tags. app=agon is always applied and cannot be overridden.')
param extraTags object = {}

@description('Extra browser origins allowed to read the feed and sandbox containers, for example a custom domain.')
param extraCorsOrigins array = []

var tags = union(extraTags, {
  app: 'agon'
})

// Stable per resource group, so a redeploy updates the same resources.
var suffix = uniqueString(resourceGroup().id)

module monitoring 'modules/monitoring.bicep' = {
  name: 'agon-monitoring'
  params: {
    location: location
    tags: tags
    workspaceName: 'log-agon-${suffix}'
    appInsightsName: 'appi-agon-${suffix}'
  }
}

module identities 'modules/identities.bicep' = {
  name: 'agon-identities'
  params: {
    location: location
    tags: tags
  }
}

module staticSite 'modules/staticsite.bicep' = {
  name: 'agon-staticsite'
  params: {
    location: staticSiteLocation
    tags: tags
    name: 'swa-agon-${suffix}'
  }
}

module storage 'modules/storage.bicep' = {
  name: 'agon-storage'
  params: {
    location: location
    tags: tags
    name: 'stagon${suffix}'
    corsAllowedOrigins: concat([
      'https://${staticSite.outputs.defaultHostname}'
    ], extraCorsOrigins)
  }
}

module cosmos 'modules/cosmos.bicep' = {
  name: 'agon-cosmos'
  params: {
    location: location
    tags: tags
    name: 'cosmos-agon-${suffix}'
  }
}

module cosmosAccess 'modules/cosmos-access.bicep' = {
  name: 'agon-cosmos-access'
  params: {
    accountName: cosmos.outputs.accountName
    databaseName: cosmos.outputs.databaseName
    fleetPrincipalId: identities.outputs.fleetPrincipalId
    mcpPrincipalId: identities.outputs.mcpPrincipalId
    apiPrincipalId: identities.outputs.apiPrincipalId
    publisherPrincipalId: identities.outputs.publisherPrincipalId
    publisherSandboxPrincipalId: identities.outputs.publisherSandboxPrincipalId
    ciPrincipalId: ciPrincipalId
  }
}

output storageAccountName string = storage.outputs.name
output feedBaseUrl string = storage.outputs.feedBaseUrl
output sandboxBaseUrl string = storage.outputs.sandboxBaseUrl
output queueNames array = storage.outputs.queueNames

output cosmosAccountName string = cosmos.outputs.accountName
output cosmosEndpoint string = cosmos.outputs.endpoint
output cosmosDatabaseName string = cosmos.outputs.databaseName
output cosmosContainerNames array = cosmos.outputs.containerNames

// One string per identity, so infra/rbac.bicep can take them as plain parameters.
output idFleetPrincipalId string = identities.outputs.fleetPrincipalId
output idMcpPrincipalId string = identities.outputs.mcpPrincipalId
output idApiPrincipalId string = identities.outputs.apiPrincipalId
output idLogicappPrincipalId string = identities.outputs.logicappPrincipalId
output idPublisherPrincipalId string = identities.outputs.publisherPrincipalId
output idPublisherSandboxPrincipalId string = identities.outputs.publisherSandboxPrincipalId
output identityClientIds object = identities.outputs.clientIds
output identityResourceIds object = identities.outputs.resourceIds

output staticSiteName string = staticSite.outputs.name
output staticSiteDefaultHostname string = staticSite.outputs.defaultHostname

output logAnalyticsWorkspaceName string = monitoring.outputs.workspaceName
output appInsightsName string = monitoring.outputs.appInsightsName

// Echoed so infra/rbac.bicep reads it from agon-main like the other identity inputs.
output ciPrincipalId string = ciPrincipalId
