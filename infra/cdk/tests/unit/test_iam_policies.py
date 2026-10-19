"""Cross-cutting IAM policy validation across all stacks.

Ensures that managed policies follow least-privilege: exact actions, specific
ARN resources (no wildcards), and no trailing-star actions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import aws_cdk as cdk
import aws_cdk.assertions as assertions

from cdk_constructs.dynamo_table import DynamoTable
from cdk_constructs.kms_key import KmsKey
from cdk_constructs.secure_bucket import SecureBucket
from stacks.data_stack import DataStack
from stacks.inference_stack import InferenceStack
from stacks.ingestion_stack import IngestionStack
from stacks.security_stack import SecurityStack

if TYPE_CHECKING:
    from deployment_config import DeploymentConfig


# ── Helpers ─────────────────────────────────────────────────────────────────


def _all_managed_policies(template: assertions.Template) -> dict[str, Any]:
    return dict(template.find_resources("AWS::IAM::ManagedPolicy"))


def _ingestion_template(config: DeploymentConfig) -> assertions.Template:
    app = cdk.App()
    env = cdk.Environment(account="123456789012", region="us-east-2")
    security = SecurityStack(app, "Sec", config=config, env=env)
    data = DataStack(
        app,
        "Data",
        config=config,
        vpc=security.vpc,
        kms_key=security.kms_key,
        workload_subnet_type=security.workload_subnet_type,
        env=env,
    )
    inference = InferenceStack(
        app,
        "Inference",
        config=config,
        vpc=security.vpc,
        kms_key=security.kms_key,
        staging_read_policy=data.staging_read_policy,
        results_read_write_policy=data.results_read_write_policy,
        job_table_read_write_policy=data.job_table_read_write_policy,
        kms_encrypt_decrypt_policy=security.kms_encrypt_decrypt_policy,
        file_system=data.model_fs.file_system,
        access_point=data.model_fs.access_point,
        workload_subnet_type=security.workload_subnet_type,
        env=env,
    )
    ingestion = IngestionStack(
        app,
        "Ingestion",
        config=config,
        vpc=security.vpc,
        workload_subnet_type=security.workload_subnet_type,
        landing_bucket=data.landing_bucket,
        staging_bucket=data.staging_bucket,
        results_bucket=data.results_bucket,
        landing_read_policy=data.landing_read_policy,
        staging_read_write_policy=data.staging_read_write_policy,
        job_table_read_write_policy=data.job_table_read_write_policy,
        kms_encrypt_decrypt_policy=security.kms_encrypt_decrypt_policy,
        job_queue_arn_ssm=inference.job_queue_arn_ssm,
        job_definition_arn_ssm=inference.job_definition_arn_ssm,
        env=env,
    )
    return assertions.Template.from_stack(ingestion)


# ── SecureBucket policy tests ───────────────────────────────────────────────


def test_secure_bucket_read_policy_exact_actions(dev_config: DeploymentConfig) -> None:
    """read_policy must grant exactly s3:GetObject, s3:ListBucket, s3:GetBucketLocation."""
    app = cdk.App()
    stack = cdk.Stack(app, "TestStack")
    bucket = SecureBucket(stack, "TestBucket", config=dev_config, component="test")
    # Trigger lazy creation
    _ = bucket.read_policy
    template = assertions.Template.from_stack(stack)
    template.has_resource_properties(
        "AWS::IAM::ManagedPolicy",
        {
            "PolicyDocument": {
                "Statement": [
                    {
                        "Action": ["s3:GetObject", "s3:ListBucket", "s3:GetBucketLocation"],
                        "Effect": "Allow",
                        "Resource": assertions.Match.any_value(),
                    }
                ]
            }
        },
    )


def test_secure_bucket_read_policy_scoped_to_bucket_arn(
    dev_config: DeploymentConfig,
) -> None:
    """read_policy must scope resources to specific bucket ARN and objects."""
    app = cdk.App()
    stack = cdk.Stack(app, "TestStack")
    bucket = SecureBucket(stack, "TestBucket", config=dev_config, component="test")
    _ = bucket.read_policy
    template = assertions.Template.from_stack(stack)
    policies = _all_managed_policies(template)
    assert len(policies) == 1
    statement = next(iter(policies.values()))["Properties"]["PolicyDocument"]["Statement"][0]
    resources = statement["Resource"]
    # Must have exactly 2 resources: the bucket ARN and bucket ARN/*
    assert len(resources) == 2
    # Both must be Fn::GetAtt or Fn::Join refs (not wildcards)
    for r in resources:
        assert r != "*"


def test_secure_bucket_read_write_policy_adds_put_and_delete(
    dev_config: DeploymentConfig,
) -> None:
    """read_write_policy must add s3:PutObject, s3:DeleteObject to read actions."""
    app = cdk.App()
    stack = cdk.Stack(app, "TestStack")
    bucket = SecureBucket(stack, "TestBucket", config=dev_config, component="test")
    _ = bucket.read_write_policy
    template = assertions.Template.from_stack(stack)
    template.has_resource_properties(
        "AWS::IAM::ManagedPolicy",
        {
            "PolicyDocument": {
                "Statement": [
                    {
                        "Action": [
                            "s3:GetObject",
                            "s3:ListBucket",
                            "s3:GetBucketLocation",
                            "s3:PutObject",
                            "s3:DeleteObject",
                        ],
                        "Effect": "Allow",
                        "Resource": assertions.Match.any_value(),
                    }
                ]
            }
        },
    )


# ── DynamoTable policy tests ───────────────────────────────────────────────


def test_dynamo_table_read_write_policy_exact_actions(
    dev_config: DeploymentConfig,
) -> None:
    """DynamoTable.read_write_policy must have exact DynamoDB CRUD actions."""
    app = cdk.App()
    stack = cdk.Stack(app, "TestStack")
    key = KmsKey(stack, "Key", config=dev_config)
    table = DynamoTable(stack, "Table", config=dev_config, encryption_key=key.key)
    _ = table.read_write_policy
    template = assertions.Template.from_stack(stack)
    template.has_resource_properties(
        "AWS::IAM::ManagedPolicy",
        {
            "PolicyDocument": {
                "Statement": [
                    {
                        "Action": [
                            "dynamodb:GetItem",
                            "dynamodb:PutItem",
                            "dynamodb:UpdateItem",
                            "dynamodb:DeleteItem",
                            "dynamodb:Query",
                            "dynamodb:Scan",
                            "dynamodb:BatchGetItem",
                            "dynamodb:BatchWriteItem",
                        ],
                        "Effect": "Allow",
                        "Resource": assertions.Match.any_value(),
                    }
                ]
            }
        },
    )


def test_dynamo_table_policy_scoped_to_table_and_indexes(
    dev_config: DeploymentConfig,
) -> None:
    """DynamoTable policy resources must include table ARN + index ARN pattern."""
    app = cdk.App()
    stack = cdk.Stack(app, "TestStack")
    key = KmsKey(stack, "Key", config=dev_config)
    table = DynamoTable(stack, "Table", config=dev_config, encryption_key=key.key)
    _ = table.read_write_policy
    template = assertions.Template.from_stack(stack)
    policies = _all_managed_policies(template)
    assert len(policies) == 1
    statement = next(iter(policies.values()))["Properties"]["PolicyDocument"]["Statement"][0]
    resources = statement["Resource"]
    # Must have 2 resources: table ARN and table ARN/index/*
    assert len(resources) == 2
    for r in resources:
        assert r != "*"


# ── KmsKey policy tests ─────────────────────────────────────────────────────


def test_kms_encrypt_decrypt_policy_exact_actions(
    dev_config: DeploymentConfig,
) -> None:
    """KmsKey.encrypt_decrypt_policy must have exact KMS actions."""
    app = cdk.App()
    stack = cdk.Stack(app, "TestStack")
    key = KmsKey(stack, "Key", config=dev_config)
    _ = key.encrypt_decrypt_policy
    template = assertions.Template.from_stack(stack)
    template.has_resource_properties(
        "AWS::IAM::ManagedPolicy",
        {
            "PolicyDocument": {
                "Statement": [
                    {
                        "Action": [
                            "kms:Encrypt",
                            "kms:Decrypt",
                            "kms:GenerateDataKey",
                            "kms:GenerateDataKeyWithoutPlaintext",
                            "kms:DescribeKey",
                            "kms:ReEncryptFrom",
                            "kms:ReEncryptTo",
                        ],
                        "Effect": "Allow",
                        "Resource": assertions.Match.any_value(),
                    }
                ]
            }
        },
    )


def test_kms_policy_scoped_to_key_arn(dev_config: DeploymentConfig) -> None:
    """KMS policy must target specific key ARN, not wildcard."""
    app = cdk.App()
    stack = cdk.Stack(app, "TestStack")
    key = KmsKey(stack, "Key", config=dev_config)
    _ = key.encrypt_decrypt_policy
    template = assertions.Template.from_stack(stack)
    policies = _all_managed_policies(template)
    assert len(policies) == 1
    statement = next(iter(policies.values()))["Properties"]["PolicyDocument"]["Statement"][0]
    resources = statement["Resource"]
    # KMS policy should have a single key ARN resource (as a Fn::GetAtt ref)
    if isinstance(resources, list):
        for r in resources:
            assert r != "*"
    else:
        assert resources != "*"


# ── Global wildcard checks ─────────────────────────────────────────────────


def _extract_all_actions_and_resources(
    template: assertions.Template,
) -> list[tuple[list[str], list[str]]]:
    """Return (actions, resources) pairs from every managed policy statement."""
    policies = _all_managed_policies(template)
    results: list[tuple[list[str], list[str]]] = []
    for policy in policies.values():
        statements = policy["Properties"]["PolicyDocument"]["Statement"]
        for stmt in statements:
            actions_raw = stmt.get("Action", [])
            resources_raw = stmt.get("Resource", [])
            actions = actions_raw if isinstance(actions_raw, list) else [actions_raw]
            resources = resources_raw if isinstance(resources_raw, list) else [resources_raw]
            results.append((actions, resources))
    return results


def test_no_managed_policy_uses_star_resource(dev_config: DeploymentConfig) -> None:
    """No managed policy should have Resource: '*' (except known exceptions).

    Known exceptions: ecr:GetAuthorizationToken requires Resource: '*' per AWS docs.
    """
    template = _ingestion_template(dev_config)
    pairs = _extract_all_actions_and_resources(template)
    for actions, resources in pairs:
        if "*" in resources:
            # Allow ecr:GetAuthorizationToken which requires Resource: '*'
            allowed_star_actions = {"ecr:GetAuthorizationToken"}
            action_set = set(actions)
            assert action_set.issubset(allowed_star_actions), (
                f"Found Resource: '*' with disallowed actions: {actions}"
            )


def test_no_managed_policy_uses_wildcard_actions(dev_config: DeploymentConfig) -> None:
    """No managed policy should use trailing wildcard actions (e.g. 's3:*')."""
    template = _ingestion_template(dev_config)
    pairs = _extract_all_actions_and_resources(template)
    for actions, _resources in pairs:
        for action in actions:
            if isinstance(action, str):
                assert not action.endswith("*"), (
                    f"Found wildcard action: {action}. Use explicit action names."
                )
