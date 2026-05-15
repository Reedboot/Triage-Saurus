#!/usr/bin/env python3
"""
Module Registry — Track what resources AND security findings each Terraform module creates.

When a module is scanned, we extract:
1. All resource types it declares  (azurerm_kubernetes_cluster, etc.)
2. Its outputs and variables
3. Any security findings from the opengrep scan (e.g. private_cluster_enabled=false)

Findings are stored against the module so that every consuming repo inherits them.
A flaw in a shared module is a flaw in every service that uses it.

Usage:
  python3 Scripts/Context/module_registry.py analyze <module-repo-path>
"""

import re
import json
import sys
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Set, Any
from dataclasses import dataclass, asdict

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "Scripts" / "Persist"))
try:
    from db_helpers import DB_PATH as DEFAULT_DB
except ImportError:
    DEFAULT_DB = REPO_ROOT / "Output" / "Data" / "cozo.db"


@dataclass
class ModuleMetadata:
    module_source: str
    module_name: str
    resource_types: List[str]
    outputs: Dict[str, str]
    variables: Dict[str, Any]
    description: str = ""

    def to_dict(self) -> Dict:
        return asdict(self)


# ── .tf parsing helpers ───────────────────────────────────────────────────────

def extract_resources_from_tf(file_path: Path) -> Set[str]:
    resource_types: Set[str] = set()
    try:
        content = file_path.read_text(encoding="utf-8")
        for match in re.finditer(r'resource\s+"([^"]+)"\s+"[^"]+"', content):
            resource_types.add(match.group(1))
    except Exception as e:
        print(f"Warning: Could not parse {file_path}: {e}", file=sys.stderr)
    return resource_types


def extract_outputs_from_tf(file_path: Path) -> Dict[str, str]:
    outputs: Dict[str, str] = {}
    try:
        content = file_path.read_text(encoding="utf-8")
        for match in re.finditer(
            r'output\s+"([^"]+)"\s*\{[^}]*?value\s*=\s*([^\n]+)', content, re.DOTALL
        ):
            name = match.group(1)
            value = re.sub(r'["\']', "", match.group(2)).split("\n")[0].strip()
            outputs[name] = value
    except Exception as e:
        print(f"Warning: Could not extract outputs from {file_path}: {e}", file=sys.stderr)
    return outputs


def extract_variables_from_tf(file_path: Path) -> Dict[str, Any]:
    variables: Dict[str, Any] = {}
    try:
        content = file_path.read_text(encoding="utf-8")
        for match in re.finditer(r'variable\s+"([^"]+)"\s*\{([^}]*)\}', content, re.DOTALL):
            var_name = match.group(1)
            default_match = re.search(r'default\s*=\s*([^\n]+)', match.group(2))
            variables[var_name] = (
                default_match.group(1).strip().strip("'\"") if default_match else None
            )
    except Exception as e:
        print(f"Warning: Could not extract variables from {file_path}: {e}", file=sys.stderr)
    return variables


def analyze_module(module_path: str) -> ModuleMetadata:
    """Walk all .tf files in a module repo and return its full metadata."""
    root = Path(module_path)
    if not root.exists():
        raise ValueError(f"Module path does not exist: {module_path}")

    all_resources: Set[str] = set()
    all_outputs: Dict[str, str] = {}
    all_variables: Dict[str, Any] = {}

    for tf_file in root.glob("**/*.tf"):
        if ".terraform" in tf_file.parts:
            continue
        all_resources.update(extract_resources_from_tf(tf_file))
        all_outputs.update(extract_outputs_from_tf(tf_file))
        all_variables.update(extract_variables_from_tf(tf_file))

    return ModuleMetadata(
        module_source="",
        module_name=root.name,
        resource_types=sorted(all_resources),
        outputs=all_outputs,
        variables=all_variables,
        description=f"Module: {root.name}",
    )


# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_conn(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = db_path or str(DEFAULT_DB)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS module_registry (
            id            INTEGER PRIMARY KEY,
            module_source TEXT UNIQUE NOT NULL,
            module_name   TEXT NOT NULL,
            resource_types TEXT NOT NULL,
            outputs       TEXT,
            variables     TEXT,
            findings      TEXT,
            description   TEXT,
            scanned_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS module_usage (
            id                      INTEGER PRIMARY KEY,
            experiment_id           TEXT NOT NULL,
            repo_id                 INTEGER NOT NULL,
            module_instance_name    TEXT NOT NULL,
            module_source           TEXT NOT NULL,
            source_file             TEXT NOT NULL,
            source_line             INTEGER,
            resolved_resource_types TEXT,
            FOREIGN KEY (module_source) REFERENCES module_registry(module_source),
            FOREIGN KEY (repo_id)       REFERENCES repositories(id)
        );
    """)
    conn.commit()
    return conn


# ── Findings capture ──────────────────────────────────────────────────────────

def capture_module_findings(
    module_source: str,
    module_experiment_id: str,
    db_path: Optional[str] = None,
) -> int:
    """After scanning a module, pull its findings from cozo.db and store them
    in module_registry.findings so every consumer inherits them.

    Returns the number of findings captured.
    """
    conn = _get_conn(db_path)
    try:
        # Ensure findings table has inherited_from_module column (migration safety)
        findings_cols = [r["name"] for r in conn.execute("PRAGMA table_info(findings)").fetchall()]
        findings_col_set = set(findings_cols)
        if not findings_cols:
            print("ℹ️  Skipping module findings capture: findings table not initialized")
            return 0

        if "inherited_from_module" not in findings_cols:
            try:
                conn.execute("ALTER TABLE findings ADD COLUMN inherited_from_module TEXT")
                conn.commit()
            except Exception:
                pass  # Table may not exist yet in this db

        def _findings_col_expr(*candidates: str, default: str = "NULL") -> str:
            for name in candidates:
                if name in findings_col_set:
                    return f"f.{name}"
            return default

        severity_expr = _findings_col_expr("severity", "base_severity")
        severity_score_expr = _findings_col_expr("severity_score", default="0")
        source_line_start_expr = _findings_col_expr("source_line_start")
        code_snippet_expr = _findings_col_expr("code_snippet")
        attack_impact_expr = _findings_col_expr("attack_impact")
        category_expr = _findings_col_expr("category")
        order_by_expr = "f.severity_score DESC" if "severity_score" in findings_col_set else "f.id DESC"

        resources_cols = conn.execute("PRAGMA table_info(resources)").fetchall()
        if resources_cols:
            rows = conn.execute(
                f"""
                SELECT f.title, f.description,
                       {severity_expr} AS severity,
                       {severity_score_expr} AS severity_score,
                       f.rule_id, f.source_file,
                       {source_line_start_expr} AS source_line_start,
                       {code_snippet_expr} AS code_snippet,
                       {attack_impact_expr} AS attack_impact,
                       {category_expr} AS category,
                       r.resource_type, r.resource_name
                FROM findings f
                LEFT JOIN resources r ON f.resource_id = r.id
                WHERE f.experiment_id = ?
                ORDER BY {order_by_expr}
                """,
                (module_experiment_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                f"""
                SELECT f.title, f.description,
                       {severity_expr} AS severity,
                       {severity_score_expr} AS severity_score,
                       f.rule_id, f.source_file,
                       {source_line_start_expr} AS source_line_start,
                       {code_snippet_expr} AS code_snippet,
                       {attack_impact_expr} AS attack_impact,
                       {category_expr} AS category,
                       NULL AS resource_type, NULL AS resource_name
                FROM findings f
                WHERE f.experiment_id = ?
                ORDER BY {order_by_expr}
                """,
                (module_experiment_id,),
            ).fetchall()

        findings = [dict(r) for r in rows]

        if findings:
            conn.execute(
                "UPDATE module_registry SET findings = ? WHERE module_source = ?",
                (json.dumps(findings), module_source),
            )
            conn.commit()
            print(f"📋 Captured {len(findings)} finding(s) from module scan into registry")
        else:
            print("ℹ️  No findings found for this module scan")

        return len(findings)
    finally:
        conn.close()


# ── Registration ──────────────────────────────────────────────────────────────

def register_module(module_metadata: ModuleMetadata, db_path: Optional[str] = None) -> None:
    """Register (or update) a module in cozo.db."""
    conn = _get_conn(db_path)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO module_registry
                (module_source, module_name, resource_types, outputs, variables, description)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                module_metadata.module_source,
                module_metadata.module_name,
                json.dumps(module_metadata.resource_types),
                json.dumps(module_metadata.outputs) if module_metadata.outputs else None,
                json.dumps(module_metadata.variables) if module_metadata.variables else None,
                module_metadata.description,
            ),
        )
        conn.commit()
        print(f"✅ Registered module: {module_metadata.module_source}")
        print(f"   Name: {module_metadata.module_name}")
        print(f"   Resource types: {len(module_metadata.resource_types)}")
        print(f"   Outputs: {len(module_metadata.outputs)}")
        print(f"   Variables: {len(module_metadata.variables)}")
    finally:
        conn.close()


def lookup_module(module_source: str, db_path: Optional[str] = None) -> Optional[ModuleMetadata]:
    """Look up a module by its source URL. Returns None if not registered."""
    conn = _get_conn(db_path)
    try:
        row = conn.execute(
            """
            SELECT module_source, module_name, resource_types, outputs, variables, description
            FROM module_registry WHERE module_source = ?
            """,
            (module_source,),
        ).fetchone()
        if row:
            return ModuleMetadata(
                module_source=row["module_source"],
                module_name=row["module_name"],
                resource_types=json.loads(row["resource_types"]),
                outputs=json.loads(row["outputs"]) if row["outputs"] else {},
                variables=json.loads(row["variables"]) if row["variables"] else {},
                description=row["description"] or "",
            )
        return None
    finally:
        conn.close()


def get_module_findings(module_source: str, db_path: Optional[str] = None) -> List[Dict]:
    """Return the stored security findings for a module."""
    conn = _get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT findings FROM module_registry WHERE module_source = ?",
            (module_source,),
        ).fetchone()
        if row and row["findings"]:
            return json.loads(row["findings"])
        return []
    finally:
        conn.close()


# ── Usage recording with finding inheritance ──────────────────────────────────

def record_module_usage(
    experiment_id: str,
    repo_id: int,
    module_instance_name: str,
    module_source: str,
    source_file: str,
    source_line: int,
    resolved_resource_types: List[str],
    db_path: Optional[str] = None,
) -> None:
    """Record a module invocation.

    Writes to three places:
    1. module_usage — the invocation record
    2. resources    — one row per inferred resource type (discovered_by='module_inference')
    3. findings     — inherited copies of the module's findings, tagged with
                      inherited_from_module so they appear in the consuming repo's
                      security review and diagram warnings
    """
    conn = _get_conn(db_path)
    try:
        # 1. Record the module invocation
        conn.execute(
            """
            INSERT INTO module_usage
                (experiment_id, repo_id, module_instance_name, module_source,
                 source_file, source_line, resolved_resource_types)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                experiment_id, repo_id, module_instance_name, module_source,
                source_file, source_line, json.dumps(resolved_resource_types),
            ),
        )

        # 2. Insert each inferred resource into the resources table
        inferred_resource_ids: Dict[str, int] = {}
        for rtype in resolved_resource_types:
            resource_name = f"{module_instance_name}.{rtype}"
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO resources
                    (experiment_id, repo_id, resource_name, resource_type,
                     provider, discovered_by, discovery_method,
                     source_file, source_line_start, status)
                VALUES (?, ?, ?, ?,
                        ?, 'module_inference', 'module_registry',
                        ?, ?, 'active')
                """,
                (
                    experiment_id, repo_id, resource_name, rtype,
                    _infer_provider(rtype), source_file, source_line,
                ),
            )
            # Fetch the row id (either new or existing)
            row = conn.execute(
                "SELECT id FROM resources WHERE experiment_id=? AND repo_id=? AND resource_name=?",
                (experiment_id, repo_id, resource_name),
            ).fetchone()
            if row:
                inferred_resource_ids[rtype] = row["id"]

        # 3. Inherit findings from the module registry
        module_findings = get_module_findings(module_source, db_path)
        inherited_count = 0

        # Ensure findings table has the inherited_from_module column
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(findings)").fetchall()}
        if "inherited_from_module" not in cols:
            try:
                conn.execute("ALTER TABLE findings ADD COLUMN inherited_from_module TEXT")
                cols.add("inherited_from_module")
            except Exception:
                pass

        for f in module_findings:
            # Map finding to the closest matching inferred resource
            f_rtype = f.get("resource_type") or ""
            resource_id = inferred_resource_ids.get(f_rtype)

            finding_title = f"[Inherited] {f.get('title', 'Module finding')}"
            finding_desc = (
                f"⚠️ Inherited from module `{module_source}`.\n\n"
                + (f.get("description") or "")
            )
            finding_severity = f.get("severity", "medium")
            finding_score = f.get("severity_score", 5)

            insert_columns = [
                "experiment_id",
                "repo_id",
                "title",
                "description",
                "severity_score",
                "resource_id",
                "rule_id",
                "source_file",
            ]
            insert_values = [
                experiment_id,
                repo_id,
                finding_title,
                finding_desc,
                finding_score,
                resource_id,
                f.get("rule_id"),
                f.get("source_file"),
            ]

            # findings schema varies across DB versions: severity may be stored
            # as either "severity" or "base_severity".
            if "severity" in cols:
                insert_columns.append("severity")
                insert_values.append(finding_severity)
            elif "base_severity" in cols:
                insert_columns.append("base_severity")
                insert_values.append(finding_severity)

            if "source_line_start" in cols:
                insert_columns.append("source_line_start")
                insert_values.append(f.get("source_line_start"))
            elif "source_line" in cols:
                insert_columns.append("source_line")
                insert_values.append(f.get("source_line_start"))

            if "code_snippet" in cols:
                insert_columns.append("code_snippet")
                insert_values.append(f.get("code_snippet"))

            if "attack_impact" in cols:
                insert_columns.append("attack_impact")
                insert_values.append(f.get("attack_impact"))

            if "inherited_from_module" in cols:
                insert_columns.append("inherited_from_module")
                insert_values.append(module_source)

            placeholders = ", ".join("?" for _ in insert_columns)
            conn.execute(
                f"INSERT INTO findings ({', '.join(insert_columns)}) VALUES ({placeholders})",
                insert_values,
            )
            inherited_count += 1

        conn.commit()

        if inherited_count:
            print(
                f"⚠️  Inherited {inherited_count} finding(s) from module "
                f"`{module_source}` into repo {repo_id}"
            )

    finally:
        conn.close()


def _infer_provider(resource_type: str) -> str:
    prefix = resource_type.split("_")[0].lower()
    return {
        "azurerm": "azure", "azuread": "azure", "azuredevops": "azure",
        "aws": "aws", "google": "gcp",
        "helm": "kubernetes", "kubernetes": "kubernetes",
        "random": "hashicorp", "time": "hashicorp",
        "terraform": "hashicorp", "null": "hashicorp",
    }.get(prefix, prefix)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] != "analyze":
        print(__doc__)
        sys.exit(1)
    metadata = analyze_module(sys.argv[2])
    print(json.dumps(metadata.to_dict(), indent=2))
