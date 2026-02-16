#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class ScoreUpdate:
    old_score: int
    new_score: int
    drivers: list[str]


def _now_stamp() -> str:
    return datetime.now().strftime("%d/%m/%Y %H:%M")


def _severity(score: int) -> tuple[str, str]:
    # Mirror the repo’s common scale.
    if score >= 9:
        return "🔴", "Critical"
    if score >= 7:
        return "🟠", "High"
    if score >= 5:
        return "🟡", "Medium"
    if score >= 3:
        return "🟢", "Low"
    return "⚪", "Informational"


def _find_heading(lines: list[str], heading: str) -> int | None:
    for i, line in enumerate(lines):
        if line.strip() == heading:
            return i
    return None


def _slice_section_body(lines: list[str], heading_idx: int) -> tuple[int, int]:
    start = heading_idx + 1
    end = len(lines)
    for j in range(start, len(lines)):
        if re.match(r"^#{1,3}\s+", lines[j]):
            end = j
            break
    return start, end


def _extract_first_match(lines: list[str], pattern: re.Pattern[str]) -> str:
    for line in lines:
        m = pattern.search(line)
        if m:
            return m.group(1).strip()
    return ""


def _normalise_compounds_with(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    if s.lower() in {"none", "none identified", "n/a"}:
        return ""
    return s


def _normalise_status(s: str) -> str:
    s = (s or "").strip().lower()
    if s in {"yes", "y"}:
        return "yes"
    if s in {"no", "n", "not applicable", "n/a"}:
        return "no"
    if "don’t know" in s or "don't know" in s:
        return "dont_know"
    return s


def _gather_confirmed_context(lines: list[str]) -> list[str]:
    confirmed: list[str] = []

    assumptions_heading = "### ⚠️ Assumptions"
    assumptions_idx = _find_heading(lines, assumptions_heading)
    if assumptions_idx is not None:
        a0, a1 = _slice_section_body(lines, assumptions_idx)
        for line in lines[a0:a1]:
            m = re.match(r"^\-\s+Confirmed\s+\(user\):\s*(.+?)\s*$", line.strip(), flags=re.IGNORECASE)
            if m:
                confirmed.append(m.group(1).strip())

    # Pull in applicability evidence too (often contains the most specific statements).
    applicability_heading = "### ✅ Applicability"
    applicability_idx = _find_heading(lines, applicability_heading)
    if applicability_idx is not None:
        b0, b1 = _slice_section_body(lines, applicability_idx)
        for line in lines[b0:b1]:
            m = re.match(r"^\-\s+\*\*Evidence:\*\*\s*(.+?)\s*$", line.strip())
            if m and m.group(1).strip() and "title-only" not in m.group(1).lower():
                confirmed.append(m.group(1).strip())

    # Deduplicate while preserving order.
    out: list[str] = []
    seen: set[str] = set()
    for c in confirmed:
        k = re.sub(r"\s+", " ", c.strip().lower())
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(c)
    return out


def _classify_issue(lines: list[str]) -> str:
    title = (lines[0].lstrip("#").replace("🟣", "").strip().lower() if lines else "").strip()
    resource_type = _extract_first_match(lines, re.compile(r"^\-\s+\*\*Resource Type:\*\*\s*(.+?)\s*$")).lower()
    blob = "blob" in title
    public_blob = "public blob" in title or "prevent public blob" in title
    key_vault = "key vault" in title or "keyvault" in title or "key vault" in resource_type
    storage = "storage" in title or "storage account" in resource_type

    if key_vault and any(k in title for k in ["private link", "private endpoint", "public access", "public network", "firewall"]):
        return "kv_network"
    if storage and public_blob:
        return "storage_public_blob"
    if storage and any(k in title for k in ["public access", "public network", "firewall", "virtual network", "vnet", "network"]):
        return "storage_network"
    if blob:
        return "storage_public_blob"
    if "nsg" in title or "network security group" in title:
        return "network_rules"
    if "sql" in title:
        return "sql"
    if "aks" in title or "kubernetes" in title:
        return "aks"
    return "generic"


def _has_any(ctx: str, needles: list[str]) -> bool:
    return any(n in ctx for n in needles)


def _compute_score_update(lines: list[str]) -> ScoreUpdate | None:
    overall = _extract_first_match(lines, re.compile(r"^\-\s+\*\*Overall Score:\*\*.*?(\d+)\/10\s*$"))
    if not overall:
        return None
    try:
        old = int(overall)
    except ValueError:
        return None

    applicability_status = _extract_first_match(lines, re.compile(r"^\-\s+\*\*Status:\*\*\s*(.+?)\s*$"))
    status = _normalise_status(applicability_status)

    # If not applicable, push to informational to avoid misleading prioritization.
    if status == "no":
        return ScoreUpdate(old_score=old, new_score=0, drivers=["Not applicable (per Applicability status)"])

    confirmed = _gather_confirmed_context(lines)
    ctx = " ".join(c.lower() for c in confirmed)
    issue_kind = _classify_issue(lines)

    compounds_with = _normalise_compounds_with(
        _extract_first_match(lines, re.compile(r"^\-\s+\*\*Compounds with:\*\*\s*(.+?)\s*$"))
    )

    delta = 0
    drivers: list[str] = []

    # Scope signal (minor unless the issue is already clearly applicable).
    if "production" in ctx:
        delta += 1
        drivers.append("+1 production scope")

    # Issue-specific risk drivers (avoid inferring unrelated risk).
    if issue_kind in {"kv_network", "storage_network"}:
        if ("internet-facing" in ctx) or ("application gateway" in ctx and "public" in ctx):
            delta += 1
            drivers.append("+1 internet-facing entrypoint")
        if _has_any(ctx, ["no private endpoints", "no private endpoint", "no private link"]):
            delta += 1
            drivers.append("+1 no private endpoints/private link")
        if _has_any(ctx, ["public network access is enabled", "public endpoint", "publicly reachable"]):
            delta += 1
            drivers.append("+1 public endpoint path")
        if _has_any(ctx, ["all networks", "no firewall"]):
            delta += 2
            drivers.append("+2 all networks/no firewall")

    if issue_kind == "kv_network":
        if _has_any(ctx, ["store production secrets", "production secrets/keys"]):
            delta += 1
            drivers.append("+1 stores production secrets/keys")

    if issue_kind == "storage_public_blob":
        # Only score up if public/anonymous blob access is actually confirmed.
        # Guard: don't treat "not confirmed" language as confirmation.
        if status == "yes" and _has_any(ctx, ["public blob access", "anonymous", "anonymous access", "container public access"]):
            delta += 3
            drivers.append("+3 public/anonymous blob access confirmed")

    # Countermeasure drivers (only when explicitly confirmed).
    # Treat "mixed/some all networks" as not fully mitigated.
    if ("selected networks" in ctx or "firewall allowlist" in ctx) and not _has_any(ctx, ["some", "mixed", "all networks", "no firewall"]):
        delta -= 1
        drivers.append("-1 firewall allowlisting (selected networks)")
    if "strict rbac" in ctx:
        delta -= 1
        drivers.append("-1 strict RBAC")
    if "mfa" in ctx:
        delta -= 1
        drivers.append("-1 MFA present")

    # Compounding driver (reflect that this issue amplifies others).
    if compounds_with:
        delta += 1
        drivers.append(f"+1 compounds with: {compounds_with}")

    new = max(0, min(10, old + delta))
    if not drivers and new == old:
        return None
    return ScoreUpdate(old_score=old, new_score=new, drivers=drivers)


def _replace_overall_score(lines: list[str], new_score: int) -> list[str]:
    emoji, label = _severity(new_score)
    out: list[str] = []
    for line in lines:
        if line.strip().startswith("- **Overall Score:**") and re.search(r"\d+\/10\s*$", line):
            out.append(f"- **Overall Score:** {emoji} {label} {new_score}/10")
        else:
            out.append(line)
    return out


def _upsert_score_drivers(lines: list[str], drivers: list[str]) -> list[str]:
    rationale_heading = "### 📐 Rationale"
    ridx = _find_heading(lines, rationale_heading)
    if ridx is None:
        return lines
    b0, b1 = _slice_section_body(lines, ridx)

    # Remove any previous Score drivers block inserted by this tool.
    body = lines[b0:b1]
    cleaned: list[str] = []
    in_block = False
    for line in body:
        if line.strip() == "Score drivers (confirmed):":
            in_block = True
            continue
        if in_block:
            if line.strip().startswith("- "):
                continue
            if line.strip() == "":
                # End the block at the first blank line after bullets.
                in_block = False
                continue
            # If it wasn't bullets/blank, stop treating as our block.
            in_block = False
        cleaned.append(line)

    prefix = ["Score drivers (confirmed):"] + [f"- {d}" for d in drivers] + [""]
    new_body = prefix + cleaned
    return lines[:b0] + new_body + lines[b1:]


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
    ap = argparse.ArgumentParser(description="Adjust finding scores using confirmed countermeasures and compounding.")
    ap.add_argument("--path", default="Output/Findings/Cloud", help="Finding file or folder to scan")
    ap.add_argument("--in-place", action="store_true", help="Write changes to disk (default: dry-run)")
    args = ap.parse_args()

    root = Path(args.path)
    paths = iter_findings(root)
    if not paths:
        print(f"No markdown findings found under: {root}")
        return 1

    changed = 0
    skipped = 0
    for path in paths:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        upd = _compute_score_update(lines)
        if upd is None:
            skipped += 1
            continue

        new_lines = list(lines)
        new_lines = _replace_overall_score(new_lines, upd.new_score)
        new_lines = _upsert_score_drivers(new_lines, upd.drivers)
        new_lines = _update_last_updated(new_lines)

        if new_lines == lines:
            skipped += 1
            continue

        changed += 1
        if args.in_place:
            path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            print(f"UPDATED: {path} ({upd.old_score}/10 -> {upd.new_score}/10)")
        else:
            print(f"WOULD UPDATE: {path} ({upd.old_score}/10 -> {upd.new_score}/10)")

    print(f"Scanned: {len(paths)} | changed: {changed} | skipped: {skipped} | mode: {'in-place' if args.in_place else 'dry-run'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
