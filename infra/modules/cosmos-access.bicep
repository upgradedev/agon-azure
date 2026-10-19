// Cosmos DB data-plane role assignments (spec 7.1).
//
// These are Microsoft.DocumentDB child resources, not Microsoft.Authorization role
// assignments, so the CI identity can deploy them with Contributor on the group.
// Scopes are an account, a database or a container, never a document field or a
// status value: bundle status transitions are enforced in code (spec 3.2 row 8).
//
// Every operation runs one at a time and the groups run in a fixed order, because
// the Cosmos control plane answers overlapping writes on one account with a
// conflict.

@description('Existing Cosmos DB account name (from modules/cosmos.bicep).')
param accountName string

@description('Database that holds the containers.')
param databaseName string

@description('Principal id of id-fleet.')
param fleetPrincipalId string

@description('Principal id of id-mcp.')
param mcpPrincipalId string

@description('Principal id of id-api.')
param apiPrincipalId string

@description('Principal id of id-publisher.')
param publisherPrincipalId string

@description('Principal id of id-publisher-sandbox.')
param publisherSandboxPrincipalId string

@description('Object id of the CI service principal (agon-azure-ci).')
@minLength(1)
param ciPrincipalId string

resource account 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' existing = {
  name: accountName
}

var databaseScope = '${account.id}/dbs/${databaseName}'

// Built-in data roles that exist on every account.
resource builtInReader 'Microsoft.DocumentDB/databaseAccounts/sqlRoleDefinitions@2024-11-15' existing = {
  parent: account
  name: '00000000-0000-0000-0000-000000000001'
}

resource builtInContributor 'Microsoft.DocumentDB/databaseAccounts/sqlRoleDefinitions@2024-11-15' existing = {
  parent: account
  name: '00000000-0000-0000-0000-000000000002'
}

// Built-in data roles, container scope. role is 'reader' or 'contributor'.
var builtInGrants = [
  // Spec 7.1 table.
  {
    principalId: fleetPrincipalId
    role: 'reader'
    container: 'matchstate'
  }
  {
    principalId: fleetPrincipalId
    role: 'reader'
    container: 'charters'
  }
  {
    principalId: fleetPrincipalId
    role: 'contributor'
    container: 'checkpoints'
  }
  {
    principalId: fleetPrincipalId
    role: 'contributor'
    container: 'bundles'
  }
  {
    principalId: mcpPrincipalId
    role: 'reader'
    container: 'matchstate'
  }
  {
    principalId: mcpPrincipalId
    role: 'reader'
    container: 'charters'
  }
  {
    principalId: apiPrincipalId
    role: 'contributor'
    container: 'approvals'
  }
  {
    principalId: apiPrincipalId
    role: 'reader'
    container: 'bundles'
  }
  {
    principalId: publisherPrincipalId
    role: 'reader'
    container: 'bundles'
  }
  {
    principalId: publisherSandboxPrincipalId
    role: 'reader'
    container: 'bundles'
  }
  // BEYOND THE SPEC 7.1 TABLE. Spec 9.3 puts the spend circuit breaker in the
  // usage container: the api enforces the live-run caps and the fleet records
  // token and Speech use. Remove these two if that design changes.
  {
    principalId: fleetPrincipalId
    role: 'contributor'
    container: 'usage'
  }
  {
    principalId: apiPrincipalId
    role: 'contributor'
    container: 'usage'
  }
  // Spec 14 item 5: the api runs the one verifier for the queue and HTTP routes.
  // verify() reads the bundle, reads the approver's role, replaces the nonce on
  // consume (etag compare-and-set) and creates the approval (approvals above).
  {
    principalId: apiPrincipalId
    role: 'contributor'
    container: 'nonces'
  }
  {
    principalId: apiPrincipalId
    role: 'reader'
    container: 'approvers'
  }
  // The fleet issues nonces create-only (createOnlyGrants below), keeps the run
  // ledger, and reads approval records and roles; it never writes an approval
  // (the api verifies first, spec 3.4 step 5).
  {
    principalId: fleetPrincipalId
    role: 'contributor'
    container: 'runs'
  }
  {
    principalId: fleetPrincipalId
    role: 'reader'
    container: 'approvals'
  }
  {
    principalId: fleetPrincipalId
    role: 'reader'
    container: 'approvers'
  }
  // BEYOND THE SPEC 7.1 TABLE (spec 11.2 S1 "Cosmos once S0 passes"). The CI
  // identity runs the S1 durable-approval spike against a real Cosmos checkpoint
  // store, so it writes the checkpoints container and nothing else. It is not in
  // metadataPrincipals below: if the SDK needs the account-scope readMetadata
  // action at client start, add it there with a reason, not a wider data role.
  {
    principalId: ciPrincipalId
    role: 'contributor'
    container: 'checkpoints'
  }
]

