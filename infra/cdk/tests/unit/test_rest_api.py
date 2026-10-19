"""REST API tests: IAM auth, request validation, logging, throttling."""

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


def test_all_methods_have_iam_auth(dev_config: DeploymentConfig) -> None:
    """Every API method must use AWS_IAM authorization."""
    template = _template(dev_config)
    methods = template.find_resources("AWS::ApiGateway::Method")
    # Exclude OPTIONS (CORS preflight, which CDK may add without auth)
    non_options = {
        k: v for k, v in methods.items() if v["Properties"].get("HttpMethod") != "OPTIONS"
    }
    assert len(non_options) >= 2, "Expected at least POST and GET methods"
    for logical_id, method in non_options.items():
        auth_type = method["Properties"].get("AuthorizationType")
        assert auth_type == "AWS_IAM", (
            f"Method {logical_id} has AuthorizationType={auth_type}, expected AWS_IAM"
        )


def test_post_method_has_request_validator(dev_config: DeploymentConfig) -> None:
    """POST method must have a request validator attached."""
    template = _template(dev_config)
    methods = template.find_resources("AWS::ApiGateway::Method")
    post_methods = {k: v for k, v in methods.items() if v["Properties"].get("HttpMethod") == "POST"}
    assert len(post_methods) >= 1, "Expected at least one POST method"
    for _logical_id, method in post_methods.items():
        # RequestValidatorId must be set (a Ref to the validator resource)
        validator_ref = method["Properties"].get("RequestValidatorId")
        assert validator_ref is not None, "POST method must have RequestValidatorId"


def test_access_log_destination_configured(dev_config: DeploymentConfig) -> None:
    """API Stage must have access log destination configured (AwsSolutions-APIG1)."""
    template = _template(dev_config)
    # The deployment stage configures access logging
    template.has_resource_properties(
        "AWS::ApiGateway::Stage",
        {
            "AccessLogSetting": assertions.Match.object_like(
                {"DestinationArn": assertions.Match.any_value()}
            ),
        },
    )


def test_throttling_configured_25_rps(dev_config: DeploymentConfig) -> None:
    """Usage plan must configure throttling at 25 requests per second."""
    template = _template(dev_config)
    template.has_resource_properties(
        "AWS::ApiGateway::UsagePlan",
        {
            "Throttle": {
                "RateLimit": 25,
                "BurstLimit": 10,
            }
        },
    )


def test_api_key_required_on_methods(dev_config: DeploymentConfig) -> None:
    """All methods (POST, GET) must require an API key for rate limiting."""
    template = _template(dev_config)
    methods = template.find_resources("AWS::ApiGateway::Method")
    non_options = {
        k: v for k, v in methods.items() if v["Properties"].get("HttpMethod") != "OPTIONS"
    }
    for logical_id, method in non_options.items():
        assert method["Properties"].get("ApiKeyRequired") is True, (
            f"Method {logical_id} does not require API key"
        )


def test_request_validator_validates_body(dev_config: DeploymentConfig) -> None:
    """Request validator must validate request body."""
    template = _template(dev_config)
    template.has_resource_properties(
        "AWS::ApiGateway::RequestValidator",
        {"ValidateRequestBody": True},
    )


def test_stage_has_execution_logging(dev_config: DeploymentConfig) -> None:
    """Stage must have method-level execution logging (AwsSolutions-APIG6)."""
    template = _template(dev_config)
    template.has_resource_properties(
        "AWS::ApiGateway::Stage",
        {
            "MethodSettings": assertions.Match.array_with(
                [assertions.Match.object_like({"LoggingLevel": "INFO"})]
            )
        },
    )
