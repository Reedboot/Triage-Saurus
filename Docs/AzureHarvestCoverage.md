# Azure harvest coverage

The Azure harvest is subscription-scoped. Every provider passes the selected
subscription ID to Azure CLI, so resources are collected independently for
each subscription returned by `az account list`; resources in another tenant
or outside the caller's RBAC permissions are not visible to the harvest.

## AI and Foundry coverage

| Service | Resource types collected | Notes |
|---|---|---|
| Microsoft Foundry / Azure OpenAI / AI Services | `Microsoft.CognitiveServices/accounts` | Includes OpenAI, Speech, Vision, Language, Document Intelligence, Content Safety, and other account kinds. Network access, private endpoints, local-auth status, and endpoint are captured. |
| Foundry projects | `Microsoft.CognitiveServices/accounts/projects` | Child resources retain their parent account ID. |
| Foundry project connections | `Microsoft.CognitiveServices/accounts/projects/connections` | Collected as child inventory; connection secrets are not fetched. |
| Model deployments | `Microsoft.CognitiveServices/accounts/deployments` | Collected as child inventory; deployment data is not treated as a separate network endpoint. |
| Azure Machine Learning / Foundry hubs | `Microsoft.MachineLearningServices/workspaces` | Workspace URL, public access, and private endpoint state are captured. |
| ML serving and workspace assets | `onlineEndpoints`, endpoint deployments, `batchEndpoints`, batch deployments, `computes`, `connections`, and `datastores` | Collected as workspace child resources with parent IDs. |
| Azure AI Search | `Microsoft.Search/searchServices` | Service endpoint and network controls are collected. |

Azure AI Search indexes, indexers, data sources, skillsets, aliases, and
synonym maps are data-plane objects. They are intentionally not queried by
the subscription harvest because doing so requires data-plane credentials
(admin keys or appropriate Search data-plane roles) and would make a
subscription inventory dependent on per-service authentication.

## Known adjacent gaps

The harvest does not claim to enumerate every Azure resource provider. Services
not currently registered in `Scripts/Harvest/harvest_azure_assets.py` should be
added with a provider-specific collector when their network exposure or
dependency relationships affect architecture analysis. Examples for future
coverage include Azure Bot Service, Azure Maps, Communication Services, Media
Services, and Notification Hubs.
