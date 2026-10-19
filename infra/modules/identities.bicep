// User-assigned managed identities, one per role in spec section 7.1. Keyless: no
// identity here holds a secret. Every Azure RBAC grant for them lives in
// infra/rbac.bicep (deployed by the owner-side session); Cosmos data-plane grants
// live in modules/cosmos-access.bicep.

@description('Region for the identities.')
param location string

@description('Tags applied to every resource; must include app=agon.')
param tags object

// Fixed names from spec 7.1. Order is the order of the outputs.
var names = [
  'id-fleet'
  'id-mcp'
  'id-api'
  'id-logicapp'
  'id-publisher'
  'id-publisher-sandbox'
]

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = [for name in names: {
  name: name
  location: location
  tags: tags
}]

output fleetPrincipalId string = identity[0].properties.principalId
output mcpPrincipalId string = identity[1].properties.principalId
output apiPrincipalId string = identity[2].properties.principalId
output logicappPrincipalId string = identity[3].properties.principalId
output publisherPrincipalId string = identity[4].properties.principalId
output publisherSandboxPrincipalId string = identity[5].properties.principalId

output clientIds object = {
  'id-fleet': identity[0].properties.clientId
  'id-mcp': identity[1].properties.clientId
  'id-api': identity[2].properties.clientId
  'id-logicapp': identity[3].properties.clientId
  'id-publisher': identity[4].properties.clientId
  'id-publisher-sandbox': identity[5].properties.clientId
}

output resourceIds object = {
  'id-fleet': identity[0].id
  'id-mcp': identity[1].id
  'id-api': identity[2].id
  'id-logicapp': identity[3].id
  'id-publisher': identity[4].id
  'id-publisher-sandbox': identity[5].id
}
