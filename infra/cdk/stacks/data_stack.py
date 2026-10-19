from __future__ import annotations

from typing import TYPE_CHECKING, Any

import aws_cdk as cdk

from cdk_constructs.dynamo_table import DynamoTable
from cdk_constructs.efs_seeder import EfsSeeder
from cdk_constructs.elastic_filesystem import ElasticFileSystem
from cdk_constructs.secure_bucket import SecureBucket

if TYPE_CHECKING:
    import aws_cdk.aws_dynamodb as dynamodb
    import aws_cdk.aws_ec2 as ec2
    import aws_cdk.aws_iam as iam
    import aws_cdk.aws_kms as kms
    import aws_cdk.aws_s3 as s3
    from constructs import Construct

    from deployment_config import DeploymentConfig


class DataStack(cdk.Stack):
    """Stateful layer: S3 pipeline buckets, DynamoDB job table, EFS model store."""

    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        config: DeploymentConfig,
        vpc: ec2.IVpc,
        kms_key: kms.IKey,
        workload_subnet_type: ec2.SubnetType,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, id, **kwargs)

        # ── Access logging bucket (dedicated, no logging on itself) ─────────
        self._access_logs = SecureBucket(
            self,
            "AccessLogs",
            config=config,
            component="access-logs",
            encryption_key=kms_key,
            is_logging_bucket=True,
            expiration_days=90,
        )

        # ── S3 pipeline buckets (video I/O; NOT model weights) ───────────────
        self._landing = SecureBucket(
            self,
            "Landing",
            config=config,
            component="landing",
            encryption_key=kms_key,
            event_bridge=True,
            expiration_days=7,
            server_access_logs_bucket=self._access_logs.bucket,
        )
        self._staging = SecureBucket(
            self,
            "Staging",
            config=config,
            component="staging",
            encryption_key=kms_key,
            expiration_days=7,
            server_access_logs_bucket=self._access_logs.bucket,
        )
        self._results = SecureBucket(
            self,
            "Results",
            config=config,
            component="results",
            encryption_key=kms_key,
            expiration_days=30,
            server_access_logs_bucket=self._access_logs.bucket,
        )

        # ── DynamoDB job table (TTL auto-expiry) ─────────────────────────────
        self._jobs = DynamoTable(
            self,
            "Jobs",
            config=config,
            encryption_key=kms_key,
        )

        # ── EFS model store (seeded once, mounted read-only) ─────────────────
        self._model_fs = ElasticFileSystem(
            self,
            "Models",
            config=config,
            vpc=vpc,
            encryption_key=kms_key,
            subnet_type=workload_subnet_type,
        )

        # ── One-shot EFS seeder (temporary public-subnet EC2) ────────────────
        self._seeder = EfsSeeder(
            self,
            "Seeder",
            config=config,
            vpc=vpc,
            file_system=self._model_fs.file_system,
            access_point=self._model_fs.access_point,
            auto_seed=config.auto_seed_efs,
        )

        for key, value in config.tags.items():
            cdk.Tags.of(self).add(key, value)

        cdk.CfnOutput(self, "LandingBucketName", value=self._landing.bucket.bucket_name)
        cdk.CfnOutput(self, "StagingBucketName", value=self._staging.bucket.bucket_name)
        cdk.CfnOutput(self, "ResultsBucketName", value=self._results.bucket.bucket_name)
        cdk.CfnOutput(self, "JobTableName", value=self._jobs.table.table_name)

    @property
    def access_logs_bucket(self) -> s3.IBucket:
        return self._access_logs.bucket

    @property
    def landing_bucket(self) -> s3.IBucket:
        return self._landing.bucket

    @property
    def staging_bucket(self) -> s3.IBucket:
        return self._staging.bucket

    @property
    def results_bucket(self) -> s3.IBucket:
        return self._results.bucket

    @property
    def job_table(self) -> dynamodb.ITable:
        return self._jobs.table

    @property
    def model_fs(self) -> ElasticFileSystem:
        return self._model_fs

    # ── Managed policy accessors (for cross-stack wiring) ────────────────────

    @property
    def landing_read_policy(self) -> iam.ManagedPolicy:
        return self._landing.read_policy

    @property
    def staging_read_policy(self) -> iam.ManagedPolicy:
        return self._staging.read_policy

    @property
    def staging_read_write_policy(self) -> iam.ManagedPolicy:
        return self._staging.read_write_policy

    @property
    def results_read_write_policy(self) -> iam.ManagedPolicy:
        return self._results.read_write_policy

    @property
    def job_table_read_write_policy(self) -> iam.ManagedPolicy:
        return self._jobs.read_write_policy
