# Cloud Service Patterns

This document explains the generalized service pattern system used across all cloud providers.

## Overview

Many cloud services follow similar hierarchical patterns:
- **Parent service** (e.g., Storage Account, API Management, Service Bus)
- **Child resources** (e.g., containers, APIs, topics)
- **Ingress endpoints** (e.g., operations, methods, blobs)
- **Authorization** (e.g., keys, subscriptions, policies, IAM roles)
- **Egress** (e.g., backend services, databases, logging)

The service pattern system in `Scripts/Persist/resource_type_db.py` defines these patterns once and applies them consistently across all cloud providers.

## Supported Patterns

### 1. API Gateway Pattern
**Services:** API Management, API Gateway, REST APIs

**Structure:**
```
Parent (APIM/API Gateway)
├── API Resources
│   ├── Operations/Methods/Routes (INGRESS)
│   ├── Policies
│   └── Backends (EGRESS)
└── Auth (Subscriptions, API Keys, Authorizers)
```

**Providers:**
- **Azure:** `azurerm_api_management` → `azurerm_api_management_api` → `azurerm_api_management_api_operation`
- **AWS:** `aws_api_gateway_rest_api` → `aws_api_gateway_resource` → `aws_api_gateway_method`
- **AWS v2:** `aws_apigatewayv2_api` → `aws_apigatewayv2_route`
- **GCP:** `google_api_gateway_api` → `google_api_gateway_api_config`
- **Oracle:** `oci_apigateway_gateway` → `oci_apigateway_deployment`
- **Alibaba:** `alicloud_api_gateway_api`

**Ingress:** Internet/Client → Operations (labeled with auth type: API Key, OAuth, JWT, mTLS, etc.)

**Auth Detection:** Subscriptions, API keys, OAuth providers, JWT validators, certificates

---

### 2. Storage Pattern
**Services:** Storage Accounts, S3 Buckets, Cloud Storage

**Structure:**
```
Parent (Storage Account/Bucket)
├── Containers/Buckets
│   └── Blobs/Objects (INGRESS via SAS/signed URLs)
├── Queues
├── Tables
└── Auth (SAS tokens, IAM policies, access keys)
```

**Providers:**
- **Azure:** `azurerm_storage_account` → `azurerm_storage_container` → `azurerm_storage_blob`
- **AWS:** `aws_s3_bucket` → `aws_s3_bucket_object`
- **GCP:** `google_storage_bucket` → `google_storage_bucket_object`

**Ingress:** Client → Containers (labeled with: SAS Token, IAM Policy, Access Key)

**Egress:** Lifecycle policies, replication, logging to other storage/databases

---

### 3. Messaging Pattern
**Services:** Service Bus, Event Hub, SNS/SQS, Pub/Sub

**Structure:**
```
Parent (Namespace)
├── Topics (INGRESS from apps)
│   └── Subscriptions (EGRESS to apps)
├── Queues (INGRESS and EGRESS)
└── Rules/Filters
```

**Providers:**
- **Azure Service Bus:** `azurerm_servicebus_namespace` → `azurerm_servicebus_topic` → `azurerm_servicebus_subscription`
- **Azure Event Hub:** `azurerm_eventhub_namespace` → `azurerm_eventhub` → `azurerm_eventhub_consumer_group`
- **AWS:** `aws_sns_topic`, `aws_sqs_queue`, `aws_sns_topic_subscription`
- **GCP:** `google_pubsub_topic` → `google_pubsub_subscription`

**Ingress:** Applications → Topics/Queues

**Egress:** Subscriptions → Applications (event-driven)

---

### 4. Serverless Pattern
**Services:** Functions, Lambda, Cloud Functions

**Structure:**
```
Parent (Function App/Lambda)
├── Functions (INGRESS via triggers)
├── Triggers (HTTP, Queue, Timer, Event)
├── Bindings (Input/Output)
└── Auth (Function keys, IAM roles)
```

**Providers:**
- **Azure:** `azurerm_function_app` → `azurerm_function_app_function`
- **AWS:** `aws_lambda_function` → `aws_lambda_event_source_mapping`
- **GCP:** `google_cloudfunctions_function`

**Ingress:** Event-driven (HTTP, queue messages, timers, blob triggers)

**Egress:** Output bindings to databases, storage, other services

---

### 5. Key Vault Pattern
**Services:** Key Vault, Secrets Manager, KMS

**Structure:**
```
Parent (Key Vault/KMS)
├── Secrets
├── Keys
├── Certificates
└── Access Policies/IAM Bindings
```

**Providers:**
- **Azure:** `azurerm_key_vault` → `azurerm_key_vault_secret`, `azurerm_key_vault_key`
- **AWS:** `aws_kms_key`, `aws_secretsmanager_secret`
- **GCP:** `google_kms_key_ring` → `google_kms_crypto_key`, `google_secret_manager_secret`

