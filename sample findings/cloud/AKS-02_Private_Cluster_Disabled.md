# AKS-02: Private Cluster Disabled

- **Description:** Cluster uses a public control plane instead of a private endpoint.
- **Evidence:** `privateCluster` is false and control plane has a public IP.
- **Suggested severity:** High
