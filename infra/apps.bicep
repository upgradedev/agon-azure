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
// job-agon-fleet (spec 3.2 row 2, 3.4 steps 2 to 6): event-driven from the runs
// and decisions queues. KEDA reads both queue lengths as id-fleet (Storage Queue
// Data Reader in infra/rbac.bicep), and `python -m agon.fleet.worker` runs
// W-fulltime as id-fleet: a runs message to its approval pause and the approval
// card, a decisions pointer to the resumed run and its publish request. At
// startup it finds or creates the Foundry prompt agent versions for the pinned
// prompts (--ensure-agents) and verifies them; on any mismatch it exits without
// touching a message. Deployed only when the agon-ai outputs it needs are passed
// (the deploy workflow passes them after the ai job), so a failed ai job never
// blocks the intake job above.
//
// The publisher jobs join this file when their entry points exist.

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

@description('Foundry project endpoint: the foundryProjectEndpoint output of ai.bicep (deployment agon-ai). Empty skips the fleet job.')
param foundryProjectEndpoint string = ''

@description('Model deployment names: the foundryDeploymentNames output of agon-ai. The fleet job needs lightModel and writerModel among them.')
param foundryDeploymentNames array = []

@description('Deployment for the analyst, the localiser-editor and the structured roles (spec 3.2 row 4: luna for everything but the Writer).')
param lightModel string = 'gpt-5.6-luna'

@description('Deployment for the Writer prompt agent (spec 3.2 row 4: terra).')
param writerModel string = 'gpt-5.6-terra'

@description('Translator custom-domain endpoint: the translatorCustomEndpoint output of agon-ai (never the global endpoint).')
param translatorCustomEndpoint string = ''

@description('Speech endpoint: the speechEndpoint output of agon-ai.')
param speechEndpoint string = ''

@description('Speech resource id: the speechResourceId output of agon-ai (Microsoft Entra calls to Speech name it).')
param speechResourceId string = ''

@description('Static site hostname: the staticSiteDefaultHostname output of agon-main, for the card evidence link. Optional.')
param staticSiteHostname string = ''

@description('Trigger base URL of la-agon-teams-approval with api-version only (listCallbackUrl basePath; never the SAS sp, sv, sig). Empty: no Teams card.')
param teamsFlowUrl string = ''

@description('Trigger base URL of la-agon-outlook-approval with api-version only. Empty: no approval email.')
param outlookFlowUrl string = ''

@description('Trigger base URL of la-agon-teams-post with api-version only (listCallbackUrl basePath; never the SAS). Empty: no Newsroom post (half-time brief, newsroom notification).')
param teamsPostUrl string = ''

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

resource idFleet 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: 'id-fleet'
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

var fleetQueues = [
  'runs'
  'decisions'
]

var fleetEnabled = !empty(foundryProjectEndpoint) && !empty(translatorCustomEndpoint) && !empty(speechEndpoint) && !empty(speechResourceId) && contains(foundryDeploymentNames, lightModel) && contains(foundryDeploymentNames, writerModel)

var blobBase = storage.properties.primaryEndpoints.blob

