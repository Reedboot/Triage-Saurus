# Skaffold Workload Detection - Multi-Cloud Support

## Overview

The Skaffold parser detects Kubernetes workloads deployed via Skaffold across **all cloud providers**:
- Azure AKS
- AWS EKS  
- GCP GKE
- On-premises Kubernetes
- Any Kubernetes cluster

## Architecture Patterns Detected

### Pattern 1: API Gateway → Kubernetes API Service

**Azure:**
```
Internet → Azure APIM → AKS Service (API)
```

**AWS:**
```
Internet → AWS API Gateway → EKS Service (API)
```

**GCP:**
```
Internet → Google API Gateway → GKE Service (API)
```

**Detection:**
- API Gateway resources: `azurerm_api_management_api`, `aws_api_gateway`, `google_api_gateway_api`
- Kubernetes workload with "api", "service", "web", "frontend", "backend" in name
- Inferred connection: `API Gateway → kubernetes_service`

### Pattern 2: Messaging → Kubernetes Worker/Consumer

**Azure:**
```
Service Bus Queue/Topic → AKS Deployment (QueueListener)
```

**AWS:**
```
SQS Queue / SNS Topic → EKS Deployment (Consumer)
```

**GCP:**
```
Pub/Sub Topic → GKE Deployment (Subscriber)
```

**Detection:**
- Messaging resources: `azurerm_servicebus_*`, `aws_sqs_queue`, `aws_sns_topic`, `google_pubsub_topic`
- Kubernetes workload with "worker", "queue", "consumer", "subscriber", "listener" in name
- Inferred connection: `Messaging → kubernetes_deployment`

## Supported Resource Types

### API Gateways (Entry Points)
- **Azure:** `azurerm_api_management_api`
- **AWS:** `aws_api_gateway`, `aws_apigatewayv2_api`, `aws_api_gateway_rest_api`
- **GCP:** `google_api_gateway_api`, `google_api_gateway_gateway`

### Messaging Services (Data)
- **Azure:** `azurerm_servicebus_namespace`, `azurerm_servicebus_queue`, `azurerm_servicebus_topic`
- **AWS:** `aws_sqs_queue`, `aws_sns_topic`, `aws_kinesis_stream`, `aws_mq_broker`
- **GCP:** `google_pubsub_topic`, `google_pubsub_subscription`

### Kubernetes Workloads (Compute)
- **All Providers:** `kubernetes_service`, `kubernetes_deployment`, `kubernetes_pod`

## Example: Multi-Cloud Deployments

### AWS EKS with SQS

**Terraform:**
```hcl
resource "aws_sqs_queue" "orders" {
  name = "order-processing-queue"
}

resource "aws_api_gateway" "api" {
  name = "order-api"
}
```

**Skaffold:**
```yaml
manifests:
  helm:
    releases:
    - name: order-api
      # ...
    - name: order-worker
      # ...
```

**Detected:**
- `aws_api_gateway.api` → `kubernetes_service.order-api` (routes_to_backend)
- `aws_sqs_queue.orders` → `kubernetes_deployment.order-worker` (consumed_by)

### GCP GKE with Pub/Sub

**Terraform:**
```hcl
resource "google_pubsub_topic" "events" {
  name = "customer-events"
}

resource "google_api_gateway_api" "api" {
  api_id = "customer-api"
}
```

**Skaffold:**
```yaml
manifests:
  helm:
    releases:
    - name: customer-api-service
      # ...
    - name: event-subscriber
      # ...
```

**Detected:**
- `google_api_gateway_api.api` → `kubernetes_service.customer-api-service` (routes_to_backend)
- `google_pubsub_topic.events` → `kubernetes_deployment.event-subscriber` (consumed_by)

## Naming Conventions

### API/Service Workloads
Detected when workload name contains:
- `api`, `service`, `web`, `frontend`, `backend`
- AND does NOT contain: `worker`, `queue`, `consumer`

**Examples:**
- `payment-api` → `kubernetes_service`
- `user-service` → `kubernetes_service`
- `web-frontend` → `kubernetes_service`

### Worker/Consumer Workloads
Detected when workload name contains:
- `worker`, `queue`, `consumer`, `subscriber`, `listener`

**Examples:**
- `order-worker` → `kubernetes_deployment`
- `event-consumer` → `kubernetes_deployment`
- `queue-listener` → `kubernetes_deployment`

## Configuration

No configuration required! The parser automatically:
1. Scans for `skaffold.yaml` in repo root
2. Extracts workload definitions
3. Infers connections based on resource types and naming patterns
4. Works across all cloud providers

## Limitations

- **Requires Skaffold file**: Repos without `skaffold.yaml` won't have workloads detected
- **Naming-based inference**: Relies on common naming patterns (api, worker, etc.)
- **Helm chart inference**: Generic chart names may not be classified correctly
- **External charts**: Remote Helm charts are detected but internal resources not expanded

## Testing

```bash
# Test Skaffold parser
python3 Scripts/Context/skaffold_parser.py <repo_path>

# Extract full context with workloads
python3 -c "
from Context.context_extraction import extract_context
context = extract_context('/path/to/repo')
print(f'K8s workloads: {len([r for r in context.resources if \"kubernetes\" in r.resource_type])}')
print(f'Connections: {len(context.connections)}')
"

# Run exposure analysis
python3 Scripts/Analyze/exposure_analyzer.py --experiment <exp_id>
```

## Future Enhancements

- Detect database connections from Kubernetes config (env vars, secrets)
- Parse Helm chart values for additional configuration
- Detect service mesh (Istio, Linkerd) routing
- Support Kustomize overlays
- Detect external service dependencies (Redis, MongoDB, etc.)
