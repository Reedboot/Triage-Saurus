# A02: Cryptographic Failures

- **Description:** Sensitive data is stored without strong encryption and uses deprecated TLS settings in transit.
- **Evidence:** Database column contains plaintext SSNs; TLS config allows TLS 1.0.
- **Suggested severity:** High
