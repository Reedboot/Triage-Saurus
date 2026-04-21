# Comprehensive Detection Rules Validation Report

**Validation Date:** 2026-04-21  
**Test Scope:** 7 Detection Rules × 6 GOAT Repositories

---

## Executive Summary

| Metric | Value |
|--------|-------|
| **Total Detection Rules Tested** | 7 |
| **Total Repositories Scanned** | 6 |
| **Total Findings Detected** | 8 |
| **Repositories with Findings** | 4/6 |
| **Rules with Detections** | 2/7 |
| **Scan Success Rate** | 66.7% (4/6) |

---

## Rules Validation Matrix

| Rule | EKS GOAT | K8s GOAT | AWS GOAT | Azure GOAT | GCP GOAT | Terraform GOAT | **TOTAL** |
|------|----------|----------|----------|-----------|----------|----------------|-----------|
| DIND in K8s | 0 | 0 | 0 | ⏱️ | ⏱️ | 0 | **0** |
| RBAC Wildcards | 3 | 0 | 1 | ⏱️ | ⏱️ | 2 | **6** |
| Privileged Containers | 1 | 1 | 0 | ⏱️ | ⏱️ | 0 | **2** |
| HostPath Mounts | 0 | 0 | 0 | ⏱️ | ⏱️ | 0 | **0** |
| K8s Metadata SSRF | 0 | 0 | 0 | ⏱️ | ⏱️ | 0 | **0** |
| Helm v2 Tiller | 0 | 0 | 0 | ⏱️ | ⏱️ | 0 | **0** |
| Jenkins Exposed | 0 | 0 | 0 | ⏱️ | ⏱️ | 0 | **0** |
| **REPO TOTALS** | **4** | **1** | **1** | **⏱️** | **⏱️** | **2** | **8** |

**Legend:** ⏱️ = Scan timed out (>300s)

---

## Detailed Findings by Repository

### 1. EKS GOAT (www-project-eks-goat)
**Status:** ✓ Scanned Successfully  
**Total Findings:** 4

| Rule | Count | Details |
|------|-------|---------|
| RBAC Wildcards | 3 | RBAC role/binding configurations with wildcard permissions |
| Privileged Containers | 1 | Container running with elevated privileges |
| **SUBTOTAL** | **4** | |

**Expected:** 1 CRITICAL (exposed_jenkins)  
**Actual:** 4  
**Variance:** ⚠️ Different findings detected - RBAC and Privileged instead of Jenkins

---

### 2. K8s GOAT (kubernetes-goattest)
**Status:** ✓ Scanned Successfully  
**Total Findings:** 1

| Rule | Count | Details |
|------|-------|---------|
| Privileged Containers | 1 | Container running with elevated privileges |
| **SUBTOTAL** | **1** | |

**Expected:** 17+ (DIND, privileged, hostpath, RBAC)  
**Actual:** 1  
**Variance:** ⚠️ Only 1 finding detected vs expected 17+

**Note:** This significant difference suggests either:
- The detection rules need refinement to identify more issues
- The Kubernetes GOAT repo may not have all expected vulnerability patterns
- Incomplete coverage of the detection ruleset for K8s manifests

---

### 3. AWS GOAT (AWSGoat)
**Status:** ✓ Scanned Successfully  
**Total Findings:** 1

| Rule | Count | Details |
|------|-------|---------|
| RBAC Wildcards | 1 | IAM policy or configuration with wildcard permissions |
| **SUBTOTAL** | **1** | |

**Expected:** 0-3 (no K8s, may have jenkins)  
**Actual:** 1  
**Variance:** ✓ Within expected range

---

### 4. Azure GOAT (AzureGoat)
**Status:** ❌ Scan Timeout  
**Duration:** >600 seconds  
**Total Findings:** Cannot determine

**Notes:**
- Large repository causing extended scan times
- Opengrep scan exceeded 10-minute timeout threshold
- Resource constraints may be limiting scan performance

---

### 5. GCP GOAT (GCPGoat)
**Status:** ❌ Scan Timeout  
**Duration:** >300 seconds  
**Total Findings:** Cannot determine

**Notes:**
- Consistent timeout issues with GCP GOAT scanning
- Similar size/complexity issues as Azure GOAT
- Potential resource saturation or recursive scanning overhead

---

### 6. Terraform GOAT (TerraformGoat)
**Status:** ✓ Scanned Successfully  
**Total Findings:** 2

| Rule | Count | Details |
|------|-------|---------|
| RBAC Wildcards | 2 | Terraform configurations with RBAC wildcard patterns |
| **SUBTOTAL** | **2** | |

**Expected:** 3+ (mixed providers)  
**Actual:** 2  
**Variance:** ⚠️ Slightly below expected range

---

## Findings Summary

### Rules That Triggered
- ✓ **RBAC Wildcards:** 6 findings across 3 repos
  - EKS GOAT: 3 findings
  - AWS GOAT: 1 finding
  - Terraform GOAT: 2 findings

- ✓ **Privileged Containers:** 2 findings across 2 repos
  - EKS GOAT: 1 finding
  - K8s GOAT: 1 finding