// Spec 7.1: the Publisher may create receipts and provenance entries and read
// them back, but never replace, upsert or delete one. Create-only is what makes
// the published and provenance containers append-only by interface.
resource createOnlyRole 'Microsoft.DocumentDB/databaseAccounts/sqlRoleDefinitions@2024-11-15' = {
  parent: account
  name: guid(account.id, 'agon-create-only')
  properties: {
    roleName: 'Agon create-only data writer'
    type: 'CustomRole'
    assignableScopes: [
      databaseScope
    ]
    permissions: [
      {
        dataActions: [
          'Microsoft.DocumentDB/databaseAccounts/readMetadata'
          'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/items/create'
          'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/items/read'
          'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/executeQuery'
        ]
      }
    ]
  }
}

// BEYOND THE SPEC 7.1 TABLE. Metadata only (account, database and container
// definitions; no item can be read or written with it). The SDK reads the
// account description when a client starts, and a role scoped to one container
// may not cover that read. This avoids a second owner-side RBAC round trip if it
// does not. Remove it if the live deny tests show container scope is enough.
resource metadataRole 'Microsoft.DocumentDB/databaseAccounts/sqlRoleDefinitions@2024-11-15' = {
  parent: account
  name: guid(account.id, 'agon-metadata-reader')
  properties: {
    roleName: 'Agon metadata reader'
    type: 'CustomRole'
    assignableScopes: [
      account.id
    ]
    permissions: [
      {
        dataActions: [
          'Microsoft.DocumentDB/databaseAccounts/readMetadata'
        ]
      }
    ]
  }
  dependsOn: [
    createOnlyRole
  ]
}

@batchSize(1)
resource builtInAssignments 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = [for grant in builtInGrants: {
  parent: account
  name: guid(account.id, grant.principalId, grant.role, grant.container)
  properties: {
    roleDefinitionId: grant.role == 'reader' ? builtInReader.id : builtInContributor.id
    principalId: grant.principalId
    scope: '${databaseScope}/colls/${grant.container}'
  }
  dependsOn: [
    metadataRole
  ]
}]

// Create-only grants. The publisher's two keep their original assignment names
// (same guid() inputs). The fleet issues approval nonces but may never replace
// one: only the verifier, running as id-api, consumes a nonce.
var createOnlyGrants = [
  {
    principalId: publisherPrincipalId
    container: 'published'
  }
  {
    principalId: publisherPrincipalId
    container: 'provenance'
  }
  {
    principalId: fleetPrincipalId
    container: 'nonces'
  }
]

@batchSize(1)
resource createOnlyAssignments 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = [for grant in createOnlyGrants: {
  parent: account
  name: guid(account.id, grant.principalId, 'agon-create-only', grant.container)
  properties: {
    roleDefinitionId: createOnlyRole.id
    principalId: grant.principalId
    scope: '${databaseScope}/colls/${grant.container}'
  }
  dependsOn: [
    builtInAssignments
  ]
}]

var metadataPrincipals = [
  fleetPrincipalId
  mcpPrincipalId
  apiPrincipalId
  publisherPrincipalId
  publisherSandboxPrincipalId
]

@batchSize(1)
resource metadataAssignments 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = [for principalId in metadataPrincipals: {
  parent: account
  name: guid(account.id, principalId, 'agon-metadata-reader')
  properties: {
    roleDefinitionId: metadataRole.id
    principalId: principalId
    scope: account.id
  }
  dependsOn: [
    createOnlyAssignments
  ]
}]

output builtInAssignmentCount int = length(builtInGrants)
output createOnlyAssignmentCount int = length(createOnlyGrants)
output metadataAssignmentCount int = length(metadataPrincipals)
