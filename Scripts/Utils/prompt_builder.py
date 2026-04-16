#!/usr/bin/env python3
"""Prompt builder module for loading agent instructions and templates.

This module loads agent instructions from the Agents/ folder and templates from
the Templates/ folder at runtime, enabling dynamic prompt construction for AI
analysis without hardcoding instructions in the code.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional

# Configure logging
logger = logging.getLogger(__name__)

# Cache for loaded agent instructions to avoid repeated file I/O
_AGENT_CACHE: Dict[str, str] = {}
_TEMPLATE_CACHE: Dict[str, str] = {}

# Paths
REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR = REPO_ROOT / "Agents"
TEMPLATES_DIR = REPO_ROOT / "Templates"


def load_agent_instruction(agent_name: str, use_cache: bool = True) -> str:
    """Load agent instruction file from Agents/ folder.
    
    Args:
        agent_name: Name of agent file without .md extension (e.g., "DevSkeptic")
        use_cache: Whether to use cached content (default: True)
    
    Returns:
        Full content of the agent instruction file
        
    Raises:
        FileNotFoundError: If agent file doesn't exist
    """
    # Check cache first
    if use_cache and agent_name in _AGENT_CACHE:
        logger.debug(f"Loading agent '{agent_name}' from cache")
        return _AGENT_CACHE[agent_name]
    
    # Load from file
    agent_path = AGENTS_DIR / f"{agent_name}.md"
    if not agent_path.exists():
        logger.error(f"Agent file not found: {agent_path}")
        raise FileNotFoundError(f"Agent file not found: {agent_path}")
    
    logger.info(f"Loading agent instruction file: {agent_path}")
    content = agent_path.read_text(encoding="utf-8")
    
    # Cache for future use
    _AGENT_CACHE[agent_name] = content
    
    return content


def load_template(template_name: str, use_cache: bool = True) -> str:
    """Load template file from Templates/ folder.
    
    Args:
        template_name: Name of template file without .md extension (e.g., "CloudFinding")
        use_cache: Whether to use cached content (default: True)
    
    Returns:
        Full content of the template file
        
    Raises:
        FileNotFoundError: If template file doesn't exist
    """
    # Check cache first
    if use_cache and template_name in _TEMPLATE_CACHE:
        logger.debug(f"Loading template '{template_name}' from cache")
        return _TEMPLATE_CACHE[template_name]
    
    # Load from file
    template_path = TEMPLATES_DIR / f"{template_name}.md"
    if not template_path.exists():
        logger.error(f"Template file not found: {template_path}")
        raise FileNotFoundError(f"Template file not found: {template_path}")
    
    logger.info(f"Loading template file: {template_path}")
    content = template_path.read_text(encoding="utf-8")
    
    # Cache for future use
    _TEMPLATE_CACHE[template_name] = content
    
    return content


def extract_section(content: str, section_heading: str) -> Optional[str]:
    """Extract a specific section from markdown content.
    
    Args:
        content: Full markdown content
        section_heading: Heading to extract (e.g., "## Review Checklist")
    
    Returns:
        Section content including heading, or None if not found
    """
    lines = content.split('\n')
    section_lines = []
    in_section = False
    heading_level = None
    
    for line in lines:
        # Check if this is the target heading
        if line.strip().startswith('#') and section_heading.lower() in line.lower():
            in_section = True
            heading_level = len(line) - len(line.lstrip('#'))
            section_lines.append(line)
            continue
        
        # If we're in the section
        if in_section:
            # Check if we've hit another heading at same or higher level
            if line.strip().startswith('#'):
                current_level = len(line) - len(line.lstrip('#'))
                if current_level <= heading_level:
                    # End of section
                    break
            section_lines.append(line)
    
    if section_lines:
        return '\n'.join(section_lines)
    return None


def build_review_prompt(
    baseline_data: dict,
    agent_instructions: List[str],
    repo_name: str,
    experiment_id: str,
    instruction: Optional[str] = None
) -> str:
    """Build a review prompt for AI to enhance script-generated baseline.
    
    Args:
        baseline_data: Dict containing script baseline data:
            - findings: List of findings with scores
            - resources: List of detected resources
            - diagrams: List of Mermaid diagrams
            - placeholder_tldr: Script-generated placeholder summary
        agent_instructions: List of loaded agent instruction contents
        repo_name: Name of the repository being analyzed
        experiment_id: Experiment ID
        instruction: Optional custom instruction (uses default if not provided)
    
    Returns:
        Complete prompt string for AI review
    """
    default_instruction = """Review script-generated baseline. Enhance TLDRs, adjust scores with reasoning, 
