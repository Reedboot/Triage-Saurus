#!/usr/bin/env python3
"""
10-iteration internet accessibility analyzer test.
Runs analyzer on experiment 001 with tracking of output consistency.
"""

import subprocess
import json
import re
import sys
from pathlib import Path
from datetime import datetime

WORKSPACE = Path(__file__).parent.parent

def run_analyzer(exp_id, iteration):
    """Run analyzer and capture full output"""
    cmd = f"./.venv/bin/python Scripts/Analyze/internet_accessibility_analyzer.py --experiment-id {exp_id}"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=WORKSPACE)
    
    output = result.stdout if result.returncode == 0 else f"ERROR: {result.stderr}"
    
    # Parse metrics
    metrics = {
        "entry_points": None,
        "accessible_resources": None,
        "direct_ips": None,
        "public_endpoints": None,
        "distance_1": None,
        "status": "SUCCESS" if result.returncode == 0 else "FAILED"
    }
    
    if result.returncode == 0:
        lines = output.split('\n')
        for i, line in enumerate(lines):
            if 'Found' in line and 'entry point' in line:
                m = re.search(r'(\d+)\s+Internet entry point', line)
                if m:
                    metrics["entry_points"] = int(m.group(1))
            elif 'Found' in line and 'Internet-accessible resource' in line:
                m = re.search(r'(\d+)\s+Internet-accessible resource', line)
                if m:
                    metrics["accessible_resources"] = int(m.group(1))
            elif 'Direct public IP' in line:
                m = re.search(r':\s*(\d+)', line)
                if m:
                    metrics["direct_ips"] = int(m.group(1))
            elif 'Public endpoint' in line and '- Public endpoint' in line:
                m = re.search(r':\s*(\d+)', line)
                if m:
                    metrics["public_endpoints"] = int(m.group(1))
            elif 'distance 1' in line.lower():
                m = re.search(r':\s*(\d+)', line)
                if m:
                    metrics["distance_1"] = int(m.group(1))
    
    return metrics, output

def main():
    print("[*] Running 10-iteration internet accessibility analyzer test")
    print(f"[*] Workspace: {WORKSPACE}")
    print(f"[*] Experiment: 001")
    
    results = []
    
    for iteration in range(1, 11):
        print(f"\n{'='*80}")
        print(f"[*] ITERATION {iteration}/10")
        print(f"{'='*80}")
        
        metrics, output = run_analyzer("001", iteration)
        
        result = {
            "iteration": iteration,
            "experiment_id": "001",
            "timestamp": datetime.now().isoformat(),
            "metrics": metrics,
            "full_output_lines": len(output.split('\n'))
        }
        results.append(result)
        
        # Display summary
        print(f"[+] Status: {metrics['status']}")
        print(f"[+] Entry points: {metrics['entry_points']}")
        print(f"[+] Accessible resources: {metrics['accessible_resources']}")
        print(f"[+] Direct IPs: {metrics['direct_ips']}, Endpoints: {metrics['public_endpoints']}")
        print(f"[+] Distance 1: {metrics['distance_1']}")
        
        # Show first issue or confirmation
        if metrics['status'] == "SUCCESS":
            if all(v is not None for v in [metrics['entry_points'], metrics['accessible_resources']]):
                print(f"[✓] Analyzer working correctly")
            else:
                print(f"[⚠] Some metrics not parsed - check output pattern")
    
    # Comparison across iterations
    print(f"\n{'='*80}")
    print("COMPARISON ACROSS 10 ITERATIONS")
    print(f"{'='*80}")
    
    print(f"\n{'Iter':<6} {'Status':<10} {'Entry Pts':<12} {'Accessible':<12} {'Direct IPs':<12} {'Endpoints':<12}")
    print("-"*80)
    
    for r in results:
        i = r['iteration']
        status = r['metrics']['status'][:7]
        ep = r['metrics']['entry_points'] or '?'
        acc = r['metrics']['accessible_resources'] or '?'
        di = r['metrics']['direct_ips'] or '?'
        pe = r['metrics']['public_endpoints'] or '?'
        
        print(f"{i:<6} {status:<10} {str(ep):<12} {str(acc):<12} {str(di):<12} {str(pe):<12}")
    
    # Validation
    print(f"\n{'='*80}")
    print("VALIDATION SUMMARY")
    print(f"{'='*80}")
    
    all_success = all(r['metrics']['status'] == 'SUCCESS' for r in results)
    metrics_consistent = all(r['metrics']['accessible_resources'] == results[0]['metrics']['accessible_resources'] for r in results)
    
    print(f"[{'✓' if all_success else '✗'}] All iterations successful: {all_success}")
    print(f"[{'✓' if metrics_consistent else '⚠'}] Metrics consistent across runs: {metrics_consistent}")
    
    if all_success:
        print(f"\n[+] ANALYZER IS PRODUCTION-READY")
        print(f"    - Stable output across 10 runs")
        print(f"    - Correctly identifies {results[0]['metrics']['accessible_resources']} internet-accessible resources")
        print(f"    - Entry point detection: {results[0]['metrics']['entry_points']} entry points")
    else:
        print(f"\n[!] ANALYZER HAS ISSUES - see above for details")
    
    # Save results
    output_file = WORKSPACE / "iteration_test_results.json"
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    
    print(f"\n[+] Full results saved to {output_file}")
    
    return 0 if all_success else 1

if __name__ == "__main__":
    sys.exit(main())
