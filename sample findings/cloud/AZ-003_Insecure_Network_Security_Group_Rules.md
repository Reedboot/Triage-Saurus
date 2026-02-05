# AZ-003: Insecure Network Security Group Rules

- **Description:** NSG contains wide-open inbound rules (0.0.0.0/0) for management ports.
- **Evidence:** NSG rules include CIDR 0.0.0.0/0 on ports 22, 3389, or 1433.
- **Suggested severity:** High
