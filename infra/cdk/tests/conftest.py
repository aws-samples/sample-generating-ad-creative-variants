from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("CDK_DEPLOY_ACCOUNT", "123456789012")
os.environ.setdefault("CDK_DEPLOY_REGION", "us-east-2")

import aws_cdk as cdk

from deployment_config import DeploymentConfig


@pytest.fixture
def dev_config() -> DeploymentConfig:
    return DeploymentConfig(
        project="aurora",
        env="dev",
        account="123456789012",
        region="us-east-2",
        stack_prefix="aurora-dev",
        removal_policy="destroy",
        encryption="s3_managed",
        versioning=True,
        block_public_access=True,
        enforce_ssl=True,
        tags={"Environment": "dev", "Project": "aurora", "ManagedBy": "cdk"},
    )


@pytest.fixture
def prod_config() -> DeploymentConfig:
    return DeploymentConfig(
        project="aurora",
        env="prod",
        account="987654321098",
        region="us-east-2",
        stack_prefix="aurora-prod",
        removal_policy="retain",
        encryption="s3_managed",
        versioning=True,
        block_public_access=True,
        enforce_ssl=True,
        tags={"Environment": "prod", "Project": "aurora", "ManagedBy": "cdk"},
    )


@pytest.fixture
def app() -> cdk.App:
    return cdk.App()


@pytest.fixture
def _test_env() -> cdk.Environment:
    return cdk.Environment(account="123456789012", region="us-east-2")
