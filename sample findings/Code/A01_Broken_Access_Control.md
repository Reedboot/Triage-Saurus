# A01: Broken Access Control

- **Description:** Insecure direct object reference allows users to access other users' records by changing an ID in requests.
- **Evidence:** GET /api/orders/123 returns another tenant's data when ID is modified.
- **Suggested severity:** Critical
