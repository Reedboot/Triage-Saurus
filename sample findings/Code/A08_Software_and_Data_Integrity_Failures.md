# A08: Software and Data Integrity Failures

- **Description:** Build pipeline pulls unsigned artifacts from a public registry without integrity checks.
- **Evidence:** CI logs show unsigned dependency downloads; no checksum verification configured.
- **Suggested severity:** High
