// One Azure RBAC role assignment scoped to one blob container. Used by
// infra/rbac.bicep only; needs Microsoft.Authorization/roleAssignments/write.

@description('Storage account that holds the container.')
param storageAccountName string

@description('Blob container name.')
param containerName string

@description('Principal id of the managed identity that receives the role.')
param principalId string

@description('Built-in role definition GUID.')
param roleDefinitionGuid string

@description('Why this grant exists, kept on the assignment for auditors.')
param reason string

resource account 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' existing = {
  parent: account
  name: 'default'
}

resource container 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = {
  parent: blobService
  name: containerName
}

resource assignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: container
  name: guid(container.id, principalId, roleDefinitionGuid)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleDefinitionGuid)
    principalId: principalId
    principalType: 'ServicePrincipal'
    description: reason
  }
}

output id string = assignment.id
