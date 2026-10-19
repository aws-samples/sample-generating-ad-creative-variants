from __future__ import annotations

from typing import TYPE_CHECKING

import aws_cdk as cdk
import aws_cdk.assertions as assertions

from stacks.data_stack import DataStack
from stacks.security_stack import SecurityStack

if TYPE_CHECKING:
    from deployment_config import DeploymentConfig


def _template(config: DeploymentConfig) -> assertions.Template:
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
    return assertions.Template.from_stack(data)


def test_four_buckets_including_access_logs(dev_config: DeploymentConfig) -> None:
    _template(dev_config).resource_count_is("AWS::S3::Bucket", 4)


def test_buckets_kms_encrypted(dev_config: DeploymentConfig) -> None:
    template = _template(dev_config)
    for bucket in template.find_resources("AWS::S3::Bucket").values():
        rules = bucket["Properties"]["BucketEncryption"]["ServerSideEncryptionConfiguration"]
        assert rules[0]["ServerSideEncryptionByDefault"]["SSEAlgorithm"] == "aws:kms"


def test_job_table_ttl(dev_config: DeploymentConfig) -> None:
    _template(dev_config).has_resource_properties(
        "AWS::DynamoDB::Table",
        {"TimeToLiveSpecification": {"AttributeName": "expires_at", "Enabled": True}},
    )


def test_efs_encrypted(dev_config: DeploymentConfig) -> None:
    _template(dev_config).has_resource_properties("AWS::EFS::FileSystem", {"Encrypted": True})


def test_seeder_launch_template(dev_config: DeploymentConfig) -> None:
    _template(dev_config).resource_count_is("AWS::EC2::LaunchTemplate", 1)


def test_dev_is_destroyable(dev_config: DeploymentConfig) -> None:
    template = _template(dev_config)
    for fs in template.find_resources("AWS::EFS::FileSystem").values():
        assert fs.get("DeletionPolicy") in (None, "Delete")


def test_prod_retains_efs(prod_config: DeploymentConfig) -> None:
    template = _template(prod_config)
    template.has_resource("AWS::EFS::FileSystem", {"DeletionPolicy": "Retain"})


def test_access_logging_bucket_exists(dev_config: DeploymentConfig) -> None:
    """Data stack must create exactly 4 buckets: access-logs + 3 data buckets."""
    template = _template(dev_config)
    buckets = template.find_resources("AWS::S3::Bucket")
    assert len(buckets) == 4


def test_data_buckets_have_logging_configuration(dev_config: DeploymentConfig) -> None:
    """Landing, staging, and results buckets must log to the access-logs bucket."""
    template = _template(dev_config)
    buckets = template.find_resources("AWS::S3::Bucket")
    buckets_with_logging = {
        k: v for k, v in buckets.items() if v["Properties"].get("LoggingConfiguration") is not None
    }
    # 3 data buckets should have LoggingConfiguration (access-logs bucket does not)
    assert len(buckets_with_logging) == 3, (
        f"Expected 3 buckets with LoggingConfiguration, found {len(buckets_with_logging)}"
    )


def test_access_logs_bucket_has_no_logging(dev_config: DeploymentConfig) -> None:
    """Access-logs bucket must NOT have LoggingConfiguration (would be circular)."""
    template = _template(dev_config)
    buckets = template.find_resources("AWS::S3::Bucket")
    buckets_without_logging = {
        k: v for k, v in buckets.items() if v["Properties"].get("LoggingConfiguration") is None
    }
    # Exactly 1 bucket (access-logs) should have no logging config
    assert len(buckets_without_logging) == 1


def test_efs_access_point_uses_uid_gid_1000(dev_config: DeploymentConfig) -> None:
    """EFS access point must use UID/GID 1000 (not root)."""
    template = _template(dev_config)
    template.has_resource_properties(
        "AWS::EFS::AccessPoint",
        {
            "PosixUser": {"Uid": "1000", "Gid": "1000"},
            "RootDirectory": {
                "CreationInfo": {
                    "OwnerUid": "1000",
                    "OwnerGid": "1000",
                    "Permissions": "0755",
                }
            },
        },
    )


def test_access_logs_bucket_has_expiration(dev_config: DeploymentConfig) -> None:
    """Access-logs bucket should have lifecycle expiration (90 days)."""
    template = _template(dev_config)
    buckets = template.find_resources("AWS::S3::Bucket")
    # Find the access-logs bucket (it has no LoggingConfiguration)
    for _k, bucket in buckets.items():
        props = bucket["Properties"]
        if props.get("LoggingConfiguration") is None:
            lifecycle = props.get("LifecycleConfiguration", {})
            rules = lifecycle.get("Rules", [])
            # Should have an expiration rule
            has_expiration = any(r.get("ExpirationInDays") is not None for r in rules)
            assert has_expiration, "Access-logs bucket must have expiration lifecycle rule"
            return
    raise AssertionError("Could not find the access-logs bucket")


def test_efs_seeder_downloads_editor_to_both_paths(dev_config: DeploymentConfig) -> None:
    """Seeder must provide editor checkpoint at both paths needed by Aurora code.

    - /models/aurora_editor.safetensors (for run_single_sample.py / resolve_editor_ckpt)
    - /models/aurora_video_editor/model.safetensors (for editor_bridge_video / default_paths)
    """
    from cdk_constructs.efs_seeder import _DOWNLOAD_PY

    assert "aurora_editor.safetensors" in _DOWNLOAD_PY
    assert "aurora_video_editor" in _DOWNLOAD_PY
    assert "model.safetensors" in _DOWNLOAD_PY
    assert "local_dir=model_dir" in _DOWNLOAD_PY
