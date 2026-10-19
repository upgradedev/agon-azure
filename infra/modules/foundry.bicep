// Microsoft Foundry resource, one Foundry project and two model deployments
// (spec 3.2 row 4, 3.1, 9.1, 11.2 S3 and gate C2).
//
// API version 2026-03-01 for every Microsoft.CognitiveServices type below.
// Read on 2026-10-19 from
// https://learn.microsoft.com/en-us/azure/templates/microsoft.cognitiveservices/accounts/projects
// (version list: 2026-07-01 is the latest GA, then 2026-05-01, 2026-03-01).
// 2026-07-01 and 2026-05-01 are not used because the pinned Bicep CLI v0.44.1
// (ci.yml and deploy.yml) has no type data for them: they compile with BCP081,
// which ci/bicep_check.py fails on. 2026-03-01 is the newest GA version with
// type data, so the compiler still validates every property name here.
//
// Keyless: disableLocalAuth is true, so the account keys cannot authenticate any
// call. Every caller uses Microsoft Entra ID and a role from infra/rbac.bicep.
// No managed identity on the account or the project: nothing here needs one yet.
// If a later lane registers an Entra-authenticated MCP server through a Foundry
// Toolbox, the project may need a system identity then; add it with that change.
//
// Every write on one Cognitive Services account runs one at a time (project,
// then luna, then terra, then the App Insights connection), because the
// resource provider answers overlapping child writes on one account with
// "another operation is in progress".

@description('Region for the Foundry resource. Sweden Central per spec 3.1.')
param location string

@description('Tags applied to every resource; must include app=agon.')
param tags object

@description('Foundry resource (Cognitive Services account) name, 2 to 64 characters.')
@minLength(2)
@maxLength(64)
param name string

@description('Globally unique custom subdomain. Required for Microsoft Entra ID authentication.')
@minLength(2)
@maxLength(64)
param customSubDomainName string

@description('Foundry project name.')
@minLength(2)
@maxLength(64)
param projectName string = 'agon'

@description('Existing workspace-based Application Insights component that the project traces to.')
@minLength(1)
param appInsightsName string

@description('Model version for both deployments, pinned with no automatic upgrade.')
param modelVersion string = '2026-07-09'

@description('Capacity per deployment, in thousands of tokens per minute. Quota is 100 per model on DataZoneStandard in Sweden Central (STATE.md, 2026-10-19).')
@minValue(1)
@maxValue(100)
param capacity int = 20

// Spec 3.2 row 4: terra for the Writer, luna for everything else. The deployment
// name equals the model name, so a log line names both at once.
var modelNames = [
  'gpt-5.6-luna'
  'gpt-5.6-terra'
]

resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appInsightsName
}

resource account 'Microsoft.CognitiveServices/accounts@2026-03-01' = {
  name: name
  location: location
  tags: tags
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: customSubDomainName
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
    allowProjectManagement: true
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2026-03-01' = {
  parent: account
  name: projectName
  location: location
  tags: tags
  properties: {
    displayName: 'Agon'
    description: 'Agon match reports: analyst, writer, localiser-editor, triage and desk-editor (spec 3.2 row 4).'
  }
}

// EU Data Zone deployments (spec 3.1). raiPolicyName is the default content
// filter; the Writer guardrail of spec 7.4 is a separate change.
@batchSize(1)
resource deployments 'Microsoft.CognitiveServices/accounts/deployments@2026-03-01' = [for model in modelNames: {
  parent: account
  name: model
  sku: {
    name: 'DataZoneStandard'
    capacity: capacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: model
      version: modelVersion
    }
    versionUpgradeOption: 'NoAutoUpgrade'
    raiPolicyName: 'Microsoft.DefaultV2'
  }
  dependsOn: [
    project
  ]
}]

// Project tracing to the connected App Insights (spec 3.2 rows 4 and 14). The
// 2026-03-01 GA API has category AppInsights on project connections. The
// documented shape stores the component's connection string as an ApiKey
// credential: telemetry ingestion still accepts it because local authentication
// is on in modules/monitoring.bicep (a known gap in docs/OPERATIONS.md). It is
// read from the existing resource at deploy time and never appears in an output.
resource appInsightsConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2026-03-01' = {
  parent: project
  name: 'appi-agon'
  properties: {
    category: 'AppInsights'
    target: appInsights.id
    authType: 'ApiKey'
    isSharedToAll: true
    credentials: {
      key: appInsights.properties.ConnectionString
    }
    metadata: {
      ApiType: 'Azure'
      ResourceId: appInsights.id
    }
  }
  dependsOn: [
    deployments
  ]
}

output accountName string = account.name
output accountId string = account.id
output endpoint string = account.properties.endpoint
// The v1 inference base is <this>openai/v1/ (learn.microsoft.com/en-us/azure/ai-foundry/openai/api-version-lifecycle).
// Safe access with a built fallback, so a missing key never fails the deploy.
output openAiEndpoint string = account.properties.endpoints[?'OpenAI Language Model Instance API'] ?? 'https://${customSubDomainName}.openai.azure.com/'
output projectName string = project.name
output projectEndpoint string = project.properties.endpoints[?'AI Foundry API'] ?? 'https://${customSubDomainName}.services.ai.azure.com/api/projects/${project.name}'
output deploymentNames array = [for (model, i) in modelNames: deployments[i].name]
output appInsightsConnectionName string = appInsightsConnection.name
