#!/usr/bin/env python3
"""
Fix provider inheritance for docker_container and kubernetes_* resources.

This script corrects the bug where docker containers and kubernetes resources
are assigned provider='docker' and provider='kubernetes' instead of inheriting
the cloud provider from their parent or the experiment context.

Usage:
    python3 Scripts/Persist/fix_provider_inheritance.py --experiment 004
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db_helpers import fix_nested_resource_providers, get_db_connection


def main():
    parser = argparse.ArgumentParser(
        description="Fix provider inheritance for nested resources (docker/kubernetes)"
    )
    parser.add_argument(
        "--experiment",
        required=True,
        help="Experiment ID to fix"
    )
    parser.add_argument(
        "--repo-id",
        type=int,
        default=None,
        help="Optional repo_id to scope the fix (if not specified, fixes all repos in experiment)"
    )
    
    args = parser.parse_args()
    
    print(f"Fixing provider inheritance for experiment {args.experiment}...")
    
    # Get initial state
    with get_db_connection() as conn:
        initial = conn.execute(
            """SELECT DISTINCT provider FROM resources 
               WHERE experiment_id = ? 
               ORDER BY provider""",
            (args.experiment,)
        ).fetchall()
        print(f"Initial providers: {[row['provider'] for row in initial]}")
    
    # Apply fix
    results = fix_nested_resource_providers(args.experiment, args.repo_id)
    
    print(f"\nResults:")
    print(f"  docker_container resources fixed: {results['docker_fixed']}")
    print(f"  kubernetes_* resources fixed: {results['kubernetes_fixed']}")
    if results['errors']:
        print(f"  Errors encountered: {results['errors']}")
    
    # Get final state
    with get_db_connection() as conn:
        final = conn.execute(
            """SELECT DISTINCT provider FROM resources 
               WHERE experiment_id = ? 
               ORDER BY provider""",
            (args.experiment,)
        ).fetchall()
        print(f"\nFinal providers: {[row['provider'] for row in final]}")
    
    # Show resource counts by provider
    with get_db_connection() as conn:
        counts = conn.execute(
            """SELECT provider, COUNT(*) as cnt FROM resources 
               WHERE experiment_id = ? 
               GROUP BY provider 
               ORDER BY provider""",
            (args.experiment,)
        ).fetchall()
        print(f"\nResource counts by provider:")
        for row in counts:
            print(f"  {row['provider']}: {row['cnt']}")
    
    print("\n✅ Provider inheritance fix complete!")


if __name__ == '__main__':
    main()
