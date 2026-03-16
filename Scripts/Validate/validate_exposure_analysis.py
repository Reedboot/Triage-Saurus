#!/usr/bin/env python3
"""
validate_exposure_analysis.py

Validation tests for Internet Exposure Analysis.
"""

import sys
import sqlite3
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "Persist"))
sys.path.insert(0, str(Path(__file__).parent.parent / "Analyze"))

import db_helpers
from resource_normalizer import ResourceNormalizer, UnifiedRole
from graph_traversal import GraphTraversal
from risk_scorer import RiskScorer


def test_normalizer_coverage():
    """Validate resource normalizer coverage."""
    print("\n[*] Testing Normalizer Coverage...")
    
    normalizer = ResourceNormalizer()
    
    aws_tests = [
        ("aws_internet_gateway", UnifiedRole.ENTRY_POINT),
        ("aws_waf_web_acl", UnifiedRole.COUNTERMEASURE),
        ("aws_s3_bucket", UnifiedRole.DATA),
        ("aws_instance", UnifiedRole.COMPUTE),
    ]
    
    azure_tests = [
        ("azurerm_public_ip", UnifiedRole.ENTRY_POINT),
        ("azurerm_application_gateway", UnifiedRole.COUNTERMEASURE),
        ("azurerm_storage_account", UnifiedRole.DATA),
        ("azurerm_linux_virtual_machine", UnifiedRole.COMPUTE),
    ]
    
    gcp_tests = [
        ("google_compute_address", UnifiedRole.ENTRY_POINT),
        ("google_compute_firewall", UnifiedRole.COUNTERMEASURE),
        ("google_storage_bucket", UnifiedRole.DATA),
        ("google_compute_instance", UnifiedRole.COMPUTE),
    ]
    
    all_tests = aws_tests + azure_tests + gcp_tests
    passed = 0
    
    for rtype, expected_role in all_tests:
        result = normalizer.normalize("test", rtype)
        if result.normalized_role == expected_role:
            print(f"  ✓ {rtype:40} → {expected_role.value}")
            passed += 1
        else:
            print(f"  ✗ {rtype:40} → {result.normalized_role.value}")
    
    print(f"\n  [{passed}/{len(all_tests)} passed]")
    return passed == len(all_tests)


def test_traversal_correctness():
    """Validate graph traversal."""
    print("\n[*] Testing Traversal Correctness...")
    
    resources = [
        {"id": 1, "resource_name": "igw", "resource_type": "aws_internet_gateway", "provider": "aws"},
        {"id": 2, "resource_name": "waf", "resource_type": "aws_waf_web_acl", "provider": "aws"},
        {"id": 3, "resource_name": "app", "resource_type": "aws_instance", "provider": "aws"},
        {"id": 4, "resource_name": "db", "resource_type": "aws_db_instance", "provider": "aws"},
    ]
    
    connections = [
        {"source_resource_id": 1, "target_resource_id": 2},  # IGW → WAF
        {"source_resource_id": 2, "target_resource_id": 3},  # WAF → App
        {"source_resource_id": 1, "target_resource_id": 4},  # IGW → DB (direct!)
    ]
    
    traversal = GraphTraversal()
    traversal.setup(resources, connections)
    classifications = traversal.classify_exposure()
    
    tests = [
        (3, "mitigated"),
        (4, "direct_exposure"),
    ]
    
    passed = 0
    for resource_id, expected in tests:
        actual = classifications[resource_id].exposure_level
        if actual == expected:
            print(f"  ✓ Resource {resource_id} → {actual}")
            passed += 1
        else:
            print(f"  ✗ Resource {resource_id} → {actual} (expected {expected})")
    
    print(f"\n  [{passed}/{len(tests)} passed]")
    return passed == len(tests)


def test_risk_scoring():
    """Validate risk scoring."""
    print("\n[*] Testing Risk Scoring...")
    
    tests = [
        ("CRITICAL", "direct_exposure", 10.0),
        ("HIGH", "mitigated", 4.0),
        ("MEDIUM", "isolated", 0.6),
    ]
    
    passed = 0
    for severity, exposure, expected in tests:
        score = RiskScorer.score_resource(1, severity, exposure, has_countermeasure=(exposure == "mitigated"))
        # Allow small floating point differences
        if abs(score.final_risk_score - expected) < 0.1:
            print(f"  ✓ {severity:8} + {exposure:18} → {score.final_risk_score:.1f}")
            passed += 1
        else:
            print(f"  ✗ {severity:8} + {exposure:18} → {score.final_risk_score:.1f} (expected {expected})")
    
    print(f"\n  [{passed}/{len(tests)} passed]")
    return passed == len(tests)


def test_database_persistence():
    """Validate database persistence."""
    print("\n[*] Testing Database Persistence...")
    
    with db_helpers.get_db_connection() as conn:
        cursor = conn.execute("SELECT COUNT(*) FROM exposure_analysis WHERE experiment_id = 'TEST_002'")
        count = cursor.fetchone()[0]
        
        if count >= 3:
            print(f"  ✓ Found {count} exposure records")
            return True
        else:
            print(f"  ✗ Expected ≥3 records, found {count}")
            return False


def test_summary_rendering():
    """Validate summary files."""
    print("\n[*] Testing Summary Rendering...")
    
    summary_dir = Path("/home/neil/code/Triage-Saurus/Output/Summary/Cloud/test")
    files = list(summary_dir.glob("Internet_Exposure_*.md"))
    
    if len(files) >= 2:
        print(f"  ✓ Found {len(files)} summary files")
        return True
    else:
        print(f"  ✗ Expected ≥2 summary files, found {len(files)}")
        return False


def main():
    """Run validation tests."""
    print("=" * 60)
    print("  Exposure Analysis Validation")
    print("=" * 60)
    
    results = {
        "Normalizer Coverage": test_normalizer_coverage(),
        "Traversal Correctness": test_traversal_correctness(),
        "Risk Scoring": test_risk_scoring(),
        "Database Persistence": test_database_persistence(),
        "Summary Rendering": test_summary_rendering(),
    }
    
    print("\n" + "=" * 60)
    for test_name, passed in results.items():
        status = "✓" if passed else "✗"
        print(f"  {status} {test_name}")
    
    print("=" * 60)
    all_passed = all(results.values())
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
