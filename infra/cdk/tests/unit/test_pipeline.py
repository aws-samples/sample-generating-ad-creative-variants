"""Pipeline construct tests: V2 type, gates, artifacts bucket security."""

from __future__ import annotations

from typing import TYPE_CHECKING

import aws_cdk as cdk
import aws_cdk.assertions as assertions

from stacks.pipeline_stack import PipelineStack

if TYPE_CHECKING:
    from deployment_config import DeploymentConfig


def _template(config: DeploymentConfig) -> assertions.Template:
    app = cdk.App()
    stack = PipelineStack(
        app,
        "TestPipeline",
        config=config,
        env=cdk.Environment(account="123456789012", region="us-east-2"),
    )
    return assertions.Template.from_stack(stack)


def test_pipeline_is_v2_type(dev_config: DeploymentConfig) -> None:
    """Pipeline must use V2 type for improved features."""
    template = _template(dev_config)
    template.has_resource_properties(
        "AWS::CodePipeline::Pipeline",
        {"PipelineType": "V2"},
    )


def test_gates_wave_has_three_codebuild_steps(dev_config: DeploymentConfig) -> None:
    """The Gates wave must produce 3 CodeBuild projects (Quality, Tests, Security)."""
    template = _template(dev_config)
    projects = template.find_resources("AWS::CodeBuild::Project")
    # Find gate projects by matching their step names in the build spec or source
    gate_names = {"QualityGate", "UnitTests", "SecurityGate"}
    gate_projects = {}
    for logical_id, resource in projects.items():
        for gate_name in gate_names:
            if gate_name in logical_id:
                gate_projects[gate_name] = resource
                break
    assert len(gate_projects) == 3, (
        f"Expected 3 gate projects ({gate_names}), "
        f"found: {list(gate_projects.keys())} "
        f"out of all projects: {list(projects.keys())}"
    )


def test_artifacts_bucket_has_encryption(dev_config: DeploymentConfig) -> None:
    """Pipeline artifacts bucket must have server-side encryption."""
    template = _template(dev_config)
    buckets = template.find_resources("AWS::S3::Bucket")
    # Find the artifacts bucket by name pattern
    artifacts_found = False
    for _logical_id, bucket in buckets.items():
        props = bucket.get("Properties", {})
        name = props.get("BucketName", "")
        if "cicd-artifacts" in str(name):
            encryption = props.get("BucketEncryption", {})
            rules = encryption.get("ServerSideEncryptionConfiguration", [])
            assert len(rules) > 0, "Artifacts bucket must have encryption rules"
            artifacts_found = True
            break
    assert artifacts_found, "Could not find cicd-artifacts bucket"


def test_artifacts_bucket_has_versioning(dev_config: DeploymentConfig) -> None:
    """Pipeline artifacts bucket must have versioning enabled."""
    template = _template(dev_config)
    buckets = template.find_resources("AWS::S3::Bucket")
    for _logical_id, bucket in buckets.items():
        props = bucket.get("Properties", {})
        name = props.get("BucketName", "")
        if "cicd-artifacts" in str(name):
            versioning = props.get("VersioningConfiguration", {})
            assert versioning.get("Status") == "Enabled"
            return
    raise AssertionError("Could not find cicd-artifacts bucket")


def test_artifacts_bucket_blocks_public_access(dev_config: DeploymentConfig) -> None:
    """Pipeline artifacts bucket must block all public access."""
    template = _template(dev_config)
    buckets = template.find_resources("AWS::S3::Bucket")
    for _logical_id, bucket in buckets.items():
        props = bucket.get("Properties", {})
        name = props.get("BucketName", "")
        if "cicd-artifacts" in str(name):
            block = props.get("PublicAccessBlockConfiguration", {})
            assert block.get("BlockPublicAcls") is True
            assert block.get("BlockPublicPolicy") is True
            assert block.get("IgnorePublicAcls") is True
            assert block.get("RestrictPublicBuckets") is True
            return
    raise AssertionError("Could not find cicd-artifacts bucket")


def test_pipeline_has_codecommit_source(dev_config: DeploymentConfig) -> None:
    """Pipeline must have a CodeCommit source action."""
    template = _template(dev_config)
    template.has_resource_properties(
        "AWS::CodePipeline::Pipeline",
        {
            "Stages": assertions.Match.array_with(
                [
                    assertions.Match.object_like(
                        {
                            "Name": "Source",
                        }
                    )
                ]
            )
        },
    )