### Rules With No Detections
- ✗ DIND in K8s: 0 findings
- ✗ HostPath Mounts: 0 findings
- ✗ K8s Metadata SSRF: 0 findings
- ✗ Helm v2 Tiller: 0 findings
- ✗ Jenkins Exposed: 0 findings

---

## Key Observations

### 1. RBAC Wildcard Detection Working Well
The RBAC wildcard detection rule is performing as expected with 6 detections across multiple providers (Kubernetes, AWS, and Terraform). This demonstrates good cross-platform coverage.

### 2. Privileged Container Detection Limited
Only 2 detections for privileged containers, primarily in Kubernetes GOAT repos. The rule may need refinement to catch more variations.

### 3. Kubernetes-Specific Rules Not Triggering
Most Kubernetes-specific rules (DIND, HostPath, SSRF, Helm v2) detected zero findings:
- May indicate these vulnerabilities are not present in the GOAT repos
- May indicate detection rules need adjustment for the specific patterns in these repos
- Could reflect gaps in the rule definitions

### 4. No Jenkins Detection
The exposed_jenkins rule detected zero findings, even in the EKS GOAT repo which was expected to have Jenkins exposure. This suggests:
- Jenkins port 8080 may not be exposed in accessible configurations
- Detection rule may not match the actual Jenkins deployment pattern
- Rule needs validation against real Jenkins deployments

### 5. Large Repo Timeouts
Azure GOAT and GCP GOAT repositories consistently timeout during scans:
- Both appear to be large repositories
- Opengrep may have performance issues with very large codebase scans
- Recommend:
  - Increasing scan timeout thresholds
  - Using more selective rule configurations for large repos
  - Running scans in parallel with dedicated resources

---

## Test Execution Details

### Scan Command
```bash
opengrep scan --config /mnt/c/Repos/Triage-Saurus/Rules/ --json <repo_path>
```

### Environment
- **Opengrep Version:** 1.16.1
- **Total Test Duration:** ~75 minutes
- **Timeout Threshold:** 300-600 seconds per repo

### Test Results Summary

| Repository | Status | Duration | Findings |
|-----------|--------|----------|----------|
| EKS GOAT | ✓ Complete | ~45s | 4 |
| K8s GOAT | ✓ Complete | ~30s | 1 |
| AWS GOAT | ✓ Complete | ~25s | 1 |
| Azure GOAT | ❌ Timeout | >600s | N/A |
| GCP GOAT | ❌ Timeout | >300s | N/A |
| Terraform GOAT | ✓ Complete | ~40s | 2 |

---

## Recommendations

### 1. Investigation of K8s GOAT Discrepancy (Priority: HIGH)
- **Issue:** Expected 17+ findings, got 1
- **Action:** 
  - Review K8s GOAT manifests to verify expected vulnerability patterns
  - Compare with detection rule thresholds
  - Validate rules are matching the specific YAML patterns in the repo

### 2. Rule Coverage Validation (Priority: MEDIUM)
- **Issue:** 5 of 7 rules detected zero findings
- **Action:**
  - Create positive test cases for each rule
  - Verify rules against real-world vulnerability patterns
  - Consider if patterns exist in GOAT repos

### 3. Performance Optimization (Priority: MEDIUM)
- **Issue:** Azure and GCP GOAT scans timeout
- **Action:**
  - Increase scan timeout to 20+ minutes for very large repos
  - Consider filtering specific directories for faster scans
  - Profile opengrep performance on large repos
  - Run large scans with dedicated resources

### 4. Jenkins Detection Rule Validation (Priority: MEDIUM)
- **Issue:** No Jenkins findings despite expected detection
- **Action:**
  - Verify exposed_jenkins rule matches EKS GOAT deployment
  - Test rule against known Jenkins port configurations
  - Check if Jenkins is actually exposed on port 8080

### 5. DIND Detection Enhancement (Priority: LOW)
- **Issue:** No DIND findings detected
- **Action:**
  - Verify DIND detection patterns against actual Docker-in-Docker configs
  - Test rule against common DIND Kubernetes manifests
  - Ensure rule covers both DinD and docker.sock mounts

---

## Conclusion

The validation run successfully tested 7 detection rules against 6 GOAT repositories with partial success:

- **4 of 6 repositories** were successfully scanned
- **8 total findings** were detected
- **2 of 7 rules** triggered detections (RBAC Wildcards and Privileged Containers)
- **2 scans timed out** (Azure and GCP GOAT repos)

The results show that RBAC wildcard detection is working well and can identify misconfigurations across multiple cloud providers. However, the significant discrepancy in the K8s GOAT findings (1 vs. expected 17+) warrants investigation. Future work should focus on:

1. Investigating why K8s GOAT only detected 1 finding vs 17+ expected
2. Optimizing performance for large repository scans
3. Validating all rules have appropriate test coverage
4. Enhancing rules that currently detect zero findings

---

**Report Generated:** 2026-04-21 17:28:56 UTC  
**Validation Script:** `/mnt/c/Repos/Triage-Saurus/validate_rules.py`
