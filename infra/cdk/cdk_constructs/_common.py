"""Shared helpers for CDK constructs."""

from __future__ import annotations

import aws_cdk as cdk

REMOVAL_POLICY_MAP: dict[str, cdk.RemovalPolicy] = {
    "destroy": cdk.RemovalPolicy.DESTROY,
    "retain": cdk.RemovalPolicy.RETAIN,
    "snapshot": cdk.RemovalPolicy.SNAPSHOT,
}
