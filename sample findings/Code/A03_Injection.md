# A03: Injection

- **Description:** User input is concatenated into SQL queries without parameterization, enabling SQL injection.
- **Evidence:** Payload "' OR 1=1 --" returns all rows in /search endpoint.
- **Suggested severity:** High
