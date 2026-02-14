#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from datetime import datetime
from pathlib import Path


try:
    from finding_text import cloud_description_for_title  # type: ignore
except Exception:
    cloud_description_for_title = None


def _now_stamp() -> str:
    return datetime.now().strftime("%d/%m/%Y %H:%M")


def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = s.replace("🟣", "")
    s = re.sub(r"[“”\"'`]", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _extract_title(lines: list[str], fallback: str) -> str:
    for line in lines[:5]:
        if line.startswith("#"):
            return line.lstrip("#").replace("🟣", "").strip()
    return fallback


def _extract_description(lines: list[str]) -> tuple[int | None, str]:
    for i, line in enumerate(lines):
        if line.strip().startswith("- **Description:**"):
            return i, line.replace("- **Description:**", "", 1).strip()
    return None, ""


def _looks_auto_generated_description(desc: str) -> bool:
    d = (desc or "").strip().lower()
    d = re.sub(r"^[^a-z0-9]+", "", d)
    if not d:
        return False
    prefixes = [
        # Earlier auto-generated wording (v1)
        "key vault is accessible via a public endpoint",
        "key vault is reachable via public networks",
        "key vault authorization should be enforced",
        "key vault secrets/keys should have lifecycle controls",
        "key vault should have recovery protections",
        "storage may allow anonymous/public blob access",
        "storage allows shared key authentication",
        "storage allows insecure transport",
        "storage network access should be restricted",
        "storage configuration may allow broader-than-intended access",
        "inbound management ports (ssh/rdp) are exposed too broadly",
        "inbound network rules are overly broad",
        "sql firewall allows broad azure-sourced connectivity",
        "sql encryption at rest is not enforced",
        "sql auditing is not enabled",
        "aks rbac is disabled or not enforced",
        "acr admin user enables shared credentials",
        "ddos protection is not enabled",
        "privileged identities lack strong authentication controls",
        "workloads rely on long-lived credentials",
        "endpoint protection is missing",
        "insecure deployment/management protocols are enabled",

        # Current auto-generated wording (v2)
        "secrets/keys are reachable over the public network path",
        "if key vault can be reached from public networks",
        "over-privileged key vault access",
        "long-lived secrets/keys increase blast radius",
        "without recovery protections",
        "if blobs/containers allow anonymous access",
        "shared keys are effectively high-privilege passwords",
        "allowing http increases the chance",
        "broad storage network access increases the chance",
        "storage configuration may allow broader-than-intended access",
        "exposed ssh/rdp makes it much easier",
        "overly broad inbound rules expand attack surface",
        "allowing broad azure-sourced access expands who can reach your databases",
        "without encryption at rest",
        "without auditing",
        "if aks rbac isn’t enforced",
        "shared admin credentials for the container registry",
        "without ddos protection",
        "weak authentication for privileged accounts",
        "long-lived credentials are easier to leak",
        "without endpoint protection",
        "allowing weaker deployment/management protocols",
        "security configuration does not meet baseline guidance:",
    ]
    return any(d.startswith(p) for p in prefixes)


def _update_last_updated(lines: list[str]) -> list[str]:
    stamp = _now_stamp()
    out: list[str] = []
    updated = False
    for line in lines:
        if re.match(r"^\-\s*🗓️\s*\*\*Last updated:\*\*", line):
            out.append(f"- 🗓️ **Last updated:** {stamp}")
            updated = True
        else:
            out.append(line)
    return out if updated else lines


def iter_findings(root: Path) -> list[Path]:
    if root.is_file() and root.suffix.lower() == ".md":
        return [root]
    if not root.exists():
        return []
    return sorted([p for p in root.rglob("*.md") if p.is_file()])


def main() -> int:
    ap = argparse.ArgumentParser(description="Replace title-repeated Description lines with short explanatory descriptions.")
    ap.add_argument("--path", default="Output/Findings/Cloud", help="Finding file or folder to scan")
    ap.add_argument("--in-place", action="store_true", help="Write changes to disk (default: dry-run)")
    ap.add_argument(
        "--refresh-auto",
        action="store_true",
        help="Also refresh descriptions that look auto-generated (to pick up improved wording).",
    )
    args = ap.parse_args()

    if cloud_description_for_title is None:
        raise SystemExit("ERROR: could not import cloud_description_for_title (Skills/finding_text.py)")

    root = Path(args.path)
    paths = iter_findings(root)
    if not paths:
        print(f"No markdown findings found under: {root}")
        return 1

    changed = 0
    skipped = 0

    for path in paths:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        title = _extract_title(lines, path.stem.replace("_", " "))
        desc_idx, desc = _extract_description(lines)

        # Default: only fix cases where Description repeats the title (common in title-only imports).
        # With --refresh-auto: also refresh descriptions that look auto-generated.
        if desc_idx is None:
            skipped += 1
            continue

        if not args.refresh_auto:
            if _norm(desc) != _norm(title):
                skipped += 1
                continue
        else:
            if (_norm(desc) != _norm(title)) and (not _looks_auto_generated_description(desc)):
                skipped += 1
                continue

        new_desc = cloud_description_for_title(title).strip()
        if not new_desc or _norm(new_desc) == _norm(title):
            skipped += 1
            continue

        new_lines = list(lines)
        new_lines[desc_idx] = f"- **Description:** {new_desc}"
        new_lines = _update_last_updated(new_lines)

        if args.in_place:
            path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            print(f"UPDATED: {path}")
        else:
            print(f"WOULD UPDATE: {path}")
        changed += 1

    print(f"Scanned: {len(paths)} | changed: {changed} | skipped: {skipped} | mode: {'in-place' if args.in_place else 'dry-run'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
