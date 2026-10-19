// The Office 365 Outlook API connection for the approval email channel (spec 14
// item 5). Deployed as its own deployment, agon-connections, and ONLY when the
// connection does not exist yet: the deploy workflow checks first and never
// redeploys it, so the owner's OAuth consent on it is never reset.
//
// After the first deploy the connection reads "Unauthenticated" until the owner
// authorises it once in the portal (rg-agon-swc, API connection agon-outlook,
// Edit API connection, Authorize, Save). Until then flows.bicep skips the Outlook
// approval Logic App and says so in the job summary.
//
// The Teams connection is NOT here: it is agon-teams, created for gate T and
// authorised by the owner on 2026-10-19; flows.bicep only references it.

targetScope = 'resourceGroup'

@description('Region of the connection; the same as the Logic Apps that use it.')
param location string

@description('Extra tags. app=agon is always applied and cannot be overridden.')
param extraTags object = {}

var tags = union(extraTags, {
  app: 'agon'
})

resource outlook 'Microsoft.Web/connections@2016-06-01' = {
  name: 'agon-outlook'
  location: location
  tags: tags
  properties: {
    displayName: 'Agon Outlook approval email (owner consent required)'
    api: {
      id: subscriptionResourceId('Microsoft.Web/locations/managedApis', location, 'office365')
    }
  }
}

output outlookConnectionName string = outlook.name
