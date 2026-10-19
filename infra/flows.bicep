// The production approval flows (spec 14 items 4 and 5), deployed as their own
// deployment, agon-flows, so a failure here never touches the public site.
//
// la-agon-teams-approval: the Teams card lifecycle in one channel thread.
//   1. HTTP trigger (Entra-authenticated: only id-fleet and the CI identity may
//      call it; see accessControl) carries the sealed bundle's card fields.
//   2. Optional erratum: a threaded reply under the parent report's card names
//      the correction before the new card is posted (ReplyWithCardToConversation).
//   3. Post adaptive card and wait for a response (gate T settled that this works
//      on a standard channel and returns data, responder and messageId).
//   4. The response goes, unchanged, to the decisions-teams queue with the Logic
//      App's managed identity. The api's intake verifies it (agon.intake); this
//      flow never decides anything itself.
//   5. The same card is then updated in place (UpdateCardInConversation): changes
//      requested, or published once the published manifest is readable, or an
//      honest "not confirmed yet" after 20 minutes. An erratum also replies in the
//      parent's thread once its correction is published.
//
// la-agon-outlook-approval: the Office 365 Outlook "Send approval email" channel,
// for an officer away from Teams. Deployed only once the agon-outlook connection
// is authorised (outlookConnected). An email cannot carry tick boxes, so its
// options name the Confirm rows in words (agon.governance.channels.outlook) and it
// approves the destinations as offered.
//
// The team, channel and officer address are parameters read from repository
// variables at deploy time; none of them is committed.

targetScope = 'resourceGroup'

@description('Region of the Logic Apps. Sweden Central per spec 3.1.')
param location string

@description('Storage account name: the storageAccountName output of main.bicep.')
@minLength(3)
param storageAccountName string

@description('Microsoft Teams team (group) id of the media office team.')
@minLength(1)
param teamsGroupId string

@description('Standard channel id of the approval channel in that team.')
@minLength(1)
param teamsChannelId string

@description('App (client) id of the CI identity, allowed to fire the flows for drills.')
@minLength(1)
param ciClientId string

@description('True once the agon-outlook connection reads Connected; the Outlook flow is skipped until then.')
param outlookConnected bool = false

@description('Officer mailbox for the approval email. Required when outlookConnected is true.')
param officerEmail string = ''

@description('Issuer the trigger policy requires: managed identity and CI tokens for Azure Resource Manager are v1 tokens from this issuer.')
param tokenIssuer string = 'https://sts.windows.net/${tenant().tenantId}/'

@description('Token audience for Azure Storage data-plane calls made with the Logic App identity.')
param storageAudience string = 'https://storage.azure.com/'

@description('Extra tags. app=agon is always applied and cannot be overridden.')
param extraTags object = {}

var tags = union(extraTags, {
  app: 'agon'
})

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

resource logicIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: 'id-logicapp'
}

resource fleetIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: 'id-fleet'
}

// Created and authorised for gate T on 2026-10-19. Referenced, never redeclared:
// redeploying it could reset the owner's consent.
resource teamsConnection 'Microsoft.Web/connections@2016-06-01' existing = {
  name: 'agon-teams'
}

resource outlookConnection 'Microsoft.Web/connections@2016-06-01' existing = {
  name: 'agon-outlook'
}

var queueBase = storage.properties.primaryEndpoints.queue
var recipient = {
  groupId: teamsGroupId
  channelId: teamsChannelId
}

// Only these two identities may fire a flow: the fleet worker after it issues a
// nonce, and the CI identity for drills. Tokens are for Azure Resource Manager.
var callers = {
  type: 'AAD'
  claims: [
    {
      name: 'iss'
      value: tokenIssuer
    }
  ]
}
var accessControl = {
  triggers: {
    openAuthenticationPolicies: {
      policies: {
        fleet: union(callers, {
          claims: concat(callers.claims, [
            {
              name: 'appid'
              value: fleetIdentity.properties.clientId
            }
          ])
        })
        ci: union(callers, {
          claims: concat(callers.claims, [
            {
              name: 'appid'
              value: ciClientId
            }
          ])
        })
      }
    }
  }
}

