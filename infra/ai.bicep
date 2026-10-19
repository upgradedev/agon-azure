// Agon AI services (spec 3.2 rows 4, 6 and 7, 9.1, 11.2 S3 and S4, gate C2):
// the Microsoft Foundry resource with its project and two model deployments,
// Azure AI Translator and Azure AI Speech.
//
// A separate deployment, agon-ai, NOT part of agon-main. Deployed by the "ai" job
// of .github/workflows/deploy.yml after agon-main has succeeded. The reason is the
// judge path: a first deploy of these resources can fail for reasons this
// repository cannot check in advance (a free-tier slot already taken in the
// subscription, unaccepted AI services terms, Data Zone capacity or the model
// version refused, an unregistered provider). Inside agon-main any of those
// would fail the whole deployment, skip the web and readiness jobs and leave the
// scheduled readiness check reading a failed agon-main. Here a failure turns the
// ai job and the gate C2 smoke red and nothing else.
//
// Same names as before the split (aif-, trl-, spch-agon-<suffix>, the suffix being
// uniqueString of the resource group id), so a redeploy updates the same
// resources. Always incremental, like agon-main: the resource group holds
// resources this template does not declare.
//
// Azure RBAC on these accounts is in infra/rbac.bicep, which reads this
// deployment's outputs (docs/OPERATIONS.md, owner step 3).

targetScope = 'resourceGroup'

@description('Region for the Foundry, Translator and Speech accounts. Sweden Central per spec 3.1.')
param location string = resourceGroup().location

@description('Existing workspace-based Application Insights component the Foundry project traces to: the appInsightsName output of agon-main.')
@minLength(1)
param appInsightsName string

@description('Translator pricing tier (modules/aiservices.bicep).')
@allowed([
  'F0'
  'S1'
])
param translatorSku string = 'F0'

@description('Speech pricing tier (modules/aiservices.bicep).')
@allowed([
  'F0'
  'S0'
])
param speechSku string = 'S0'

@description('Extra tags. app=agon is always applied and cannot be overridden.')
param extraTags object = {}

var tags = union(extraTags, {
  app: 'agon'
})

// The same suffix as main.bicep, so the names match across both deployments.
var suffix = uniqueString(resourceGroup().id)

module foundry 'modules/foundry.bicep' = {
  name: 'agon-foundry'
  params: {
    location: location
    tags: tags
    name: 'aif-agon-${suffix}'
    customSubDomainName: 'aif-agon-${suffix}'
    appInsightsName: appInsightsName
  }
}

module aiServices 'modules/aiservices.bicep' = {
  name: 'agon-aiservices'
  params: {
    location: location
    tags: tags
    translatorName: 'trl-agon-${suffix}'
    translatorSubDomain: 'trl-agon-${suffix}'
    translatorSku: translatorSku
    speechName: 'spch-agon-${suffix}'
    speechSubDomain: 'spch-agon-${suffix}'
    speechSku: speechSku
  }
}

// Microsoft Foundry (spec 3.2 row 4). Deployment names are confirmed at gate C2
// by .github/workflows/foundry-smoke.yml and are not judge-facing before then.
output foundryAccountName string = foundry.outputs.accountName
output foundryEndpoint string = foundry.outputs.endpoint
output foundryOpenAiEndpoint string = foundry.outputs.openAiEndpoint
output foundryProjectName string = foundry.outputs.projectName
output foundryProjectEndpoint string = foundry.outputs.projectEndpoint
output foundryDeploymentNames array = foundry.outputs.deploymentNames

// Azure AI Translator and Speech (spec 3.2 rows 6 and 7).
output translatorAccountName string = aiServices.outputs.translatorName
output translatorEndpoint string = aiServices.outputs.translatorEndpoint
output translatorCustomEndpoint string = aiServices.outputs.translatorCustomEndpoint
output translatorRegion string = aiServices.outputs.translatorRegion
output speechAccountName string = aiServices.outputs.speechName
output speechEndpoint string = aiServices.outputs.speechEndpoint
output speechRegion string = aiServices.outputs.speechRegion
output speechResourceId string = aiServices.outputs.speechResourceId