identify missing assets, validate Mermaid diagrams."""
    
    task_instruction = instruction or default_instruction
    
    # Build the prompt
    prompt_parts = [
        "You are REVIEWING script-generated baseline data for a security triage.",
        "",
        "# AGENT INSTRUCTIONS (your methodology):",
        "The following agent instructions define how to perform systematic security review:",
        ""
    ]
    
    # Add agent instructions
    for i, agent_content in enumerate(agent_instructions, 1):
        prompt_parts.append(f"## Agent Instruction Set {i}")
        prompt_parts.append("")
        prompt_parts.append(agent_content)
        prompt_parts.append("")
        prompt_parts.append("---")
        prompt_parts.append("")
    
    # Add baseline data context
    prompt_parts.extend([
        "# BASELINE DATA (from Phase 1-3 scripts):",
        f"Repository: {repo_name}",
        f"Experiment: {experiment_id}",
        ""
    ])
    
    # Add findings summary
    findings = baseline_data.get("findings", [])
    prompt_parts.append(f"## Findings ({len(findings)} detected by OpenGrep):")
    for finding in findings[:10]:  # Limit to first 10 for prompt size
        prompt_parts.append(f"- [{finding.get('severity_score', 'N/A')}/10] {finding.get('title', 'Untitled')}")
        prompt_parts.append(f"  File: {finding.get('source_file', 'unknown')}")
        prompt_parts.append(f"  Rule: {finding.get('rule_id', 'unknown')}")
    if len(findings) > 10:
        prompt_parts.append(f"... and {len(findings) - 10} more findings")
    prompt_parts.append("")
    
    # Add resources summary
    resources = baseline_data.get("resources", [])
    prompt_parts.append(f"## Resources ({len(resources)} detected by scripts):")
    for resource in resources[:15]:  # Limit to first 15
        prompt_parts.append(f"- {resource.get('resource_type', 'unknown')}: {resource.get('resource_name', 'unnamed')}")
    if len(resources) > 15:
        prompt_parts.append(f"... and {len(resources) - 15} more resources")
    prompt_parts.append("")
    
    # Add diagrams
    diagrams = baseline_data.get("diagrams", [])
    if diagrams:
        prompt_parts.append(f"## Architecture Diagrams ({len(diagrams)} generated by scripts):")
        for diagram in diagrams:
            prompt_parts.append(f"### {diagram.get('title', 'Untitled Diagram')}")
            prompt_parts.append("```mermaid")
            prompt_parts.append(diagram.get('mermaid_code', ''))
            prompt_parts.append("```")
            prompt_parts.append("")
    
    # Add placeholder TLDR
    placeholder_tldr = baseline_data.get("placeholder_tldr", "")
    if placeholder_tldr:
        prompt_parts.append(f"## Script-Generated TLDR:")
        prompt_parts.append(placeholder_tldr)
        prompt_parts.append("")
    
    # Add task instructions
    prompt_parts.extend([
        "# YOUR TASK:",
        task_instruction,
        "",
        "Specifically:",
        "1. Review each finding - adjust scores using DevSkeptic/PlatformSkeptic guidance",
        "2. Enhance TLDR - create human-readable summary with full context",
        "3. Identify gaps - find assets/relationships scripts missed",
        "4. Validate Mermaid diagrams - check hierarchy, connections, grouping",
        "5. Explain changes - provide reasoning for all adjustments",
        "",
        "## DIAGRAM VALIDATION CHECKLIST:",
        "- Is APIM shown as parent subgraph containing APIs? (APIs contain Operations?)",
        "- Are private endpoints explicit nodes (not hidden)?",
        "- Is VNet integration shown (not direct App Service → SQL)?",
        "- Are all code-referenced resources in the diagram?",
        "- Are resources grouped correctly (Compute/Database/Identity/Network)?",
        "- Are parent-child relationships correct (Storage Account → Container → Blob)?",
        "",
        "# OUTPUT JSON SCHEMA:",
        "Return a JSON object with the following structure:",
        "```json",
        "{",
        '  "enhanced_tldr": "<AI summary with full context>",',
        '  "score_adjustments": [',
        '    {',
        '      "finding_id": <id>,',
        '      "old_score": <1-10>,',
        '      "new_score": <1-10>,',
        '      "reasoning": "<explanation using agent guidance>",',
        '      "agent_used": "DevSkeptic|PlatformSkeptic|SecurityAgent"',
        '    }',
        '  ],',
        '  "new_assets": [',
        '    {',
        '      "name": "<asset name>",',
        '      "type": "<resource type>",',
        '      "confidence": "high|medium|low",',
        '      "how_discovered": "<explanation>"',
        '    }',
        '  ],',
        '  "description_enhancements": [',
        '    {',
        '      "finding_id": <id>,',
        '      "enhanced_description": "<human-readable description>"',
        '    }',
        '  ],',
        '  "diagram_corrections": [',
        '    {',
        '      "diagram_title": "<title>",',
        '      "issue_type": "missing_asset|missing_connection|incorrect_hierarchy|incorrect_grouping",',
        '      "correction": "<explanation of fix>",',
        '      "original_snippet": "<relevant part of original>",',
        '      "corrected_mermaid_code": "<full corrected diagram or null if explanation only>"',
        '    }',
        '  ]',
        "}",
        "```"
    ])
    
    return "\n".join(prompt_parts)


def build_context_extraction_prompt(
    agent_content: str,
    baseline_data: dict,
    repo_name: str,
    experiment_id: str,
) -> str:
    """Build a focused prompt for AI-powered context extraction / gap analysis.

    This runs BEFORE the reviewer agents so they receive enriched context.
    The goal is not security review but architecture completeness:
    identify resources the scripts missed, clarify ambiguous connections,
    and fill in protocol/port/auth gaps so the reviewer agents have
    accurate material to work from.

    Args:
        agent_content: Full content of ContextDiscoveryAgent.md
        baseline_data: Dict with findings, resources, diagrams
        repo_name: Repository name
        experiment_id: Experiment ID

    Returns:
        Focused context-extraction prompt string.
    """
    resources = baseline_data.get("resources", [])
    findings = baseline_data.get("findings", [])
    diagrams = baseline_data.get("diagrams", [])
    roles = baseline_data.get("roles", [])
    ports = baseline_data.get("ports", [])
    attack_paths = baseline_data.get("attack_paths", [])

    # Include only the Architecture / Discovery sections of the agent — not the
    # full 80 KB of bash scripts and output templates.  We extract the first
    # 8 000 chars (covers purpose, discovery scope, and relationship rules).
    agent_excerpt = agent_content[:8000]
    if len(agent_content) > 8000:
        agent_excerpt += "\n\n[... remaining agent instructions truncated for prompt size ...]"

    prompt_parts = [
        "You are acting as the **Context Discovery Agent** for a security triage portal.",
        "Your role is NOT to review security findings — that is done by separate agents.",
        "Your role is to VALIDATE and ENRICH the script-extracted architecture context.",
        "",
        "# AGENT INSTRUCTIONS (excerpt — architecture and discovery scope):",
        agent_excerpt,
        "",
        "---",
        "",
        "# SCRIPT-EXTRACTED BASELINE:",
        f"Repository: {repo_name}  |  Experiment: {experiment_id}",
        "",
        f"## Resources detected by scripts ({len(resources)} total — showing top 30):",
    ]

    for r in resources[:30]:
        prompt_parts.append(
            f"- {r.get('resource_type', 'unknown')}: {r.get('resource_name', 'unnamed')} "
            f"[{r.get('provider', '?')}]"
        )
    if len(resources) > 30:
        prompt_parts.append(f"  … and {len(resources) - 30} more")

    if diagrams:
        prompt_parts.append(f"\n## Architecture diagrams ({len(diagrams)}):")
        for d in diagrams[:2]:
            prompt_parts.append(f"### {d.get('title', 'Diagram')}")
            prompt_parts.append("```mermaid")
            prompt_parts.append(d.get("mermaid_code", ""))
            prompt_parts.append("```")

    if roles:
        prompt_parts.append(f"\n## Roles / permissions ({len(roles)} total — showing top 20):")
        for role in roles[:20]:
            prompt_parts.append(
                f"- {role.get('identity_name', 'unknown')} | role={role.get('role_name', role.get('permissions', 'unknown'))} "
                f"| scope={role.get('scope_name', role.get('resource_name', 'unknown'))} | principal={role.get('principal_name', role.get('principal_id', 'unknown'))}"
            )
    if attack_paths:
        prompt_parts.append(f"\n## Candidate attack paths ({len(attack_paths)} total — showing top 8):")
        for attack_path in attack_paths[:8]:
            prompt_parts.append(
                f"- {attack_path.get('title', 'Attack path')} | path={attack_path.get('path', 'unknown')} "
                f"| impact={attack_path.get('impact', 'unknown')} | evidence={'; '.join((attack_path.get('evidence') or [])[:2])}"
            )
    if ports:
        prompt_parts.append(f"\n## Internet/public endpoint evidence ({len(ports)} total — showing top 15):")
        for port in ports[:15]:
            prompt_parts.append(
                f"- resource_id={port.get('resource_id')} port={port.get('port')} protocol={port.get('protocol')} evidence={port.get('evidence')}"
            )

    prompt_parts.append(f"\n## Findings summary ({len(findings)} detected by OpenGrep):")
    for f in findings[:10]:
        prompt_parts.append(f"- [{f.get('severity_score', '?')}/10] {f.get('title', 'Untitled')}")
    if len(findings) > 10:
        prompt_parts.append(f"  … and {len(findings) - 10} more")

    prompt_parts.extend([
        "",
        "# YOUR TASK:",
        "1. Identify resources the scripts missed (check diagram vs resource list for gaps)",
        "2. Clarify ambiguous connections (missing protocol/port, unclear auth method)",
        "3. Flag architecture patterns that need attention before security review",
        "4. Surface privilege/identity chains that reviewers must inspect (for example: compromised compute -> managed identity -> automation/resource control -> broader role scope)",
        "5. Surface public data endpoints separately from anonymous public access (for example: public endpoint with auth vs anonymous blob/container access)",
        "4. Do NOT score findings or make security judgements — that is for the reviewer agents",
        "",
        "Return JSON only:",
        "```json",
        "{",
        '  "context_summary": "<2-3 sentence description of what this repo does architecturally>",',
        '  "attack_paths": [{"title": "<path title>", "path": "<A -> B -> C>", "summary": "<why this path is plausible>", "impact": "<what an attacker gets>", "confidence": "high|medium|low", "evidence": ["<finding or clue>"]}],',
        '  "new_assets": [{"name": "<name>", "type": "<type>", "confidence": "high|medium|low", "how_discovered": "<why>"}],',
        '  "connection_gaps": [{"from": "<resource>", "to": "<resource>", "missing": "protocol|port|auth|all", "inferred": "<value>"}],',
        '  "architecture_notes": ["<note about pattern, boundary, or data flow>"],',
        '  "open_questions": ["<question for security reviewers>"]',
        "}",
        "```",
    ])

    return "\n".join(prompt_parts)


def build_architecture_review_prompt(
    agent_content: str,
    baseline_data: dict,
    repo_name: str,
    experiment_id: str,
) -> str:
    """Build a focused prompt for architecture validation and diagram repair."""
    resources = baseline_data.get("resources", [])
    findings = baseline_data.get("findings", [])
    diagrams = baseline_data.get("diagrams", [])
    roles = baseline_data.get("roles", [])
    ports = baseline_data.get("ports", [])
    attack_paths = baseline_data.get("attack_paths", [])

    agent_excerpt = agent_content[:8000]
    if len(agent_content) > 8000:
        agent_excerpt += "\n\n[... remaining agent instructions truncated for prompt size ...]"

    prompt_parts = [
        "You are acting as the Architecture Validation Agent for a security triage portal.",
        "Your job is to validate the generated architecture diagrams, identify missing or incorrect resources and relationships, and recommend concrete code or rule changes that would fix the source of truth.",
        "Focus on architecture fidelity: hierarchy, grouping, Internet exposure, missing edges, and direct vs indirect access.",
        "",
        "# AGENT INSTRUCTIONS (excerpt — architecture and validation scope):",
        agent_excerpt,
        "",
        "---",
        "",
        "# SCRIPT-EXTRACTED BASELINE:",
        f"Repository: {repo_name}  |  Experiment: {experiment_id}",
        "",
        f"## Resources detected by scripts ({len(resources)} total — showing top 30):",
    ]

    for r in resources[:30]:
        prompt_parts.append(
            f"- {r.get('resource_type', 'unknown')}: {r.get('resource_name', 'unnamed')} "
            f"[{r.get('provider', '?')}]"
        )
    if len(resources) > 30:
        prompt_parts.append(f"  ... and {len(resources) - 30} more")

    if diagrams:
        prompt_parts.append(f"\n## Architecture diagrams ({len(diagrams)}):")
        for d in diagrams[:3]:
            prompt_parts.append(f"### {d.get('title', 'Diagram')}")
            prompt_parts.append("```mermaid")
            prompt_parts.append(d.get("mermaid_code", "") or d.get("code_snippet", "") or d.get("code", ""))
            prompt_parts.append("```")

    if findings:
        prompt_parts.append(f"\n## Findings summary ({len(findings)} detected by OpenGrep):")
        for f in findings[:10]:
            prompt_parts.append(f"- [{f.get('severity_score', '?')}/10] {f.get('title', 'Untitled')}")
        if len(findings) > 10:
            prompt_parts.append(f"  ... and {len(findings) - 10} more")

    if roles:
        prompt_parts.append(f"\n## Roles / permissions ({len(roles)} total — showing top 20):")
        for role in roles[:20]:
            prompt_parts.append(
                f"- {role.get('identity_name', 'unknown')} | role={role.get('role_name', role.get('permissions', 'unknown'))} "
                f"| scope={role.get('scope_name', role.get('resource_name', 'unknown'))} | principal={role.get('principal_name', role.get('principal_id', 'unknown'))}"
            )

    if attack_paths:
        prompt_parts.append(f"\n## Candidate attack paths ({len(attack_paths)} total — showing top 8):")
        for attack_path in attack_paths[:8]:
            prompt_parts.append(
                f"- {attack_path.get('title', 'Attack path')} | path={attack_path.get('path', 'unknown')} "
                f"| impact={attack_path.get('impact', 'unknown')} | evidence={'; '.join((attack_path.get('evidence') or [])[:2])}"
            )

    if ports:
        prompt_parts.append(f"\n## Exposure evidence ({len(ports)} total — showing top 15):")
        for port in ports[:15]:
            prompt_parts.append(
                f"- resource_id={port.get('resource_id')} port={port.get('port')} protocol={port.get('protocol')} evidence={port.get('evidence')}"
            )

    prompt_parts.extend([
        "",
        "# YOUR TASK:",
        "1. Validate hierarchy, connectivity, and Internet exposure in the diagrams.",
        "2. Identify missing assets, missing edges, and incorrect parent-child relationships.",
        "3. Validate privilege attack paths in the architecture: if compromised compute can manage automation, identities, or broad RBAC scopes, require explicit arrows/notes.",
        "4. Distinguish anonymous public access from authenticated public endpoints for data services such as Storage and Cosmos DB.",
        "5. If the issue should be fixed in code or rules, call out the concrete file or rule and the change needed.",
        "6. Return JSON only.",
        "",
        "```json",
        "{",
        '  "architecture_summary": "<2-3 sentence summary of the architecture and validation outcome>",',
        '  "attack_paths": [{"title": "<path title>", "path": "<A -> B -> C>", "summary": "<why this path matters>", "impact": "<what an attacker gets>", "confidence": "high|medium|low", "evidence": ["<finding or clue>"]}],',
        '  "new_assets": [{"name": "<name>", "type": "<type>", "confidence": "high|medium|low", "how_discovered": "<why>"}],',
        '  "diagram_corrections": [{"diagram_title": "<title>", "issue_type": "missing_asset|missing_connection|incorrect_hierarchy|incorrect_grouping|internet_exposure", "correction": "<what to change>", "original_snippet": "<original snippet>", "corrected_mermaid_code": "<full corrected diagram or null>"}],',
        '  "learning_suggestions": [{"kind": "rule_change|code_change|diagram_fix", "target": "<file or rule>", "rationale": "<why>", "example_evidence": "<evidence>", "proposed_change": "<specific change>"}],',
        '  "open_questions": [{"question": "<question>", "file": "<path>", "line": <line or null>, "asset": "<asset>"}],',
        '  "fixed_information": ["<correction or clarification>"]',
        "}",
        "```",
    ])

    return "\n".join(prompt_parts)


def build_focused_prompt(
    agent_name: str,
    agent_content: str,
    baseline_data: dict,
    repo_name: str,
    experiment_id: str,
) -> str:
    """Build a focused single-agent review prompt to stay within token limits.

    Unlike build_review_prompt (which inlines all agents), this builds a
    compact prompt for ONE reviewer role with tighter data limits so each
    Copilot job comfortably fits within the model's context window.

    Args:
        agent_name: Reviewer label, e.g. "SecurityAgent", "DevSkeptic"
        agent_content: Full content of that agent's instruction file
        baseline_data: Dict with findings, resources, diagrams, skeptic_reviews
        repo_name: Repository name
        experiment_id: Experiment ID

    Returns:
        Complete prompt string for this single reviewer.
    """
    role_instructions = {
        "SecurityAgent":    "Focus on attack paths, severity accuracy, and missing security controls.",
        "DevSkeptic":       "Focus on developer-perspective score adjustments and false-positive dismissals.",
        "PlatformSkeptic":  "Focus on cloud/infra misconfiguration risk and platform-level mitigations.",
    }
    role_hint = role_instructions.get(agent_name, "Review findings from your perspective.")

    findings = baseline_data.get("findings", [])
    resources = baseline_data.get("resources", [])
    roles = baseline_data.get("roles", [])
    ports = baseline_data.get("ports", [])
    attack_paths = baseline_data.get("attack_paths", [])

    prompt_parts = [
        f"You are acting as the **{agent_name}** reviewer for a security triage portal.",
        f"{role_hint}",
        "",
        "# YOUR AGENT INSTRUCTIONS:",
        agent_content,
        "",
        "---",
        "",
        "# BASELINE DATA:",
        f"Repository: {repo_name}  |  Experiment: {experiment_id}",
        "",
        f"## Findings ({len(findings)} total — showing top 15 by severity):",
    ]

    for finding in findings[:15]:
        prompt_parts.append(
            f"- [{finding.get('severity_score', 'N/A')}/10] {finding.get('title', 'Untitled')}"
            f"  (rule: {finding.get('rule_id', '?')}, file: {finding.get('source_file', '?')})"
        )
    if len(findings) > 15:
        prompt_parts.append(f"  … and {len(findings) - 15} more findings")

    prompt_parts.append("")
    prompt_parts.append(f"## Resources ({len(resources)} total — showing top 20):")
    for resource in resources[:20]:
        prompt_parts.append(
            f"- {resource.get('resource_type', 'unknown')}: {resource.get('resource_name', 'unnamed')}"
        )
    if len(resources) > 20:
        prompt_parts.append(f"  … and {len(resources) - 20} more resources")

    if roles:
        prompt_parts.append("")
        prompt_parts.append(f"## Roles / permissions ({len(roles)} total — showing top 15):")
        for role in roles[:15]:
            prompt_parts.append(
                f"- {role.get('identity_name', 'unknown')} | role={role.get('role_name', role.get('permissions', 'unknown'))} "
                f"| scope={role.get('scope_name', role.get('resource_name', 'unknown'))} | principal={role.get('principal_name', role.get('principal_id', 'unknown'))}"
            )

    if attack_paths:
        prompt_parts.append("")
        prompt_parts.append(f"## Candidate attack paths ({len(attack_paths)} total — showing top 8):")
        for attack_path in attack_paths[:8]:
            prompt_parts.append(
                f"- {attack_path.get('title', 'Attack path')} | path={attack_path.get('path', 'unknown')} "
                f"| impact={attack_path.get('impact', 'unknown')} | evidence={'; '.join((attack_path.get('evidence') or [])[:2])}"
            )

    if ports:
        prompt_parts.append("")
        prompt_parts.append(f"## Exposure evidence ({len(ports)} total — showing top 10):")
        for port in ports[:10]:
            prompt_parts.append(
                f"- resource_id={port.get('resource_id')} port={port.get('port')} protocol={port.get('protocol')} evidence={port.get('evidence')}"
            )

    prompt_parts.extend([
        "",
        "# YOUR TASK:",
        "Before scoring findings, explicitly look for attack paths involving compromised compute, managed identities, automation accounts/runbooks, and broad Contributor/Owner scopes.",
        "Also check whether public data services are anonymously reachable or only exposed via authenticated public endpoints, and call out missing Internet arrows or auth labels when diagrams flatten that distinction.",
        "Review the findings above using your agent instructions. Return JSON only:",
        "```json",
        "{",
        '  "enhanced_tldr": "<1-2 sentence summary from your reviewer perspective>",',
        '  "attack_paths": [{"title": "<path title>", "path": "<A -> B -> C>", "summary": "<why this path matters>", "impact": "<what an attacker gets>", "confidence": "high|medium|low", "evidence": ["<finding or clue>"]}],',
        '  "score_adjustments": [',
        '    {"finding_id": <id>, "old_score": <1-10>, "new_score": <1-10>,',
        '     "reasoning": "<why>", "agent_used": "' + agent_name + '"}',
        '  ],',
        '  "new_assets": [{"name": "<name>", "type": "<type>", "confidence": "high|medium|low", "how_discovered": "<why>"}],',
        '  "observations": [{"title": "<title>", "detail": "<detail>"}],',
        '  "open_questions": ["<question>"],',
        '  "action_items": ["<action>"]',
        "}",
        "```",
    ])

    return "\n".join(prompt_parts)


def clear_cache() -> None:
    """Clear the agent and template caches.
    
    Useful for testing or when agent files are updated during runtime.
    """
    global _AGENT_CACHE, _TEMPLATE_CACHE
    _AGENT_CACHE.clear()
    _TEMPLATE_CACHE.clear()
    logger.info("Cleared agent and template caches")


if __name__ == "__main__":
    # Test the module
    logging.basicConfig(level=logging.INFO)
    
    print("Testing prompt_builder module...")
    print()
    
    # Test loading agent instructions
    try:
        dev_skeptic = load_agent_instruction("DevSkeptic")
        print(f"✓ Loaded DevSkeptic ({len(dev_skeptic)} chars)")
        
        platform_skeptic = load_agent_instruction("PlatformSkeptic")
        print(f"✓ Loaded PlatformSkeptic ({len(platform_skeptic)} chars)")
        
        security_agent = load_agent_instruction("SecurityAgent")
        print(f"✓ Loaded SecurityAgent ({len(security_agent)} chars)")
    except FileNotFoundError as e:
        print(f"✗ Error loading agent: {e}")
    
    print()
    
    # Test loading templates
    try:
        cloud_finding = load_template("CloudFinding")
        print(f"✓ Loaded CloudFinding template ({len(cloud_finding)} chars)")
        
        code_finding = load_template("CodeFinding")
        print(f"✓ Loaded CodeFinding template ({len(code_finding)} chars)")
    except FileNotFoundError as e:
        print(f"✗ Error loading template: {e}")
    
    print()
    print("All tests passed!")