// One queue message, shape agon.intake.decode_message expects: base64 of
// {"run_id": ..., "payload": ...}.
func queueSend(uri string, identityId string, audience string, composeName string) object => {
  type: 'Http'
  inputs: {
    method: 'POST'
    uri: uri
    headers: {
      'x-ms-version': '2021-08-06'
      'Content-Type': 'application/xml'
    }
    body: '<QueueMessage><MessageText>@{base64(string(outputs(\'${composeName}\')))}</MessageText></QueueMessage>'
    authentication: {
      type: 'ManagedServiceIdentity'
      identity: identityId
      audience: audience
    }
  }
}

func teamsPath(operation string) string => '/v1.0/teams/conversation/${operation}/poster/@{encodeURIComponent(\'Flow bot\')}/location/@{encodeURIComponent(\'Channel\')}'

func statusCard(title string, lines array) object => {
  type: 'AdaptiveCard'
  '$schema': 'http://adaptivecards.io/schemas/adaptive-card.json'
  version: '1.4'
  body: concat([
    {
      type: 'TextBlock'
      size: 'Medium'
      weight: 'Bolder'
      wrap: true
      text: title
    }
  ], map(lines, line => {
    type: 'TextBlock'
    wrap: true
    text: line
  }))
}

resource teamsFlow 'Microsoft.Logic/workflows@2019-05-01' = {
  name: 'la-agon-teams-approval'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${logicIdentity.id}': {}
    }
  }
  properties: {
    state: 'Enabled'
    accessControl: accessControl
    parameters: {
      '$connections': {
        value: {
          teams: {
            connectionId: teamsConnection.id
            connectionName: teamsConnection.name
            id: subscriptionResourceId('Microsoft.Web/locations/managedApis', location, 'teams')
          }
        }
      }
    }
    definition: {
      '$schema': 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#'
      contentVersion: '1.0.0.0'
      parameters: {
        '$connections': {
          type: 'Object'
          defaultValue: {}
        }
      }
      triggers: {
        manual: {
          type: 'Request'
          kind: 'Http'
          inputs: {
            schema: {
              type: 'object'
              required: [
                'run_id'
                'bundle_sha256'
                'nonce'
                'title'
                'published_url'
              ]
              properties: {
                run_id: {
                  type: 'string'
                }
                bundle_sha256: {
                  type: 'string'
                }
                nonce: {
                  type: 'string'
                }
                title: {
                  type: 'string'
                }
                match_line: {
                  type: 'string'
                }
                expires_at: {
                  type: 'string'
                }
                editions: {
                  type: 'array'
                }
                attested: {
                  type: 'string'
                }
                gate_cleared: {
                  type: 'string'
                }
                confirm_rows: {
                  type: 'array'
                }
                destinations: {
                  type: 'array'
                }
                evidence_url: {
                  type: 'string'
                }
                published_url: {
                  type: 'string'
                }
                verify_url: {
                  type: 'string'
                }
                parent_message_id: {
                  type: 'string'
                }
                erratum_note: {
                  type: 'string'
                }
              }
            }
          }
        }
      }
      actions: {
        Confirm_rows: {
          type: 'Select'
          runAfter: {}
          inputs: {
            from: '@coalesce(triggerBody()?[\'confirm_rows\'], json(\'[]\'))'
            select: {
              type: 'Input.Toggle'
              id: '@{concat(\'confirm:\', item()?[\'id\'])}'
              title: '@{item()?[\'text\']}'
              valueOn: 'true'
              valueOff: 'false'
              isRequired: true
              errorMessage: 'Tick this sentence before approving.'
            }
          }
        }
        Destination_toggles: {
          type: 'Select'
          runAfter: {
            Confirm_rows: [
              'Succeeded'
            ]
          }
          inputs: {
            from: '@coalesce(triggerBody()?[\'destinations\'], json(\'[]\'))'
            select: {
              type: 'Input.Toggle'
              id: '@{concat(\'dest:\', item()?[\'id\'])}'
              title: '@{item()?[\'label\']}'
              value: 'true'
              valueOn: 'true'
              valueOff: 'false'
            }
          }
        }
        Is_erratum: {
          type: 'If'
          runAfter: {
            Erratum_notice: [
              'Succeeded'
            ]
          }
          expression: {
            and: [
              {
                not: {
                  equals: [
                    '@coalesce(triggerBody()?[\'parent_message_id\'], \'\')'
                    ''
                  ]
                }
              }
            ]
          }
          actions: {
            Reply_in_parent_thread: {
              type: 'ApiConnection'
              runAfter: {}
              inputs: {
                host: {
                  connection: {
                    name: '@parameters(\'$connections\')[\'teams\'][\'connectionId\']'
                  }
                }
                method: 'post'
                path: teamsPath('replyWithAdaptivecard')
                body: {
                  recipient: recipient
                  parentMessageId: '@triggerBody()?[\'parent_message_id\']'
                  messageBody: '@{string(outputs(\'Erratum_notice\'))}'
                }
              }
            }
          }
          else: {
            actions: {}
          }
        }
        Erratum_notice: {
          type: 'Compose'
          runAfter: {
            Destination_toggles: [
              'Succeeded'
            ]
          }
          inputs: statusCard('Correction proposed for this report', [
            '@{coalesce(triggerBody()?[\'erratum_note\'], \'A corrected version is waiting for approval.\')}'
            'Review and approve it on the new card in this channel. Until then this report stays as published.'
          ])
        }
        Card: {
          type: 'Compose'
          runAfter: {
            Is_erratum: [
              'Succeeded'
            ]
          }
          inputs: {
            type: 'AdaptiveCard'
            '$schema': 'http://adaptivecards.io/schemas/adaptive-card.json'
            version: '1.4'
            body: [
              {
                type: 'TextBlock'
                size: 'Medium'
                weight: 'Bolder'
                wrap: true
                text: '@{triggerBody()?[\'title\']}'
              }
              {
                type: 'TextBlock'
                wrap: true
                isSubtle: true
                text: '@{coalesce(triggerBody()?[\'match_line\'], \'\')}'
              }
              {
                type: 'FactSet'
                facts: [
                  {
                    title: 'Bundle sha256'
                    value: '@{triggerBody()?[\'bundle_sha256\']}'
                  }
                  {
                    title: 'Editions'
                    value: '@{join(coalesce(triggerBody()?[\'editions\'], json(\'[]\')), \', \')}'
                  }
                  {
                    title: 'Approve before'
                    value: '@{coalesce(triggerBody()?[\'expires_at\'], \'\')}'
                  }
                ]
              }
              {
                type: 'TextBlock'
                wrap: true
                weight: 'Bolder'
                text: 'You are attesting'
              }
              {
                type: 'TextBlock'
                wrap: true
                text: '@{coalesce(triggerBody()?[\'attested\'], \'The headline report.\')}'
              }
              {
                type: 'TextBlock'
                wrap: true
                isSubtle: true
                text: '@{concat(\'Gate-cleared only: \', coalesce(triggerBody()?[\'gate_cleared\'], \'none\'))}'
              }
              {
                type: 'TextBlock'
                wrap: true
                weight: 'Bolder'
                text: 'Flagged sentences: tick each one before approving'
              }
              {
                type: 'Container'
                items: '@if(empty(body(\'Confirm_rows\')), createArray(json(\'{"type":"TextBlock","wrap":true,"isSubtle":true,"text":"No sentence on this bundle needs a tick."}\')), body(\'Confirm_rows\'))'
              }
              {
                type: 'TextBlock'
                wrap: true
                weight: 'Bolder'
                text: 'Destinations that receive the approved bytes'
              }
              {
                type: 'Container'
                items: '@if(empty(body(\'Destination_toggles\')), createArray(json(\'{"type":"TextBlock","wrap":true,"isSubtle":true,"text":"No destination choice on this bundle: it publishes to the club feed only."}\')), body(\'Destination_toggles\'))'
              }
              {
                type: 'TextBlock'
                wrap: true
                isSubtle: true
                size: 'Small'
                text: 'AI-drafted. Numbers and names checked by code. Nothing is published until you approve this exact hash.'
              }
            ]
            actions: [
              {
                type: 'Action.OpenUrl'
                title: 'Open evidence'
                url: '@{coalesce(triggerBody()?[\'evidence_url\'], triggerBody()?[\'verify_url\'], triggerBody()?[\'published_url\'])}'
              }
              {
                type: 'Action.Submit'
                title: 'Approve'
                style: 'positive'
                data: {
                  decision: 'approve'
                  bundle_sha256: '@{triggerBody()?[\'bundle_sha256\']}'
                  nonce: '@{triggerBody()?[\'nonce\']}'
                }
              }
              {
                type: 'Action.Submit'
                title: 'Request changes'
                associatedInputs: 'none'
                data: {
                  decision: 'changes'
                  bundle_sha256: '@{triggerBody()?[\'bundle_sha256\']}'
                  nonce: '@{triggerBody()?[\'nonce\']}'
                }
              }
            ]
          }
        }
        Post_card_and_wait: {
          type: 'ApiConnectionWebhook'
          runAfter: {
            Card: [
              'Succeeded'
            ]
          }
          inputs: {
            host: {
              connection: {
                name: '@parameters(\'$connections\')[\'teams\'][\'connectionId\']'
              }
            }
            path: '${teamsPath('gatherinput')}/$subscriptions'
            body: {
              notificationUrl: '@{listCallbackUrl()}'
              body: {
                messageBody: '@{string(outputs(\'Card\'))}'
                updateMessage: 'Decision received. Agon is checking it against the sealed bundle.'
                recipient: recipient
              }
            }
          }
        }
        Decision_message: {
          type: 'Compose'
          runAfter: {
            Post_card_and_wait: [
              'Succeeded'
            ]
          }
          inputs: {
            run_id: '@triggerBody()?[\'run_id\']'
            payload: {
              responder: '@body(\'Post_card_and_wait\')?[\'responder\']'
              data: '@body(\'Post_card_and_wait\')?[\'data\']'
              card: {
                messageId: '@body(\'Post_card_and_wait\')?[\'messageId\']'
                messageLink: '@body(\'Post_card_and_wait\')?[\'messageLink\']'
              }
            }
          }
        }
        Send_to_decisions_teams: union(queueSend('${queueBase}decisions-teams/messages', logicIdentity.id, storageAudience, 'Decision_message'), {
          runAfter: {
            Decision_message: [
              'Succeeded'
            ]
          }
        })
        Is_approve: {
          type: 'If'
          runAfter: {
            Send_to_decisions_teams: [
              'Succeeded'
            ]
            Published_card: [
              'Succeeded'
            ]
            Superseded_card: [
              'Succeeded'
            ]
            Not_confirmed_card: [
              'Succeeded'
            ]
            Changes_card: [
              'Succeeded'
            ]
          }
          expression: {
            and: [
              {
                equals: [
                  '@body(\'Post_card_and_wait\')?[\'data\']?[\'decision\']'
                  'approve'
                ]
              }
            ]
          }
          actions: {
            Until_published: {
              type: 'Until'
              runAfter: {}
              expression: '@equals(outputs(\'Check_published\')?[\'statusCode\'], 200)'
              limit: {
                count: 40
                timeout: 'PT20M'
              }
              actions: {
                Wait_30s: {
                  type: 'Wait'
                  runAfter: {}
                  inputs: {
                    interval: {
                      count: 30
                      unit: 'Second'
                    }
                  }
                }
                Check_published: {
                  type: 'Http'
                  runAfter: {
                    Wait_30s: [
                      'Succeeded'
                    ]
                  }
                  inputs: {
                    method: 'GET'
                    uri: '@triggerBody()?[\'published_url\']'
                  }
                }
              }
            }
            Was_published: {
              type: 'If'
              runAfter: {
                Until_published: [
                  'Succeeded'
                  'Failed'
                  'TimedOut'
                ]
              }
              expression: {
                and: [
                  {
                    equals: [
                      '@outputs(\'Check_published\')?[\'statusCode\']'
                      200
                    ]
                  }
                ]
              }
              actions: {
                Update_card_published: {
                  type: 'ApiConnection'
                  runAfter: {}
                  inputs: {
                    host: {
                      connection: {
                        name: '@parameters(\'$connections\')[\'teams\'][\'connectionId\']'
                      }
                    }
                    method: 'post'
                    path: teamsPath('updateAdaptivecard')
                    body: {
                      recipient: recipient
                      messageId: '@body(\'Post_card_and_wait\')?[\'messageId\']'
                      messageBody: '@{string(outputs(\'Published_card\'))}'
                    }
                  }
                }
                Erratum_published_reply: {
                  type: 'If'
                  runAfter: {
                    Update_card_published: [
                      'Succeeded'
                    ]
                  }
                  expression: {
                    and: [
                      {
                        not: {
                          equals: [
                            '@coalesce(triggerBody()?[\'parent_message_id\'], \'\')'
                            ''
                          ]
                        }
                      }
                    ]
                  }
                  actions: {
                    Reply_superseded: {
                      type: 'ApiConnection'
                      runAfter: {}
                      inputs: {
                        host: {
                          connection: {
                            name: '@parameters(\'$connections\')[\'teams\'][\'connectionId\']'
                          }
                        }
                        method: 'post'
                        path: teamsPath('replyWithAdaptivecard')
                        body: {
                          recipient: recipient
                          parentMessageId: '@triggerBody()?[\'parent_message_id\']'
                          messageBody: '@{string(outputs(\'Superseded_card\'))}'
                        }
                      }
                    }
                  }
                  else: {
                    actions: {}
                  }
                }
              }
              else: {
                actions: {
                  Update_card_not_confirmed: {
                    type: 'ApiConnection'
                    runAfter: {}
                    inputs: {
                      host: {
                        connection: {
                          name: '@parameters(\'$connections\')[\'teams\'][\'connectionId\']'
                        }
                      }
                      method: 'post'
                      path: teamsPath('updateAdaptivecard')
                      body: {
                        recipient: recipient
                        messageId: '@body(\'Post_card_and_wait\')?[\'messageId\']'
                        messageBody: '@{string(outputs(\'Not_confirmed_card\'))}'
                      }
                    }
                  }
                }
              }
            }
          }
          else: {
            actions: {
              Update_card_changes: {
                type: 'ApiConnection'
                runAfter: {}
                inputs: {
                  host: {
                    connection: {
                      name: '@parameters(\'$connections\')[\'teams\'][\'connectionId\']'
                    }
                  }
                  method: 'post'
                  path: teamsPath('updateAdaptivecard')
                  body: {
                    recipient: recipient
                    messageId: '@body(\'Post_card_and_wait\')?[\'messageId\']'
                    messageBody: '@{string(outputs(\'Changes_card\'))}'
                  }
                }
              }
            }
          }
        }
        Published_card: {
          type: 'Compose'
          runAfter: {
            Post_card_and_wait: [
              'Succeeded'
            ]
          }
          inputs: statusCard('Published', [
            '@{concat(\'Approved by \', body(\'Post_card_and_wait\')?[\'responder\']?[\'displayName\'], \' at \', body(\'Post_card_and_wait\')?[\'responseTime\'], \'.\')}'
            '@{concat(\'Published bundle sha256: \', triggerBody()?[\'bundle_sha256\'])}'
            '@{concat(\'Manifest: \', triggerBody()?[\'published_url\'])}'
            '@{concat(\'Recompute the hash in your browser: \', coalesce(triggerBody()?[\'verify_url\'], triggerBody()?[\'published_url\']))}'
          ])
        }
        Superseded_card: {
          type: 'Compose'
          runAfter: {
            Post_card_and_wait: [
              'Succeeded'
            ]
          }
          inputs: statusCard('This report was superseded', [
            '@{concat(\'Correction published as bundle \', triggerBody()?[\'bundle_sha256\'], \' at \', body(\'Post_card_and_wait\')?[\'responseTime\'], \'.\')}'
            'The earlier version stays readable and is marked superseded; it was never overwritten.'
          ])
        }
        Not_confirmed_card: {
          type: 'Compose'
          runAfter: {
            Post_card_and_wait: [
              'Succeeded'
            ]
          }
          inputs: statusCard('Approved, publication not confirmed yet', [
            '@{concat(\'Approved by \', body(\'Post_card_and_wait\')?[\'responder\']?[\'displayName\'], \' at \', body(\'Post_card_and_wait\')?[\'responseTime\'], \'.\')}'
            'The published manifest was not readable within 20 minutes. The console shows the current state of this bundle.'
          ])
        }
        Changes_card: {
          type: 'Compose'
          runAfter: {
            Post_card_and_wait: [
              'Succeeded'
            ]
          }
          inputs: statusCard('Changes requested', [
            '@{concat(\'Requested by \', body(\'Post_card_and_wait\')?[\'responder\']?[\'displayName\'], \' at \', body(\'Post_card_and_wait\')?[\'responseTime\'], \'.\')}'
            'Nothing was published.'
          ])
        }
      }
      outputs: {}
    }
  }
}

