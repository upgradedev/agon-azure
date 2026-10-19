// One Azure RBAC role assignment scoped to one storage queue. Used by
// infra/rbac.bicep only; needs Microsoft.Authorization/roleAssignments/write.

@description('Storage account that holds the queue.')
param storageAccountName string

@description('Queue name.')
param queueName string

@description('Principal id of the managed identity that receives the role.')
param principalId string

@description('Built-in role definition GUID.')
param roleDefinitionGuid string

@description('Why this grant exists, kept on the assignment for auditors.')
param reason string

resource account 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

resource queueService 'Microsoft.Storage/storageAccounts/queueServices@2023-05-01' existing = {
  parent: account
  name: 'default'
}

resource queue 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-05-01' existing = {
  parent: queueService
  name: queueName
}

resource assignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: queue
  name: guid(queue.id, principalId, roleDefinitionGuid)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleDefinitionGuid)
    principalId: principalId
    principalType: 'ServicePrincipal'
    description: reason
  }
}

output id string = assignment.id
