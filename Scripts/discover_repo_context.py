#!/usr/bin/env python3
"""Fast, non-security context discovery for a local repository (writes summary/knowledge).

This script is the "Phase 1 - Context Discovery" step described in:
- Agents/ContextDiscoveryAgent.md
- Templates/Workflows.md (Repository Scan Flow - Step 3)

It performs quick file-based discovery (no network, no git commands by default) and writes:
- Output/Summary/Repos/<RepoName>.md
- Output/Knowledge/Repos.md (repo inventory + repo root directory)

Usage:
  python3 Scripts/discover_repo_context.py /abs/path/to/repo
  python3 Scripts/discover_repo_context.py /abs/path/to/repo --repos-root /abs/path/to/repos

Exit codes:
  0 success
  2 invalid arguments
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
from dataclasses import dataclass
from pathlib import Path

from output_paths import OUTPUT_KNOWLEDGE_DIR, OUTPUT_SUMMARY_DIR


SKIP_DIR_NAMES = {
    ".git",
    ".terraform",
    "node_modules",
    "bin",
    "obj",
    "dist",
    "build",
    "target",
    "vendor",
    ".venv",
    "venv",
}

CODE_EXTS = {".cs", ".fs", ".vb", ".go", ".py", ".js", ".ts", ".java", ".kt", ".rb", ".php"}
CFG_EXTS = {".yml", ".yaml", ".json", ".toml", ".ini", ".config", ".env"}
IAC_EXTS = {".tf", ".tfvars", ".bicep"}
DOC_EXTS = {".md", ".txt"}
SQL_EXTS = {".sql"}

INGRESS_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("HTTP server bind/listen", re.compile(r"\b(Listen|ListenAndServe|app\.Run|http\.ListenAndServe|BindAddress)\b")),
    ("Routes/controllers", re.compile(r"(@app\.route|@RestController|@RequestMapping|app\.(get|post|put|delete)\(|Map(Get|Post|Put|Delete)\b|Route\[)")),
    ("Ports/exposed", re.compile(r"\b(port:|PORT=|--port\b|EXPOSE\s+\d+)\b")),
    ("APIM endpoint hints", re.compile(r"\b(azure-api\.net|ApiManagementUrl|ApiManagerBaseUrl)\b", re.IGNORECASE)),
    ("Kubernetes Ingress", re.compile(r"^\s*kind:\s*Ingress\s*$", re.IGNORECASE | re.MULTILINE)),
    ("Kubernetes Service", re.compile(r"^\s*kind:\s*Service\s*$", re.IGNORECASE | re.MULTILINE)),
]

EGRESS_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("HTTP client usage", re.compile(r"\b(HttpClient|requests\.(get|post)|axios|fetch\()\b")),
    ("Database connection strings", re.compile(r"\b(Server=|Host=|Data Source=|ConnectionString|DATABASE_URL)\b", re.IGNORECASE)),
    ("Messaging/queues", re.compile(r"\b(ServiceBus|EventHub|Kafka|RabbitMQ|SQS|PubSub)\b", re.IGNORECASE)),
    ("Cloud storage endpoints", re.compile(r"\b(blob\.core\.windows\.net|s3\.amazonaws\.com|storage\.googleapis\.com)\b", re.IGNORECASE)),
]

PROVIDER_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("Azure", re.compile(r"\bazurerm\b|provider\s+\"azurerm\"", re.IGNORECASE)),
    ("AWS", re.compile(r"\baws\b|provider\s+\"aws\"", re.IGNORECASE)),
    ("GCP", re.compile(r"\bgoogle\b|provider\s+\"google\"", re.IGNORECASE)),
]

CI_MARKERS: list[tuple[str, str]] = [
    ("GitHub Actions", ".github/workflows"),
    ("Azure Pipelines", "azure-pipelines.yml"),
    ("GitLab CI", ".gitlab-ci.yml"),
]

LANG_MARKERS: list[tuple[str, list[str]]] = [
    ("Terraform", ["*.tf", "*.tfvars"]),
    ("Bicep", ["*.bicep"]),
    (".NET", ["*.csproj", "*.sln", "Directory.Build.props"]),
    ("Node.js", ["package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml"]),
    ("Python", ["requirements.txt", "pyproject.toml", "Pipfile", "setup.py"]),
    ("Go", ["go.mod", "go.sum"]),
    ("Java", ["pom.xml", "build.gradle", "build.gradle.kts"]),
    ("Kubernetes", ["kustomization.yaml", "Chart.yaml", "values.yaml"]),
    ("Containers", ["Dockerfile", "docker-compose.yml"]),
]


def now_uk() -> str:
    return _dt.datetime.now().strftime("%d/%m/%Y %H:%M")


@dataclass(frozen=True)
class Evidence:
    label: str
    path: str
    line: int | None = None
    excerpt: str | None = None

    def fmt(self) -> str:
        if self.line is None:
            return f"- 💡 {self.label} — evidence: `{self.path}`"
        if self.excerpt:
            return f"- 💡 {self.label} — evidence: `{self.path}:{self.line}:{self.excerpt}`"
        return f"- 💡 {self.label} — evidence: `{self.path}:{self.line}`"


def iter_files(repo: Path, *, max_depth: int = 12) -> list[Path]:
    repo = repo.resolve()
    out: list[Path] = []
    for root, dirs, files in os.walk(repo):
        rel_depth = len(Path(root).relative_to(repo).parts)
        if rel_depth > max_depth:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d not in SKIP_DIR_NAMES and not d.startswith(".")]
        for name in files:
            if name.startswith("."):
                continue
            out.append(Path(root) / name)
    return out


def rel(repo: Path, p: Path) -> str:
    try:
        return p.relative_to(repo).as_posix()
    except ValueError:
        return str(p)


def _matches_marker(path_str: str, marker: str) -> bool:
    if "*" not in marker:
        return path_str.endswith("/" + marker) or path_str == marker
    if marker.startswith("*."):
        return path_str.endswith(marker[1:])
    return False


def detect_languages(files: list[Path], repo: Path) -> list[tuple[str, str]]:
    rels = [rel(repo, p) for p in files]
    detected: list[tuple[str, str]] = []
    for lang, markers in LANG_MARKERS:
        evidence = None
        for m in markers:
            for r in rels:
                if _matches_marker(r, m):
                    evidence = r
                    break
            if evidence:
                break
        if evidence:
            detected.append((lang, evidence))
    return detected


def detect_ci(repo: Path) -> str:
    for name, marker in CI_MARKERS:
        if (repo / marker).exists():
            return name
    return "Unknown"


def _scan_text_file(repo: Path, path: Path, patterns: list[tuple[str, re.Pattern[str]]], *, limit: int) -> list[Evidence]:
    out: list[Evidence] = []
    rp = rel(repo, path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for label, rx in patterns:
        if len(out) >= limit:
            break
        for m in rx.finditer(text):
            if len(out) >= limit:
                break
            line = text.count("\n", 0, m.start()) + 1
            # Keep excerpt short and single-line.
            excerpt = text.splitlines()[line - 1].strip() if 0 < line <= len(text.splitlines()) else None
            out.append(Evidence(label=label, path=rp, line=line, excerpt=excerpt))
            break  # one hit per pattern per file is enough for context
    return out


def detect_cloud_provider(files: list[Path], repo: Path) -> list[str]:
    providers: set[str] = set()
    for p in files:
        if p.suffix not in IAC_EXTS and p.suffix.lower() not in {".yaml", ".yml"}:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for name, rx in PROVIDER_PATTERNS:
            if rx.search(text):
                providers.add(name)
    return sorted(providers)


def infer_repo_type(langs: list[str], repo_name: str) -> str:
    n = repo_name.lower()
    if "Terraform" in langs or "Bicep" in langs or any(k in n for k in ["terraform", "iac", "infra", "infrastructure", "platform", "modules"]):
        return "Infrastructure"
    return "Application"


def repo_purpose(repo: Path, repo_name: str) -> tuple[str, Evidence | None]:
    for name in ["README.md", "readme.md"]:
        p = repo / name
        if p.is_file():
            try:
                lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                break
            for line in lines:
                s = line.strip()
                if s and not s.startswith("#"):
                    return s[:160], Evidence(label="README purpose hint", path=rel(repo, p), line=lines.index(line) + 1, excerpt=s)
            return f"{repo_name} repository", Evidence(label="README present", path=rel(repo, p))
    return f"{repo_name} repository", None


def ensure_repos_knowledge(repos_root: Path) -> Path:
    OUTPUT_KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_KNOWLEDGE_DIR / "Repos.md"
    if path.exists():
        text = path.read_text(encoding="utf-8", errors="replace")
        if "**Repo root directory:**" not in text:
            path.write_text(
                text.rstrip()
                + "\n\n## Repo Roots\n"
                + f"- **Repo root directory:** `{repos_root}`\n",
                encoding="utf-8",
            )
        return path

    path.write_text(
        "# 🟣 Repositories\n\n"
        "## Repo Roots\n"
        f"- **Repo root directory:** `{repos_root}`\n\n"
        "## Repository Inventory\n\n"
        "### Application Repos\n\n"
        "### Infrastructure Repos\n\n",
        encoding="utf-8",
    )
    return path


def upsert_repo_inventory(repos_md: Path, *, repo_name: str, repo_type: str, purpose: str, langs: list[str]) -> None:
    text = repos_md.read_text(encoding="utf-8", errors="replace")
    entry_line = f"- **{repo_name}** - {purpose} ({', '.join(langs)})"
    if entry_line in text:
        return

    lines = text.splitlines()
    out: list[str] = []
    inserted = False
    target_heading = "### Infrastructure Repos" if repo_type == "Infrastructure" else "### Application Repos"
    for i, l in enumerate(lines):
        out.append(l)
        if l.strip() == target_heading and not inserted:
            # insert after heading and any blank lines
            j = i + 1
            while j < len(lines) and lines[j].strip() == "":
                j += 1
            out.append(entry_line)
            inserted = True
    if not inserted:
        out.append("")
        out.append(target_heading)
        out.append(entry_line)

    repos_md.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def write_repo_summary(
    *,
    repo: Path,
    repo_name: str,
    repo_type: str,
    purpose: str,
    langs: list[tuple[str, str]],
    ci: str,
    providers: list[str],
    ingress: list[Evidence],
    egress: list[Evidence],
    extra_evidence: list[Evidence],
    scan_scope: str,
) -> Path:
    OUTPUT_SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_SUMMARY_DIR / "Repos").mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_SUMMARY_DIR / "Repos" / f"{repo_name}.md"

    lang_lines = "\n".join([f"- {l} — evidence: `{e}`" for l, e in langs]) if langs else "- (none detected)"

    provider_line = ", ".join(providers) if providers else "Unknown"
    repo_url = "N/A"

    diagram_deps: list[str] = []
    for ev in ingress + egress:
        low = (ev.excerpt or "").lower()
        if "sql" in low or "postgres" in low or "mysql" in low:
            diagram_deps.append("db[🗄️ Database]")
        if "servicebus" in low or "eventhub" in low or "kafka" in low or "rabbit" in low:
            diagram_deps.append("mq[🛰️ Messaging]")
        if "azure-api.net" in low or "apimanagement" in low:
            diagram_deps.append("apim[🛡️ APIM/Gateway]")
        if "blob.core.windows.net" in low or "s3.amazonaws.com" in low:
            diagram_deps.append("obj[🗄️ Object Storage]")
    diagram_deps = sorted(set(diagram_deps))

    dep_edges = "\n".join([f"  app --> {d.split('[')[0]}" for d in diagram_deps])
    dep_nodes = "\n".join([f"  {d}" for d in diagram_deps])

    evidence_lines: list[str] = []
    for ev in (extra_evidence + ingress + egress)[:30]:
        evidence_lines.append(ev.fmt())
    if not evidence_lines:
        evidence_lines.append("- 💡 (no notable evidence captured)")

    ingress_lines = "\n".join([ev.fmt() for ev in ingress]) if ingress else "- (no ingress signals detected in quick scan)"
    egress_lines = "\n".join([ev.fmt() for ev in egress]) if egress else "- (no egress signals detected in quick scan)"

    content = (
        f"# 🟣 Repo {repo_name}\n\n"
        "## 🗺️ Architecture Diagram\n"
        "```mermaid\n"
        "flowchart TB\n"
        "  client[👤 Client]\n"
        '  subgraph appbox[" Application "]\n'
        "    app[⚙️ Service]\n"
        "  end\n"
        f"{dep_nodes}\n"
        "  client --> app\n"
        f"{dep_edges}\n"
        "```\n\n"
        f"- **Overall Score:** 🟢 **0/10** (INFO) — *Context discovery only; update after security scans*\n\n"
        "## 🧭 Overview\n"
        f"- **Purpose:** {purpose}\n"
        f"- **Repo type:** {repo_type}\n"
        f"- **Cloud provider(s) referenced:** {provider_line}\n"
        f"- **CI/CD:** {ci}\n\n"
        "## 🛡️ Security Review\n"
        "### Languages & Frameworks (extracted)\n"
        f"{lang_lines}\n\n"
        "### 🧾 Summary\n"
        "Context discovery complete (no vulnerability scanning performed yet).\n\n"
        "### ✅ Applicability\n"
        "- **Status:** Don’t know\n"
        "- **Evidence:** N/A (context discovery only)\n\n"
        "### ⚠️ Assumptions\n"
        "- This summary is based on quick, file-based heuristics; confirm ingress/egress paths during deeper scan.\n\n"
        "### 🔎 Key Evidence (deep dive)\n"
        + "\n".join(evidence_lines)
        + "\n\n"
        "### Ingress Signals (quick)\n"
        f"{ingress_lines}\n\n"
        "### Egress Signals (quick)\n"
        f"{egress_lines}\n\n"
        "### Cloud Environment Implications\n"
        f"- **Provider(s) referenced:** {provider_line}\n"
        "- **Note:** If this repo deploys/targets cloud resources, promote reusable facts into `Output/Knowledge/<Provider>.md` once confirmed.\n\n"
        "## 🤔 Skeptic\n"
        "> Context discovery only — skeptic review runs after security scans generate material to critique.\n\n"
        "## 🤝 Collaboration\n"
        "- **Outcome:** Context discovery created/updated repo summary.\n"
        "- **Next step:** Choose scan types (IaC/SCA/SAST/Secrets).\n\n"
        "## Compounding Findings\n"
        "- **Compounds with:** None identified (context discovery only)\n\n"
        "## Meta Data\n"
        "<!-- Meta Data must remain the final section in the file. -->\n"
        f"- **Repo Name:** {repo_name}\n"
        f"- **Repo Path:** {repo.resolve()}\n"
        f"- **Repo URL:** {repo_url}\n"
        f"- **Repo Type:** {repo_type}\n"
        f"- **Languages/Frameworks:** {', '.join([l for l, _ in langs]) if langs else 'Unknown'}\n"
        f"- **CI/CD:** {ci}\n"
        f"- **Scan Scope:** {scan_scope}\n"
        "- **Scanner:** Context discovery (local heuristics)\n"
        f"- 🗓️ **Last updated:** {now_uk()}\n"
    )

    out_path.write_text(content, encoding="utf-8")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Fast, non-security context discovery for a local repo.")
    parser.add_argument("repo", help="Absolute or relative path to the repo to discover.")
    parser.add_argument(
        "--repos-root",
        default=None,
        help="Repo root directory to record in Output/Knowledge/Repos.md (default: parent of repo).",
    )
    args = parser.parse_args()

    repo = Path(args.repo).expanduser()
    if not repo.is_absolute():
        repo = repo.resolve()
    if not repo.is_dir():
        print(f"ERROR: repo path not found: {repo}")
        return 2

    repo_name = repo.name
    repos_root = Path(args.repos_root).expanduser().resolve() if args.repos_root else repo.parent.resolve()

    files = iter_files(repo)
    langs = detect_languages(files, repo)
    lang_names = [l for l, _ in langs]
    rtype = infer_repo_type(lang_names, repo_name)
    purpose, purpose_ev = repo_purpose(repo, repo_name)
    ci = detect_ci(repo)
    providers = detect_cloud_provider(files, repo)

    extra: list[Evidence] = []
    if purpose_ev:
        extra.append(purpose_ev)
    if (repo / "Dockerfile").exists():
        extra.append(Evidence(label="Dockerfile present", path="Dockerfile"))
    if (repo / ".github" / "workflows").exists():
        extra.append(Evidence(label="GitHub Actions workflows present", path=".github/workflows"))

    ingress: list[Evidence] = []
    egress: list[Evidence] = []
    text_files = [
        p
        for p in files
        if p.suffix.lower() in (CODE_EXTS | CFG_EXTS | IAC_EXTS | DOC_EXTS | SQL_EXTS)
        or p.name in {"Dockerfile", "docker-compose.yml"}
    ]

    for p in sorted(text_files)[:500]:
        if len(ingress) < 20:
            ingress.extend(_scan_text_file(repo, p, INGRESS_PATTERNS, limit=20 - len(ingress)))
        if len(egress) < 20:
            egress.extend(_scan_text_file(repo, p, EGRESS_PATTERNS, limit=20 - len(egress)))
        if len(ingress) >= 20 and len(egress) >= 20:
            break

    repos_md = ensure_repos_knowledge(repos_root)
    upsert_repo_inventory(
        repos_md,
        repo_name=repo_name,
        repo_type=rtype,
        purpose=purpose,
        langs=lang_names or ["Unknown"],
    )

    summary_path = write_repo_summary(
        repo=repo,
        repo_name=repo_name,
        repo_type=rtype,
        purpose=purpose,
        langs=langs,
        ci=ci,
        providers=providers,
        ingress=ingress,
        egress=egress,
        extra_evidence=extra,
        scan_scope="Context discovery",
    )

    print("== Context discovery complete ==")
    print(f"repo: {repo}")
    print(f"summary: {summary_path}")
    print(f"knowledge: {repos_md}")
    print(f"timestamp: {now_uk()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

