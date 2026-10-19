// Azure Static Web Apps, Free tier, for the public front end (spec 3.2 row 13).
//
// Static Web Apps is not offered in swedencentral, so the site sits in westeurope
// while the data plane stays in Sweden Central (spec 3.1). Never describe the
// whole system as running in one region.
//
// No repository is linked. The deploy workflow fetches the deployment token at run
// time with `az staticwebapp secrets list` and uploads web/ with the
// static-web-apps-deploy action, so no token is ever stored as a repo secret.

@description('Static Web Apps region. Allowed values are the regions that offer the service.')
@allowed([
  'westeurope'
  'centralus'
  'eastus2'
  'westus2'
  'eastasia'
])
param location string

@description('Tags applied to every resource; must include app=agon.')
param tags object

@description('Static site name.')
param name string

resource site 'Microsoft.Web/staticSites@2023-12-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'Free'
    tier: 'Free'
  }
  properties: {
    allowConfigFileUpdates: true
    stagingEnvironmentPolicy: 'Disabled'
  }
}

output name string = site.name
output defaultHostname string = site.properties.defaultHostname
