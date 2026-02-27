# Security Rules

This folder contains declarative security rules in opengrep/Semgrep-compatible format.

## Structure

```
Rules/
├── iac/           # Infrastructure as Code rules (40 files)
│   ├── terraform-*.yml
│   ├── azure-*.yml
│   └── kubernetes-*.yml
├── secrets/       # Secret detection rules (2 files)
│   ├── aws-access-key-id.yml
│   └── sql-connection-string.yml
├── README.md      # This file
├── Summary.md     # Complete catalog of all rules
└── CreationGuide.md  # How to create new rules
```

## Quick Start

### View All Rules
See `Summary.md` for complete catalog with descriptions.

### Create a New Rule
Follow `CreationGuide.md` for format and examples.

### Usage

**Mandatory opengrep Scan**
```bash
opengrep scan --config Rules/ /path/to/repo
```
- Run this before any skeptic reviews or manual checks; it executes the full ruleset.
- Log the exact command and target path in `Output/Audit/...`.

**Rule Design Principles**
- Each rule targets a specific service/resource misconfiguration (e.g., `azure-storage-logging-disabled`, `kubernetes-run-as-root`).
- LLMs only enrich findings after a deterministic rule match; do not rely on LLMs for initial detection.
- When new services/configs are discovered during scans, immediately codify them as new opengrep rules.

**Temporary Fallback (if opengrep unavailable)**
```bash
grep -r "pattern" --include="*.tf"
```
- Only use while restoring opengrep. Document the outage and rerun opengrep immediately after.

## Rule Statistics

- **Total:** 50+ rules
- **IaC:** 40 rules (Terraform, Azure, Kubernetes)
- **Secrets:** 2 rules (cross-language)
- **Coverage:** 86% detection rate (validated)

## Documentation

- **README.md** (this file) - Quick overview
- **Summary.md** - Complete catalog of all rules
- **CreationGuide.md** - How to create new rules
- **Agents/Instructions.md** - When to create rules (workflow integration)
- **Templates/\*Finding.md** - How findings reference rules

## License

See root LICENSE file (Personal Use License - non-commercial, no redistribution).
