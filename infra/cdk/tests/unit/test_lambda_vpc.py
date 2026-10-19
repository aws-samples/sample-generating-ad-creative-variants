"""Lambda VPC, tracing, and logging tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

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


def _get_python_lambdas(template: assertions.Template) -> dict[str, dict[str, Any]]:
    """Return only the Python 3.12 Lambda functions (not CDK log-retention Nodejs)."""
    fns = template.find_resources("AWS::Lambda::Function")
    return {k: dict(v) for k, v in fns.items() if v["Properties"].get("Runtime") == "python3.12"}


def test_all_lambdas_have_vpc_config(dev_config: DeploymentConfig) -> None:
    """All Python Lambda functions must be deployed in a VPC."""
    template = _template(dev_config)
    py_fns = _get_python_lambdas(template)
    assert len(py_fns) >= 2, "Expected at least 2 Python Lambda functions"
    for logical_id, fn in py_fns.items():
        vpc_config = fn["Properties"].get("VpcConfig")
        assert vpc_config is not None, f"Lambda {logical_id} missing VpcConfig"
        subnet_ids = vpc_config.get("SubnetIds", [])
        assert len(subnet_ids) > 0, f"Lambda {logical_id} has no SubnetIds in VpcConfig"
        sg_ids = vpc_config.get("SecurityGroupIds", [])
        assert len(sg_ids) > 0, f"Lambda {logical_id} has no SecurityGroupIds"


def test_all_lambdas_have_xray_tracing(dev_config: DeploymentConfig) -> None:
    """All Python Lambda functions must have X-Ray tracing enabled."""
    template = _template(dev_config)
    py_fns = _get_python_lambdas(template)
    for logical_id, fn in py_fns.items():
        tracing = fn["Properties"].get("TracingConfig", {})
        mode = tracing.get("Mode")
        assert mode == "Active", (
            f"Lambda {logical_id} has TracingConfig.Mode={mode}, expected 'Active'"
        )


def test_all_lambdas_have_log_group_with_retention(dev_config: DeploymentConfig) -> None:
    """All Lambda functions must have a dedicated log group with retention."""
    template = _template(dev_config)
    # Check that log groups exist with retention configured
    log_groups = template.find_resources("AWS::Logs::LogGroup")
    # Filter out API Gateway access logs and CDK internal log groups
    lambda_log_groups = {k: v for k, v in log_groups.items() if "LogRetention" not in k}
    # We should have log groups for our Lambda functions
    assert len(lambda_log_groups) >= 2, (
        f"Expected at least 2 Lambda log groups, found {len(lambda_log_groups)}"
    )
    for logical_id, lg in lambda_log_groups.items():
        retention = lg["Properties"].get("RetentionInDays")
        assert retention is not None, f"Log group {logical_id} has no RetentionInDays"
        assert retention > 0, f"Log group {logical_id} retention must be > 0"


def test_lambdas_have_log_group_reference(dev_config: DeploymentConfig) -> None:
    """Lambda functions must reference their dedicated log group (not auto-created)."""
    template = _template(dev_config)
    py_fns = _get_python_lambdas(template)
    for logical_id, fn in py_fns.items():
        # CDK sets LoggingConfig.LogGroup when a log group is explicitly provided
        logging_config = fn["Properties"].get("LoggingConfig")
        assert logging_config is not None, (
            f"Lambda {logical_id} should have LoggingConfig with explicit log group"
        )


def test_lambda_timeout_configured(dev_config: DeploymentConfig) -> None:
    """Lambda functions must have explicit timeout configured (not default 3s)."""
    template = _template(dev_config)
    py_fns = _get_python_lambdas(template)
    for logical_id, fn in py_fns.items():
        timeout = fn["Properties"].get("Timeout")
        assert timeout is not None, f"Lambda {logical_id} must have explicit Timeout"
        assert timeout > 3, f"Lambda {logical_id} timeout={timeout}s, should be > default 3s"


def test_lambda_memory_configured(dev_config: DeploymentConfig) -> None:
    """Lambda functions must have memory configured above default 128MB."""
    template = _template(dev_config)
    py_fns = _get_python_lambdas(template)
    for logical_id, fn in py_fns.items():
        memory = fn["Properties"].get("MemorySize")
        assert memory is not None, f"Lambda {logical_id} must have MemorySize set"
        assert memory >= 256, f"Lambda {logical_id} memory={memory}MB, expected >= 256MB"
