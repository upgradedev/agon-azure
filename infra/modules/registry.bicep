// Azure Container Registry, Basic, for the job images (spec 3.2 row 16 leaves GHCR
// or ACR open; ACR is chosen because a new GHCR package stays private until someone
// changes it in the GitHub UI, an owner step this avoids).
//
// Keyless: the admin user is off (anonymous pull is a Standard and Premium feature
// and is off by default). The jobs pull with their own
// managed identity and AcrPull (infra/rbac.bicep); CI pushes as Contributor on the
// resource group.

@description('Region for the registry.')
param location string

@description('Tags applied to every resource; must include app=agon.')
param tags object

@description('Globally unique registry name: 5 to 50 letters and digits.')
@minLength(5)
@maxLength(50)
param name string

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
  }
}

output name string = registry.name
output loginServer string = registry.properties.loginServer
