from __future__ import annotations

from typing import TYPE_CHECKING

import aws_cdk as cdk
import aws_cdk.assertions as assertions

from stacks.data_stack import DataStack
from stacks.inference_stack import InferenceStack
from stacks.ingestion_stack import IngestionStack
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


def test_two_lambda_handlers(dev_config: DeploymentConfig) -> None:
    # validate + csv_dispatch are our python3.12 handlers (the CDK-managed
    # log-retention provider is a separate nodejs runtime).
    template = _template(dev_config)
    fns = template.find_resources("AWS::Lambda::Function")
    py_fns = [f for f in fns.values() if f["Properties"].get("Runtime") == "python3.12"]
    assert len(py_fns) == 2


def test_rest_api_with_api_key(dev_config: DeploymentConfig) -> None:
    template = _template(dev_config)
    template.resource_count_is("AWS::ApiGateway::RestApi", 1)
    template.resource_count_is("AWS::ApiGateway::ApiKey", 1)
    template.resource_count_is("AWS::ApiGateway::UsagePlan", 1)


def test_post_and_get_methods_require_api_key(dev_config: DeploymentConfig) -> None:
    template = _template(dev_config)
    template.has_resource_properties(
        "AWS::ApiGateway::Method",
        {"HttpMethod": "POST", "ApiKeyRequired": True},
    )


def test_csv_eventbridge_rule(dev_config: DeploymentConfig) -> None:
    template = _template(dev_config)
    template.has_resource_properties(
        "AWS::Events::Rule",
        {
            "EventPattern": {
                "source": ["aws.s3"],
                "detail-type": ["Object Created"],
            }
        },
    )


def test_batch_submit_managed_policy(dev_config: DeploymentConfig) -> None:
    """batch:SubmitJob is granted via a managed policy, not inline."""
    template = _template(dev_config)
    template.has_resource_properties(
        "AWS::IAM::ManagedPolicy",
        {
            "PolicyDocument": {
                "Statement": assertions.Match.array_with(
                    [assertions.Match.object_like({"Action": "batch:SubmitJob"})]
                )
            }
        },
    )


def test_no_business_inline_policies(dev_config: DeploymentConfig) -> None:
    """No business IAM policies should be inline - only managed policies.

    CDK auto-generates inline policies for Lambda service roles (X-Ray tracing,
    VPC ENI management). Those are CDK-internal and acceptable; our business
    permissions (S3, DynamoDB, KMS, Batch) must all be customer managed policies.
    """
    template = _template(dev_config)
    policies = template.find_resources("AWS::IAM::Policy")
    # CDK-internal patterns: Custom resources, log retention, default service
    # role policies (Lambda X-Ray/VPC, execution roles)
    cdk_internal_patterns = ("Custom", "LogRetention", "DefaultPolicy")
    non_internal = {
        k: v
        for k, v in policies.items()
        if not any(pattern in k for pattern in cdk_internal_patterns)
    }
    assert len(non_internal) == 0, (
        f"Found inline policies (should be managed): {list(non_internal.keys())}"
    )
