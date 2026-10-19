from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from deployment_config import DeploymentConfig


def test_resource_name_format(dev_config: DeploymentConfig) -> None:
    assert dev_config.resource_name("artifacts", "bucket") == "aurora-dev-artifacts-bucket"


def test_resource_name_with_different_components(dev_config: DeploymentConfig) -> None:
    assert (
        dev_config.resource_name("pipeline-source", "bucket") == "aurora-dev-pipeline-source-bucket"
    )
    assert dev_config.resource_name("cicd", "pipeline") == "aurora-dev-cicd-pipeline"


def test_stack_name_format(dev_config: DeploymentConfig) -> None:
    assert dev_config.stack_name("pipeline") == "aurora-dev-pipeline"
    assert dev_config.stack_name("application") == "aurora-dev-application"


def test_prod_config_values(prod_config: DeploymentConfig) -> None:
    assert prod_config.stack_prefix == "aurora-prod"
    assert prod_config.removal_policy == "retain"
    assert prod_config.env == "prod"


def test_config_load_from_yaml() -> None:
    env_vars = {"CDK_DEPLOY_ACCOUNT": "111111111111", "CDK_DEPLOY_REGION": "us-west-2"}
    with patch.dict(os.environ, env_vars):
        config = DeploymentConfig.load("dev")
    assert config.account == "111111111111"
    assert config.region == "us-west-2"
    assert config.stack_prefix == "aurora-dev"
    assert config.removal_policy == "destroy"


def test_config_load_missing_account_raises() -> None:
    with (
        patch.dict(os.environ, {"CDK_DEPLOY_ACCOUNT": "", "CDK_DEPLOY_REGION": "us-east-2"}),
        pytest.raises(ValueError, match="CDK_DEPLOY_ACCOUNT"),
    ):
        DeploymentConfig.load("dev")


def test_config_load_missing_region_raises() -> None:
    with (
        patch.dict(os.environ, {"CDK_DEPLOY_ACCOUNT": "123456789012", "CDK_DEPLOY_REGION": ""}),
        pytest.raises(ValueError, match="CDK_DEPLOY_REGION"),
    ):
        DeploymentConfig.load("dev")


def test_config_is_frozen(dev_config: DeploymentConfig) -> None:
    with pytest.raises(Exception):  # noqa: B017
        dev_config.project = "something_else"  # type: ignore[misc]


def test_tags_propagated(dev_config: DeploymentConfig) -> None:
    assert dev_config.tags["Environment"] == "dev"
    assert dev_config.tags["Project"] == "aurora"
    assert dev_config.tags["ManagedBy"] == "cdk"
