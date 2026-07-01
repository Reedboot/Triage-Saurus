#!/usr/bin/env python3
"""Regression tests for subscription ingress Mermaid generation."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))

from app import _build_ingress_diagram


def test_network_subgraphs_split_by_vnet():
    rows = [
        (
            "blue-spoke-ukwest",
            "microsoft.network/applicationgateways",
            "rg-network",
            "",
            True,
            "",
            1,
            False,
            None,
            False,
            None,
            None,
            '{"properties":{"subnet":{"id":"/subscriptions/000/resourceGroups/rg-network/providers/Microsoft.Network/virtualNetworks/blue-spoke-ukwest/subnets/app"}}}',
            None,
            None,
        ),
        (
            "green-spoke-ukwest",
            "microsoft.network/applicationgateways",
            "rg-network",
            "",
            True,
            "",
            2,
            False,
            None,
            False,
            None,
            None,
            '{"properties":{"subnet":{"id":"/subscriptions/000/resourceGroups/rg-network/providers/Microsoft.Network/virtualNetworks/green-spoke-ukwest/subnets/app"}}}',
            None,
            None,
        ),
    ]

    diagram = _build_ingress_diagram(rows)
    mermaid = diagram["mermaid"]

    assert mermaid.count('subgraph net_') >= 2
    assert '["🔒 Network: blue-spoke-ukwest"]' in mermaid
    assert '["🔒 Network: green-spoke-ukwest"]' in mermaid
    assert "NetworkBoundary" not in mermaid
    assert "blue-spoke-ukwest, green-spoke-ukwest" not in mermaid


def test_non_vnet_entry_points_do_not_create_unnamed_network_group():
    rows = [
        (
            "blue-spoke-ukwest",
            "microsoft.network/applicationgateways",
            "rg-network",
            "",
            True,
            "",
            1,
            False,
            None,
            False,
            None,
            None,
            '{"properties":{"subnet":{"id":"/subscriptions/000/resourceGroups/rg-network/providers/Microsoft.Network/virtualNetworks/blue-spoke-ukwest/subnets/app"}}}',
            None,
            None,
        ),
        (
            "pip-public",
            "microsoft.network/publicipaddresses",
            "rg-network",
            "",
            True,
            "",
            2,
            False,
            None,
            False,
            None,
            None,
            '{"properties":{"ipAddress":"203.0.113.10"}}',
            None,
            None,
        ),
    ]

    diagram = _build_ingress_diagram(rows)
    mermaid = diagram["mermaid"]

    assert '["🔒 Network: blue-spoke-ukwest"]' in mermaid
    assert "Networks / VNet" not in mermaid
    assert "pip-public" in mermaid


def test_vmss_with_only_subnet_id_stays_inside_vnet_subgraph():
    rows = [
        (
            "app-vmss-01",
            "microsoft.compute/virtualmachinescalesets",
            "rg-app",
            "",
            False,
            "",
            1,
            False,
            None,
            False,
            None,
            None,
            '{"_extra":{"subnet_id":"/subscriptions/000/resourceGroups/rg-network/providers/Microsoft.Network/virtualNetworks/blue-spoke-ukwest/subnets/app"}}',
            None,
            None,
        ),
    ]

    diagram = _build_ingress_diagram(rows)
    mermaid = diagram["mermaid"]

    assert 'subgraph net_rg_network__blue_spoke_ukwest["🔒 Network: blue-spoke-ukwest"]' in mermaid
    assert 'app-vmss-01' in mermaid
    assert mermaid.index('subgraph net_rg_network__blue_spoke_ukwest["🔒 Network: blue-spoke-ukwest"]') < mermaid.index('app-vmss-01')
    assert mermaid.index('app-vmss-01') < mermaid.index('    end')
