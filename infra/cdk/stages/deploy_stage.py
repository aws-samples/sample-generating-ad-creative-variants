from __future__ import annotations

from typing import TYPE_CHECKING, Any

import aws_cdk as cdk
import cdk_nag
from cdk_nag import NagSuppressions

from stacks.data_stack import DataStack
from stacks.inference_stack import InferenceStack
from stacks.ingestion_stack import IngestionStack
from stacks.security_stack import SecurityStack

if TYPE_CHECKING:
    from constructs import Construct

    from deployment_config import DeploymentConfig


class DeployStage(cdk.Stage):
    """Deployment stage: security -> data -> inference -> ingestion."""

    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        config: DeploymentConfig,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, id, **kwargs)

        cdk.Aspects.of(self).add(cdk_nag.AwsSolutionsChecks(verbose=True))

        self._security_stack = SecurityStack(
            self,
            "SecurityStack",
            config=config,
            stack_name=config.stack_name("security"),
        )

        self._data_stack = DataStack(
            self,
            "DataStack",
            config=config,
            vpc=self._security_stack.vpc,
            kms_key=self._security_stack.kms_key,
            workload_subnet_type=self._security_stack.workload_subnet_type,
            stack_name=config.stack_name("data"),
        )
        self._data_stack.add_dependency(self._security_stack)

        self._inference_stack = InferenceStack(
            self,
            "InferenceStack",
            config=config,
            vpc=self._security_stack.vpc,
            kms_key=self._security_stack.kms_key,
            staging_read_policy=self._data_stack.staging_read_policy,
            results_read_write_policy=self._data_stack.results_read_write_policy,
            job_table_read_write_policy=self._data_stack.job_table_read_write_policy,
            kms_encrypt_decrypt_policy=self._security_stack.kms_encrypt_decrypt_policy,
            file_system=self._data_stack.model_fs.file_system,
            access_point=self._data_stack.model_fs.access_point,
            workload_subnet_type=self._security_stack.workload_subnet_type,
            stack_name=config.stack_name("inference"),
        )
        self._inference_stack.add_dependency(self._data_stack)

        self._ingestion_stack = IngestionStack(
            self,
            "IngestionStack",
            config=config,
            vpc=self._security_stack.vpc,
            workload_subnet_type=self._security_stack.workload_subnet_type,
            landing_bucket=self._data_stack.landing_bucket,
            staging_bucket=self._data_stack.staging_bucket,
            results_bucket=self._data_stack.results_bucket,
            landing_read_policy=self._data_stack.landing_read_policy,
            staging_read_write_policy=self._data_stack.staging_read_write_policy,
            job_table_read_write_policy=self._data_stack.job_table_read_write_policy,
            kms_encrypt_decrypt_policy=self._security_stack.kms_encrypt_decrypt_policy,
            job_queue_arn_ssm=self._inference_stack.job_queue_arn_ssm,
            job_definition_arn_ssm=self._inference_stack.job_definition_arn_ssm,
            stack_name=config.stack_name("ingestion"),
        )
        self._ingestion_stack.add_dependency(self._inference_stack)

        # ── CDK Nag suppressions (accepted risks) ───────────────────────────
        self._apply_nag_suppressions()

    def _apply_nag_suppressions(self) -> None:
        """Suppress accepted CDK Nag findings across all stacks in the stage."""
        # ── IAM5: wildcard sub-actions on resource-scoped ARNs ──────────────
        # CDK managed policies use resource-scoped ARNs; trailing wildcards
        # cover sub-actions (e.g. GetObject* includes GetObjectAcl,
        # GetObjectVersion). The resource ARN is specific.
        iam5_suppression = cdk_nag.NagPackSuppression(
            id="AwsSolutions-IAM5",
            reason=(
                "CDK managed policies use resource-scoped ARNs; trailing "
                "wildcards cover sub-actions (e.g. s3:GetObject* includes "
                "GetObjectAcl, GetObjectVersion). Resource ARNs are specific "
                "to this project's buckets, tables, and log groups."
            ),
        )
        for stack in (
            self._security_stack,
            self._data_stack,
            self._inference_stack,
            self._ingestion_stack,
        ):
            NagSuppressions.add_stack_suppressions(stack, [iam5_suppression])

        # ── IAM4: AWS managed policies required by CDK/service integrations ─
        iam4_suppression = cdk_nag.NagPackSuppression(
            id="AwsSolutions-IAM4",
            reason=(
                "AWS managed policies are required by service integrations: "
                "AmazonSSMManagedInstanceCore for EC2 Systems Manager, "
                "AWSLambdaBasicExecutionRole/AWSLambdaVPCAccessExecutionRole "
                "for Lambda logging/VPC networking, "
                "AmazonEC2ContainerServiceforEC2Role for ECS Batch workers, "
                "AmazonAPIGatewayPushToCloudWatchLogs for API GW logging."
            ),
        )
        for stack in (
            self._security_stack,
            self._data_stack,
            self._inference_stack,
            self._ingestion_stack,
        ):
            NagSuppressions.add_stack_suppressions(stack, [iam4_suppression])

        # ── L1: Lambda runtime version ─────────────────────────────────────
        # Python 3.12 is the latest stable Lambda runtime; 3.13 is not yet
        # available in AWS Lambda.
        NagSuppressions.add_stack_suppressions(
            self._data_stack,
            [
                cdk_nag.NagPackSuppression(
                    id="AwsSolutions-L1",
                    reason=(
                        "Python 3.12 is the latest stable Lambda runtime. "
                        "Python 3.13 is not yet available in AWS Lambda."
                    ),
                ),
            ],
        )
        NagSuppressions.add_stack_suppressions(
            self._ingestion_stack,
            [
                cdk_nag.NagPackSuppression(
                    id="AwsSolutions-L1",
                    reason=(
                        "Python 3.12 is the latest stable Lambda runtime. "
                        "Python 3.13 is not yet available in AWS Lambda."
                    ),
                ),
            ],
        )

        # ── SF1/SF2: CDK cr.Provider internal state machine ─────────────────
        # The Provider framework generates an internal Step Functions state
        # machine for async custom resources. It is CDK framework code, not
        # application logic — we cannot configure its logging/tracing.
        NagSuppressions.add_stack_suppressions(
            self._data_stack,
            [
                cdk_nag.NagPackSuppression(
                    id="AwsSolutions-SF1",
                    reason=(
                        "CDK cr.Provider internal state machine — "
                        "framework-generated, not application code. "
                        "Cannot configure logging on this construct."
                    ),
                ),
                cdk_nag.NagPackSuppression(
                    id="AwsSolutions-SF2",
                    reason=(
                        "CDK cr.Provider internal state machine — "
                        "framework-generated, not application code. "
                        "Cannot enable X-Ray tracing on this construct."
                    ),
                ),
            ],
        )

        # ── EC23: CDK Nag validation error on intrinsic function refs ────────
        # The VPC construct's security groups reference the VPC CIDR via
        # Fn::GetAtt which is an intrinsic. CDK Nag cannot validate intrinsics
        # and throws a validation error — this is a known edge case, not a
        # real open security group.
        NagSuppressions.add_stack_suppressions(
            self._security_stack,
            [
                cdk_nag.NagPackSuppression(
                    id="CdkNagValidationFailure",
                    reason=(
                        "CDK Nag validation error on intrinsic function refs "
                        "(Fn::GetAtt for VPC CIDR). Known edge case — security "
                        "groups are scoped to VPC CIDR, not 0.0.0.0/0."
                    ),
                ),
            ],
        )

    @property
    def security_stack(self) -> SecurityStack:
        return self._security_stack

    @property
    def data_stack(self) -> DataStack:
        return self._data_stack

    @property
    def inference_stack(self) -> InferenceStack:
        return self._inference_stack

    @property
    def ingestion_stack(self) -> IngestionStack:
        return self._ingestion_stack
