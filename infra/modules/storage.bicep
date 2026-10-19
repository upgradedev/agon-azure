// Storage account for the club feed, the per-visitor sandbox and the work queues
// (spec 3.2 row 12).
//
// Shared-key access is disabled. Everything this template creates (containers,
// queues, the lifecycle rule, CORS) is a control-plane write through Azure
// Resource Manager, which does not need the account key. The apps reach blobs and
// queues with their managed identities and the data roles in infra/rbac.bicep.
// Anonymous readers reach the two public containers without any key at all.
//
// Anonymous read is allowed on the account so that exactly two containers, feed
// and sandbox, can grant it at blob level (read a named blob, no listing). No
// other container exists, and a new one defaults to private.

@description('Region for the storage account.')
param location string

@description('Tags applied to every resource; must include app=agon.')
param tags object

@description('Globally unique storage account name: 3 to 24 lowercase letters and digits.')
@minLength(3)
@maxLength(24)
param name string

@description('Browser origins allowed to GET blobs cross-origin, for example https://<static site hostname>.')
param corsAllowedOrigins array = []

@description('Days after the last write before a sandbox blob is deleted.')
@minValue(1)
param sandboxRetentionDays int = 1

// The only two containers, both readable anonymously at blob level.
var publicContainers = [
  'feed'
  'sandbox'
]

// Work queues from spec 3.2 rows 1, 2, 9, 11 and 11b.
var queueNames = [
  'match-events'
  'runs'
  'decisions'
  'publish-requests'
  'sandbox-publish-requests'
  // Spec 14 item 5: one inbound queue per Logic App officer channel. Only
  // id-logicapp may send to them (infra/rbac.bicep), so the queue a decision
  // arrives on names its channel (agon.intake). Undeliverable decisions move to
  // intake-poison and are never dropped.
  'decisions-teams'
  'decisions-outlook'
  'intake-poison'
]

resource account 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: name
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    accessTier: 'Hot'
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowSharedKeyAccess: false
    defaultToOAuthAuthentication: true
    allowBlobPublicAccess: true
    allowCrossTenantReplication: false
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
    encryption: {
      keySource: 'Microsoft.Storage'
      services: {
        blob: {
          enabled: true
        }
        queue: {
          enabled: true
          keyType: 'Account'
        }
      }
    }
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: account
  name: 'default'
  properties: {
    cors: {
      corsRules: empty(corsAllowedOrigins) ? [] : [
        {
          allowedOrigins: corsAllowedOrigins
          allowedMethods: [
            'GET'
            'HEAD'
            'OPTIONS'
          ]
          allowedHeaders: [
            '*'
          ]
          exposedHeaders: [
            'Content-Length'
            'Content-Type'
            'ETag'
            'Last-Modified'
          ]
          maxAgeInSeconds: 3600
        }
      ]
    }
  }
}

resource containers 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = [for containerName in publicContainers: {
  parent: blobService
  name: containerName
  properties: {
    publicAccess: 'Blob'
  }
}]

// Sandbox blobs are throwaway output of the anonymous live path. The rule runs
// about once a day, so a blob can outlive the threshold by up to a day.
resource lifecycle 'Microsoft.Storage/storageAccounts/managementPolicies@2023-05-01' = {
  parent: account
  name: 'default'
  properties: {
    policy: {
      rules: [
        {
          name: 'delete-sandbox-blobs'
          enabled: true
          type: 'Lifecycle'
          definition: {
            filters: {
              blobTypes: [
                'blockBlob'
                'appendBlob'
              ]
              prefixMatch: [
                'sandbox/'
              ]
            }
            actions: {
              baseBlob: {
                delete: {
                  daysAfterModificationGreaterThan: sandboxRetentionDays
                }
              }
            }
          }
        }
      ]
    }
  }
  dependsOn: [
    containers
  ]
}

resource queueService 'Microsoft.Storage/storageAccounts/queueServices@2023-05-01' = {
  parent: account
  name: 'default'
}

resource queues 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-05-01' = [for queueName in queueNames: {
  parent: queueService
  name: queueName
}]

output name string = account.name
output id string = account.id
output blobEndpoint string = account.properties.primaryEndpoints.blob
output queueEndpoint string = account.properties.primaryEndpoints.queue
output feedBaseUrl string = '${account.properties.primaryEndpoints.blob}feed/'
output sandboxBaseUrl string = '${account.properties.primaryEndpoints.blob}sandbox/'
output containerNames array = [for (containerName, i) in publicContainers: containers[i].name]
output queueNames array = [for (queueName, i) in queueNames: queues[i].name]
