from __future__ import annotations

from typing import TYPE_CHECKING

import aws_cdk as cdk
import aws_cdk.assertions as assertions

from cdk_constructs.secure_bucket import SecureBucket

if TYPE_CHECKING:
    from deployment_config import DeploymentConfig


def _make_stack(config: DeploymentConfig) -> cdk.Stack:
    app = cdk.App()
    stack = cdk.Stack(app, "TestStack")
    SecureBucket(stack, "TestBucket", config=config, component="test")
    return stack


def test_bucket_encryption_enabled(dev_config: DeploymentConfig) -> None:
    stack = _make_stack(dev_config)
    template = assertions.Template.from_stack(stack)
    template.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "BucketEncryption": assertions.Match.object_like(
                {
                    "ServerSideEncryptionConfiguration": [
                        {
                            "ServerSideEncryptionByDefault": {
                                "SSEAlgorithm": "AES256",
                            }
                        }
                    ]
                }
            )
        },
    )


def test_bucket_versioning_enabled(dev_config: DeploymentConfig) -> None:
    stack = _make_stack(dev_config)
    template = assertions.Template.from_stack(stack)
    template.has_resource_properties(
        "AWS::S3::Bucket",
        {"VersioningConfiguration": {"Status": "Enabled"}},
    )


def test_bucket_public_access_blocked(dev_config: DeploymentConfig) -> None:
    stack = _make_stack(dev_config)
    template = assertions.Template.from_stack(stack)
    template.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            }
        },
    )


def test_bucket_naming_convention(dev_config: DeploymentConfig) -> None:
    stack = _make_stack(dev_config)
    template = assertions.Template.from_stack(stack)
    template.has_resource_properties(
        "AWS::S3::Bucket",
        {"BucketName": "aurora-dev-test-123456789012-us-east-2"},
    )


def test_bucket_ssl_policy_present(dev_config: DeploymentConfig) -> None:
    stack = _make_stack(dev_config)
    template = assertions.Template.from_stack(stack)
    template.has_resource_properties(
        "AWS::S3::BucketPolicy",
        {
            "PolicyDocument": assertions.Match.object_like(
                {
                    "Statement": assertions.Match.array_with(
                        [
                            assertions.Match.object_like(
                                {
                                    "Effect": "Deny",
                                    "Condition": {"Bool": {"aws:SecureTransport": "false"}},
                                }
                            )
                        ]
                    )
                }
            )
        },
    )


def test_bucket_lifecycle_rule(dev_config: DeploymentConfig) -> None:
    stack = _make_stack(dev_config)
    template = assertions.Template.from_stack(stack)
    template.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "LifecycleConfiguration": assertions.Match.object_like(
                {
                    "Rules": assertions.Match.array_with(
                        [
                            assertions.Match.object_like(
                                {
                                    "NoncurrentVersionExpiration": {
                                        "NoncurrentDays": 30,
                                    },
                                    "Status": "Enabled",
                                }
                            )
                        ]
                    )
                }
            )
        },
    )


def test_prod_bucket_retain_policy(prod_config: DeploymentConfig) -> None:
    stack = _make_stack(prod_config)
    template = assertions.Template.from_stack(stack)
    template.has_resource(
        "AWS::S3::Bucket",
        {"DeletionPolicy": "Retain", "UpdateReplacePolicy": "Retain"},
    )


def test_dev_bucket_destroy_policy(dev_config: DeploymentConfig) -> None:
    stack = _make_stack(dev_config)
    template = assertions.Template.from_stack(stack)
    template.has_resource(
        "AWS::S3::Bucket",
        {"DeletionPolicy": "Delete", "UpdateReplacePolicy": "Delete"},
    )