resource outlookFlow 'Microsoft.Logic/workflows@2019-05-01' = if (outlookConnected) {
  name: 'la-agon-outlook-approval'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${logicIdentity.id}': {}
    }
  }
  properties: {
    state: 'Enabled'
    accessControl: accessControl
    parameters: {
      '$connections': {
        value: {
          office365: {
            connectionId: outlookConnection.id
            connectionName: outlookConnection.name
            id: subscriptionResourceId('Microsoft.Web/locations/managedApis', location, 'office365')
          }
        }
      }
    }
    definition: {
      '$schema': 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#'
      contentVersion: '1.0.0.0'
      parameters: {
        '$connections': {
          type: 'Object'
          defaultValue: {}
        }
      }
      triggers: {
        manual: {
          type: 'Request'
          kind: 'Http'
          inputs: {
            schema: {
              type: 'object'
              required: [
                'run_id'
                'bundle_sha256'
                'nonce'
                'subject'
              ]
              properties: {
                run_id: {
                  type: 'string'
                }
                bundle_sha256: {
                  type: 'string'
                }
                nonce: {
                  type: 'string'
                }
                subject: {
                  type: 'string'
                }
                summary: {
                  type: 'string'
                }
                confirm_rows: {
                  type: 'array'
                }
                verify_url: {
                  type: 'string'
                }
              }
            }
          }
        }
      }
      actions: {
        Row_ids: {
          type: 'Select'
          runAfter: {}
          inputs: {
            from: '@coalesce(triggerBody()?[\'confirm_rows\'], json(\'[]\'))'
            select: '@item()?[\'id\']'
          }
        }
        Row_lines: {
          type: 'Select'
          runAfter: {
            Row_ids: [
              'Succeeded'
            ]
          }
          inputs: {
            from: '@coalesce(triggerBody()?[\'confirm_rows\'], json(\'[]\'))'
            select: '@concat(\'<li>\', item()?[\'text\'], \'</li>\')'
          }
        }
        Send_approval_email: {
          type: 'ApiConnectionWebhook'
          runAfter: {
            Row_lines: [
              'Succeeded'
            ]
          }
          inputs: {
            host: {
              connection: {
                name: '@parameters(\'$connections\')[\'office365\'][\'connectionId\']'
              }
            }
            path: '/approvalmail/$subscriptions'
            body: {
              NotificationUrl: '@{listCallbackUrl()}'
              Message: {
                To: officerEmail
                Subject: '@{triggerBody()?[\'subject\']}'
                Options: 'Approve and confirm flagged sentences, Approve, Request changes'
                Body: '@{concat(\'<p>\', coalesce(triggerBody()?[\'summary\'], \'\'), \'</p><p>Bundle sha256: \', triggerBody()?[\'bundle_sha256\'], \'</p><p>Flagged sentences. Choose "Approve and confirm flagged sentences" only if you have read each one:</p><ul>\', join(body(\'Row_lines\'), \'\'), \'</ul><p>Approving here approves the destinations as offered. Recompute the hash: \', coalesce(triggerBody()?[\'verify_url\'], \'\'), \'</p>\')}'
                Importance: 'Normal'
                HideHTMLMessage: false
                ShowHTMLConfirmationDialog: false
              }
            }
          }
        }
        Decision_message: {
          type: 'Compose'
          runAfter: {
            Send_approval_email: [
              'Succeeded'
            ]
          }
          inputs: {
            run_id: '@triggerBody()?[\'run_id\']'
            payload: {
              bundle_sha256: '@triggerBody()?[\'bundle_sha256\']'
              nonce: '@triggerBody()?[\'nonce\']'
              confirm_rows: '@body(\'Row_ids\')'
              response: {
                SelectedOption: '@body(\'Send_approval_email\')?[\'SelectedOption\']'
                UserEmailAddress: '@body(\'Send_approval_email\')?[\'UserEmailAddress\']'
              }
            }
          }
        }
        Send_to_decisions_outlook: union(queueSend('${queueBase}decisions-outlook/messages', logicIdentity.id, storageAudience, 'Decision_message'), {
          runAfter: {
            Decision_message: [
              'Succeeded'
            ]
          }
        })
      }
      outputs: {}
    }
  }
}

output teamsFlowName string = teamsFlow.name
output outlookFlowName string = outlookConnected ? 'la-agon-outlook-approval' : ''
