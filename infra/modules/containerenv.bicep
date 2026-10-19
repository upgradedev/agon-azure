// Azure Container Apps environment, Consumption only (spec 3.2 rows 2, 9, 11), with
// logs to the existing Log Analytics workspace. An idle Consumption environment
// costs nothing; jobs bill per execution second.

@description('Region for the environment. Sweden Central per spec 3.1.')
param location string

@description('Tags applied to every resource; must include app=agon.')
param tags object

@description('Environment name.')
@minLength(2)
@maxLength(32)
param name string

@description('Existing Log Analytics workspace name (from modules/monitoring.bicep).')
@minLength(4)
param workspaceName string

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: workspaceName
}

resource environment 'Microsoft.App/managedEnvironments@2025-01-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: workspace.properties.customerId
        sharedKey: workspace.listKeys().primarySharedKey
      }
    }
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
    zoneRedundant: false
  }
}

output name string = environment.name
