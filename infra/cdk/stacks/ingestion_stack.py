from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import aws_cdk as cdk
import aws_cdk.aws_ec2 as ec2
import aws_cdk.aws_iam as iam
import aws_cdk.aws_lambda as lambda_

from cdk_constructs.event_rule import EventRule
from cdk_constructs.lambda_function import LambdaFunction
from cdk_constructs.rest_api import RestApi

if TYPE_CHECKING:
    import aws_cdk.aws_s3 as s3
    from constructs import Construct

    from deployment_config import DeploymentConfig

_CDK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAMBDAS_DIR = os.path.join(_CDK_DIR, "lambdas")


class IngestionStack(cdk.Stack):
    """Front door: synchronous API + asynchronous CSV batch dispatch.

    Both paths validate -> stage to S3 -> record in DynamoDB -> submit a Batch
    job to the inference queue.
    """

    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        config: DeploymentConfig,
        vpc: ec2.IVpc,
        workload_subnet_type: ec2.SubnetType,
        landing_bucket: s3.IBucket,
        staging_bucket: s3.IBucket,
        results_bucket: s3.IBucket,
        landing_read_policy: iam.IManagedPolicy,
        staging_read_write_policy: iam.IManagedPolicy,
        job_table_read_write_policy: iam.IManagedPolicy,
        kms_encrypt_decrypt_policy: iam.IManagedPolicy,
        job_queue_arn_ssm: str,
        job_definition_arn_ssm: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, id, **kwargs)

        region = cdk.Stack.of(self).region
        account = cdk.Stack.of(self).account

        common_env = {
            "LANDING_BUCKET": landing_bucket.bucket_name,
            "STAGING_BUCKET": staging_bucket.bucket_name,
            "RESULTS_BUCKET": results_bucket.bucket_name,
            "JOB_TABLE": config.resource_name("jobs", "table"),
            "JOB_QUEUE_ARN_SSM": job_queue_arn_ssm,
            "JOB_DEFINITION_ARN_SSM": job_definition_arn_ssm,
            "MAX_NUM_FRAMES": str(config.max_num_frames),
            "MIN_NUM_FRAMES": str(config.min_num_frames),
            "MAX_DIMENSION": str(config.max_dimension),
            "ALLOWED_VIDEO_EXT": ",".join(config.allowed_video_ext),
            "ALLOWED_IMAGE_EXT": ",".join(config.allowed_image_ext),
            "ALLOWED_MODELS": ",".join(config.allowed_models),
        }

        vpc_subnets = ec2.SubnetSelection(subnet_type=workload_subnet_type)

        # ── Validate / preprocess Lambda (API handler) ───────────────────────
        self._validate = LambdaFunction(
            self,
            "Validate",
            code_path=os.path.join(LAMBDAS_DIR, "validate"),
            timeout_seconds=60,
            memory_mb=512,
            environment=common_env,
            vpc=vpc,
            vpc_subnets=vpc_subnets,
        ).function

        # ── Async CSV dispatch Lambda (EventBridge-triggered) ────────────────
        self._csv_dispatch = LambdaFunction(
            self,
            "CsvDispatch",
            code_path=os.path.join(LAMBDAS_DIR, "csv_dispatch"),
            timeout_seconds=300,
            memory_mb=512,
            environment=common_env,
            vpc=vpc,
            vpc_subnets=vpc_subnets,
        ).function

        # ── Managed policy for Batch job submission ──────────────────────────
        # Resources scoped to this account's Batch queues and job definitions.
        # CDK-generated Batch resource names use internal logical IDs (not our
        # prefix), so we scope to account + region only.
        batch_submit_policy = iam.ManagedPolicy(
            self,
            "BatchSubmitPolicy",
            managed_policy_name=config.resource_name("ingestion", "batch-submit-policy"),
            statements=[
                iam.PolicyStatement(
                    actions=["batch:SubmitJob"],
                    resources=[
                        f"arn:aws:batch:{region}:{account}:job-queue/*",
                        f"arn:aws:batch:{region}:{account}:job-definition/*",
                    ],
                ),
            ],
        )

        # ── Managed policy for SSM parameter reads (Batch ARN lookup) ────────
        ssm_read_policy = iam.ManagedPolicy(
            self,
            "SsmReadPolicy",
            managed_policy_name=config.resource_name("ingestion", "ssm-read-policy"),
            statements=[
                iam.PolicyStatement(
                    actions=["ssm:GetParameter"],
                    resources=[
                        f"arn:aws:ssm:{region}:{account}:parameter{job_queue_arn_ssm}",
                        f"arn:aws:ssm:{region}:{account}:parameter{job_definition_arn_ssm}",
                    ],
                ),
            ],
        )

        # ── Attach all managed policies to both Lambda roles ─────────────────
        for fn in (self._validate, self._csv_dispatch):
            role = fn.role
            assert role is not None  # noqa: S101
            role.add_managed_policy(landing_read_policy)
            role.add_managed_policy(staging_read_write_policy)
            role.add_managed_policy(job_table_read_write_policy)
            role.add_managed_policy(kms_encrypt_decrypt_policy)
            role.add_managed_policy(batch_submit_policy)
            role.add_managed_policy(ssm_read_policy)

        # ── CSV drops in landing/batch/ -> EventBridge -> dispatch Lambda ─────
        EventRule(
            self,
            "CsvUploadTrigger",
            bucket_name=landing_bucket.bucket_name,
            prefix="batch/",
            target=self._csv_dispatch,
        )

        # ── REST API ─────────────────────────────────────────────────────────
        self._rest = RestApi(self, "Rest", config=config, handler=self._validate)

        for key, value in config.tags.items():
            cdk.Tags.of(self).add(key, value)

        cdk.CfnOutput(self, "ApiEndpoint", value=f"{self._rest.url}jobs")
        cdk.CfnOutput(self, "ApiKeyId", value=self._rest.api_key_id)

    @property
    def validate_function(self) -> lambda_.IFunction:
        return self._validate

    @property
    def csv_dispatch_function(self) -> lambda_.IFunction:
        return self._csv_dispatch
