# A10: Server-Side Request Forgery

- **Description:** Server fetch endpoint allows user-supplied URLs without validation, enabling SSRF.
- **Evidence:** Requesting http://169.254.169.254/latest/meta-data returns metadata.
- **Suggested severity:** High
