# Service Pattern Theory - Real-World Examples

This document demonstrates how the service pattern system handles complex, real-world scenarios.

## Pattern Theory Validation

### ✅ SQL Server Pattern
**Theory:** Server → Databases → Users/Auth → Logging

**Reality:**
```
azurerm_mssql_server (parent)
├── azurerm_mssql_database (children)
├── azurerm_sql_active_directory_administrator (auth)
├── azurerm_mssql_firewall_rule (ingress control)
└── azurerm_monitor_diagnostic_setting (egress to logs)
```

**Ingress:** Applications → SQL Server (port 1433)  
**Auth:** SQL Auth, Azure AD, IAM, SSL Certificates  
**Egress:** Logs → Application Insights / Log Analytics  

**Architecture Flow:**
```mermaid
graph LR
    App[Application]
    SQL[SQL Server]
    DB1[Database 1]
    DB2[Database 2]
    AAD[Azure AD Auth]
    Logs[Application Insights]
    
    App -->|SQL Auth / AAD| SQL
    SQL --> DB1
    SQL --> DB2
    AAD -.->|authenticates| SQL
    SQL -->|diagnostic logs| Logs
```

---

### ✅ Storage Account Pattern
**Theory:** Account → Containers → Blobs (with anon access option)

**Reality:**
```
azurerm_storage_account (parent)
├── azurerm_storage_container (children with access control)
│   └── azurerm_storage_blob (objects)
├── azurerm_storage_queue (messaging)
├── azurerm_storage_table (NoSQL)
├── azurerm_storage_account_sas (auth - SAS tokens)
└── azurerm_storage_account_network_rules (ingress control)
```

**Ingress:** Client → Containers (HTTPS)  
**Auth:** SAS Tokens, Access Keys, Azure AD, Anonymous (configurable)  
**Egress:** Lifecycle → Archive tier, Replication → Secondary region  

**Architecture Flow:**
```mermaid
graph LR
    Client[Client Apps]
    SA[Storage Account]
    C1[Container 1<br/>Private]
    C2[Container 2<br/>Public Anon]
    Blob[Blobs]
    SAS[SAS Token]
    Archive[Archive Storage]
    
    Client -->|HTTPS + SAS| SA
    SA --> C1
    SA --> C2
    C1 --> Blob
    C2 -->|anonymous read| Blob
    SAS -.->|authorizes| C1
    SA -->|lifecycle| Archive
```

---

### ✅ Cosmos DB Pattern
**Theory:** Account → Databases → Containers (auth required, no anon)

**Reality:**
```
azurerm_cosmosdb_account (parent)
├── azurerm_cosmosdb_sql_database (children - SQL API)
│   └── azurerm_cosmosdb_sql_container (with partition key)
├── azurerm_cosmosdb_mongo_database (MongoDB API)
│   └── azurerm_cosmosdb_mongo_collection
├── azurerm_cosmosdb_sql_role_assignment (auth - RBAC)
└── azurerm_cosmosdb_sql_role_definition (custom roles)
```

**Ingress:** Application → Cosmos DB (HTTPS, port 443 or 10255)  
**Auth:** Connection String, RBAC, Resource Tokens (NO anonymous access)  
**Egress:** Change Feed → Functions, Replication → Secondary regions  

**Architecture Flow:**
```mermaid
graph LR
    App[Application]
    Cosmos[Cosmos DB Account]
    DB1[SQL Database]
    DB2[MongoDB Database]
    Container[Container]
    RBAC[RBAC Auth]
    Feed[Change Feed]
    Func[Azure Function]
    
    App -->|Connection String + RBAC| Cosmos
    Cosmos --> DB1
    Cosmos --> DB2
    DB1 --> Container
    RBAC -.->|authorizes| App
    Container -->|changes| Feed
    Feed --> Func
```

---

### ✅ AKS Cluster Pattern
**Theory:** Cluster (API access) → Services (ingress) → Pods (egress)

**Reality:**
```
azurerm_kubernetes_cluster (parent - has API endpoint)
├── azurerm_kubernetes_cluster_node_pool (compute nodes)
├── kubernetes_deployment (workloads)
├── kubernetes_service (internal routing)
├── kubernetes_ingress (external access)
├── kubernetes_config_map (config)
├── kubernetes_secret (sensitive data)
└── kubernetes_network_policy (egress control)
```

**Ingress:**  
- Cluster API → kubectl/CI-CD (Azure AD / certificate auth)  
- Ingress Controller → Services → Pods (HTTP/HTTPS)  

**Egress:**  
- Pods → External APIs, Databases, Storage  
- Pods → Service Bus, Event Hub  

**Architecture Flow:**
```mermaid
graph TB
    Internet[Internet]
    APIM[API Management]
    Ingress[Ingress Controller]
    AKS[AKS Cluster]
    Svc1[Service 1]
    Svc2[Service 2]
    Pod1[Pod 1]
    Pod2[Pod 2]
    SQL[SQL Database]
    SB[Service Bus]
    
    Internet --> APIM
    APIM --> Ingress
    Ingress --> AKS
    AKS --> Svc1
    AKS --> Svc2
    Svc1 --> Pod1
    Svc2 --> Pod2
    Pod1 -->|egress| SQL
    Pod2 -->|egress| SB
```

---

### ✅ App Service Pattern
**Theory:** App Service Plan (VM) → App Services

**Reality:**
```
azurerm_service_plan (parent - hosted on VMs)
├── azurerm_linux_web_app (app 1)
├── azurerm_windows_web_app (app 2)
├── azurerm_linux_function_app (serverless)
├── azurerm_web_app_deployment_slot (staging)
└── azurerm_app_service_virtual_network_swift_connection (VNet integration)
```

