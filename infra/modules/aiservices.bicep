// Azure AI Translator and Azure AI Speech (spec 3.2 rows 6 and 7, 9.1, 11.2 S4).
//
// API version 2026-03-01, the newest GA version of Microsoft.CognitiveServices
// that the pinned Bicep CLI v0.44.1 has type data for (see modules/foundry.bicep).
//
// Both are keyless: disableLocalAuth is true and each has a custom subdomain,
// which Microsoft Entra ID authentication requires. id-fleet reaches them with
// Cognitive Services User from infra/rbac.bicep.
//
// SKUs. Translator defaults to F0 (free). Speech defaults to S0, because spec 3.2
// row 7 names S0 for the DragonHD voices and whether F0 serves those voices is
// not verified. Microsoft allows one free (F0) account of each kind per
// subscription, and this subscription holds other projects, so check before the
// first deploy (docs/OPERATIONS.md, owner step 1b). If an F0 slot is taken, set
// translatorSku to S1; the tier of an existing account can be changed in place.

@description('Region for both accounts. Sweden Central per spec 3.1.')
param location string

@description('Tags applied to every resource; must include app=agon.')
param tags object

@description('Translator account name.')
@minLength(2)
@maxLength(64)
param translatorName string

@description('Globally unique custom subdomain for Translator.')
@minLength(2)
@maxLength(64)
param translatorSubDomain string

@description('Translator pricing tier: F0 (free, one per subscription) or S1.')
@allowed([
  'F0'
  'S1'
])
param translatorSku string = 'F0'

@description('Speech account name.')
@minLength(2)
@maxLength(64)
param speechName string

@description('Globally unique custom subdomain for Speech.')
@minLength(2)
@maxLength(64)
param speechSubDomain string

@description('Speech pricing tier: S0 (spec 3.2 row 7) or F0 (free, one per subscription).')
@allowed([
  'F0'
  'S0'
])
param speechSku string = 'S0'

resource translator 'Microsoft.CognitiveServices/accounts@2026-03-01' = {
  name: translatorName
  location: location
  tags: tags
  kind: 'TextTranslation'
  sku: {
    name: translatorSku
  }
  properties: {
    customSubDomainName: translatorSubDomain
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource speech 'Microsoft.CognitiveServices/accounts@2026-03-01' = {
  name: speechName
  location: location
  tags: tags
  kind: 'SpeechServices'
  sku: {
    name: speechSku
  }
  properties: {
    customSubDomainName: speechSubDomain
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

output translatorName string = translator.name
output translatorEndpoint string = translator.properties.endpoint
output translatorRegion string = translator.location
output translatorResourceId string = translator.id
// The endpoint Microsoft Entra calls must use. properties.endpoint of a
// TextTranslation account is the global https://api.cognitive.microsofttranslator.com/
// (read from agon-ai on 2026-10-19), which needs extra region and resource
// headers; the custom domain serves /translator/text/v3.0/translate with a
// plain bearer token (agon.agents.translator).
output translatorCustomEndpoint string = 'https://${translator.properties.customSubDomainName}.cognitiveservices.azure.com/'
output speechName string = speech.name
output speechEndpoint string = speech.properties.endpoint
output speechRegion string = speech.location
// A Speech call with a Microsoft Entra token names the resource id
// (aad#<resource id>#<token>), so the fleet needs it beside the endpoint.
output speechResourceId string = speech.id
