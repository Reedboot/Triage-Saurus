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

**Manual Detection:**
```bash
# Example: Check for nonsensitive() usage
grep -r "nonsensitive(" --include="*.tf"
```

**Automated (Future):**
```bash
# When opengrep installed
opengrep scan --config Rules/ /path/to/repo
```

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