**Ingress:** HTTPS → App Service (port 443/80)  
**Auth:** Managed Identity, App Settings, Connection Strings  
**Egress:**  
- App → SQL Database (via VNet or public endpoint)  
- App → Storage Account  
- App → Service Bus  
- Logs → Application Insights  

**Architecture Flow:**
```mermaid
graph LR
    User[Users]
    FD[Front Door]
    Plan[App Service Plan<br/>VMs]
    App1[Web App 1]
    App2[Web App 2]
    MI[Managed Identity]
    SQL[SQL Database]
    KV[Key Vault]
    AI[App Insights]
    
    User -->|HTTPS| FD
    FD --> Plan
    Plan --> App1
    Plan --> App2
    App1 -->|Managed Identity| MI
    MI -.->|auth| KV
    App1 -->|connection string| SQL
    App1 -->|telemetry| AI
```

---

### ✅ Key Vault + Logging Pattern
**Theory:** Vault → Secrets/Keys → Logs to App Insights

**Reality:**
```
azurerm_key_vault (parent)
├── azurerm_key_vault_secret (sensitive strings)
├── azurerm_key_vault_key (encryption keys)
├── azurerm_key_vault_certificate (TLS certs)
├── azurerm_key_vault_access_policy (auth)
└── azurerm_monitor_diagnostic_setting (egress to logs)
```

**Ingress:** Applications → Key Vault (HTTPS)  
**Auth:** Access Policies, RBAC, Managed Identity  
**Egress:** Audit Logs → Application Insights / Log Analytics  

**Architecture Flow:**
```mermaid
graph LR
    App[Application]
    MI[Managed Identity]
    KV[Key Vault]
    Secret[Secrets]
    Keys[Encryption Keys]
    Cert[Certificates]
    AI[Application Insights]
    
    App -->|Managed Identity| MI
    MI -.->|auth via RBAC| KV
    KV --> Secret
    KV --> Keys
    KV --> Cert
    App -->|reads| Secret
    KV -->|audit logs| AI
```

---

## Cross-Service Integration Example

**Scenario:** Complete web application stack

```mermaid
graph TB
    Internet[Internet Users]
    APIM[API Management]
    AKS[AKS Cluster]
    App[App Service]
    SQL[SQL Server]
    Cosmos[Cosmos DB]
    Storage[Storage Account]
    KV[Key Vault]
    SB[Service Bus]
    AI[Application Insights]
    
    %% Ingress
    Internet -->|API Key| APIM
    APIM -->|routes to| AKS
    Internet -->|HTTPS| App
    
    %% Auth
    AKS -.->|Managed Identity| KV
    App -.->|Managed Identity| KV
    
    %% Data Access
    AKS -->|SQL Auth| SQL
    AKS -->|Connection String| Cosmos
    App -->|SAS Token| Storage
    
    %% Messaging
    AKS -->|publishes| SB
    App -->|subscribes| SB
    
    %% Monitoring
    APIM -->|logs| AI
    AKS -->|logs| AI
    App -->|logs| AI
    SQL -->|diagnostics| AI
    Storage -->|analytics| AI
```

---

## Pattern Coverage Matrix

| Pattern | Parent | Children | Ingress | Auth | Egress | Azure | AWS | GCP |
|---------|--------|----------|---------|------|--------|-------|-----|-----|
| **API Gateway** | APIM/Gateway | Operations | Internet/Client | API Key, OAuth, JWT | Backends | ✅ | ✅ | ✅ |
| **Storage** | Account/Bucket | Containers, Blobs | Client | SAS, IAM, Anon | Lifecycle, Replication | ✅ | ✅ | ✅ |
| **Messaging** | Namespace | Topics, Queues | Apps | Connection String | Subscriptions | ✅ | ✅ | ✅ |
| **Database** | Server | Databases | Apps | SQL, AAD, SSL | Logs, Replicas | ✅ | ✅ | ✅ |
| **Cosmos DB** | Account | Databases, Containers | Apps | RBAC, Tokens | Change Feed | ✅ | ✅ | ✅ |
| **Kubernetes** | Cluster | Pods, Services | Ingress | RBAC, Tokens | External APIs | ✅ | ✅ | ✅ |
| **App Service** | Plan | Web Apps, Functions | HTTPS | Managed Identity | DB, Storage | ✅ | ✅ | ✅ |
| **Key Vault** | Vault | Secrets, Keys | Apps | RBAC, Access Policy | Audit Logs | ✅ | ✅ | ✅ |
| **Monitoring** | Workspace | Metrics, Logs | Telemetry | N/A | Alerts, Actions | ✅ | ✅ | ✅ |
| **Serverless** | Function App | Functions, Triggers | Events | Function Keys | Output Bindings | ✅ | ✅ | ✅ |

---

## Benefits Demonstrated

✅ **Consistent structure** across all cloud providers  
✅ **Automatic grouping** of parent → child hierarchies  
✅ **Ingress detection** for all service types  
✅ **Auth mechanism** identification  
✅ **Egress patterns** for logging, replication, backends  
✅ **Cross-service** integration mapping  
✅ **Scalable** - new services fit existing patterns  

## Pattern Usage in Diagrams

All these patterns are automatically rendered in architecture diagrams with:
- Proper nesting (parent → children)
- Ingress arrows with auth labels
- Egress arrows to databases/storage/messaging
- Consistent icons and colors per service type

The pattern system makes it easy to understand complex architectures at a glance! 🎯
