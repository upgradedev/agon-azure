// Azure Cosmos DB for NoSQL, serverless, keys disabled (spec 3.2 row 8).
//
// Every container is pre-created here, because a managed identity holding only a
// data-plane role cannot create databases or containers (spec trap 5). With local
// auth disabled, the only way in is Microsoft Entra ID plus the data roles in
// modules/cosmos-access.bicep.

@description('Region for the account.')
param location string

@description('Tags applied to every resource; must include app=agon.')
param tags object

@description('Globally unique account name: 3 to 44 lowercase letters, digits and hyphens.')
@minLength(3)
@maxLength(44)
param name string

@description('Database name.')
param databaseName string = 'agon'

// Containers and partition keys from spec 3.2 row 8.
var containerSpecs = [
  {
    name: 'matchstate'
    partitionKey: '/match_id'
  }
  {
    name: 'checkpoints'
    partitionKey: '/workflow_name'
  }
  {
    name: 'charters'
    partitionKey: '/id'
  }
  {
    name: 'bundles'
    partitionKey: '/id'
  }
  {
    name: 'approvals'
    partitionKey: '/id'
  }
  {
    name: 'published'
    partitionKey: '/id'
  }
  {
    name: 'provenance'
    partitionKey: '/id'
  }
  {
    name: 'usage'
    partitionKey: '/id'
  }
  // Governance state the one verifier and the run ledger need (spec 14 item 5):
  // nonces (single-use approval requests, consumed by etag compare-and-set),
  // approvers (principal to role) and runs (the fleet's run ledger). All keyed by
  // /id like the other key-addressed containers (agon.governance.store_cosmos).
  {
    name: 'nonces'
    partitionKey: '/id'
  }
  {
    name: 'approvers'
    partitionKey: '/id'
  }
  {
    name: 'runs'
    partitionKey: '/id'
  }
  // Publisher rule 6: an erratum claims its parent create-only, then marks the
  // supersession published after its receipt exists (agon.governance.publisher).
  {
    name: 'supersessions'
    partitionKey: '/id'
  }
]

resource account 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' = {
  name: name
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    capabilities: [
      {
        name: 'EnableServerless'
      }
    ]
    disableLocalAuth: true
    disableKeyBasedMetadataWriteAccess: true
    minimalTlsVersion: 'Tls12'
    publicNetworkAccess: 'Enabled'
  }
}

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-11-15' = {
  parent: account
  name: databaseName
  tags: tags
  properties: {
    resource: {
      id: databaseName
    }
  }
}

// One at a time: the Cosmos control plane rejects overlapping operations on one
// account with a conflict, which would fail a first deploy at random.
@batchSize(1)
resource containers 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = [for spec in containerSpecs: {
  parent: database
  name: spec.name
  tags: tags
  properties: {
    resource: {
      id: spec.name
      partitionKey: {
        paths: [
          spec.partitionKey
        ]
        kind: 'Hash'
        version: 2
      }
    }
  }
}]

output accountName string = account.name
output accountId string = account.id
output endpoint string = account.properties.documentEndpoint
output databaseName string = database.name
output containerNames array = [for (spec, i) in containerSpecs: containers[i].name]
