from __future__ import annotations

from typing import TYPE_CHECKING, Any

import aws_cdk as cdk
import aws_cdk.aws_iam as iam
import aws_cdk.aws_ssm as ssm

from cdk_constructs.batch_compute import BatchCompute
from cdk_constructs.image_build import ImageBuild

if TYPE_CHECKING:
    import aws_cdk.aws_ec2 as ec2
    import aws_cdk.aws_efs as efs
    import aws_cdk.aws_kms as kms
    from constructs import Construct

    from deployment_config import DeploymentConfig


class InferenceStack(cdk.Stack):
    """Runs the Aurora editor container on GPU via AWS Batch.

    Exposes:
      - job_queue_arn      : submit render jobs here
      - job_definition_arn : the Aurora editor container job definition
    """

    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        config: DeploymentConfig,
        vpc: ec2.IVpc,
        kms_key: kms.IKey,
        staging_read_policy: iam.IManagedPolicy,
        results_read_write_policy: iam.IManagedPolicy,
        job_table_read_write_policy: iam.IManagedPolicy,
        kms_encrypt_decrypt_policy: iam.IManagedPolicy,
        file_system: efs.IFileSystem,
        access_point: efs.IAccessPoint,
        workload_subnet_type: ec2.SubnetType,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, id, **kwargs)

        # ── Bedrock managed policy (scoped to deployment region) ─────────────
        managed_policies: list[iam.IManagedPolicy] = [
            staging_read_policy,
            results_read_write_policy,
            job_table_read_write_policy,
            kms_encrypt_decrypt_policy,
        ]

        bedrock_models = [m for m in config.allowed_models if m not in config.non_bedrock_models]
        if bedrock_models:
            bedrock_resources: list[str] = []
            account = cdk.Stack.of(self).account
            regional_prefixes = ("us", "eu", "apac")
            for model_id in bedrock_models:
                bedrock_resources.append(
                    f"arn:aws:bedrock:us-*:{account}:inference-profile/{model_id}"
                )
                head, _, tail = model_id.partition(".")
                foundation_id = tail if head in regional_prefixes and tail else model_id
                bedrock_resources.append(f"arn:aws:bedrock:us-*::foundation-model/{foundation_id}")
            bedrock_policy = iam.ManagedPolicy(
                self,
                "BedrockInvokePolicy",
                managed_policy_name=config.resource_name("batch", "bedrock-policy"),
                statements=[
                    iam.PolicyStatement(
                        actions=["bedrock:InvokeModel"],
                        resources=bedrock_resources,
                    ),
                ],
            )
            managed_policies.append(bedrock_policy)

        # ── Batch compute + queue + job definition ───────────────────────────
        self._batch = BatchCompute(
            self,
            "GpuBatch",
            config=config,
            vpc=vpc,
            file_system=file_system,
            access_point=access_point,
            managed_policies=managed_policies,
            subnet_type=workload_subnet_type,
        )

        # On-demand CodeBuild image builder — only when we own the ECR repo
        # (i.e. no external worker image URI was supplied).
        if self._batch.repository is not None:
            self._worker_build = ImageBuild(
                self,
                "WorkerBuild",
                config=config,
                repository=self._batch.repository,
                encryption_key=kms_key,
            )
            cdk.CfnOutput(self, "WorkerBuildProject", value=self._worker_build.project_name)
            cdk.CfnOutput(
                self,
                "WorkerBuildSourceBucket",
                value=self._worker_build.source_bucket.bucket_name,
            )

        # ── SSM parameters for cross-stack references ───────────────────────
        # Using SSM instead of CfnOutput/exports avoids the CloudFormation
        # export deadlock when the job definition is replaced.
        self._job_queue_arn_param = ssm.StringParameter(
            self,
            "JobQueueArnParam",
            parameter_name=f"/{config.stack_prefix}/inference/job-queue-arn",
            string_value=self._batch.job_queue_arn,
        )
        self._job_definition_arn_param = ssm.StringParameter(
            self,
            "JobDefinitionArnParam",
            parameter_name=f"/{config.stack_prefix}/inference/job-definition-arn",
            string_value=self._batch.job_definition_arn,
        )

        for key, value in config.tags.items():
            cdk.Tags.of(self).add(key, value)

        cdk.CfnOutput(self, "JobQueueArn", value=self._batch.job_queue_arn)
        cdk.CfnOutput(self, "JobDefinitionArn", value=self._batch.job_definition_arn)

    @property
    def job_queue_arn(self) -> str:
        return self._batch.job_queue_arn

    @property
    def job_definition_arn(self) -> str:
        return self._batch.job_definition_arn

    @property
    def job_queue_arn_ssm(self) -> str:
        """SSM parameter name storing the job queue ARN."""
        return self._job_queue_arn_param.parameter_name

    @property
    def job_definition_arn_ssm(self) -> str:
        """SSM parameter name storing the job definition ARN."""
        return self._job_definition_arn_param.parameter_name
