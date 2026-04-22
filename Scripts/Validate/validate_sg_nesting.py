#!/usr/bin/env python3
"""
Validate SG rule nesting in generated Mermaid diagrams.
Tests that security group rules render as subgraph containers with proper nesting.
"""

import sys
import json
import re
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from Scripts.Persist.db_helpers import get_db_connection


class MermaidDiagramValidator:
    """Validates Mermaid diagram structure and SG rule nesting."""
    
    def __init__(self, experiment_id: str):
        self.experiment_id = experiment_id
        self.results = {
            'sg_rule_count': 0,
            'sg_rules_as_subgraphs': 0,
            'ec2_instances': 0,
            'ec2_in_rules': 0,
            'containers': 0,
            'containers_in_ec2': 0,
            'issues': [],
            'mermaid_code': '',
        }
    
    def fetch_diagram(self) -> str:
        """Fetch diagram from database."""
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT mermaid_code FROM cloud_diagrams WHERE experiment_id = ? AND provider = 'aws'",
                [self.experiment_id]
            ).fetchone()
            if row:
                return row['mermaid_code']
            return ''
    
    def validate(self) -> bool:
        """Run all validation checks."""
        mermaid_code = self.fetch_diagram()
        if not mermaid_code:
            self.results['issues'].append('No AWS diagram found in database')
            return False
        
        self.results['mermaid_code'] = mermaid_code
        
        # Check for basic Mermaid syntax
        if not self._validate_syntax():
            return False
        
        # Check SG rule rendering
        self._validate_sg_rules(mermaid_code)
        
        # Check EC2 nesting
        self._validate_ec2_nesting(mermaid_code)
        
        # Check container nesting
        self._validate_container_nesting(mermaid_code)
        
        return len(self.results['issues']) == 0
    
    def _validate_syntax(self) -> bool:
        """Check basic Mermaid syntax validity."""
        code = self.results['mermaid_code']
        
        # Should start with flowchart
        if not code.startswith('flowchart'):
            self.results['issues'].append('Diagram does not start with "flowchart"')
            return False
        
        # Count matching subgraph/end
        subgraph_count = code.count('subgraph ')
        end_count = code.count('\n  end')
        
        if subgraph_count != end_count:
            self.results['issues'].append(
                f'Mismatched subgraph/end: {subgraph_count} subgraphs but {end_count} ends'
            )
            return False
        
        # Check for unclosed brackets
        open_brackets = code.count('[')
        close_brackets = code.count(']')
        if open_brackets != close_brackets:
            self.results['issues'].append(
                f'Mismatched brackets: {open_brackets} open, {close_brackets} close'
            )
            return False
        
        return True
    
    def _validate_sg_rules(self, code: str) -> None:
        """Validate that SG rules render as subgraphs."""
        # Find all SG rule nodes (patterns like "allow_.*_sg")
        sg_rule_pattern = r'([a-zA-Z0-9_]*(?:allow|deny|ingress|egress)[a-zA-Z0-9_]*(?:_sg|_in_|_out)[\w]*)'
        rules_found = set(re.findall(sg_rule_pattern, code))
        
        self.results['sg_rule_count'] = len(rules_found)
        
        # Check if each rule is a subgraph (should have "subgraph rule_id[...]")
        for rule in rules_found:
            # Look for subgraph definition for this rule
            subgraph_pattern = rf'subgraph {rule}\['
            if re.search(subgraph_pattern, code):
                self.results['sg_rules_as_subgraphs'] += 1
            else:
                self.results['issues'].append(f'SG rule "{rule}" is not a subgraph')
    
    def _validate_ec2_nesting(self, code: str) -> None:
        """Validate that EC2 instances are nested inside SG rules."""
        # Find EC2 instance patterns
        ec2_pattern = r'([a-zA-Z0-9_]*instance[a-zA-Z0-9_]*)'
        instances_found = set(re.findall(ec2_pattern, code))
        
        # Filter to actual instances (avoid false matches)
        instances = [i for i in instances_found if len(i) > 3]
        self.results['ec2_instances'] = len(instances)
        
        # Check nesting: EC2 should appear after an SG rule subgraph declaration
        lines = code.split('\n')
        in_sg_rule = False
        sg_rule_indent = 0
        
        for i, line in enumerate(lines):
            # Track SG rule subgraph entry
            if 'subgraph' in line and ('allow' in line or 'deny' in line or 'sg' in line):
                in_sg_rule = True
                sg_rule_indent = len(line) - len(line.lstrip())
                continue
            
            # Track SG rule subgraph exit
            if in_sg_rule and line.strip() == 'end':
                current_indent = len(line) - len(line.lstrip())
                if current_indent == sg_rule_indent:
                    in_sg_rule = False
                continue
            
            # Check if EC2 is inside SG rule
            for instance in instances:
                if instance in line:
                    if in_sg_rule:
                        self.results['ec2_in_rules'] += 1
                    break
    
    def _validate_container_nesting(self, code: str) -> None:
        """Validate that containers are nested inside EC2 instances."""
        # Find container patterns (docker, jenkins, pod, etc.)
        container_patterns = [
            r'([a-zA-Z0-9_]*(?:jenkins|nginx|postgres|redis|container)[a-zA-Z0-9_]*)',
            r'([a-zA-Z0-9_]*_0)',  # Container suffix pattern
        ]
        
        containers_found = set()
        for pattern in container_patterns:
            containers_found.update(re.findall(pattern, code))
        
        # Filter to likely containers
        containers = [c for c in containers_found if len(c) > 2 and c not in ['and', 'end', 'the']]
        self.results['containers'] = len(containers)
        
        # Check nesting: containers should be inside EC2 subgraph
        lines = code.split('\n')
        in_ec2 = False
        ec2_indent = 0
        
        for line in lines:
            # Track EC2 subgraph entry
            if 'subgraph' in line and 'instance' in line:
                in_ec2 = True
                ec2_indent = len(line) - len(line.lstrip())
                continue
            
            # Track EC2 subgraph exit
            if in_ec2 and line.strip() == 'end':
                current_indent = len(line) - len(line.lstrip())
                if current_indent == ec2_indent:
                    in_ec2 = False
                continue
            
            # Check if containers are inside EC2
            for container in containers:
                if container in line:
                    if in_ec2:
                        self.results['containers_in_ec2'] += 1
                    break
    
    def print_results(self) -> None:
        """Print validation results."""
        print("\n" + "="*70)
        print(f"Validation Results for Experiment {self.experiment_id}")
        print("="*70)
        
        print(f"\n📊 Diagram Structure:")
        print(f"  SG Rules found:           {self.results['sg_rule_count']}")
        print(f"  SG Rules as subgraphs:    {self.results['sg_rules_as_subgraphs']}")
        if self.results['sg_rule_count'] > 0:
            pct = (self.results['sg_rules_as_subgraphs'] / self.results['sg_rule_count']) * 100
            print(f"  └─ Subgraph %:            {pct:.1f}%")
        
        print(f"\n  EC2 Instances found:      {self.results['ec2_instances']}")
        print(f"  EC2 Instances in rules:   {self.results['ec2_in_rules']}")
        if self.results['ec2_instances'] > 0:
            pct = (self.results['ec2_in_rules'] / self.results['ec2_instances']) * 100
            print(f"  └─ Nested %:              {pct:.1f}%")
        
        print(f"\n  Containers found:         {self.results['containers']}")
        print(f"  Containers in EC2:        {self.results['containers_in_ec2']}")
        if self.results['containers'] > 0:
            pct = (self.results['containers_in_ec2'] / self.results['containers']) * 100
            print(f"  └─ Nested %:              {pct:.1f}%")
        
        if self.results['issues']:
            print(f"\n⚠️  Issues found ({len(self.results['issues'])}):")
            for issue in self.results['issues']:
                print(f"  • {issue}")
        else:
            print(f"\n✅ All validations passed!")
        
        print("\n" + "="*70)


def validate_experiment(experiment_id: str, repo_name: str) -> Dict:
    """Validate a single experiment."""
    validator = MermaidDiagramValidator(experiment_id)
    success = validator.validate()
    validator.print_results()
    
    return {
        'experiment_id': experiment_id,
        'repo_name': repo_name,
        'success': success,
        'results': validator.results,
    }


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: validate_sg_nesting.py <experiment_id> [<repo_name>]")
        sys.exit(1)
    
    exp_id = sys.argv[1]
    repo = sys.argv[2] if len(sys.argv) > 2 else 'unknown'
    
    result = validate_experiment(exp_id, repo)
    sys.exit(0 if result['success'] else 1)
