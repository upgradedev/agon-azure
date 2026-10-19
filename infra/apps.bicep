// Container Apps jobs (spec 3.2 rows 2, 9, 11), deployed as their own deployment,
// agon-apps, after CI has pushed the image, so a failure here never touches the
// public site. One image (Dockerfile) carries every job; each job runs one module
// with its own managed identity.
//
// job-agon-intake (spec 14 item 5): event-driven from the two inbound decision
// queues. KEDA reads the queue length as id-api (Storage Queue Data Reader in
// infra/rbac.bicep), starts one execution, and `python -m agon.intake` drains both
// queues through the channel adapters into the one verifier, then exits after 20
// idle seconds. The image is pulled with id-api and AcrPull.
//
// The fleet and publisher jobs join this file when their entry points exist.

targetScope = 'resourceGroup'

@description('Region of the jobs. Sweden Central per spec 3.1.')
param location string

@description('Full image reference, <registry>/agon:<commit sha>, pushed by CI.')
@minLength(1)
param image string

@description('Storage account name: the storageAccountName output of main.bicep.')
@minLength(3)
param storageAccountName string

@description('Cosmos DB account name: the cosmosAccountName output of main.bicep.')
@minLength(3)
param cosmosAccountName string

@description('Cosmos DB database name: the cosmosDatabaseName output of main.bicep.')
@minLength(1)
param cosmosDatabaseName string

@description('Registry name: the registryName output of main.bicep.')
@minLength(5)
param registryName string

@description('Container Apps environment name: the containerEnvName output of main.bicep.')
@minLength(2)
param environmentName string

@description('Extra tags. app=agon is always applied and cannot be overridden.')
param extraTags object = {}

var tags = union(extraTags, {
  app: 'agon'
})

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' existing = {
  name: cosmosAccountName
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: registryName
}

resource environment 'Microsoft.App/managedEnvironments@2025-01-01' existing = {
  name: environmentName
}

resource idApi 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: 'id-api'
}

var intakeQueues = [
  'decisions-teams'
  'decisions-outlook'
]

resource intakeJob 'Microsoft.App/jobs@2025-01-01' = {
  name: 'job-agon-intake'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${idApi.id}': {}
    }
  }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Event'
      replicaTimeout: 600
      replicaRetryLimit: 1
      eventTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
        scale: {
          minExecutions: 0
          maxExecutions: 1
          pollingInterval: 15
          rules: [for queue in intakeQueues: {
            name: queue
            type: 'azure-queue'
            identity: idApi.id
            metadata: {
              accountName: storage.name
              queueName: queue
              queueLength: '1'
            }
          }]
        }
      }
      registries: [
        {
          server: registry.properties.loginServer
          identity: idApi.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'intake'
          image: image
          command: [
            'python'
            '-m'
            'agon.intake'
            '--idle-exit'
            '20'
          ]
          env: [
            {
              name: 'AGON_QUEUE_ACCOUNT_URL'
              value: storage.properties.primaryEndpoints.queue
            }
            {
              name: 'AGON_COSMOS_ENDPOINT'
              value: cosmos.properties.documentEndpoint
            }
            {
              name: 'AGON_COSMOS_DATABASE'
              value: cosmosDatabaseName
            }
            {
              name: 'AZURE_CLIENT_ID'
              value: idApi.properties.clientId
            }
          ]
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
        }
      ]
    }
  }
}

output intakeJobName string = intakeJob.name
