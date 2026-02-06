# A07: Identification and Authentication Failures

- **Description:** Sessions are not invalidated on password change and MFA is optional for admins.
- **Evidence:** Active sessions remain valid after password reset; admin login lacks MFA prompt.
- **Suggested severity:** High
