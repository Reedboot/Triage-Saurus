#!/usr/bin/env python3
"""
Run 10 iterative scans with internet accessibility analysis.
Tracks improvements across iterations and logs findings.
"""

import subprocess
import json
import sqlite3
import sys
import os
import re
from pathlib import Path
from datetime import datetime
from collections import defaultdict

# Configuration
REPO_PATH = "/mnt/c/Repos/terragoat"
WORKSPACE_ROOT = Path(__file__).parent.parent
DB_PATH = WORKSPACE_ROOT / "Output" / "Data" / "cozo.db"
EXPERIMENTS_DIR = WORKSPACE_ROOT / "Output" / "Learning" / "experiments"
ITERATIONS = 10


def build_wsl_env():
    """Ensure user-local binaries (like opengrep) are available in subprocess PATH."""
    env = os.environ.copy()
    home = env.get("HOME", "")
    user_local_bin = os.path.join(home, ".local", "bin") if home else ""
    path_parts = env.get("PATH", "").split(os.pathsep)
    if user_local_bin and user_local_bin not in path_parts:
        env["PATH"] = user_local_bin + os.pathsep + env.get("PATH", "")
    return env

def run_command(cmd, description=""):
    """Run command and return output"""
    if description:
        print(f"\n[*] {description}")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=600,
        env=build_wsl_env(),
        cwd=WORKSPACE_ROOT,
    )
    
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        detail = stderr if stderr else stdout
        print(f"[!] Command failed: {detail[:500]}")
        return None
    
    return result.stdout

def get_latest_experiment_id():
    """Extract latest experiment ID from database or directory"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM experiments ORDER BY CAST(id AS INTEGER) DESC LIMIT 1")
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None
    except Exception as exc:
        print(f"[!] Failed to query latest experiment ID: {exc}")
        return None

def run_pipeline(iteration):
    """Run full pipeline for iteration"""
    cmd = [
        "./.venv/bin/python",
        "Scripts/Utils/run_pipeline.py",
        "--repo",
        REPO_PATH,
        "--name",
        f"iter_{iteration}",
        "--skip-phase2",
    ]
    
    output = run_command(cmd, f"Pipeline: Iteration {iteration}")
    
    # Parse experiment ID from output
    if output:
        match = re.search(r'experiment_id["\']?\s*[:=]\s*["\']?(\d+)', output, re.IGNORECASE)
        if match:
            return match.group(1)
    
    # Fallback: get latest from DB
    exp_id = get_latest_experiment_id()
    print(f"[*] Using latest experiment ID from DB: {exp_id}")
    return exp_id

def run_analyzer(experiment_id):
    """Run internet accessibility analyzer"""
    cmd = [
        "./.venv/bin/python",
        "Scripts/Analyze/internet_accessibility_analyzer.py",
        "--experiment-id",
        str(experiment_id),
    ]
    
    output = run_command(cmd, f"Analyzer: Experiment {experiment_id}")
    
    if not output:
        return None
    
    # Parse accessibility metrics
    metrics = {
        "entry_points": 0,
        "accessible_resources": 0,
        "direct_ips": 0,
        "public_endpoints": 0,
        "distance_1": 0,
    }
    
    for line in output.split('\n'):
        if 'entry point' in line.lower():
            m = re.search(r'(\d+)', line)
            if m:
                metrics["entry_points"] = int(m.group(1))
        elif 'accessible resource' in line.lower():
            m = re.search(r'(\d+)', line)
            if m:
                metrics["accessible_resources"] = int(m.group(1))
        elif 'direct public ip' in line.lower():
            m = re.search(r'(\d+)', line)
            if m:
                metrics["direct_ips"] = int(m.group(1))
        elif 'public endpoint' in line.lower():
            m = re.search(r'(\d+)', line)
            if m:
                metrics["public_endpoints"] = int(m.group(1))
        elif 'distance 1' in line.lower():
            m = re.search(r'(\d+)', line)
            if m:
                metrics["distance_1"] = int(m.group(1))
    
    return metrics

def compare_iterations(results):
    """Analyze improvements across iterations"""
    print("\n" + "="*80)
    print("ITERATION COMPARISON SUMMARY")
    print("="*80)
    
    print(f"\n{'Iter':<6} {'ExpID':<8} {'Entry Pts':<12} {'Accessible':<12} {'Direct IPs':<12} {'Endpoints':<12} {'Dist 1':<8}")
    print("-"*80)
    
    for i, result in enumerate(results, 1):
        exp_id = result.get('experiment_id', 'N/A')
        ep = result['metrics'].get('entry_points', 0)
        ar = result['metrics'].get('accessible_resources', 0)
        di = result['metrics'].get('direct_ips', 0)
        pe = result['metrics'].get('public_endpoints', 0)
        d1 = result['metrics'].get('distance_1', 0)
        
        print(f"{i:<6} {str(exp_id):<8} {ep:<12} {ar:<12} {di:<12} {pe:<12} {d1:<8}")
        
        # Show deltas
        if i > 1:
            prev = results[i-2]['metrics']
            deltas = []
            
            if ep != prev.get('entry_points', 0):
                deltas.append(f"entry_pts{ep-prev.get('entry_points', 0):+d}")
            if ar != prev.get('accessible_resources', 0):
                deltas.append(f"accessible{ar-prev.get('accessible_resources', 0):+d}")
            if di != prev.get('direct_ips', 0):
                deltas.append(f"ips{di-prev.get('direct_ips', 0):+d}")
            
            if deltas:
                print(f"      DELTA: {', '.join(deltas)}")
    
    print("\n[*] Full results saved to iteration_results.json")

def main():
    print("[*] Starting 10-iteration internet accessibility analysis")
    print(f"[*] Repository: {REPO_PATH}")
    print(f"[*] Database: {DB_PATH}")
    print(f"[*] Workspace: {WORKSPACE_ROOT}")
    
    results = []
    
    for iteration in range(1, ITERATIONS + 1):
        print(f"\n{'='*80}")
        print(f"[*] ITERATION {iteration}/{ITERATIONS}")
        print(f"{'='*80}")
        
        # Run pipeline
        experiment_id = run_pipeline(iteration)
        if not experiment_id:
            print(f"[!] Failed to get experiment ID for iteration {iteration}")
            continue
        
        print(f"[+] Experiment ID: {experiment_id}")
        
        # Run analyzer
        metrics = run_analyzer(experiment_id)
        if not metrics:
            print(f"[!] Failed to run analyzer")
            continue
        
        result = {
            "iteration": iteration,
            "experiment_id": experiment_id,
            "timestamp": datetime.now().isoformat(),
            "metrics": metrics
        }
        results.append(result)
        
        print(f"[+] Entry points: {metrics['entry_points']}")
        print(f"[+] Accessible resources: {metrics['accessible_resources']}")
        print(f"[+] Direct IPs: {metrics['direct_ips']}, Endpoints: {metrics['public_endpoints']}")
    
    # Show comparison
    compare_iterations(results)
    
    # Save results
    output_file = WORKSPACE_ROOT / "iteration_results.json"
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    
    print(f"\n[+] Results saved to {output_file}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