**Ingress:** Applications with proper access policies/IAM roles

**Auth:** Access policies, RBAC, IAM bindings

---

## Pattern Usage

### Automatic Grouping
Resources are automatically grouped by pattern in `_group_parent_services()`:

```python
# All APIM components → "API Management"
# All storage components → "Storage Account"  
# All messaging components → "Service Bus Namespace" / "Pub/Sub"
```

### Auth Detection
The `_detect_api_auth_mechanism()` function uses patterns to identify auth resources:

```python
# Checks for: subscription, api_key, oauth, jwt, certificate, sas, iam
# Returns: "API Key", "OAuth", "JWT", "mTLS", "SAS Token", "IAM", "HTTPS"
```

### Ingress Detection
The `is_ingress_resource()` function identifies ingress endpoints:

```python
# Returns True for: api_operation, api_method, api_route, etc.
```

### Helper Functions

```python
# Get pattern for a resource
pattern_name, pattern_config = get_service_pattern("azurerm_api_management_api")
# Returns: ("api_gateway", {...})

# Get pattern components from a list of resources
components = get_pattern_components("api_gateway", resource_types)
# Returns: {"parent": [...], "operation": [...], "auth_resources": [...]}

# Check if resource is an ingress endpoint
is_ingress = is_ingress_resource("aws_api_gateway_method")
# Returns: True

# Check if resource is an auth mechanism
is_auth = is_auth_resource("azurerm_api_management_subscription")
# Returns: True
```

## Architecture Diagram Rendering

Patterns are used to render consistent architecture diagrams:

1. **Grouping:** All pattern components grouped under parent service
2. **Nesting:** Operations/endpoints nested within parent
3. **Ingress arrows:** Internet/Client → Ingress endpoints (with auth labels)
4. **Egress arrows:** Service → Backend/Database/Logging (with connection type)

### Example: API Gateway
```mermaid
Internet -->|API Key| APIM_Operation_1
Internet -->|OAuth| APIM_Operation_2
APIM_Operation_1 -->|routes to| Backend_App
Backend_App -->|queries| SQL_Database
Backend_App -->|messages| Service_Bus
```

## Adding New Patterns

To add a new pattern:

1. **Define pattern in `_SERVICE_PATTERNS`:**
   ```python
   "new_pattern": {
       "description": "...",
       "providers": {
           "azure": {...},
           "aws": {...},
       },
       "ingress_pattern": "...",
       "auth_detection": [...],
   }
   ```

2. **Update `_group_parent_services()`** to handle new pattern friendly names

3. **Add rendering logic** in `report_generation.py` if special handling needed

## Benefits

✅ **Consistency:** Same behavior across all cloud providers  
✅ **Maintainability:** Add new providers by updating pattern config  
✅ **Scalability:** New services fit into existing patterns  
✅ **Accuracy:** Ingress/egress/auth automatically detected  
✅ **Diagrams:** Automatic hierarchical rendering  

## Future Enhancements

- [ ] Auto-detect egress patterns (backends, databases, logging)
- [ ] Support for multi-tenant patterns (dedicated vs shared resources)
- [ ] Pattern-based security rule generation
- [ ] Cost optimization patterns (serverless vs always-on)
- [ ] Compliance patterns (data residency, encryption, audit)

---

## Cloud Provider Equivalents

The pattern system automatically maps equivalent services across providers:

| Azure | AWS | GCP | Pattern |
|-------|-----|-----|---------|
| API Management | API Gateway | API Gateway | `api_gateway` |
| Storage Account | S3 Bucket | Cloud Storage | `storage` |
| Service Bus | SNS/SQS | Pub/Sub | `messaging` |
| SQL Server | RDS | Cloud SQL | `database` |
| Cosmos DB | DynamoDB | Firestore | `cosmos_db` |
| AKS | EKS | GKE | `kubernetes` |
| App Service | Elastic Beanstalk | App Engine | `app_service` |
| Key Vault | Secrets Manager / KMS | Secret Manager / KMS | `key_vault` |
| Application Insights | CloudWatch | Cloud Monitoring | `monitoring` |
| Function App | Lambda | Cloud Functions | `serverless` |

## Real-World Examples

See [ServicePatternExamples.md](ServicePatternExamples.md) for detailed examples showing:
- SQL Server with databases, users, and auth
- Storage Account with containers and anonymous access
- Cosmos DB with required auth
- AKS clusters with ingress and egress
- App Service hosted on VM plans
- Key Vault with logging to Application Insights
- Complete multi-service integration scenarios

---

## Contributing

To add a new service pattern:

1. Define it in `_SERVICE_PATTERNS` with all providers
2. Add to `_group_parent_services()` friendly name mapping
3. Test with real Terraform resources
4. Document ingress/egress/auth patterns
5. Add to ServicePatternExamples.md

