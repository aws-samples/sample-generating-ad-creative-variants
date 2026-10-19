from __future__ import annotations

from typing import TYPE_CHECKING

import aws_cdk.aws_dynamodb as dynamodb
import aws_cdk.aws_iam as iam
from constructs import Construct

from cdk_constructs._common import REMOVAL_POLICY_MAP

if TYPE_CHECKING:
    import aws_cdk.aws_kms as kms

    from deployment_config import DeploymentConfig


class DynamoTable(Construct):
    """DynamoDB table with pay-per-request billing, KMS encryption, and TTL."""

    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        config: DeploymentConfig,
        encryption_key: kms.IKey,
        ttl_attribute: str = "expires_at",
    ) -> None:
        super().__init__(scope, id)

        self._config = config
        self._read_write_policy: iam.ManagedPolicy | None = None

        self._table = dynamodb.Table(
            self,
            "Table",
            table_name=config.resource_name("jobs", "table"),
            partition_key=dynamodb.Attribute(name="job_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.CUSTOMER_MANAGED,
            encryption_key=encryption_key,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True,
            ),
            time_to_live_attribute=ttl_attribute,
            removal_policy=REMOVAL_POLICY_MAP[config.removal_policy],
        )

        self._table.add_global_secondary_index(
            index_name="status-created_at-index",
            partition_key=dynamodb.Attribute(name="status", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="created_at", type=dynamodb.AttributeType.STRING),
        )

    @property
    def table(self) -> dynamodb.ITable:
        return self._table

    @property
    def read_write_policy(self) -> iam.ManagedPolicy:
        """Lazily created managed policy granting read-write access to this table."""
        if self._read_write_policy is None:
            self._read_write_policy = iam.ManagedPolicy(
                self,
                "ReadWritePolicy",
                managed_policy_name=self._config.resource_name("jobs", "table-rw-policy"),
                statements=[
                    iam.PolicyStatement(
                        actions=[
                            "dynamodb:GetItem",
                            "dynamodb:PutItem",
                            "dynamodb:UpdateItem",
                            "dynamodb:DeleteItem",
                            "dynamodb:Query",
                            "dynamodb:Scan",
                            "dynamodb:BatchGetItem",
                            "dynamodb:BatchWriteItem",
                        ],
                        resources=[
                            self._table.table_arn,
                            f"{self._table.table_arn}/index/*",
                        ],
                    ),
                ],
            )
        return self._read_write_policy
