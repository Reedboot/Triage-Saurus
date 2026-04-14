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

def run_command(cmd, description=""):
    """Run command and return output"""
    if description:
        print(f"\n[*] {description}")
    
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=600)
    
    if result.returncode != 0:
        print(f"[!] Command failed: {result.stderr[:200]}")
        return None
    
    return result.stdout

def get_latest_experiment_id():
    """Extract latest experiment ID from database or directory"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT experiment_id FROM experiments ORDER BY experiment_id DESC LIMIT 1")
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None
    except:
        return None

def run_pipeline(iteration):
    """Run full pipeline for iteration"""
    cmd = f"cd {WORKSPACE_ROOT} && ./.venv/bin/python Scripts/Utils/run_pipeline.py --repo {REPO_PATH} --name iter_{iteration} --skip-phase2"
    
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
    cmd = f"cd {WORKSPACE_ROOT} && ./.venv/bin/python Scripts/Analyze/internet_accessibility_analyzer.py --experiment-id {experiment_id}"
    
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
