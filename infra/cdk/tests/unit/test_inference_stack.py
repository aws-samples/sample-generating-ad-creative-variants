from __future__ import annotations

from typing import TYPE_CHECKING

import aws_cdk as cdk
import aws_cdk.assertions as assertions

from stacks.data_stack import DataStack
from stacks.inference_stack import InferenceStack
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
    return assertions.Template.from_stack(inference)


def test_batch_compute_environment(dev_config: DeploymentConfig) -> None:
    _template(dev_config).resource_count_is("AWS::Batch::ComputeEnvironment", 1)


def test_batch_job_queue(dev_config: DeploymentConfig) -> None:
    _template(dev_config).resource_count_is("AWS::Batch::JobQueue", 1)


def test_batch_job_definition(dev_config: DeploymentConfig) -> None:
    _template(dev_config).resource_count_is("AWS::Batch::JobDefinition", 1)


def test_ecr_repo_created_when_no_image_uri(dev_config: DeploymentConfig) -> None:
    # No worker_image_uri in dev config -> an ECR repo is created to push to.
    _template(dev_config).resource_count_is("AWS::ECR::Repository", 1)


def test_bedrock_managed_policy(dev_config: DeploymentConfig) -> None:
    # The worker's Nova planner path needs bedrock:InvokeModel via managed policy.
    template = _template(dev_config)
    template.has_resource_properties(
        "AWS::IAM::ManagedPolicy",
        {
            "PolicyDocument": {
                "Statement": assertions.Match.array_with(
                    [assertions.Match.object_like({"Action": "bedrock:InvokeModel"})]
                )
            }
        },
    )


def test_job_definition_requests_gpu(dev_config: DeploymentConfig) -> None:
    template = _template(dev_config)
    template.has_resource_properties(
        "AWS::Batch::JobDefinition",
        {
            "ContainerProperties": {
                "ResourceRequirements": assertions.Match.array_with(
                    [assertions.Match.object_like({"Type": "GPU", "Value": "1"})]
                )
            }
        },
    )


def test_no_business_inline_policies(dev_config: DeploymentConfig) -> None:
    """No business IAM policies should be inline - only managed policies.

    CDK auto-generates inline policies for execution roles (ECR pull, CW Logs),
    CodeBuild service roles, and Lambda service roles (X-Ray, VPC ENI). Those are
    CDK-internal and acceptable; our business permissions (S3, DynamoDB, KMS,
    Bedrock) must all be customer managed policies.
    """
    template = _template(dev_config)
    policies = template.find_resources("AWS::IAM::Policy")
    # CDK-internal patterns: Custom resources, log retention, default service
    # role policies (execution roles, CodeBuild roles, Lambda service roles)
    cdk_internal_patterns = ("Custom", "LogRetention", "DefaultPolicy")
    non_internal = {
        k: v
        for k, v in policies.items()
        if not any(pattern in k for pattern in cdk_internal_patterns)
    }
    assert len(non_internal) == 0, (
        f"Found inline policies (should be managed): {list(non_internal.keys())}"
    )


def test_batch_job_role_uses_managed_policies(dev_config: DeploymentConfig) -> None:
    """Batch job role must use managed policies (not inline) for business perms."""
    template = _template(dev_config)
    # The job role should have ManagedPolicyArns referencing our managed policies
    roles = template.find_resources("AWS::IAM::Role")
    job_roles = {k: v for k, v in roles.items() if "BatchJobRole" in k}
    assert len(job_roles) == 1, f"Expected 1 BatchJobRole, found {len(job_roles)}"
    role = next(iter(job_roles.values()))
    managed_arns = role["Properties"].get("ManagedPolicyArns", [])
    # Should have at least 4 managed policies: staging-read, results-rw, jobs-rw, kms
    assert len(managed_arns) >= 4, (
        f"BatchJobRole should have >= 4 managed policy ARNs, has {len(managed_arns)}"
    )


def test_bedrock_policy_uses_scoped_region(dev_config: DeploymentConfig) -> None:
    """Bedrock managed policy must use scoped region (not full wildcard '*')."""
    template = _template(dev_config)
    policies = template.find_resources("AWS::IAM::ManagedPolicy")
    bedrock_policies = {k: v for k, v in policies.items() if "Bedrock" in k or "bedrock" in str(v)}
    assert len(bedrock_policies) >= 1, "Expected at least one Bedrock policy"
    for _k, policy in bedrock_policies.items():
        statements = policy["Properties"]["PolicyDocument"]["Statement"]
        for stmt in statements:
            resources = stmt.get("Resource", [])
            if isinstance(resources, str):
                resources = [resources]
            for resource in resources:
                if isinstance(resource, str):
                    # Direct string resources should not contain '*' for region
                    assert resource != "*", "Bedrock policy should not use Resource: '*'"


def test_compute_environment_has_gpu_instance_types(dev_config: DeploymentConfig) -> None:
    """Compute environment must specify GPU instance types."""
    template = _template(dev_config)
    template.has_resource_properties(
        "AWS::Batch::ComputeEnvironment",
        {
            "ComputeResources": assertions.Match.object_like(
                {
                    "InstanceTypes": assertions.Match.any_value(),
                }
            )
        },
    )


def test_compute_environment_instance_types_are_gpu(dev_config: DeploymentConfig) -> None:
    """Compute environment instance types must be GPU types (g-family)."""
    template = _template(dev_config)
    envs = template.find_resources("AWS::Batch::ComputeEnvironment")
    assert len(envs) == 1
    compute = next(iter(envs.values()))
    instance_types = compute["Properties"]["ComputeResources"]["InstanceTypes"]
    for itype in instance_types:
        assert itype.startswith("g"), f"Instance type {itype} is not a GPU type (expected g-family)"


def test_job_definition_has_retry_attempts(dev_config: DeploymentConfig) -> None:
    """Job definition should have retry attempts configured."""
    template = _template(dev_config)
    template.has_resource_properties(
        "AWS::Batch::JobDefinition",
        {"RetryStrategy": {"Attempts": 2}},
    )


def test_job_definition_has_efs_volume(dev_config: DeploymentConfig) -> None:
    """Job definition must mount the EFS model store volume."""
    template = _template(dev_config)
    template.has_resource_properties(
        "AWS::Batch::JobDefinition",
        {
            "ContainerProperties": {
                "Volumes": assertions.Match.array_with(
                    [
                        assertions.Match.object_like(
                            {
                                "Name": "models",
                                "EfsVolumeConfiguration": assertions.Match.object_like(
                                    {"TransitEncryption": "ENABLED"}
                                ),
                            }
                        )
                    ]
                )
            }
        },
    )