resource fleetJob 'Microsoft.App/jobs@2025-01-01' = if (fleetEnabled) {
  name: 'job-agon-fleet'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${idFleet.id}': {}
    }
  }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Event'
      // A full run (Foundry agents, Translator in three languages, Speech in five
      // editions) takes minutes; an hour leaves room for a slow model day. The
      // runs message stays invisible for 30 minutes (agon.fleet.worker).
      replicaTimeout: 3600
      replicaRetryLimit: 0
      eventTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
        scale: {
          minExecutions: 0
          // Two, so a decision is not held behind a long run in the other execution.
          maxExecutions: 2
          pollingInterval: 30
          rules: [for queue in fleetQueues: {
            name: queue
            type: 'azure-queue'
            identity: idFleet.id
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
          identity: idFleet.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'fleet'
          image: image
          command: [
            'python'
            '-m'
            'agon.fleet.worker'
            '--ensure-agents'
            '--idle-exit'
            '60'
          ]
          env: [
            {
              name: 'AGON_QUEUE_ACCOUNT_URL'
              value: storage.properties.primaryEndpoints.queue
            }
            {
              name: 'AGON_BLOB_ACCOUNT_URL'
              value: blobBase
            }
            {
              name: 'AGON_FEED_BASE_URL'
              value: '${blobBase}feed/'
            }
            {
              name: 'AGON_SANDBOX_BASE_URL'
              value: '${blobBase}sandbox/'
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
              value: idFleet.properties.clientId
            }
            {
              name: 'AGON_FOUNDRY_PROJECT_ENDPOINT'
              value: foundryProjectEndpoint
            }
            {
              name: 'AGON_FOUNDRY_STRUCTURED_MODEL'
              value: lightModel
            }
            {
              name: 'AGON_FOUNDRY_AGENT_MODELS'
              value: string({
                analyst: lightModel
                writer: writerModel
                localiser_editor: lightModel
              })
            }
            {
              name: 'AGON_TRANSLATOR_ENDPOINT'
              value: translatorCustomEndpoint
            }
            {
              name: 'AGON_SPEECH_ENDPOINT'
              value: speechEndpoint
            }
            {
              name: 'AGON_SPEECH_RESOURCE_ID'
              value: speechResourceId
            }
            {
              name: 'AGON_SITE_URL'
              value: empty(staticSiteHostname) ? '' : 'https://${staticSiteHostname}/'
            }
            {
              name: 'AGON_TEAMS_FLOW_URL'
              value: teamsFlowUrl
            }
            {
              name: 'AGON_TEAMS_POST_URL'
              value: teamsPostUrl
            }
            {
              name: 'AGON_OUTLOOK_FLOW_URL'
              value: outlookFlowUrl
            }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
    }
  }
}

// Spec 3.2 rows 11 and 11b: one Publisher job per target, each with its own
// identity and its own trigger queue. `python -m agon.publish` recomputes the
// bundle hash from the bytes first, reads the approval record, then writes
// create-only into the one container its identity may write (feed or sandbox).
var publishers = [
  {
    name: 'job-agon-publisher'
    target: 'feed'
    queue: 'publish-requests'
    identity: 'id-publisher'
  }
  {
    name: 'job-agon-publisher-sandbox'
    target: 'sandbox'
    queue: 'sandbox-publish-requests'
    identity: 'id-publisher-sandbox'
  }
]

resource publisherIdentities 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = [for publisher in publishers: {
  name: publisher.identity
}]

resource publisherJobs 'Microsoft.App/jobs@2025-01-01' = [for (publisher, i) in publishers: {
  name: publisher.name
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${publisherIdentities[i].id}': {}
    }
  }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Event'
      replicaTimeout: 900
      replicaRetryLimit: 1
      eventTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
        scale: {
          minExecutions: 0
          maxExecutions: 1
          pollingInterval: 15
          rules: [
            {
              name: publisher.queue
              type: 'azure-queue'
              identity: publisherIdentities[i].id
              metadata: {
                accountName: storage.name
                queueName: publisher.queue
                queueLength: '1'
              }
            }
          ]
        }
      }
      registries: [
        {
          server: registry.properties.loginServer
          identity: publisherIdentities[i].id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'publisher'
          image: image
          command: [
            'python'
            '-m'
            'agon.publish'
            '--target'
            publisher.target
            '--idle-exit'
            '20'
          ]
          env: [
            {
              name: 'AGON_PUBLISH_TARGET'
              value: publisher.target
            }
            {
              name: 'AGON_QUEUE_ACCOUNT_URL'
              value: storage.properties.primaryEndpoints.queue
            }
            {
              name: 'AGON_BLOB_ACCOUNT_URL'
              value: storage.properties.primaryEndpoints.blob
            }
            {
              name: 'AGON_PUBLIC_BASE_URL'
              value: take(storage.properties.primaryEndpoints.blob, length(storage.properties.primaryEndpoints.blob) - 1)
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
              value: publisherIdentities[i].properties.clientId
            }
            {
              name: 'AGON_TEAMS_POST_URL'
              value: publisher.target == 'feed' ? teamsPostUrl : ''
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
}]

output intakeJobName string = intakeJob.name
output fleetJobName string = fleetEnabled ? fleetJob.name : ''
output publisherJobNames array = [for (publisher, i) in publishers: publisherJobs[i].name]
