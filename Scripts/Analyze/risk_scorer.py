#!/usr/bin/env python3
"""
risk_scorer.py

Compute exposure risk scores combining:
1. OpenGrep rule severity (code vulnerability)
2. Exposure multiplier (network reachability)

Risk Score = severity_score × exposure_multiplier

Severity scale (0-10):
  - Critical: 10
  - High: 8
  - Medium: 6
  - Low: 3
  - None: 0

Exposure multipliers:
  - Direct internet exposure (no countermeasure): ×1.5
  - Behind unvalidated countermeasure: ×1.0
  - Properly mitigated: ×0.5
  - Isolated from internet: ×0.1
"""

from typing import Optional, Dict, List
from dataclasses import dataclass


# OpenGrep severity to score mapping (keys are always uppercase via severity_to_score)
_SEVERITY_SCORES = {
    "CRITICAL": 10,
    "HIGH": 8,
    "MEDIUM": 6,
    "LOW": 3,
    "INFO": 1,
    "NONE": 0,
}


@dataclass
class RiskScore:
    """Computed risk score for a resource."""
    resource_id: int
    opengrep_rule_id: Optional[str]
    rule_severity: Optional[str]
    severity_score: float
    exposure_level: str  # direct_exposure, mitigated, isolated
    exposure_multiplier: float
    final_risk_score: float
    exposure_factor: str
    vulnerability_factor: str

    def to_dict(self) -> dict:
        return {
            "resource_id": self.resource_id,
            "opengrep_rule_id": self.opengrep_rule_id,
            "rule_severity": self.rule_severity,
            "severity_score": self.severity_score,
            "exposure_level": self.exposure_level,
            "exposure_multiplier": self.exposure_multiplier,
            "final_risk_score": round(self.final_risk_score, 2),
            "exposure_factor": self.exposure_factor,
            "vulnerability_factor": self.vulnerability_factor,
        }


class RiskScorer:
    """Compute risk scores combining exposure and vulnerability."""

    @staticmethod
    def severity_to_score(severity: Optional[str]) -> float:
        """Convert OpenGrep severity string to numeric score."""
        if not severity:
            return 0
        return _SEVERITY_SCORES.get(severity.strip().upper(), 3)  # normalize case

    @staticmethod
    def get_exposure_multiplier(exposure_level: str, has_countermeasure: bool = False) -> float:
        """Get multiplier based on exposure level and countermeasure presence."""
        if exposure_level == "direct_exposure":
            return 1.5
        elif exposure_level == "mitigated":
            # Behind countermeasure: lower multiplier since mitigated
            return 0.5 if has_countermeasure else 1.0
        elif exposure_level == "isolated":
            return 0.1
        else:
            return 1.0

    @staticmethod
    def compute_score(
        severity_score: float,
        exposure_level: str,
        has_countermeasure: bool = False,
    ) -> float:
        """
        Compute final risk score.

        Returns clamped score in range [0, 10]
        """
        multiplier = RiskScorer.get_exposure_multiplier(exposure_level, has_countermeasure)
        score = severity_score * multiplier
        return min(10.0, max(0.0, score))

    @staticmethod
    def score_resource(
        resource_id: int,
        rule_severity: Optional[str],
        exposure_level: str,
        rule_id: Optional[str] = None,
        has_countermeasure: bool = False,
    ) -> RiskScore:
        """
        Score a single resource given its vulnerability and exposure.

        Args:
            resource_id: Database resource ID
            rule_severity: OpenGrep rule severity (CRITICAL, HIGH, MEDIUM, LOW, INFO)
            exposure_level: exposure classification (direct_exposure, mitigated, isolated)
            rule_id: OpenGrep rule ID (for tracking)
            has_countermeasure: Whether path includes countermeasure

        Returns:
            RiskScore object
        """
        severity_score = RiskScorer.severity_to_score(rule_severity)
        multiplier = RiskScorer.get_exposure_multiplier(exposure_level, has_countermeasure)
        final_score = RiskScorer.compute_score(severity_score, exposure_level, has_countermeasure)

        # Determine factors for explanation
        vuln_factor = "No code vulnerability" if severity_score == 0 else f"{rule_severity} vulnerability"
        exp_factor = {
            "direct_exposure": "Directly exposed to internet",
            "mitigated": "Behind security controls",
            "isolated": "Isolated from internet",
        }.get(exposure_level, "Unknown exposure")

        return RiskScore(
            resource_id=resource_id,
            opengrep_rule_id=rule_id,
            rule_severity=rule_severity,
            severity_score=severity_score,
            exposure_level=exposure_level,
            exposure_multiplier=multiplier,
            final_risk_score=final_score,
            exposure_factor=exp_factor,
            vulnerability_factor=vuln_factor,
        )

    @staticmethod
    def score_multiple(
        resources: List[dict],
    ) -> List[RiskScore]:
        """
        Score multiple resources.

        Args:
            resources: List of dicts with keys:
              - resource_id, rule_severity, exposure_level, rule_id, has_countermeasure

        Returns:
            List of RiskScore objects
        """
        return [
            RiskScorer.score_resource(
                resource_id=r["resource_id"],
                rule_severity=r.get("rule_severity"),
                exposure_level=r.get("exposure_level", "isolated"),
                rule_id=r.get("rule_id"),
                has_countermeasure=r.get("has_countermeasure", False),
            )
            for r in resources
        ]


if __name__ == "__main__":
    # Test scoring
    test_cases = [
        # Direct exposure + CRITICAL vulnerability = highest risk
        {
            "resource_id": 1,
            "rule_severity": "CRITICAL",
            "exposure_level": "direct_exposure",
            "rule_id": "CKV_AWS_20",
            "has_countermeasure": False,
        },
        # Mitigated + HIGH vulnerability = medium risk
        {
            "resource_id": 2,
            "rule_severity": "HIGH",
            "exposure_level": "mitigated",
            "rule_id": "CKV_AWS_21",
            "has_countermeasure": True,
        },
        # Isolated + MEDIUM vulnerability = low risk
        {
            "resource_id": 3,
            "rule_severity": "MEDIUM",
            "exposure_level": "isolated",
            "rule_id": "CKV_AWS_22",
            "has_countermeasure": False,
        },
        # No vulnerability but exposed = medium risk
        {
            "resource_id": 4,
            "rule_severity": None,
            "exposure_level": "direct_exposure",
            "rule_id": None,
            "has_countermeasure": False,
        },
    ]

    scores = RiskScorer.score_multiple(test_cases)
    for score in scores:
        print(f"Resource {score.resource_id}: {score.final_risk_score:.2f} "
              f"({score.vulnerability_factor} + {score.exposure_factor})")
