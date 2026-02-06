# A04: Insecure Design

- **Description:** Password reset flow lacks rate limiting and account verification, enabling abuse.
- **Evidence:** Unlimited reset requests allowed per user with no secondary verification.
- **Suggested severity:** Medium
